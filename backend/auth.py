# Session authentication — Argon2id access code + signed session cookie

import logging
import os
import secrets
import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

log = logging.getLogger(__name__)

COOKIE_NAME = 'pitcrew_session'

# Argon2id hash of the access code — generate with `python -m backend.auth`
CODE_HASH = os.environ.get('PITCREW_CODE_HASH', '')

# An unset key means sessions are signed with a per-process random secret, so
# a restart logs everyone out. That beats shipping a guessable default that
# would let anyone forge a session cookie.
SECRET_KEY = os.environ.get('PITCREW_SECRET_KEY', '')
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    # Silent under `python -m backend.auth`, which only needs the hasher
    if __name__ != '__main__':
        log.warning("PITCREW_SECRET_KEY not set — using an ephemeral signing key; "
                    "sessions will not survive a restart")

SESSION_TTL = int(os.environ.get('PITCREW_SESSION_TTL', 60 * 60 * 12))  # 12h

_ph = PasswordHasher()  # Argon2id defaults
_signer = TimestampSigner(SECRET_KEY)


# ── Access code ─────────────────────────────────────────────────────────────

def hash_code(code: str) -> str:
    """Argon2id hash for PITCREW_CODE_HASH."""
    return _ph.hash(code)


def verify_code(code: str) -> bool:
    # Fail closed: with no hash configured nobody gets in, rather than
    # falling back to an open instance
    if not CODE_HASH:
        log.error("PITCREW_CODE_HASH not set — login is disabled")
        return False
    try:
        return _ph.verify(CODE_HASH, code)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


# ── Session cookie ──────────────────────────────────────────────────────────

def make_session_cookie() -> str:
    return _signer.sign(b'ok').decode()


def valid_session(cookie: str | None) -> bool:
    if not cookie:
        return False
    try:
        _signer.unsign(cookie, max_age=SESSION_TTL)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_session(pitcrew_session: str | None = Cookie(default=None)):
    """FastAPI dependency — rejects requests without a valid session cookie."""
    if not valid_session(pitcrew_session):
        raise HTTPException(401, 'Unauthorized')


# ── Simple in-memory rate limiter ───────────────────────────────────────────

class _RateLimiter:
    """Token-bucket style per-IP limiter."""

    def __init__(self, max_calls: int, window_seconds: int, message: str):
        self.max_calls = max_calls
        self.window = window_seconds
        self.message = message
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str):
        now = time.monotonic()
        bucket = self._hits[key]
        # Prune expired entries
        self._hits[key] = bucket = [t for t in bucket if now - t < self.window]
        if len(bucket) >= self.max_calls:
            raise HTTPException(429, self.message)
        bucket.append(now)


def client_ip(request: Request) -> str:
    # Behind NPM the socket peer is always the proxy — key on the original
    # client from X-Forwarded-For instead (first hop, set by the proxy)
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


# 10 AI calls per minute per IP
ai_limiter = _RateLimiter(10, 60, 'Too many AI requests — try again shortly')

# 8 login attempts per 15 minutes per IP — brute-force brake on the access code
login_limiter = _RateLimiter(8, 15 * 60, 'Too many attempts — try again later')


async def rate_limit_ai(request: Request):
    """FastAPI dependency — rate-limits AI endpoints per client IP."""
    ai_limiter.check(client_ip(request))


async def rate_limit_login(request: Request):
    """FastAPI dependency — rate-limits login attempts per client IP."""
    login_limiter.check(client_ip(request))


# ── Hash generator ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Read from a prompt rather than argv so the code stays out of shell history
    from getpass import getpass

    entered = getpass('Access code: ')
    if entered != getpass('Confirm: '):
        raise SystemExit('Codes do not match')
    if not entered:
        raise SystemExit('Empty code')
    print(hash_code(entered))
