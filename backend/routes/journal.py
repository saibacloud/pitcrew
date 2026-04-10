# Journal routes

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db import (
    get_car, get_journal, get_journals, count_journals,
    create_journal, update_journal, soft_delete_journal,
)
from backend.services import gemini

router = APIRouter(prefix='/api', tags=['journal'])


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


@router.post('/cars/{car_id}/research', status_code=201)
async def research(car_id: int, req: ResearchRequest):
    car = await get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')

    prompt = gemini.build_research_prompt(car, req.query)
    try:
        answer = await gemini.research(prompt)
    except Exception as e:
        raise HTTPException(502, f'AI request failed: {e}')

    entry_id = await create_journal(car_id, {
        'type': 'research',
        'title': req.query,
        'body': answer,
    })
    return await get_journal(entry_id)
