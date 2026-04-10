# Parts / Cart routes

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db import (
    get_car, get_part, get_parts, count_parts,
    create_part, update_part, soft_delete_part,
)

router = APIRouter(prefix='/api', tags=['parts'])


class PartBody(BaseModel):
    name: str
    part_number: Optional[str] = None
    supplier: Optional[str] = None
    url: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = 1
    category: Optional[str] = 'Mechanical'
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


@router.get('/cars/{car_id}/parts')
async def list_parts(
    car_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    items = await get_parts(car_id, limit=limit, offset=offset)
    total = await count_parts(car_id)
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


@router.post('/cars/{car_id}/parts', status_code=201)
async def add_part(car_id: int, body: PartBody):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    try:
        part_id = await create_part(car_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await get_part(part_id)


@router.patch('/parts/{part_id}')
async def edit_part(part_id: int, body: PartPatch):
    part = await get_part(part_id)
    if not part:
        raise HTTPException(404, 'Part not found')
    try:
        rows = await update_part(part_id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if rows == 0:
        raise HTTPException(404, 'Part not found or no changes')
    return await get_part(part_id)


@router.delete('/parts/{part_id}', status_code=204)
async def remove_part(part_id: int):
    rows = await soft_delete_part(part_id)
    if rows == 0:
        raise HTTPException(404, 'Part not found')
