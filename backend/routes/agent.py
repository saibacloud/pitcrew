# Agent API — what an external mechanic agent reads and writes, per /llms.txt
# Agents never reach the owner routes: owner-sourced parts skip the fitment gate.

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend import db, fitment
from backend.agent_keys import require_agent
from backend.fetch import FetchError, domain_tier, fetch_page
from backend.routes.build import build_sheet
from backend.routes.manuals import STATIC_DIR, _sidecar_pages
from backend.services.gemini import rank_passages

router = APIRouter(prefix='/api/agent', tags=['agent'])

MAX_EXCERPT = 600     # an excerpt is one catalogue row or line span, not a page
# Web pages keep table rows on one line; PDF text layers can wrap a row over a few
EXCERPT_LINES = {'web_page': 1, 'manual_page': 3}
CONTEXT_LINES = 2
VERIFYING_TIERS = {'catalogue', 'oem'}
MAX_SOURCE_TEXT = 2_000_000
Level = Literal['verified', 'conditional', 'unverified']
Category = Literal['Mechanical', 'Electrical', 'Body', 'Interior', 'Consumables']
Origin = Literal['oem', 'oe_supplier', 'aftermarket']


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _car(car_id: int) -> dict:
    car = await db.get_car(car_id)
    if not car:
        raise HTTPException(404, 'Car not found')
    return car


async def _agent_part(part_id: int) -> dict:
    part = await db.get_part(part_id)
    if not part or part['source'] != 'agent':
        raise HTTPException(404, 'Agent part not found')
    return part


async def _car_source(car_id: int, source_id: int) -> dict:
    source = await db.get_source(source_id)
    if not source or source['car_id'] != car_id:
        raise HTTPException(400, f'Source {source_id} not found for this car')
    return source


def _gate_json(result) -> dict:
    return {'level': result.level, 'reasons': result.reasons,
            'open_fields': result.open_fields, 'no_fit': result.no_fit}


_idem_locks: defaultdict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _idempotent(key: dict, idem_key: Optional[str], payload: Any, run):
    """Replay the stored success for a retried Idempotency-Key; reject the key reused for other input.

    Only successful responses are stored, so a retry after an error runs again.
    """
    if not idem_key:
        status, body = await run()
        return JSONResponse(body, status_code=status)
    if len(idem_key) > 200:
        raise HTTPException(400, 'Idempotency-Key is too long')
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    # Serialises concurrent retries of one key so the first stores before the next looks
    async with _idem_locks[(key['id'], idem_key)]:
        stored = await db.get_idempotent(key['id'], idem_key)
        if stored:
            if stored['request_hash'] != request_hash:
                raise HTTPException(422, 'Idempotency-Key was already used for a different request')
            return JSONResponse(json.loads(stored['response']), status_code=stored['status_code'])
        status, body = await run()
        await db.store_idempotent(key['id'], idem_key, request_hash, status, json.dumps(body, default=str))
    return JSONResponse(body, status_code=status)


def _manual_path(manual: dict) -> Optional[str]:
    relative = (manual.get('file_path') or '').removeprefix('/static/')
    path = os.path.normpath(os.path.join(STATIC_DIR, relative))
    if not path.startswith(os.path.normpath(STATIC_DIR) + os.sep) or not os.path.exists(path):
        return None
    return path if path.lower().endswith('.pdf') else None


async def _manual_pages(manual: dict) -> dict[int, str]:
    path = _manual_path(manual)
    if not path:
        raise HTTPException(400, 'Manual is not a readable PDF')
    return await asyncio.to_thread(_sidecar_pages, path)


async def _check_url(car_id: int, url: Optional[str], part_number: str) -> None:
    """A purchase link must be a page pitcrew fetched that shows this part number."""
    if url is None:
        return
    sources = await db.get_web_sources(car_id, url)
    if not any(fitment.part_number_in_text(part_number, s['content']) for s in sources):
        raise HTTPException(400, 'url must be a fetched source page that shows the part number')


# ── Reads ────────────────────────────────────────────────────────────────────

@router.get('/cars')
async def list_cars(key: dict = Depends(require_agent)):
    return {'items': await db.get_cars()}


@router.get('/cars/{car_id}')
async def car_state(car_id: int, key: dict = Depends(require_agent)):
    car = await _car(car_id)
    manuals = await db.get_manuals(car_id)
    return {
        'car': car,
        'build': await build_sheet(car_id),
        'parts': await db.get_parts(car_id, limit=500),
        'journal': await db.get_journals(car_id, limit=30),
        'manuals': [{k: m[k] for k in ('id', 'title', 'category', 'tier', 'created_at')} for m in manuals],
        'inventory': await db.get_inventory(),
    }


