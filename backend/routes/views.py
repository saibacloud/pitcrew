# Photo views + pins routes

import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend.db import (
    get_car, get_view, get_views, create_view, soft_delete_view,
    get_photopin, get_photopins, create_photopin, update_photopin, soft_delete_photopin,
    VALID_VIEW_ANGLES,
)
from backend.services import gemini

router = APIRouter(prefix='/api', tags=['views', 'pins'])

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'static')
VIEWS_DIR = os.path.join(STATIC_DIR, 'uploads', 'views')

MAX_VIEW_UPLOAD = 20 * 1024 * 1024  # 20 MB


class PhotoPinBody(BaseModel):
    label: str
    notes: Optional[str] = None
    x_pct: Optional[float] = None
    y_pct: Optional[float] = None


@router.get('/cars/{car_id}/views')
async def list_views(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return await get_views(car_id)


@router.post('/cars/{car_id}/views/{angle}')
async def upload_view(car_id: int, angle: str, file: UploadFile = File(...)):
    if angle not in VALID_VIEW_ANGLES:
        raise HTTPException(400, f'Angle must be one of {sorted(VALID_VIEW_ANGLES)}')
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    ext = Path(file.filename).suffix.lower() if file.filename else '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
        raise HTTPException(400, 'Unsupported file type')

    contents = await file.read()
    if len(contents) > MAX_VIEW_UPLOAD:
        raise HTTPException(400, f'File too large (max {MAX_VIEW_UPLOAD // (1024*1024)}MB)')

    # Compress for storage
    contents = gemini.compress_for_storage(contents)

    filename = f'view_{car_id}_{angle}_{int(time.time() * 1000)}{ext}'
    dest = os.path.join(VIEWS_DIR, filename)
    with open(dest, 'wb') as f:
        f.write(contents)

    file_path = f'/static/uploads/views/{filename}'
    name = Path(file.filename).stem if file.filename else angle
    try:
        view_id = await create_view(car_id, name, angle, file_path)
    except ValueError as e:
        os.remove(dest)
        raise HTTPException(400, str(e))
    return await get_view(view_id)


@router.delete('/views/{view_id}', status_code=204)
async def remove_view(view_id: int):
    view = await get_view(view_id)
    if not view:
        raise HTTPException(404, 'View not found')
    # Soft delete — keep file on disk
    rows = await soft_delete_view(view_id)
    if rows == 0:
        raise HTTPException(404, 'View not found')


@router.get('/views/{view_id}/pins')
async def list_pins(view_id: int):
    view = await get_view(view_id)
    if not view:
        raise HTTPException(404, 'View not found')
    return await get_photopins(view_id)


@router.post('/views/{view_id}/pins', status_code=201)
async def add_pin(view_id: int, body: PhotoPinBody):
    view = await get_view(view_id)
    if not view:
        raise HTTPException(404, 'View not found')
    pin_id = await create_photopin(view_id, body.model_dump())
    return await get_photopin(pin_id)


@router.post('/pins/{pin_id}/research')
async def research_pin(pin_id: int):
    """Send photo + pin context to Gemini and return research about that specific component."""
    pin = await get_photopin(pin_id)
    if not pin:
        raise HTTPException(404, 'Pin not found')

    view = await get_view(pin['view_id'])
    if not view:
        raise HTTPException(404, 'View not found')

    car = await get_car(view['car_id'])
    if not car:
        raise HTTPException(404, 'Car not found')

    relative = view['file_path'].removeprefix('/static/')
    filepath = os.path.join(STATIC_DIR, relative)
    if not os.path.exists(filepath):
        raise HTTPException(404, 'Image file not found on disk')

    with open(filepath, 'rb') as f:
        raw_bytes = f.read()

    clean_bytes, mime_type = gemini.strip_exif(raw_bytes)
    prompt_text = gemini.build_pin_research_prompt(car, pin, view.get('angle', ''))

    try:
        summary = await gemini.research_with_image(prompt_text, clean_bytes, mime_type)
        await update_photopin(pin_id, {'ai_summary': summary})
        return {'summary': summary}
    except Exception as e:
        raise HTTPException(502, f'AI research failed: {e}')


@router.delete('/pins/{pin_id}', status_code=204)
async def remove_pin(pin_id: int):
    rows = await soft_delete_photopin(pin_id)
    if rows == 0:
        raise HTTPException(404, 'Pin not found')
