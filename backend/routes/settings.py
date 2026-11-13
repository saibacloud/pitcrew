# Settings routes - the AI provider key. The key is write-only: no response carries it.

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth import rate_limit_ai
from backend.services.ai import settings as ai_settings

router = APIRouter(prefix='/api/settings', tags=['settings'])


@router.get('/ai')
async def get_ai_settings():
    return await ai_settings.status()


# The body is parsed by hand: a validation error response would echo the submitted key
@router.put('/ai', dependencies=[Depends(rate_limit_ai)])
async def put_ai_settings(request: Request):
    try:
        body = json.loads(await request.body() or b'{}')
    except ValueError:
        raise HTTPException(400, 'Body must be JSON')
    if not isinstance(body, dict):
        raise HTTPException(400, 'Body must be a JSON object')
    api_key, model = body.get('api_key'), body.get('model')
    if not isinstance(api_key, (str, type(None))) or not isinstance(model, (str, type(None))):
        raise HTTPException(400, 'api_key and model must be strings')
    if len(api_key or '') > 512 or len(model or '') > 100:
        raise HTTPException(400, 'api_key or model is too long')
    try:
        return await ai_settings.save(api_key, model)
    except ai_settings.SettingsError as e:
        raise HTTPException(e.status, e.detail)


@router.delete('/ai')
async def delete_ai_settings():
    return await ai_settings.remove()
