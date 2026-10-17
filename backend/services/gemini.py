# Gemini AI service
# All AI interactions, prompt building, image prep

import asyncio
import io
import json
import logging
import math
import os
import re
import time
from collections import Counter
from typing import NamedTuple
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


DOCS_SYSTEM_INSTRUCTION = """\
You answer questions about one vehicle using only the documents the owner uploaded for it.

- Use only what the documents say. Do not fill gaps from general knowledge. If the documents do not answer the question, say so plainly and point to the closest thing they do cover.
- Copy part numbers, torque values, capacities, clearances and specifications exactly as printed, with units.
- Cite the document and page for every value, e.g. (Workshop Manual, p. 147), using the page numbering each document's label describes.
- Manuals often cover several engines, trims, markets or model years. Use the vehicle profile to pick the matching variant. If a value differs by variant, give each and say which applies to this vehicle or what to check.
- Read tables, diagrams, exploded views and callout numbers as well as body text.

Format: **bold** section headings and "- " bullet lists. No tables and no preamble."""


def build_docsearch_prompt(car: dict, question: str) -> str:
    return (
        f"{vehicle_profile(car)}\n\n"
        f"Answer this question using only the documents above.\n\n"
        f"Question: {question}"
    )


# ── In-app chat ──────────────────────────────────────────────────────────────

# Fixed rules for every chat answer. The car contract that follows them is rebuilt from
# the database for every message, so no answer is ever given without it.
CHAT_SYSTEM_INSTRUCTION = """\
You are the pitcrew workshop assistant for one specific car, described in the CAR CONTRACT below. You MUST follow these rules in every answer.

The car:
- Answer for this exact car only. The car contract is the source of truth for its identity. Confirmed build sheet values are fact; do not second-guess them.
- If the answer depends on something the contract does not settle (an unconfirmed or missing build field, an option, a production date split), say which, give the answer for each variant, and tell the owner how to check.
- Use the service history and installed parts: they change what is due and what fits.

Accuracy:
- Use Google Search to check every fluid specification, capacity, torque value, part number and procedure against a source for this exact variant before stating it.
- Never invent or pattern-guess a part number, specification or capacity. If you cannot verify a value, write "not verified" beside it.
- Part numbers you mention are for research only. Fitment for ordering is verified separately in the pitcrew cart; never tell the owner a part is confirmed to fit.

Tools and materials:
- The owner's inventory is listed in the contract. Prefer what they already have, and say when they already have what a step needs.
- Name every tool and consumable specifically, with size or specification (e.g. "14 mm socket", "brake and parts cleaner", "ATF meeting the specification in the manual"), so it can be tracked.
- Mention safety briefly where it matters (supporting the car on stands, hot fluids, disposal).

Format: concise, **bold** section headings and "- " bullet lists. No tables and no preamble."""


def car_contract(car: dict, build: dict, pending: dict, parts: list[dict],
                 services: list[dict], inventory: list[dict]) -> str:
    """The per-car context block every chat answer is given."""
    lines = ['CAR CONTRACT', '', 'Identity:']
    identity = [('Year', car.get('year')), ('Make', car.get('make')), ('Model', car.get('model')),
                ('Trim', car.get('trim')), ('Engine', car.get('engine')), ('Colour', car.get('color')),
                ('VIN', car.get('vin')), ('Options / package', car.get('options')),
                ("Owner's notes", car.get('notes'))]
    lines += [f'- {label}: {value}' for label, value in identity if value not in (None, '')]

    def fmt(value):
        if isinstance(value, list):
            return ', '.join(f"{o['code']} ({o['description']})" if o.get('description') else o['code']
                             for o in value)
        if isinstance(value, bool):
            return 'yes' if value else 'no'
        return str(value)

    lines += ['', 'Build sheet (confirmed):']
    lines += [f'- {field}: {fmt(value)}' for field, value in build.items()] or ['- none confirmed']
    if pending:
        lines += ['', 'Build sheet (unconfirmed, do not rely on):']
        lines += [f'- {field}: {fmt(value)}' for field, value in pending.items()]

    installed = [p for p in parts if p.get('status') == 'installed']
    planned = [p for p in parts if p.get('status') != 'installed']
    if installed:
        lines += ['', 'Installed parts:']
        lines += [f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '') for p in installed]
    if planned:
        lines += ['', 'Parts in the cart (not yet installed):']
        lines += [f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '')
                  + f" [{p.get('status')}" + (f", fitment {p['confidence']}" if p.get('confidence') else '') + ']'
                  for p in planned]
    if services:
        lines += ['', 'Recent service log:']
        lines += [f"- {s['created_at'][:10]}" + (f" at {s['odometer']} km" if s.get('odometer') else '')
                  + f": {s['title']}" for s in services]
    lines += ['', "Owner's inventory (tools, consumables, loose parts):"]
    lines += [f"- {i['name']}" + (f" ({i['detail']})" if i.get('detail') else '') for i in inventory] or ['- none recorded']
    return '\n'.join(lines)


