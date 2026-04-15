# Car routes

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend.db import get_car, get_cars, create_car, update_car, soft_delete_car, update_car_photo
from backend.services.gemini import compress_for_storage

router = APIRouter(prefix='/api', tags=['cars'])

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'static')
UPLOADS_DIR = os.path.join(STATIC_DIR, 'uploads')

MAX_CAR_PHOTO_UPLOAD = 20 * 1024 * 1024  # 20 MB


class CarBody(BaseModel):
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim: Optional[str] = None
    options: Optional[str] = None
    engine: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    notes: Optional[str] = None


class CarPatch(BaseModel):
    options: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim: Optional[str] = None
    engine: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    notes: Optional[str] = None


@router.get('/cars')
async def list_cars():
    return await get_cars()


@router.post('/cars', status_code=201)
async def add_car(body: CarBody):
    car_id = await create_car(body.model_dump())
    return await get_car(car_id)


@router.get('/cars/{car_id}')
async def fetch_car(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return car


@router.patch('/cars/{car_id}')
async def edit_car(car_id: int, body: CarPatch):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    rows = await update_car(car_id, body.model_dump(exclude_none=True))
    if rows == 0:
        raise HTTPException(404, 'Car not found or no changes')
    return await get_car(car_id)


@router.delete('/cars/{car_id}', status_code=204)
async def remove_car(car_id: int):
    rows = await soft_delete_car(car_id)
    if rows == 0:
        raise HTTPException(404, 'Car not found')


@router.post('/cars/{car_id}/photo')
async def upload_car_photo(car_id: int, file: UploadFile = File(...)):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    ext = Path(file.filename).suffix.lower() if file.filename else '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        raise HTTPException(400, 'Unsupported file type')
    contents = await file.read()
    if len(contents) > MAX_CAR_PHOTO_UPLOAD:
        raise HTTPException(400, f'File too large (max {MAX_CAR_PHOTO_UPLOAD // (1024*1024)}MB)')
    contents = compress_for_storage(contents)
    # Compressed images are saved in original format extension
    dest = os.path.join(UPLOADS_DIR, f'car_{car_id}{ext}')
    with open(dest, 'wb') as f:
        f.write(contents)
    photo_url = f'/static/uploads/car_{car_id}{ext}'
    await update_car_photo(car_id, photo_url)
    return {'photo_url': photo_url}


@router.delete('/cars/{car_id}/photo', status_code=204)
async def delete_car_photo(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    if car.get('photo_url'):
        filename = car['photo_url'].split('/')[-1]
        filepath = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
    await update_car_photo(car_id, None)
