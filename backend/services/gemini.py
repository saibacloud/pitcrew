# Gemini AI service
# All AI interactions, prompt building, image prep

import io
import logging
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from google import genai
from google.genai import types

log = logging.getLogger(__name__)

# Retries cover 408/429/5xx and transient network errors with exponential backoff
client = genai.Client(
    api_key=os.getenv('GENAI_API_KEY'),
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2.0, max_delay=15.0),
    ),
)

MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest')


# ── Prompt helpers ───────────────────────────────────────────────────────────

def car_description(car: dict) -> str:
    return ' '.join(filter(None, [
        str(car.get('year') or ''),
        car.get('make'),
        car.get('model'),
        car.get('trim'),
        f"({car['engine']})" if car.get('engine') else None,
    ])) or 'Unknown car'


def vehicle_profile(car: dict, installed_parts: list[dict] = ()) -> str:
    """Everything known about the car that can change which part fits."""
    lines = [f"Vehicle: {car_description(car)}"]
    if car.get('vin'):
        lines.append(f"VIN / chassis number: {car['vin']}")
    if car.get('options'):
        lines.append(f"Options / package: {car['options']}")
    if car.get('color'):
        lines.append(f"Colour: {car['color']}")
    if car.get('notes'):
        lines.append(f"Owner's notes: {car['notes']}")
    if installed_parts:
        lines.append("Parts the owner has installed (these can change fitment):")
        lines.extend(
            f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '')
            for p in installed_parts
        )
    return '\n'.join(lines)


# Shared by text and photo research. Grounded search answers are only as good as
# the verification rules the model is held to.
PARTS_SYSTEM_INSTRUCTION = """\
You are an expert automotive technician and parts specialist helping the owner of one specific vehicle, described in the vehicle profile.

Accuracy rules:
- Use Google Search to verify every part number, torque value, fluid spec and capacity against a source for this exact vehicle before stating it.
- Fitment often changes with market, engine code, transmission, body style, build date and VIN or chassis range. Use the VIN, options and owner's notes to narrow it down. If the answer still depends on a variant you cannot confirm, give each variant's value and say what the owner should check to tell them apart.
- Mark each part number as OEM or aftermarket, and name the brand for aftermarket parts. Give the current number when a source shows it has been superseded.
- If you cannot verify a value, write "not verified" beside it. Never invent or pattern-guess a part number.
- If the request could mean more than one component, state the assumption you made.

Format: **bold** section headings and "- " bullet lists. No tables and no preamble."""


def build_research_prompt(car: dict, query: str, installed_parts: list[dict] = ()) -> str:
    return (
        f"{vehicle_profile(car, installed_parts)}\n\n"
        f"Answer this question for the vehicle above in under 400 words.\n\n"
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


def build_pin_research_prompt(
    car: dict, pin: dict, angle: str, installed_parts: list[dict] = (), has_marker: bool = True,
) -> str:
    angle_label = ANGLE_LABELS.get(angle, angle)
    label = pin.get('label', '')
    pin_notes = pin.get('notes', '')

    if has_marker:
        images = (
            f"Image 1 is the {angle_label} of this vehicle. A red ring with a crosshair marks the "
            f"component the owner pinned.\n"
            f"Image 2 is an unmarked close-up of the same photo, centred on that point "
            f"(shifted only where the photo edge cuts it off).\n"
        )
    else:
        images = f"The image is the {angle_label} of this vehicle.\n"

    return (
        f"{vehicle_profile(car, installed_parts)}\n\n"
        f"{images}"
        f"The owner labelled the component: \"{label}\".\n"
        f"{f'Owner context: {pin_notes}' + chr(10) if pin_notes else ''}\n"
        f"First identify the component from the images. Read any part number, casting mark or "
        f"label visible on it. If what you see does not match the owner's label, say so and "
        f"research the component you can see.\n\n"
        f"Then give:\n"
        f"1. Component name and function\n"
        f"2. OEM part number(s) for this vehicle\n"
        f"3. Aftermarket or compatible alternatives\n"
        f"4. Service interval and maintenance notes\n"
        f"5. Known failure modes\n\n"
        f"Keep the answer under 500 words."
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

def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=92)
    return buf.getvalue()


def pin_images(raw_bytes: bytes, x_pct: float | None, y_pct: float | None) -> list[bytes]:
    """JPEGs for pin research: the full photo with the pin marked, plus a close-up.

    Saving fresh JPEGs drops EXIF. Without a pin position, only the photo is returned.
    """
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes))).convert('RGB')
    if x_pct is None or y_pct is None:
        return [_jpeg(img)]

    w, h = img.size
    cx, cy = w * x_pct / 100, h * y_pct / 100

    # Crop before marking so the ring never hides part numbers in the close-up
    side = max(256, int(min(w, h) * 0.35))
    left = int(min(max(cx - side / 2, 0), max(w - side, 0)))
    top = int(min(max(cy - side / 2, 0), max(h - side, 0)))
    close_up = img.crop((left, top, min(left + side, w), min(top + side, h)))

    marked = img.copy()
    draw = ImageDraw.Draw(marked)
    r = max(12, int(min(w, h) * 0.04))
    line = max(3, r // 5)
    # White halo keeps the red ring visible on red or dark components
    draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline='white', width=line + 4)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline='red', width=line)
    draw.line((cx - r * 1.6, cy, cx - r * 0.5, cy), fill='red', width=line)
    draw.line((cx + r * 0.5, cy, cx + r * 1.6, cy), fill='red', width=line)
    draw.line((cx, cy - r * 1.6, cx, cy - r * 0.5), fill='red', width=line)
    draw.line((cx, cy + r * 0.5, cx, cy + r * 1.6), fill='red', width=line)

    return [_jpeg(marked), _jpeg(close_up)]


