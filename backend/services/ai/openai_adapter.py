# OpenAI adapter (Responses API through the openai SDK)

import asyncio
import base64
import json
import logging
import os
import time

from openai import (
    APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError, NotFoundError,
    PermissionDeniedError, RateLimitError,
)

from backend.services.ai.base import (
    Answer, EmptyResponse, Image, Message, PdfBytes, PdfFile, PdfLimits, ValidationFailed,
    describe_error, sources_markdown,
)

log = logging.getLogger(__name__)

# OpenAI's recommended general model; supports web_search, image and file input on the Responses API
DEFAULT_MODEL = 'gpt-6-astra'

_MB = 1024 * 1024
_INLINE_LIMIT = 20 * _MB  # larger per-request PDFs go through the Files API to keep request bodies modest
_FILE_TTL = 47 * 3600     # uploads are created to expire after 48h
_FILE_EXPIRY = {'anchor': 'created_at', 'seconds': 48 * 3600}


class OpenAIAdapter:
    name = 'openai'
    label = 'OpenAI'
    # File inputs: each file under 50 MB, 50 MB combined per request; no page limit is documented
    pdf_limits = PdfLimits(max_bytes=50 * _MB, max_pages=300, request_bytes=50 * _MB)

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        # The SDK retries connection errors, 408, 409, 429 and 5xx with backoff
        self.client = client or AsyncOpenAI(api_key=api_key)
        self._uploaded: dict[str, tuple[str, float]] = {}  # cache key -> (file id, expiry)

    # ── Calls ────────────────────────────────────────────────────────────────

    async def generate(self, system: str, messages: list[Message], *, search: bool, label: str) -> Answer:
        """One answer; with search, the web_search tool runs and its citations are appended."""
        kwargs = {'instructions': system, 'input': [await self._message(m) for m in messages]}
        if search:
            kwargs['tools'] = [{'type': 'web_search'}]
        response = await self._call(label, **kwargs)

        text = (response.output_text or '').strip()
        if not text:
            log.warning("OpenAI %s returned no text (status=%s, incomplete=%s)", label,
                        response.status, getattr(response.incomplete_details, 'reason', None))
            raise EmptyResponse(label)
        return Answer(text + sources_markdown(_citations(response)), None)

    async def extract_json(self, system: str, prompt: str, schema: dict, label: str) -> dict:
        response = await self._call(
            label, instructions=system, input=prompt,
            text={'format': {'type': 'json_schema', 'name': label.replace(' ', '_'), 'schema': schema, 'strict': True}},
        )
        if not (response.output_text or '').strip():
            raise EmptyResponse(label)
        return json.loads(response.output_text)

    async def validate(self) -> None:
        try:
            await self.client.models.retrieve(self.model)
        except Exception as e:
            log.info("OpenAI key check failed: %s", describe_error(e))
            if isinstance(e, NotFoundError):
                raise ValidationFailed(f'Model "{self.model}" not found') from None
            if isinstance(e, (AuthenticationError, PermissionDeniedError)):
                raise ValidationFailed('OpenAI rejected this key') from None
            if isinstance(e, RateLimitError):
                raise ValidationFailed('OpenAI rate limit or quota reached') from None
            if isinstance(e, APIConnectionError):
                raise ValidationFailed('Could not reach OpenAI') from None
            if isinstance(e, APIStatusError):
                raise ValidationFailed('OpenAI could not check this key') from None
            raise ValidationFailed('Could not reach OpenAI') from None

    async def _call(self, label: str, **kwargs):
        t0 = time.monotonic()
        try:
            response = await self.client.responses.create(model=self.model, **kwargs)
        except Exception as e:
            log.warning("OpenAI %s call failed: %s", label, describe_error(e))
            raise
        log.info("OpenAI %s completed in %.1fs", label, time.monotonic() - t0)
        return response

    # ── Inputs ───────────────────────────────────────────────────────────────

    async def _message(self, m: Message) -> dict:
        role = 'assistant' if m.role == 'model' else m.role
        if all(isinstance(p, str) for p in m.parts):
            return {'role': role, 'content': '\n\n'.join(m.parts)}
        return {'role': role, 'content': [await self._part(p) for p in m.parts]}

    async def _part(self, p) -> dict:
        if isinstance(p, str):
            return {'type': 'input_text', 'text': p}
        if isinstance(p, Image):
            return {'type': 'input_image', 'image_url': _data_url(p.mime, p.data), 'detail': 'high'}
        if isinstance(p, PdfFile):
            return {'type': 'input_file', 'file_id': await self._cached_upload(p)}
        if isinstance(p, PdfBytes):
            if len(p.data) <= _INLINE_LIMIT:
                return {'type': 'input_file', 'filename': 'document.pdf',
                        'file_data': _data_url('application/pdf', p.data)}
            return {'type': 'input_file', 'file_id': await self._upload(('document.pdf', p.data))}
        raise TypeError(f'Unsupported part {type(p).__name__}')

    async def _upload(self, file) -> str:
        f = await self.client.files.create(file=file, purpose='user_data', expires_after=_FILE_EXPIRY)
        return f.id

    async def _cached_upload(self, pdf: PdfFile) -> str:
        """File ID for a whole PDF, uploaded once and reused until the upload expires."""
        hit = self._uploaded.get(pdf.cache_key)
        if hit and hit[1] > time.time():
            return hit[0]
        data = await asyncio.to_thread(_read, pdf.path)
        file_id = await self._upload((os.path.basename(pdf.path), data))
        self._uploaded[pdf.cache_key] = (file_id, time.time() + _FILE_TTL)
        return file_id


def _read(path: str) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


def _data_url(mime: str, data: bytes) -> str:
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'


def _citations(response) -> list[tuple[str, str]]:
    """(title, url) pairs from the url_citation annotations on the answer text."""
    sources: dict[str, str] = {}
    for item in response.output or []:
        if getattr(item, 'type', None) != 'message':
            continue
        for content in item.content or []:
            for a in getattr(content, 'annotations', None) or []:
                if getattr(a, 'type', None) == 'url_citation' and a.url:
                    sources.setdefault(a.url, a.title or 'Source')
    return [(title, url) for url, title in sources.items()]