async def chat(contract: str, history: list[dict], message: str) -> 'Answer':
    """One chat answer grounded with Google Search; history is [{role, body}] oldest first."""
    contents = [types.Content(role=m['role'], parts=[types.Part.from_text(text=m['body'])]) for m in history]
    contents.append(types.Content(role='user', parts=[types.Part.from_text(text=message)]))
    config = types.GenerateContentConfig(
        system_instruction=f'{CHAT_SYSTEM_INSTRUCTION}\n\n{contract}',
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    return await _generate(contents, config, 'chat')


INVENTORY_SYSTEM_INSTRUCTION = """\
You keep the owner's garage inventory: tools, consumables, fluids and loose parts they physically have. From the owner's latest message, and the assistant message before it, list inventory changes.

- add, basis "stated": the owner says they have, bought or received an item.
- add, basis "inferred": the owner says they completed a step, and the assistant's previous message said that step needs a specific item. Doing the step means they have it. Put the assistant's words naming the item in step_quote.
- remove: the owner says they used up, ran out of, broke, lost or returned an item.
- Never add from questions, plans, wishes or hypotheticals ("should I buy", "I'll get", "do I need").
- Never add the car itself or parts already fitted to it.
- quote: the owner's exact words this change rests on, copied verbatim.
- name: the plain item name ("socket", "degreaser", "brake fluid"). detail: size, specification or brand when given ("14 mm", "DOT 4"), else null.
- Skip items already in the current inventory unless removing them. Return an empty list when nothing changes."""

INVENTORY_SCHEMA = {
    'type': 'object',
    'properties': {
        'changes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'action': {'type': 'string', 'enum': ['add', 'remove']},
                    'name': {'type': 'string'},
                    'kind': {'type': 'string', 'enum': ['tool', 'consumable', 'fluid', 'part', 'other']},
                    'detail': {'type': ['string', 'null']},
                    'basis': {'type': 'string', 'enum': ['stated', 'inferred']},
                    'quote': {'type': 'string'},
                    'step_quote': {'type': ['string', 'null']},
                },
                'required': ['action', 'name', 'kind', 'detail', 'basis', 'quote', 'step_quote'],
            },
        },
    },
    'required': ['changes'],
}


async def extract_inventory(previous_answer: str | None, message: str, inventory: list[dict]) -> list[dict]:
    """Raw inventory changes proposed for one owner message; the caller validates them."""
    held = '\n'.join(f"- {i['name']}" + (f" ({i['detail']})" if i.get('detail') else '') for i in inventory)
    prompt = (
        f"Current inventory:\n{held or '- none'}\n\n"
        f"Assistant's previous message:\n{previous_answer or '(none)'}\n\n"
        f"Owner's latest message:\n{message}"
    )
    config = types.GenerateContentConfig(
        system_instruction=INVENTORY_SYSTEM_INSTRUCTION,
        response_mime_type='application/json',
        response_json_schema=INVENTORY_SCHEMA,
    )
    t0 = time.monotonic()
    response = await client.aio.models.generate_content(model=MODEL, contents=prompt, config=config)
    log.info("Gemini inventory extraction completed in %.1fs", time.monotonic() - t0)
    return json.loads(response.text or '{}').get('changes') or []


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

class Answer(NamedTuple):
    text: str
    # Google's Search Suggestions HTML; the Gemini API terms require showing it with grounded answers
    search_suggestions: str | None = None


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


async def _generate(contents, config: types.GenerateContentConfig, label: str) -> Answer:
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
    meta = response.candidates[0].grounding_metadata
    entry = meta.search_entry_point if meta else None
    return Answer(text, entry.rendered_content if entry else None)


def _grounded_config() -> types.GenerateContentConfig:
    # Temperature stays at the model default; Gemini 3 degrades below 1.0
    return types.GenerateContentConfig(
        system_instruction=PARTS_SYSTEM_INSTRUCTION,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )


