# Build sheet routes — the vehicle identity the fitment gate checks against

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import fitment
from backend.db import get_car, get_build_rows, set_build_field, clear_build_field, confirm_build_field

router = APIRouter(prefix='/api', tags=['build'])


class BuildValue(BaseModel):
    value: Any


async def build_sheet(car_id: int) -> dict:
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
            'confirmed': bool(row and row['confirmed_at']),
            'by_agent': bool(row and row['agent_key_id']),
            'updated_at': row['created_at'] if row else None,
        })
    # Unconfirmed agent values do not count towards the gate until the owner confirms them
    missing = [f for f in fitment.BASELINE_FIELDS if not (rows.get(f) and rows[f]['confirmed_at'])]
    pending = [f for f, r in rows.items() if not r['confirmed_at']]
    return {'fields': fields, 'missing_baseline': missing, 'pending': pending}


async def _require_car(car_id: int) -> None:
    if not await get_car(car_id):
        raise HTTPException(404, 'Car not found')


@router.get('/cars/{car_id}/build')
async def read_build(car_id: int):
    await _require_car(car_id)
    return await build_sheet(car_id)


@router.put('/cars/{car_id}/build/{field}')
async def write_build_field(car_id: int, field: str, body: BuildValue):
    await _require_car(car_id)
    try:
        await set_build_field(car_id, field, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await build_sheet(car_id)


@router.post('/cars/{car_id}/build/{field}/confirm')
async def confirm_build(car_id: int, field: str):
    await _require_car(car_id)
    if not await confirm_build_field(car_id, field):
        raise HTTPException(404, 'No pending value for this field')
    return await build_sheet(car_id)


@router.delete('/cars/{car_id}/build/{field}')
async def remove_build_field(car_id: int, field: str):
    await _require_car(car_id)
    try:
        await clear_build_field(car_id, field)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await build_sheet(car_id)
