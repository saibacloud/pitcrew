# Gemini AI service
# All AI interactions, prompt building, image prep

import io
import logging
import os
import time
from pathlib import Path

from PIL import Image
from google import genai
from google.genai import types

log = logging.getLogger(__name__)

client = genai.Client(api_key=os.getenv('GENAI_API_KEY'))

MODEL = 'gemini-flash-latest'


# ── Prompt helpers ───────────────────────────────────────────────────────────

def car_description(car: dict) -> str:
    return ' '.join(filter(None, [
        str(car.get('year') or ''),
        car.get('make'),
        car.get('model'),
        car.get('trim'),
        f"({car['engine']})" if car.get('engine') else None,
    ])) or 'Unknown car'


def build_research_prompt(car: dict, query: str) -> str:
    desc = car_description(car)
    options_line = f"\nOptions/package: {car['options']}" if car.get('options') else ''
    return (
        f"You are an expert automotive mechanic and technical researcher.\n"
        f"The car in question is: {desc}.{options_line}\n\n"
        f"Answer the following question concisely and practically. "
        f"Focus on specs, procedures, torque values, part numbers, and facts "
        f"specific to this car. Keep your answer under 400 words.\n\n"
        f"Question: {query}"
    )


ANGLE_LABELS = {
    'front':     'front view',
    'sideD':     "driver's side view",
    'sideP':     "passenger's side view",
    'rear':      'rear view',
    'engine':    'engine bay view',
    'underside': 'underside / chassis view',
    'interior':  'interior / cabin view',
}


def build_pin_research_prompt(car: dict, pin: dict, angle: str) -> str:
    desc = car_description(car)
    angle_label = ANGLE_LABELS.get(angle, angle)
    label = pin.get('label', '')
    x_pct = pin.get('x_pct')
    y_pct = pin.get('y_pct')
    pin_notes = pin.get('notes', '')

    position_hint = ''
    if x_pct is not None and y_pct is not None:
        position_hint = (
            f"The pin is placed at approximately {x_pct:.0f}% from the left "
            f"and {y_pct:.0f}% from the top of the image."
        )

    return (
        f"You are an expert automotive mechanic and technical researcher.\n"
        f"The vehicle is: {desc}. This photo is the {angle_label}.\n\n"
        f"A pin has been placed on a specific component labelled: \"{label}\".\n"
        f"{position_hint}\n"
        f"{f'Additional context from the user: {pin_notes}' if pin_notes else ''}\n\n"
        f"Please research this specific component and provide:\n"
        f"1. Exact component name and function\n"
        f"2. OEM part number(s) for this vehicle\n"
        f"3. Common aftermarket / compatible alternatives\n"
        f"4. Service interval or maintenance notes\n"
        f"5. Known failure modes or things to watch for\n\n"
        f"Be specific to this vehicle. Keep the answer under 500 words. "
        f"Use the photo as visual context to confirm the component if visible."
    )


def build_docsearch_prompt(car: dict, question: str) -> str:
    desc = car_description(car)
    return (
        f"You are an expert automotive technician assistant for a {desc}.\n"
        f"The content above consists of relevant excerpts from this car's uploaded documents.\n"
        f"Answer the following question as specifically as possible, citing page numbers or "
        f"section names from the excerpts where available.\n\n"
        f"Question: {question}"
    )


# ── Image helpers ────────────────────────────────────────────────────────────

MIME_MAP = {
    'JPEG': 'image/jpeg', 'PNG': 'image/png',
    'WEBP': 'image/webp', 'GIF': 'image/gif',
}


def strip_exif(raw_bytes: bytes) -> tuple[bytes, str]:
    """Strip EXIF from image bytes. Returns (clean_bytes, mime_type)."""
    img = Image.open(io.BytesIO(raw_bytes))
    fmt = (img.format or 'JPEG').upper()
    buf = io.BytesIO()
    save_kwargs: dict = {'format': fmt}
    if fmt in ('JPEG', 'WEBP'):
        save_kwargs['exif'] = b''
    img.save(buf, **save_kwargs)
    mime_type = MIME_MAP.get(fmt, 'image/jpeg')
    return buf.getvalue(), mime_type


