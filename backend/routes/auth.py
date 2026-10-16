# Auth routes — PIN login, logout, session probe

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend import auth
from backend.db import create_session, prune_sessions, revoke_session

router = APIRouter(prefix='/api/auth')


class LoginRequest(BaseModel):
    # Bounded and digits-only so nothing else reaches the Argon2 verifier
    pin: str = Field(pattern=auth.PIN_PATTERN)


@router.post('/login', dependencies=[Depends(auth.rate_limit_login)])
async def login(req: LoginRequest, resp: Response):
    if not auth.verify_pin(req.pin):
        raise HTTPException(401, 'Incorrect PIN')
    cookie, id_hash = auth.new_session()
    await prune_sessions()
    await create_session(id_hash, auth.SESSION_TTL)
    resp.set_cookie(
        auth.COOKIE_NAME, cookie,
        httponly=True, secure=True, samesite='strict',
        max_age=auth.SESSION_TTL, path='/',
    )
    return {'status': 'ok'}


@router.post('/logout')
async def logout(resp: Response, pitcrew_session: str | None = Cookie(default=None)):
    id_hash = auth.session_hash(pitcrew_session)
    if id_hash:
        await revoke_session(id_hash)
    resp.delete_cookie(
        auth.COOKIE_NAME, path='/',
        httponly=True, secure=True, samesite='strict',
    )
    return {'status': 'ok'}


@router.get('/me', dependencies=[Depends(auth.require_session)])
async def me():
    return {'status': 'ok'}
