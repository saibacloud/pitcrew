# In-app chat — a logged workshop chat that always carries the car contract,
# plus an inventory extractor that tracks what the owner has from what they say

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import db, fitment
from backend.auth import rate_limit_ai
from backend.services import gemini

log = logging.getLogger(__name__)

router = APIRouter(prefix='/api', tags=['chat'])

HISTORY_MESSAGES = 20
KINDS = {'tool', 'consumable', 'fluid', 'part', 'other'}


async def build_contract(car: dict) -> str:
    rows = await db.get_build_rows(car['id'])
    build = {f: fitment.decode_build_value(f, r['value']) for f, r in rows.items() if r['confirmed_at']}
    pending = {f: fitment.decode_build_value(f, r['value']) for f, r in rows.items() if not r['confirmed_at']}
    return gemini.car_contract(
        car, build, pending,
        await db.get_parts(car['id'], limit=200),
        await db.get_service_entries(car['id']),
        await db.get_inventory(),
    )


def _mentions(name: str, text: str) -> bool:
    return ' '.join(name.casefold().split()) in ' '.join(text.casefold().split())


def valid_changes(raw: list, message: str, previous_answer: Optional[str]) -> list[dict]:
    """Keep only extracted changes the conversation actually supports.

    The quote must be the owner's own words and name the item; an inferred item must also be
    named, verbatim, in the assistant message whose step the owner says they completed.
    """
    kept = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        name = ' '.join(str(c.get('name') or '').split())[:80]
        detail = ' '.join(str(c.get('detail') or '').split())[:60] or None
        quote, step_quote = str(c.get('quote') or ''), str(c.get('step_quote') or '')
        if not name or c.get('action') not in ('add', 'remove') or c.get('kind') not in KINDS:
            continue
        if not fitment.quote_in_text(quote, message):
            continue
        if c.get('basis') == 'inferred' and c['action'] == 'add':
            if not previous_answer or not fitment.quote_in_text(step_quote, previous_answer) \
                    or not _mentions(name, step_quote):
                continue
        elif c.get('basis') == 'stated' or c['action'] == 'remove':
            if not _mentions(name, quote):
                continue
        else:
            continue
        kept.append({'action': c['action'], 'name': name, 'kind': c['kind'], 'detail': detail,
                     'basis': 'inferred' if c.get('basis') == 'inferred' and c['action'] == 'add' else 'stated',
                     'quote': quote})
    return kept


def _change_view(item: dict, action: str) -> dict:
    return {'action': action, 'inventory_id': item['id'], 'name': item['name'], 'kind': item['kind'],
            'detail': item['detail'], 'basis': item['basis'], 'quote': item['quote'],
            'held': item['deleted_at'] is None}


async def _thread_or_404(thread_id: int) -> dict:
    thread = await db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, 'Chat not found')
    return thread


# ── Threads ──────────────────────────────────────────────────────────────────

@router.get('/cars/{car_id}/chats')
async def list_chats(car_id: int):
    if not await db.get_car(car_id):
        raise HTTPException(404, 'Car not found')
    return {'items': await db.get_threads(car_id)}


@router.post('/cars/{car_id}/chats', status_code=201)
async def new_chat(car_id: int):
    if not await db.get_car(car_id):
        raise HTTPException(404, 'Car not found')
    return await db.get_thread(await db.create_thread(car_id))


@router.get('/chats/{thread_id}')
async def read_chat(thread_id: int):
    thread = await _thread_or_404(thread_id)
    messages = await db.get_messages(thread_id)
    by_message: dict[int, list] = {}
    for item in await db.get_message_inventory([m['id'] for m in messages]):
        if item['message_id']:
            by_message.setdefault(item['message_id'], []).append(_change_view(item, 'add'))
        if item['removed_message_id']:
            by_message.setdefault(item['removed_message_id'], []).append(_change_view(item, 'remove'))
    for m in messages:
        m['inventory_changes'] = by_message.get(m['id'], [])
    return {'thread': thread, 'messages': messages}


@router.delete('/chats/{thread_id}', status_code=204)
async def remove_chat(thread_id: int):
    if not await db.soft_delete_thread(thread_id):
        raise HTTPException(404, 'Chat not found')


class ChatMessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post('/chats/{thread_id}/messages', status_code=201, dependencies=[Depends(rate_limit_ai)])
async def send_message(thread_id: int, body: ChatMessageBody):
    thread = await _thread_or_404(thread_id)
    car = await db.get_car(thread['car_id'])
    if not car:
        raise HTTPException(404, 'Car not found')
    text = body.text.strip()
    history = await db.get_messages(thread_id, limit=HISTORY_MESSAGES)
    previous_answer = next((m['body'] for m in reversed(history) if m['role'] == 'model'), None)

    # Inventory extraction reads the previous answer, not this one, so both calls run at once
    answer, extracted = await asyncio.gather(
        gemini.chat(await build_contract(car), history, text),
        gemini.extract_inventory(previous_answer, text, await db.get_inventory()),
        return_exceptions=True,
    )
    if isinstance(answer, BaseException):
        log.warning("Chat answer failed: %r", answer)
        raise HTTPException(502, 'AI request failed — try again shortly')

    user_id, model_id = await db.add_exchange(thread_id, text, answer.text, answer.search_suggestions)
    changes = []
    if isinstance(extracted, BaseException):
        log.warning("Inventory extraction failed: %r", extracted)
    else:
        changes = await db.apply_inventory_changes(user_id, valid_changes(extracted, text, previous_answer))
    messages = {m['id']: m for m in await db.get_messages(thread_id, limit=2)}
    return {
        'user': {**messages[user_id], 'inventory_changes': [
            {**c, 'held': c['action'] == 'add'} for c in changes]},
        'model': {**messages[model_id], 'inventory_changes': []},
        'thread': await db.get_thread(thread_id),
    }


# ── Inventory ────────────────────────────────────────────────────────────────

class InventoryBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: str = 'other'
    detail: Optional[str] = Field(None, max_length=60)


class InventoryPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    kind: Optional[str] = None
    detail: Optional[str] = Field(None, max_length=60)


@router.get('/inventory')
async def list_inventory():
    return {'items': await db.get_inventory()}


@router.post('/inventory', status_code=201)
async def add_inventory(body: InventoryBody):
    try:
        item_id = await db.create_inventory_item({**body.model_dump(), 'basis': 'owner'})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await db.get_inventory_item(item_id)


@router.patch('/inventory/{item_id}')
async def edit_inventory(item_id: int, body: InventoryPatch):
    data = body.model_dump(exclude_none=True)
    if data.get('detail') == '':
        data['detail'] = None  # clearing the detail field
    try:
        rows = await db.update_inventory_item(item_id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(404, 'Inventory item not found or no changes')
    return await db.get_inventory_item(item_id)


@router.delete('/inventory/{item_id}', status_code=204)
async def remove_inventory(item_id: int):
    if not await db.soft_delete_inventory_item(item_id):
        raise HTTPException(404, 'Inventory item not found')


@router.post('/inventory/{item_id}/restore')
async def restore_inventory(item_id: int):
    if not await db.restore_inventory_item(item_id):
        raise HTTPException(404, 'No removed inventory item with that ID')
    return await db.get_inventory_item(item_id)