def compress_for_storage(raw_bytes: bytes, max_edge: int = 2048) -> bytes:
    """Resize image to max_edge on longest side, return compressed JPEG/PNG."""
    img = Image.open(io.BytesIO(raw_bytes))
    fmt = (img.format or 'JPEG').upper()

    # Strip EXIF
    save_kwargs: dict = {'format': fmt}
    if fmt in ('JPEG', 'WEBP'):
        save_kwargs['exif'] = b''
        save_kwargs['quality'] = 85

    # Resize if larger than max_edge
    w, h = img.size
    if max(w, h) > max_edge:
        ratio = max_edge / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, **save_kwargs)
    return buf.getvalue()


def make_thumbnail(raw_bytes: bytes, size: int = 400) -> bytes:
    """Generate a square-ish thumbnail, always JPEG."""
    img = Image.open(io.BytesIO(raw_bytes))
    img = img.convert('RGB')
    w, h = img.size
    ratio = size / max(w, h)
    new_size = (int(w * ratio), int(h * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=80, exif=b'')
    return buf.getvalue()


# ── AI calls ─────────────────────────────────────────────────────────────────

async def research(prompt: str, temperature: float = 0.3) -> str:
    """Simple text-only Gemini call."""
    t0 = time.monotonic()
    try:
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        )
        elapsed = time.monotonic() - t0
        log.info("Gemini research completed in %.1fs", elapsed)
        return response.text
    except Exception:
        log.exception("Gemini research call failed")
        raise


async def research_with_image(
    prompt_text: str,
    image_bytes: bytes,
    mime_type: str,
    temperature: float = 0.2,
) -> str:
    """Gemini call with an image part."""
    t0 = time.monotonic()
    try:
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=types.Content(
                role='user',
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=prompt_text),
                ]
            ),
            config=types.GenerateContentConfig(temperature=temperature),
        )
        elapsed = time.monotonic() - t0
        log.info("Gemini image research completed in %.1fs", elapsed)
        return response.text.strip()
    except Exception:
        log.exception("Gemini image research call failed")
        raise


async def ask_documents(content_parts: list, car: dict, question: str) -> str:
    """Gemini call with document content parts + question."""
    content_parts.append(types.Part.from_text(text=build_docsearch_prompt(car, question)))
    t0 = time.monotonic()
    try:
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=types.Content(role='user', parts=content_parts),
            config=types.GenerateContentConfig(temperature=0.1),
        )
        elapsed = time.monotonic() - t0
        log.info("Gemini doc search completed in %.1fs", elapsed)
        return response.text.strip()
    except Exception:
        log.exception("Gemini document search call failed")
        raise


# ── Document chunking ────────────────────────────────────────────────────────

def relevant_chunks(
    text: str,
    question: str,
    chunk_size: int = 2000,
    overlap: int = 500,
    top_n: int = 8,
) -> list[str]:
    """Return the top_n overlapping chunks most relevant to the question by keyword overlap."""
    if not text:
        return []

    # Build overlapping chunks
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += chunk_size - overlap

    stop = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has',
        'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
        'might', 'can', 'for', 'of', 'to', 'in', 'on', 'at', 'by', 'from',
        'with', 'and', 'or', 'but', 'if', 'what', 'how', 'where', 'when',
        'which', 'who', 'that', 'this', 'i', 'my', 'me',
    }
    keywords = {w for w in question.lower().split() if w not in stop and len(w) > 2}
    scored = sorted(
        ((sum(1 for kw in keywords if kw in chunk.lower()), chunk) for chunk in chunks),
        key=lambda x: x[0], reverse=True,
    )
    result = [c for score, c in scored[:top_n] if score > 0]
    return result or [chunks[0]]