@router.get('/cars/{car_id}/journal')
async def car_journal(car_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                      key: dict = Depends(require_agent)):
    await _car(car_id)
    return {'items': await db.get_journals(car_id, limit=limit, offset=offset),
            'total': await db.count_journals(car_id), 'limit': limit, 'offset': offset}


@router.get('/manuals/{manual_id}/search')
async def search_manual(manual_id: int, q: str = Query(..., min_length=2, max_length=300),
                        limit: int = Query(5, ge=1, le=10), key: dict = Depends(require_agent)):
    manual = await db.get_manual(manual_id)
    if not manual:
        raise HTTPException(404, 'Manual not found')
    pages = await _manual_pages(manual)
    numbers = sorted(pages)
    ranked = [numbers[i] for i in rank_passages([pages[n] for n in numbers], q)][:limit]
    return {'manual_id': manual_id, 'tier': manual['tier'],
            'pages': [{'page': n, 'text': pages[n]} for n in ranked]}


@router.get('/sources/{source_id}')
async def read_source(source_id: int, find: Optional[str] = Query(None, max_length=100),
                      offset: int = Query(0, ge=0), limit: int = Query(20_000, ge=1, le=100_000),
                      key: dict = Depends(require_agent)):
    """Source text exactly as pitcrew stored it; quote excerpts from this, not from your own copy."""
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(404, 'Source not found')
    meta = {k: source[k] for k in ('id', 'car_id', 'kind', 'url', 'domain', 'title', 'tier',
                                    'extracted_by', 'manual_id', 'page', 'fetched_at')}
    text = source['content']
    if find:
        hits = []
        for m in re.finditer(re.escape(find), text, re.I):
            hits.append({'offset': m.start(), 'context': text[max(0, m.start() - 300):m.end() + 300]})
            if len(hits) == 20:
                break
        return {**meta, 'length': len(text), 'matches': hits}
    return {**meta, 'length': len(text), 'offset': offset, 'text': text[offset:offset + limit]}


@router.get('/parts/{part_id}/verification-packet')
async def verification_packet(part_id: int, key: dict = Depends(require_agent)):
    """What a verifier sees: the evidence and the build sheet, never the claimant's reasoning."""
    part = await _agent_part(part_id)
    claim = await db.get_current_claim(part_id)
    if not claim:
        raise HTTPException(404, 'Part has no fitment claim')
    source = await db.get_source(claim['source_id'])
    context = None
    if source:
        span = fitment.bind_excerpt(claim['excerpt'], source['content'], EXCERPT_LINES[source['kind']])
        if span:
            lines = source['content'].split('\n')
            context = {'before': lines[max(0, span[0] - CONTEXT_LINES):span[0]],
                       'after': lines[span[1]:span[1] + CONTEXT_LINES]}
    return {
        'part_id': part_id,
        'claim_id': claim['id'],
        'part_number': claim['part_number'],
        'part_name': part['name'],
        'build': await db.get_build(part['car_id']),
        'excerpt': claim['excerpt'],
        'context': context,
        'source': {k: source[k] for k in ('id', 'url', 'title', 'kind', 'manual_id', 'page')}
        if source else None,
        'claimed_by_you': claim['agent_key_id'] == key['id'],
    }


# ── Writes ───────────────────────────────────────────────────────────────────

class SourceBody(BaseModel):
    url: Optional[str] = Field(None, max_length=2000)
    manual_id: Optional[int] = None
    page: Optional[int] = Field(None, ge=1)


@router.post('/cars/{car_id}/sources', status_code=201)
async def add_source(car_id: int, body: SourceBody, key: dict = Depends(require_agent),
                     idempotency_key: Optional[str] = Header(None)):
    await _car(car_id)
    if (body.url is None) == (body.manual_id is None):
        raise HTTPException(400, 'Give either url, or manual_id and page')

    async def run():
        if body.url:
            try:
                page = await fetch_page(body.url)
            except FetchError as e:
                raise HTTPException(422, f'Fetch failed: {e}')
            data = {'kind': 'web_page', 'url': page.url, 'domain': page.domain, 'title': page.title,
                    'tier': domain_tier(page.domain), 'extracted_by': 'text', 'content': page.text}
        else:
            manual = await db.get_manual(body.manual_id)
            if not manual or manual['car_id'] != car_id:
                raise HTTPException(400, 'Manual not found for this car')
            if body.page is None:
                raise HTTPException(400, 'page is required with manual_id')
            pages = await _manual_pages(manual)
            if body.page not in pages:
                raise HTTPException(422, f'Page {body.page} has no text layer')
            data = {'kind': 'manual_page', 'title': f'{manual["title"]}, p. {body.page}',
                    'tier': manual['tier'], 'extracted_by': 'text', 'manual_id': manual['id'],
                    'page': body.page, 'content': pages[body.page]}
        data['content'] = data['content'][:MAX_SOURCE_TEXT]
        data['content_sha256'] = hashlib.sha256(data['content'].encode()).hexdigest()
        source_id = await db.create_source(car_id, key['id'], data)
        return 201, {'id': source_id, 'kind': data['kind'], 'url': data.get('url'), 'title': data['title'],
                     'tier': data['tier'], 'length': len(data['content'])}

    return await _idempotent(key, idempotency_key, {'car': car_id, **body.model_dump()}, run)


