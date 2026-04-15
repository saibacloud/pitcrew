# Journal routes

import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.auth import rate_limit_ai
from backend.db import (
    get_car, get_journal, get_journals, count_journals,
    create_journal, update_journal, soft_delete_journal,
)
from backend.services import gemini
from backend.services.gemini import compress_for_storage

router = APIRouter(prefix='/api', tags=['journal'])

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'static')
UPLOADS_DIR = os.path.join(STATIC_DIR, 'uploads')
MAX_PHOTO_UPLOAD = 20 * 1024 * 1024  # 20 MB


class JournalEntryBody(BaseModel):
    type: str
    title: str
    body: Optional[str] = None


class JournalEntryPatch(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None


class ResearchRequest(BaseModel):
    query: str


@router.get('/cars/{car_id}/journal')
async def list_journals(
    car_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    items = await get_journals(car_id, limit=limit, offset=offset)
    total = await count_journals(car_id)
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


@router.post('/cars/{car_id}/journal', status_code=201)
async def add_journal(car_id: int, body: JournalEntryBody):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    try:
        entry_id = await create_journal(car_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await get_journal(entry_id)


@router.patch('/journal/{entry_id}')
async def edit_journal(entry_id: int, body: JournalEntryPatch):
    entry = await get_journal(entry_id)
    if not entry:
        raise HTTPException(404, 'Journal entry not found')
    try:
        rows = await update_journal(entry_id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if rows == 0:
        raise HTTPException(404, 'Journal entry not found or no changes')
    return await get_journal(entry_id)


@router.delete('/journal/{entry_id}', status_code=204)
async def remove_journal(entry_id: int):
    rows = await soft_delete_journal(entry_id)
    if rows == 0:
        raise HTTPException(404, 'Journal entry not found')


@router.post('/cars/{car_id}/journal/photo', status_code=201)
async def upload_journal_photo(
    car_id: int,
    file: UploadFile = File(...),
    comment: str = Form(''),
):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    ext = Path(file.filename).suffix.lower() if file.filename else '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        raise HTTPException(400, 'Unsupported file type')
    contents = await file.read()
    if len(contents) > MAX_PHOTO_UPLOAD:
        raise HTTPException(400, f'File too large (max {MAX_PHOTO_UPLOAD // (1024*1024)}MB)')
    contents = compress_for_storage(contents)
    filename = f'journal_{car_id}_{int(time.time() * 1000)}{ext}'
    dest = os.path.join(UPLOADS_DIR, filename)
    with open(dest, 'wb') as f:
        f.write(contents)
    photo_url = f'/static/uploads/{filename}'
    title = comment.strip() or 'Photo'
    entry_id = await create_journal(car_id, {
        'type': 'photo',
        'title': title,
        'photo_url': photo_url,
    })
    return await get_journal(entry_id)


@router.post('/cars/{car_id}/research', status_code=201, dependencies=[Depends(rate_limit_ai)])
async def research(car_id: int, req: ResearchRequest):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')

    prompt = gemini.build_research_prompt(car, req.query)
    try:
        answer = await gemini.research(prompt)
    except Exception:
        raise HTTPException(502, 'AI request failed — try again shortly')

    entry_id = await create_journal(car_id, {
        'type': 'research',
        'title': req.query,
        'body': answer,
    })
    return await get_journal(entry_id)
