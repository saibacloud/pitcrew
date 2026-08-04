# Auth routes — access-code login, logout, session probe

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend import auth

router = APIRouter(prefix='/api/auth')


class LoginRequest(BaseModel):
    # Bounded so an oversized body never reaches the Argon2 verifier
    code: str = Field(min_length=1, max_length=256)


@router.post('/login', dependencies=[Depends(auth.rate_limit_login)])
async def login(req: LoginRequest, resp: Response):
    if not auth.verify_code(req.code):
        raise HTTPException(401, 'Incorrect code')
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session_cookie(),
        httponly=True, secure=True, samesite='strict',
        max_age=auth.SESSION_TTL, path='/',
    )
    return {'status': 'ok'}


@router.post('/logout')
async def logout(resp: Response):
    resp.delete_cookie(
        auth.COOKIE_NAME, path='/',
        httponly=True, secure=True, samesite='strict',
    )
    return {'status': 'ok'}


@router.get('/me', dependencies=[Depends(auth.require_session)])
async def me():
    return {'status': 'ok'}