class AgentBuildValue(BaseModel):
    value: Any
    source: Literal['owner', 'data_plate', 'catalogue', 'document']
    source_id: Optional[int] = None
    quote: Optional[str] = Field(None, max_length=MAX_EXCERPT)


@router.put('/cars/{car_id}/build/{field}')
async def write_build(car_id: int, field: str, body: AgentBuildValue, key: dict = Depends(require_agent)):
    await _car(car_id)
    if body.source in ('catalogue', 'document'):
        if body.source_id is None or not body.quote:
            raise HTTPException(400, f'{body.source} values need source_id and the quote they are read from')
        source = await _car_source(car_id, body.source_id)
        if await db._current_tier(source) not in VERIFYING_TIERS:
            raise HTTPException(400, f'{body.source} values need a catalogue or OEM source')
        if not fitment.quote_in_text(body.quote, source['content']):
            raise HTTPException(400, 'quote not found in the source text')
        if field in fitment.BUILD_FIELDS and not fitment.value_in_quote(field, body.value, body.quote):
            raise HTTPException(400, 'quote does not state this value')
    elif body.source_id is not None:
        raise HTTPException(400, f'{body.source} values do not take a source_id')
    try:
        await db.set_build_field(car_id, field, body.value, source=body.source, source_id=body.source_id,
                                 agent_key_id=key['id'], quote=body.quote)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await build_sheet(car_id)


class Predicate(BaseModel):
    field: str
    op: Literal['eq', 'in', 'range', 'has_option', 'lacks_option']
    value: Optional[str] = Field(None, max_length=64)
    values: Optional[list[str]] = Field(None, max_length=20)
    min: Optional[str] = None
    max: Optional[str] = None
    quote: str = Field(..., max_length=MAX_EXCERPT)


class ClaimBody(BaseModel):
    part_number: str = Field(..., max_length=40)
    claimed: Level
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    applies_when: list[Predicate] = Field(default_factory=list, max_length=20)
    source_id: int
    excerpt: str = Field(..., max_length=MAX_EXCERPT)


async def _claim_dict(car_id: int, claim: ClaimBody) -> dict:
    if len(fitment.normalise_part_number(claim.part_number)) < fitment.MIN_PART_NUMBER:
        raise HTTPException(400, f'part_number needs at least {fitment.MIN_PART_NUMBER} letters or digits')
    unknown = [f for f in claim.depends_on if f not in fitment.BUILD_FIELDS]
    if unknown:
        raise HTTPException(400, f'Unknown depends_on fields: {", ".join(unknown)}')
    preds = [p.model_dump(exclude_none=True) for p in claim.applies_when]
    for pred in preds:
        try:
            fitment.validate_predicate(pred)
        except ValueError as e:
            raise HTTPException(400, str(e))
    source = await _car_source(car_id, claim.source_id)
    max_lines = EXCERPT_LINES[source['kind']]
    span = fitment.bind_excerpt(claim.excerpt, source['content'], max_lines)
    if not span:
        raise HTTPException(400, f'excerpt must be {max_lines} whole line(s) of the source text, quoted from '
                                 f'GET /api/agent/sources/{{id}}')
    excerpt = '\n'.join(source['content'].split('\n')[span[0]:span[1]])
    if len(excerpt) > MAX_EXCERPT:
        raise HTTPException(400, f'the bound line is over {MAX_EXCERPT} characters; this page has no usable rows')
    return {**claim.model_dump(), 'part_number': claim.part_number.strip(), 'applies_when': preds,
            'excerpt': excerpt}


class AgentPartBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: Category = 'Mechanical'
    quantity: int = Field(1, ge=1, le=999)
    notes: Optional[str] = Field(None, max_length=5000)
    supplier: Optional[str] = Field(None, max_length=200)
    url: Optional[str] = Field(None, max_length=2000)
    price: Optional[float] = Field(None, ge=0)
    brand: Optional[str] = Field(None, max_length=100)
    origin: Optional[Origin] = None
    superseded_by: Optional[str] = Field(None, max_length=40)
    claim: ClaimBody