def compress_for_storage(raw_bytes: bytes, max_edge: int = 2048) -> bytes:
    """Resize image to max_edge on longest side, return compressed JPEG/PNG."""
    img = Image.open(io.BytesIO(raw_bytes))
    fmt = (img.format or 'JPEG').upper()

    # Apply EXIF orientation before stripping so the pixels are correctly rotated
    img = ImageOps.exif_transpose(img)

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
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')
    w, h = img.size
    ratio = size / max(w, h)
    new_size = (int(w * ratio), int(h * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=80, exif=b'')
    return buf.getvalue()


# ── AI calls ─────────────────────────────────────────────────────────────────

class EmptyResponse(Exception):
    """Gemini returned no answer text (safety block, recitation stop or empty candidate)."""


def _grounding_sources(response) -> list[tuple[str, str]]:
    """(title, uri) pairs for the web pages Google Search grounding used."""
    sources: dict[str, str] = {}
    for cand in response.candidates or []:
        meta = cand.grounding_metadata
        for chunk in (meta.grounding_chunks or []) if meta else []:
            if chunk.web and chunk.web.uri:
                sources.setdefault(chunk.web.uri, chunk.web.title or chunk.web.domain or 'Source')
    return [(title, uri) for uri, title in sources.items()]


async def _generate(contents, config: types.GenerateContentConfig, label: str) -> str:
    t0 = time.monotonic()
    try:
        response = await client.aio.models.generate_content(model=MODEL, contents=contents, config=config)
    except Exception:
        log.exception("Gemini %s call failed", label)
        raise
    log.info("Gemini %s completed in %.1fs", label, time.monotonic() - t0)

    text = (response.text or '').strip()
    if not text:
        reason = response.candidates[0].finish_reason if response.candidates else None
        log.warning("Gemini %s returned no text (finish_reason=%s)", label, reason)
        raise EmptyResponse(label)

    sources = _grounding_sources(response)
    if sources:
        text += '\n\n**Sources**\n' + '\n'.join(f'- [{title}]({uri})' for title, uri in sources)
    return text


def _grounded_config() -> types.GenerateContentConfig:
    # Temperature stays at the model default; Gemini 3 degrades below 1.0
    return types.GenerateContentConfig(
        system_instruction=PARTS_SYSTEM_INSTRUCTION,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )


async def research(prompt: str) -> str:
    """Text-only parts research grounded with Google Search."""
    return await _generate(prompt, _grounded_config(), 'research')


async def research_with_images(prompt_text: str, images: list[bytes]) -> str:
    """Photo parts research grounded with Google Search. Images are JPEG."""
    parts = [
        types.Part.from_bytes(
            data=img, mime_type='image/jpeg',
            media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH,
        )
        for img in images
    ]
    parts.append(types.Part.from_text(text=prompt_text))
    return await _generate(types.Content(role='user', parts=parts), _grounded_config(), 'image research')


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