async def research(prompt: str) -> Answer:
    """Text-only parts research grounded with Google Search."""
    return await _generate(prompt, _grounded_config(), 'research')


async def research_with_images(prompt_text: str, images: list[bytes]) -> Answer:
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
    """Answer from the given document parts only; the question goes after the documents."""
    parts = content_parts + [types.Part.from_text(text=build_docsearch_prompt(car, question))]
    config = types.GenerateContentConfig(system_instruction=DOCS_SYSTEM_INSTRUCTION)
    return (await _generate(types.Content(role='user', parts=parts), config, 'doc search')).text


# ── Documents ────────────────────────────────────────────────────────────────

# Gemini reads PDFs natively (text layer plus page images), so tables, diagrams
# and scanned pages survive. Medium is Google's recommended PDF resolution.
_PDF_RESOLUTION = types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM
_INLINE_LIMIT = 20 * 1024 * 1024
_FILE_TTL = 47 * 3600  # Files API keeps uploads for 48h

_uploaded: dict[str, tuple[str, float]] = {}  # cache key -> (file uri, expiry)


async def _upload_pdf(file) -> str:
    """Upload a PDF path or stream through the Files API and return its URI once active."""
    f = await client.aio.files.upload(file=file, config=types.UploadFileConfig(mime_type='application/pdf'))
    while f.state == types.FileState.PROCESSING:
        await asyncio.sleep(1)
        f = await client.aio.files.get(name=f.name)
    if f.state == types.FileState.FAILED:
        raise RuntimeError(f'Gemini could not process uploaded PDF {f.name}')
    return f.uri


async def pdf_file_part(path: str, cache_key: str) -> types.Part:
    """Part for a whole PDF, uploaded once and reused until the upload expires."""
    hit = _uploaded.get(cache_key)
    if hit and hit[1] > time.time():
        uri = hit[0]
    else:
        uri = await _upload_pdf(path)
        _uploaded[cache_key] = (uri, time.time() + _FILE_TTL)
    return types.Part.from_uri(file_uri=uri, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)


async def pdf_bytes_part(data: bytes) -> types.Part:
    """Part for a PDF built per question; uploaded uncached only when too big to inline."""
    if len(data) <= _INLINE_LIMIT:
        return types.Part.from_bytes(data=data, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)
    uri = await _upload_pdf(io.BytesIO(data))
    return types.Part.from_uri(file_uri=uri, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)


# ── Passage ranking ──────────────────────────────────────────────────────────

# Keeps part numbers, oil grades and thread sizes whole: 15400-plm-a02, 5w-30, m12x1.25
_TOKEN = re.compile(r'[a-z0-9]+(?:[-./][a-z0-9]+)*')
_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has',
    'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
    'might', 'can', 'for', 'of', 'to', 'in', 'on', 'at', 'by', 'from',
    'with', 'and', 'or', 'but', 'if', 'what', 'how', 'where', 'when',
    'which', 'who', 'that', 'this', 'i', 'my', 'me', 'it', 'its', 'there',
    'about', 'need', 'find', 'tell', 'show', 'car', 'vehicle',
}


def _terms(text: str) -> list[str]:
    terms = []
    for tok in _TOKEN.findall(text.lower()):
        terms.append(tok)
        if not tok.isalnum():
            terms.extend(p for p in re.split(r'[-./]', tok) if p)
    return terms


def rank_passages(passages: list[str], question: str, k1: float = 1.5, b: float = 0.75) -> list[int]:
    """Indexes of passages that match the question, best first, scored with Okapi BM25."""
    query = {t for t in _terms(question) if t not in _STOP and (len(t) > 2 or any(c.isdigit() for c in t))}
    if not query or not passages:
        return []
    counts = [Counter(_terms(p)) for p in passages]
    lengths = [sum(c.values()) for c in counts]
    avg_len = (sum(lengths) / len(lengths)) or 1
    n = len(passages)
    idf = {}
    for t in query:
        df = sum(1 for c in counts if t in c)
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    scored = []
    for i, c in enumerate(counts):
        score = sum(
            idf[t] * c[t] * (k1 + 1) / (c[t] + k1 * (1 - b + b * lengths[i] / avg_len))
            for t in query if c[t]
        )
        if score > 0:
            scored.append((score, i))
    return [i for _, i in sorted(scored, key=lambda x: -x[0])]


def text_chunks(text: str, size: int = 2000, overlap: int = 400) -> list[str]:
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), size - overlap)]