async def _part_response(part_id: int, result) -> dict:
    return {'part': await db.get_part(part_id), 'gate': _gate_json(result)}


@router.post('/cars/{car_id}/parts', status_code=201)
async def add_part(car_id: int, body: AgentPartBody, key: dict = Depends(require_agent),
                   idempotency_key: Optional[str] = Header(None)):
    await _car(car_id)

    async def run():
        claim = await _claim_dict(car_id, body.claim)
        await _check_url(car_id, body.url, claim['part_number'])
        try:
            part_id, result = await db.create_agent_part(
                car_id, key['id'], body.model_dump(exclude={'claim'}), claim)
        except db.NoFitError as e:
            raise HTTPException(422, {'message': str(e), 'gate': _gate_json(e.result)})
        return 201, await _part_response(part_id, result)

    return await _idempotent(key, idempotency_key, {'car': car_id, **body.model_dump()}, run)


@router.post('/parts/{part_id}/claims', status_code=201)
async def replace_claim(part_id: int, body: ClaimBody, key: dict = Depends(require_agent),
                        idempotency_key: Optional[str] = Header(None)):
    part = await _agent_part(part_id)

    async def run():
        claim = await _claim_dict(part['car_id'], body)
        try:
            result = await db.replace_claim(part_id, key['id'], claim)
        except db.NoFitError as e:
            raise HTTPException(422, {'message': str(e), 'gate': _gate_json(e.result)})
        except ValueError as e:
            raise HTTPException(409, str(e))
        return 201, await _part_response(part_id, result)

    return await _idempotent(key, idempotency_key, {'part': part_id, **body.model_dump()}, run)


class VerificationBody(BaseModel):
    claim_id: int
    verdict: Literal['explicit_fit', 'ambiguous', 'no_fit']
    quote: str = Field(..., min_length=1, max_length=MAX_EXCERPT)


@router.post('/parts/{part_id}/verifications', status_code=201)
async def verify_part(part_id: int, body: VerificationBody, key: dict = Depends(require_agent),
                      idempotency_key: Optional[str] = Header(None)):
    await _agent_part(part_id)

    async def run():
        try:
            result = await db.add_verification(part_id, body.claim_id, key['id'], body.verdict, body.quote)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return 201, await _part_response(part_id, result)

    return await _idempotent(key, idempotency_key, {'part': part_id, **body.model_dump()}, run)


class AgentPartPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[Category] = None
    quantity: Optional[int] = Field(None, ge=1, le=999)
    notes: Optional[str] = Field(None, max_length=5000)
    supplier: Optional[str] = Field(None, max_length=200)
    url: Optional[str] = Field(None, max_length=2000)
    price: Optional[float] = Field(None, ge=0)
    brand: Optional[str] = Field(None, max_length=100)
    origin: Optional[Origin] = None
    superseded_by: Optional[str] = Field(None, max_length=40)
    status: Optional[Literal['wishlist', 'ordered', 'received', 'installed']] = None


@router.patch('/parts/{part_id}')
async def edit_part(part_id: int, body: AgentPartPatch, key: dict = Depends(require_agent)):
    part = await _agent_part(part_id)
    data = body.model_dump(exclude_none=True)
    claim = await db.get_current_claim(part_id)
    await _check_url(part['car_id'], data.get('url'), claim['part_number'] if claim else part['part_number'])
    try:
        # Agents have no override: only the owner can order an unverified part
        await db.update_part(part_id, data, allowed=db.AGENT_PART_FIELDS + ('status',))
    except db.FitmentGateError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await db.get_part(part_id)


class AgentJournalBody(BaseModel):
    type: Literal['research', 'note', 'service', 'converse']
    title: str = Field(..., min_length=1, max_length=200)
    body: Optional[str] = Field(None, max_length=50_000)
    odometer: Optional[int] = Field(None, ge=0)
    source_ids: list[int] = Field(default_factory=list, max_length=50)


@router.post('/cars/{car_id}/journal', status_code=201)
async def add_journal(car_id: int, body: AgentJournalBody, key: dict = Depends(require_agent),
                      idempotency_key: Optional[str] = Header(None)):
    await _car(car_id)

    async def run():
        for sid in body.source_ids:
            await _car_source(car_id, sid)
        entry_id = await db.create_journal(car_id, {
            **body.model_dump(exclude={'source_ids'}),
            'agent_key_id': key['id'],
            'source_ids': json.dumps(body.source_ids) if body.source_ids else None,
        })
        return 201, await db.get_journal(entry_id)

    return await _idempotent(key, idempotency_key, {'car': car_id, **body.model_dump()}, run)
