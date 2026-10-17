# Agent credentials — bearer tokens for external mechanic agents
# Tokens are shown once at creation; the DB keeps only their SHA-256.
# CLI (from the project root): python -m backend.agent_keys create "Claude Code mechanic" | list | revoke ID

import asyncio
import hashlib
import secrets
import sys

from fastapi import Header, HTTPException

from backend import db
from backend.auth import RateLimiter

TOKEN_PREFIX = 'pc_'

# Fetching evidence reaches out to other sites, so agents get a per-key brake
agent_limiter = RateLimiter(120, 60, 'Too many agent requests — slow down')


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


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


async def _cli(args: list[str]) -> None:
    await db.init_db()
    try:
        if args[:1] == ['create'] and len(args) == 2 and args[1].strip():
            token = new_token()
            key_id = await db.create_agent_key(args[1].strip(), hash_token(token))
            print(f'Key {key_id} created. Token (shown once):\n{token}')
        elif args == ['list']:
            for k in await db.list_agent_keys():
                state = f'revoked {k["revoked_at"]}' if k['revoked_at'] else 'active'
                print(f'{k["id"]}\t{k["label"]}\t{state}\t{k["created_at"]}')
        elif args[:1] == ['revoke'] and len(args) == 2 and args[1].isdigit():
            print('Revoked' if await db.revoke_agent_key(int(args[1])) else 'No active key with that ID')
        else:
            raise SystemExit('Usage: python -m backend.agent_keys create LABEL | list | revoke ID')
    finally:
        await db.close_db()


if __name__ == '__main__':
    asyncio.run(_cli(sys.argv[1:]))
