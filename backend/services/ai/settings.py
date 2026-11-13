# AI provider settings: the owner's API key, encrypted at rest, and per-call provider resolution

import base64
import logging
import os
from typing import NamedTuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend import auth, db
from backend.services.ai.base import NoAIKey, ValidationFailed
from backend.services.ai.gemini_adapter import DEFAULT_MODEL as GEMINI_DEFAULT, GeminiAdapter
from backend.services.ai.openai_adapter import DEFAULT_MODEL as OPENAI_DEFAULT, OpenAIAdapter

log = logging.getLogger(__name__)

ADAPTERS = {'gemini': GeminiAdapter, 'openai': OpenAIAdapter}
DEFAULT_MODELS = {'gemini': GEMINI_DEFAULT, 'openai': OPENAI_DEFAULT}
_PREFIXES = (('AIza', 'gemini'), ('sk-', 'openai'))
_HKDF_INFO = b'pitcrew ai-settings api-key v1'


class SettingsError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def infer_provider(api_key: str) -> str | None:
    return next((p for prefix, p in _PREFIXES if api_key.startswith(prefix)), None)


# ── Encryption ───────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(auth.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt(token: str) -> str:
    """Raises InvalidToken when PITCREW_SECRET_KEY has changed since the key was stored."""
    return _fernet().decrypt(token.encode()).decode()


# ── Resolution ───────────────────────────────────────────────────────────────

class _Stored(NamedTuple):
    row: dict | None
    api_key: str | None  # None when there is no row or it cannot be decrypted


async def _stored() -> _Stored:
    row = await db.get_ai_settings()
    if not row:
        return _Stored(None, None)
    try:
        return _Stored(row, decrypt(row['key_enc']))
    except InvalidToken:
        return _Stored(row, None)


def _env() -> tuple[str, str] | None:
    """(key, model) for the GENAI_API_KEY fallback, always Gemini."""
    key = os.environ.get('GENAI_API_KEY', '').strip()
    return (key, os.environ.get('GEMINI_MODEL') or GEMINI_DEFAULT) if key else None


async def status() -> dict:
    stored = await _stored()
    if stored.row:
        return {'provider': stored.row['provider'], 'model': stored.row['model'],
                'key_last4': stored.row['key_last4'], 'state': 'ok' if stored.api_key else 'unreadable'}
    env = _env()
    if env:
        return {'provider': 'gemini', 'model': env[1], 'key_last4': env[0][-4:], 'state': 'env'}
    return {'provider': None, 'model': None, 'key_last4': None, 'state': 'none'}


_cached: tuple[str, str, object] | None = None  # (provider, key, adapter)


def _adapter(provider: str, api_key: str, model: str):
    """The adapter for this provider and key, reusing its SDK client until either changes."""
    global _cached
    if not _cached or _cached[:2] != (provider, api_key):
        _cached = (provider, api_key, ADAPTERS[provider](api_key, model))
    adapter = _cached[2]
    adapter.model = model
    return adapter


async def resolve():
    """The adapter to use now: the stored key, else GENAI_API_KEY as Gemini, else NoAIKey."""
    stored = await _stored()
    if stored.api_key:
        return _adapter(stored.row['provider'], stored.api_key, stored.row['model'])
    env = _env()
    if env:
        return _adapter('gemini', *env)
    raise NoAIKey('No AI API key set')


# ── Changes ──────────────────────────────────────────────────────────────────

async def save(api_key: str | None, model: str | None) -> dict:
    """Store a new key, or re-check the stored key against a new model, then store the model."""
    if not auth.SECRET_CONFIGURED:
        raise SettingsError(409, 'Set PITCREW_SECRET_KEY to store an API key')
    api_key, model = (api_key or '').strip() or None, (model or '').strip() or None
    stored = await _stored()
    if api_key:
        provider = infer_provider(api_key)
        if not provider:
            raise SettingsError(400, 'Unrecognised key. Use a Google AI Studio or OpenAI API key.')
        # A same-provider key keeps the chosen model; a new provider starts on its default
        if not model:
            same = stored.row and stored.row['provider'] == provider
            model = stored.row['model'] if same else DEFAULT_MODELS[provider]
    elif model:
        if not stored.row:
            raise SettingsError(400, 'No API key stored')
        if not stored.api_key:
            raise SettingsError(400, "Stored key can't be decrypted. Enter it again.")
        api_key, provider = stored.api_key, stored.row['provider']
    else:
        raise SettingsError(400, 'Enter an API key or a model')

    try:
        await ADAPTERS[provider](api_key, model).validate()
    except ValidationFailed as e:
        raise SettingsError(400, str(e)) from None
    await db.save_ai_settings(provider, model, encrypt(api_key), api_key[-4:])
    log.info("AI settings saved: %s, model %s", provider, model)
    return await status()


async def remove() -> dict:
    await db.delete_ai_settings()
    log.info("AI settings removed")
    return await status()
