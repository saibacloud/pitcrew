# Manual / Documents routes

import asyncio
import io
import logging
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel
from google.genai import types

from backend.auth import rate_limit_ai
from backend.db import (
    get_car, get_manual, get_manuals,
    create_manual, soft_delete_manual,
    create_journal, get_journal,
    VALID_MANUAL_CATEGORIES,
)
from backend.services import gemini

log = logging.getLogger(__name__)

router = APIRouter(prefix='/api', tags=['manuals'])

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'static')
MANUALS_DIR = os.path.join(STATIC_DIR, 'uploads', 'manuals')

MAX_MANUAL_UPLOAD = 100 * 1024 * 1024  # 100 MB

ALLOWED_EXTENSIONS: dict = {
    'manual':    {'.pdf', '.docx'},
    'reference': {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.bmp'},
    'other':     None,  # any
}

# Image formats Gemini accepts directly; other raster formats are converted to PNG
GEMINI_IMAGE_MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
RASTER_TO_PNG = {'.gif', '.bmp'}

PDF_PAGE_BUDGET = 300      # PDF pages sent whole per question, across all selected PDFs
PDF_PICKED_PAGES = 30      # pages chosen by search from a PDF that does not fit the budget
PDF_MAX_BYTES = 50 * 1024 * 1024  # Gemini's per-PDF limit
FULL_TEXT_CHARS = 300_000  # DOCX/text files below this are sent whole


# ── Document text extraction ────────────────────────────────────────────────

def _extract_sidecar(filepath: str) -> None:
    """Extract per-page PDF text into a .txt sidecar used to rank pages."""
    from pypdf import PdfReader
    reader = PdfReader(filepath)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ''
        if text.strip():
            pages.append(f'[Page {i + 1}]\n{text}')
    with open(filepath + '.txt', 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(pages))


def _sidecar_pages(filepath: str) -> dict[int, str]:
    sidecar = filepath + '.txt'
    if not os.path.exists(sidecar):
        _extract_sidecar(filepath)
    with open(sidecar, 'r', encoding='utf-8') as f:
        parts = re.split(r'^\[Page (\d+)\]\n', f.read(), flags=re.M)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _pdf_page_count(filepath: str) -> int:
    from pypdf import PdfReader
    return len(PdfReader(filepath).pages)


def _pdf_subset(filepath: str, page_numbers: list[int]) -> bytes:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(filepath)
    writer = PdfWriter()
    for n in page_numbers:
        writer.add_page(reader.pages[n - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pick_pages(pages: dict[int, str], question: str) -> list[int]:
    """Best-matching pages plus the page after each top hit, since procedures run on."""
    numbers = sorted(pages)
    ranked = [numbers[i] for i in gemini.rank_passages([pages[n] for n in numbers], question)]
    picked: set[int] = set()
    for rank, n in enumerate(ranked):
        if len(picked) >= PDF_PICKED_PAGES:
            break
        picked.add(n)
        if rank < PDF_PICKED_PAGES // 3 and n + 1 in pages and len(picked) < PDF_PICKED_PAGES:
            picked.add(n + 1)
    # No match: the opening pages usually hold the contents and model coverage
    return sorted(picked) or numbers[:PDF_PICKED_PAGES]


def _docx_text(filepath: str) -> str:
    """Paragraphs and tables in document order; spec and torque tables live in tables."""
    import docx as docx_lib
    lines = []
    for block in docx_lib.Document(filepath).iter_inner_content():
        if hasattr(block, 'rows'):
            for row in block.rows:
                lines.append(' | '.join(cell.text.strip() for cell in row.cells))
        elif block.text.strip():
            lines.append(block.text)
    return '\n'.join(lines)


def _text_excerpt(text: str, question: str) -> str:
    if len(text) <= FULL_TEXT_CHARS:
        return text
    chunks = gemini.text_chunks(text)
    ranked = gemini.rank_passages(chunks, question)[:12] or [0]
    return '\n\n[...]\n\n'.join(chunks[i] for i in sorted(ranked))


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get('/cars/{car_id}/manuals')
async def list_manuals(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return await get_manuals(car_id)


@router.post('/cars/{car_id}/manuals', status_code=201)
async def upload_manual(car_id: int, file: UploadFile = File(...), category: str = 'manual'):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    if category not in VALID_MANUAL_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_MANUAL_CATEGORIES)}")

    ext = Path(file.filename).suffix.lower() if file.filename else ''
    allowed = ALLOWED_EXTENSIONS[category]
    if allowed is not None and ext not in allowed:
        raise HTTPException(400, f'Unsupported file type for {category}. Allowed: {", ".join(sorted(allowed))}')

    contents = await file.read()
    if len(contents) > MAX_MANUAL_UPLOAD:
        raise HTTPException(400, f'File too large (max {MAX_MANUAL_UPLOAD // (1024*1024)}MB)')

    safe_name = Path(file.filename).stem[:60] if file.filename else 'file'
    filename = f"car_{car_id}_{int(time.time())}_{safe_name}{ext}"
    dest = os.path.join(MANUALS_DIR, filename)
    with open(dest, 'wb') as f:
        f.write(contents)

    if ext == '.pdf':
        await asyncio.to_thread(_extract_sidecar, dest)

    file_path = f'/static/uploads/manuals/{filename}'
    title = Path(file.filename).stem if file.filename else filename
    try:
        manual_id = await create_manual(car_id, title, file_path, category)
    except ValueError as e:
        os.remove(dest)
        raise HTTPException(400, str(e))
    return await get_manual(manual_id)


@router.delete('/manuals/{manual_id}', status_code=204)
async def remove_manual(manual_id: int):
    manual = await get_manual(manual_id)
    if not manual:
        raise HTTPException(404, 'Manual not found')
    # Soft delete — keep files on disk
    rows = await soft_delete_manual(manual_id)
    if rows == 0:
        raise HTTPException(404, 'Manual not found')


class ManualAskBody(BaseModel):
    question: str
    manual_ids: list[int]


@router.post('/cars/{car_id}/manuals/ask', dependencies=[Depends(rate_limit_ai)])
async def ask_manuals(car_id: int, body: ManualAskBody):
    """Answer a question using only the selected uploaded documents."""
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    if not body.manual_ids:
        raise HTTPException(400, 'Select at least one document')

    content_parts: list = []
    loaded: list[str] = []
    page_budget = PDF_PAGE_BUDGET

    for mid in body.manual_ids:
        doc = await get_manual(mid)
        if not doc or doc['car_id'] != car_id:
            continue
        relative = doc.get('file_path', '').removeprefix('/static/')
        filepath = os.path.normpath(os.path.join(STATIC_DIR, relative))
        if not filepath.startswith(os.path.normpath(STATIC_DIR)) or not os.path.exists(filepath):
            continue
        title = doc['title']
        ext = Path(filepath).suffix.lower()
        try:
            if ext == '.pdf':
                page_count = await asyncio.to_thread(_pdf_page_count, filepath)
                if page_count <= page_budget and os.path.getsize(filepath) <= PDF_MAX_BYTES:
                    content_parts.append(types.Part.from_text(
                        text=f'[Document: {title}, full PDF of {page_count} pages. '
                             f'Cite pages by their position in this PDF.]'
                    ))
                    cache_key = f'{filepath}:{os.path.getmtime(filepath)}'
                    content_parts.append(await gemini.pdf_file_part(filepath, cache_key))
                    page_budget -= page_count
                else:
                    pages = await asyncio.to_thread(_sidecar_pages, filepath)
                    if not pages:
                        raise HTTPException(400, f'"{title}" is a scanned PDF of {page_count} pages, too long to '
                                                 f'read whole. Split it into parts under {PDF_PAGE_BUDGET} pages.')
                    picked = _pick_pages(pages, body.question)
                    subset = await asyncio.to_thread(_pdf_subset, filepath, picked)
                    # pypdf can carry shared images/fonts into a subset; past Gemini's
                    # PDF limit the picked pages go as extracted text instead
                    if len(subset) <= PDF_MAX_BYTES:
                        content_parts.append(types.Part.from_text(
                            text=f'[Document: {title}, selected pages only. In order, the pages of this PDF are '
                                 f'original pages {", ".join(map(str, picked))}. Cite the original page numbers.]'
                        ))
                        content_parts.append(await gemini.pdf_bytes_part(subset))
                    else:
                        log.info("Subset of %s is %d bytes; sending page text", title, len(subset))
                        content_parts.append(types.Part.from_text(
                            text=f'[Document: {title}, extracted text of selected pages]\n\n'
                                 + '\n\n'.join(f'[Page {n}]\n{pages[n]}' for n in picked if n in pages)
                        ))
                    page_budget = max(0, page_budget - len(picked))
            elif ext in GEMINI_IMAGE_MIME or ext in RASTER_TO_PNG:
                with open(filepath, 'rb') as f:
                    data = f.read()
                mime = GEMINI_IMAGE_MIME.get(ext, 'image/png')
                if ext in RASTER_TO_PNG:
                    buf = io.BytesIO()
                    Image.open(io.BytesIO(data)).convert('RGB').save(buf, format='PNG')
                    data = buf.getvalue()
                content_parts.append(types.Part.from_text(text=f'[Document: {title}, image]'))
                content_parts.append(types.Part.from_bytes(
                    data=data, mime_type=mime,
                    media_resolution=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH,
                ))
            else:
                if ext == '.docx':
                    text = await asyncio.to_thread(_docx_text, filepath)
                else:
                    with open(filepath, 'r', errors='replace') as f:
                        text = f.read()
                if not text.strip():
                    continue
                content_parts.append(types.Part.from_text(
                    text=f'[Document: {title}, text without page numbers]\n\n{_text_excerpt(text, body.question)}'
                ))
            loaded.append(title)
        except HTTPException:
            raise
        except Exception:
            log.exception("Could not prepare document %s", mid)
            raise HTTPException(502, f'Error reading "{title}"')

    if not content_parts:
        raise HTTPException(400, 'Could not read any of the selected documents')

    try:
        answer = await gemini.ask_documents(content_parts, car, body.question)
    except Exception:
        raise HTTPException(502, 'AI document search failed — try again shortly')

    # Persist the answer to the journal so it's never lost
    entry_body = answer + (f'\n\nSources: {", ".join(loaded)}' if loaded else '')
    entry_id = await create_journal(car_id, {
        'type': 'docsearch',
        'title': body.question,
        'body': entry_body,
    })
    return {'answer': answer, 'sources': loaded, 'journal_entry': await get_journal(entry_id)}
