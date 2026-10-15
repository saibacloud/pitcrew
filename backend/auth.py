# Session authentication — Argon2id PIN + signed, server-revocable session cookie

import hashlib
import logging
import os
import re
import secrets
import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from backend.db import session_active

log = logging.getLogger(__name__)

COOKIE_NAME = 'pitcrew_session'

# Argon2id hash of the PIN — generate with `python -m backend.auth`
PIN_HASH = os.environ.get('PITCREW_PIN_HASH', '')
PIN_PATTERN = r'^\d{6,12}$'

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


# ── PIN ─────────────────────────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    """Argon2id hash for PITCREW_PIN_HASH."""
    return _ph.hash(pin)


def verify_pin(pin: str) -> bool:
    # Fail closed: with no hash configured nobody gets in, rather than
    # falling back to an open instance
    if not PIN_HASH:
        log.error("PITCREW_PIN_HASH not set — login is disabled")
        return False
    try:
        return _ph.verify(PIN_HASH, pin)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


# ── Session cookie ──────────────────────────────────────────────────────────
# The cookie carries a signed random session ID. The DB stores only its SHA-256,
# so logout revokes the session server-side and a leaked DB yields no cookies.

def new_session() -> tuple[str, str]:
    """(cookie value, session ID hash to store)."""
    session_id = secrets.token_urlsafe(32)
    return _signer.sign(session_id.encode()).decode(), _hash_id(session_id)


def session_hash(cookie: str | None) -> str | None:
    """Stored hash for a cookie whose signature and age check out, else None."""
    if not cookie:
        return None
    try:
        return _hash_id(_signer.unsign(cookie, max_age=SESSION_TTL).decode())
    except (BadSignature, SignatureExpired):
        return None


def _hash_id(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


async def require_session(pitcrew_session: str | None = Cookie(default=None)):
    """FastAPI dependency — rejects requests without a live, unrevoked session."""
    id_hash = session_hash(pitcrew_session)
    if not id_hash or not await session_active(id_hash):
        raise HTTPException(401, 'Unauthorized')


# ── Simple in-memory rate limiter ───────────────────────────────────────────

class _RateLimiter:
    """Token-bucket style per-IP limiter."""

    def __init__(self, max_calls: int, window_seconds: int, message: str):
        self.max_calls = max_calls
        self.window = window_seconds
        self.message = message
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, record: bool = True):
        now = time.monotonic()
        bucket = self._hits[key]
        # Prune expired entries
        self._hits[key] = bucket = [t for t in bucket if now - t < self.window]
        if len(bucket) >= self.max_calls:
            raise HTTPException(429, self.message)
        if record:
            bucket.append(now)

    def record(self, key: str):
        self._hits[key].append(time.monotonic())


def client_ip(request: Request) -> str:
    # Behind NPM the socket peer is always the proxy — key on the original
    # client from X-Forwarded-For instead (first hop, set by the proxy)
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


# 10 AI calls per minute per IP
ai_limiter = _RateLimiter(10, 60, 'Too many AI requests — try again shortly')

# 8 login attempts per 15 minutes per IP — brute-force brake on the PIN
login_limiter = _RateLimiter(8, 15 * 60, 'Too many attempts — try again later')

# 20 failed logins per hour across all clients. X-Forwarded-For is client-supplied,
# so the per-IP limit alone cannot stop a distributed or spoofed PIN guess.
login_failures = _RateLimiter(20, 60 * 60, 'Too many attempts — try again later')
GLOBAL_KEY = 'all'


async def rate_limit_ai(request: Request):
    """FastAPI dependency — rate-limits AI endpoints per client IP."""
    ai_limiter.check(client_ip(request))


async def rate_limit_login(request: Request):
    """FastAPI dependency — rate-limits login attempts per client IP and globally."""
    login_failures.check(GLOBAL_KEY, record=False)
    login_limiter.check(client_ip(request))


# ── Hash generator ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Read from a prompt rather than argv so the PIN stays out of shell history
    from getpass import getpass

    entered = getpass('PIN (6-12 digits): ')
    if not re.fullmatch(PIN_PATTERN, entered):
        raise SystemExit('PIN must be 6 to 12 digits')
    if entered != getpass('Confirm: '):
        raise SystemExit('PINs do not match')
    print(hash_pin(entered))
