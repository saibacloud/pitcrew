# Agent credentials — bearer tokens for external mechanic agents
# Configured like the PIN: generate a token locally, put only its hash in the deployment's
# PITCREW_AGENT_KEYS env secret, and give the token to the agent session.
# Generate (from the project root): python -m backend.agent_keys LABEL

import hashlib
import logging
import os
import re
import secrets
import sys

from fastapi import Header, HTTPException

from backend import db
from backend.auth import RateLimiter

log = logging.getLogger(__name__)

TOKEN_PREFIX = 'pc_'
_LABEL = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
_HASH = re.compile(r'^[0-9a-f]{64}$')

# Fetching evidence reaches out to other sites, so agents get a per-key brake
agent_limiter = RateLimiter(120, 60, 'Too many agent requests — slow down')


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def parse_keys(value: str) -> dict[str, str]:
    """label -> token hash from 'label:sha256,label:sha256'. Bad or duplicate entries are skipped."""
    keys: dict[str, str] = {}
    for entry in (e.strip() for e in (value or '').split(',')):
        if not entry:
            continue
        label, _, token_hash = entry.partition(':')
        label, token_hash = label.strip().lower(), token_hash.strip().lower()
        if not _LABEL.match(label) or not _HASH.match(token_hash):
            log.error("Ignoring malformed PITCREW_AGENT_KEYS entry %r", label or entry[:12])
        elif label in keys or token_hash in keys.values():
            log.error("Ignoring duplicate PITCREW_AGENT_KEYS entry %r", label)
        else:
            keys[label] = token_hash
    return keys


async def load_agent_keys() -> None:
    """Startup: the env secret is the source of truth; an unset secret revokes every key."""
    keys = parse_keys(os.environ.get('PITCREW_AGENT_KEYS', ''))
    await db.sync_agent_keys(keys)
    log.info("Agent keys active: %s", ', '.join(keys) or 'none')


async def require_agent(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency — the calling agent's key {id, label}, or 401."""
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token.startswith(TOKEN_PREFIX):
        raise HTTPException(401, 'Agent bearer token required', headers={'WWW-Authenticate': 'Bearer'})
    key = await db.get_agent_key(hash_token(token.strip()))
    if not key:
        raise HTTPException(401, 'Unknown or revoked agent token', headers={'WWW-Authenticate': 'Bearer'})
    agent_limiter.check(f'agent:{key["id"]}')
    return key


if __name__ == '__main__':
    if len(sys.argv) != 2 or not _LABEL.match(sys.argv[1]):
        raise SystemExit('Usage: python -m backend.agent_keys LABEL  (lowercase letters, digits, - or _)')
    token = new_token()
    print(f'Token for the agent session (shown once):\n{token}\n')
    print(f'Add to PITCREW_AGENT_KEYS (comma-separate several):\n{sys.argv[1]}:{hash_token(token)}')
