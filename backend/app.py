# Main app runner for pitcrew
# Run with: uvicorn backend.app:app --reload
# FastAPI: REST endpoints, serves static/ as the frontend

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.db import (
    init_db,
    get_cars, get_car, create_car, update_car, delete_car, update_car_photo,
    get_parts, get_part, create_part, update_part, delete_part,
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')
UPLOADS_DIR = os.path.join(STATIC_DIR, 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

# ── Static frontend ───────────────────────────────────────────────────────────

app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/')
async def index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

# ── Cars ──────────────────────────────────────────────────────────────────────


class CarBody(BaseModel):
    nickname: str
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim: Optional[str] = None
    engine: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    notes: Optional[str] = None


class CarPatch(BaseModel):
    nickname: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim: Optional[str] = None
    engine: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    notes: Optional[str] = None


@app.get('/api/cars')
async def list_cars():
    return await get_cars()


@app.post('/api/cars', status_code=201)
async def add_car(body: CarBody):
    car_id = await create_car(body.model_dump())
    return await get_car(car_id)


@app.get('/api/cars/{car_id}')
async def fetch_car(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return car


@app.patch('/api/cars/{car_id}')
async def edit_car(car_id: int, body: CarPatch):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    await update_car(car_id, body.model_dump(exclude_none=True))
    return await get_car(car_id)


@app.delete('/api/cars/{car_id}', status_code=204)
async def remove_car(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    await delete_car(car_id)


@app.post('/api/cars/{car_id}/photo')
async def upload_car_photo(car_id: int, file: UploadFile = File(...)):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    ext = Path(file.filename).suffix.lower() if file.filename else '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        raise HTTPException(400, 'Unsupported file type')
    dest = os.path.join(UPLOADS_DIR, f'car_{car_id}{ext}')
    contents = await file.read()
    with open(dest, 'wb') as f:
        f.write(contents)
    photo_url = f'/static/uploads/car_{car_id}{ext}'
    await update_car_photo(car_id, photo_url)
    return {'photo_url': photo_url}


@app.delete('/api/cars/{car_id}/photo', status_code=204)
async def delete_car_photo(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    # Delete file if it exists
    if car.get('photo_url'):
        filename = car['photo_url'].split('/')[-1]
        filepath = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
    # Clear photo_url in DB
    await update_car_photo(car_id, None)

# ── Parts ─────────────────────────────────────────────────────────────────────


class PartBody(BaseModel):
    name: str
    part_number: Optional[str] = None
    supplier: Optional[str] = None
    url: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = 1
    category: Optional[str] = 'Mechanical'
    status: Optional[str] = 'wishlist'
    notes: Optional[str] = None


class PartPatch(BaseModel):
    name: Optional[str] = None
    part_number: Optional[str] = None
    supplier: Optional[str] = None
    url: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    category: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


@app.get('/api/cars/{car_id}/parts')
async def list_parts(car_id: int):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return await get_parts(car_id)


@app.post('/api/cars/{car_id}/parts', status_code=201)
async def add_part(car_id: int, body: PartBody):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    part_id = await create_part(car_id, body.model_dump())
    return await get_part(part_id)


@app.patch('/api/parts/{part_id}')
async def edit_part(part_id: int, body: PartPatch):
    part = await get_part(part_id)
    if not part:
        raise HTTPException(404, 'Part not found')
    await update_part(part_id, body.model_dump(exclude_none=True))
    return await get_part(part_id)


@app.delete('/api/parts/{part_id}', status_code=204)
async def remove_part(part_id: int):
    part = await get_part(part_id)
    if not part:
        raise HTTPException(404, 'Part not found')
    await delete_part(part_id)

# ── Chat (stub - wired to Gemini in Phase 3) ──────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    car_id: Optional[int] = None


@app.post('/api/chat')
async def chat(req: ChatRequest):
    return {
        'response': f'(stub) you said: {req.message}',
        'citations': [],
    }
