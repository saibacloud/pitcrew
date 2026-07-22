# Bearer token authentication + rate limiting

import logging
import os
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

API_TOKEN = os.environ.get('API_TOKEN', '')


async def require_token(request: Request):
    """FastAPI dependency — rejects requests without a valid Bearer token."""
    if not API_TOKEN:
        log.warning("API_TOKEN not set — all requests allowed (dev mode)")
        return
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer ') or not secrets.compare_digest(auth[7:], API_TOKEN):
        raise HTTPException(401, 'Unauthorized')


# ── Simple in-memory rate limiter ───────────────────────────────────────────

class _RateLimiter:
    """Token-bucket style per-IP limiter."""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str):
        now = time.monotonic()
        bucket = self._hits[key]
        # Prune expired entries
        self._hits[key] = bucket = [t for t in bucket if now - t < self.window]
        if len(bucket) >= self.max_calls:
            raise HTTPException(429, 'Too many AI requests — try again shortly')
        bucket.append(now)


# 10 AI calls per minute per IP
ai_limiter = _RateLimiter(max_calls=10, window_seconds=60)


async def rate_limit_ai(request: Request):
    """FastAPI dependency — rate-limits AI endpoints per client IP."""
    # Behind NPM the socket peer is always the proxy — key on the original
    # client from X-Forwarded-For instead (first hop, set by the proxy)
    fwd = request.headers.get('X-Forwarded-For', '')
    client = fwd.split(',')[0].strip() if fwd else (
        request.client.host if request.client else 'unknown')
    ai_limiter.check(client)
