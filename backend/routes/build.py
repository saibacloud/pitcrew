# Build sheet routes — the vehicle identity the fitment gate checks against

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import fitment
from backend.db import get_car, get_build_rows, set_build_field, clear_build_field

router = APIRouter(prefix='/api', tags=['build'])


class BuildValue(BaseModel):
    value: Any


async def _sheet(car_id: int) -> dict:
    rows = await get_build_rows(car_id)
    fields = []
    for name, spec in fitment.BUILD_FIELDS.items():
        row = rows.get(name)
        fields.append({
            'field': name,
            'label': spec['label'],
            'baseline': spec['baseline'],
            'kind': spec['kind'],
            'options': spec.get('options'),
            'value': fitment.decode_build_value(name, row['value']) if row else None,
            'source': row['source'] if row else None,
            'updated_at': row['created_at'] if row else None,
        })
    missing = [f for f in fitment.BASELINE_FIELDS if f not in rows]
    return {'fields': fields, 'missing_baseline': missing}


async def _require_car(car_id: int) -> None:
    if not await get_car(car_id):
        raise HTTPException(404, 'Car not found')


@router.get('/cars/{car_id}/build')
async def read_build(car_id: int):
    await _require_car(car_id)
    return await _sheet(car_id)


@router.put('/cars/{car_id}/build/{field}')
async def write_build_field(car_id: int, field: str, body: BuildValue):
    await _require_car(car_id)
    try:
        await set_build_field(car_id, field, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _sheet(car_id)


@router.delete('/cars/{car_id}/build/{field}')
async def remove_build_field(car_id: int, field: str):
    await _require_car(car_id)
    try:
        await clear_build_field(car_id, field)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _sheet(car_id)
