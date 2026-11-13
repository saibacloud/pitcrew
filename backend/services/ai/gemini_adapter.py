# Google Gemini adapter (google-genai SDK)

import asyncio
import io
import json
import logging
import time

from google import genai
from google.genai import errors, types

from backend.services.ai.base import (
    Answer, EmptyResponse, Image, Message, PdfBytes, PdfFile, PdfLimits, ValidationFailed,
    describe_error, sources_markdown,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'gemini-flash-latest'

# Gemini reads PDFs natively (text layer plus page images), so tables, diagrams
# and scanned pages survive. Medium is Google's recommended PDF resolution.
_PDF_RESOLUTION = types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM
_IMAGE_RESOLUTION = types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH
_INLINE_LIMIT = 20 * 1024 * 1024
_FILE_TTL = 47 * 3600  # Files API keeps uploads for 48h


class GeminiAdapter:
    name = 'gemini'
    label = 'Gemini'
    # 50 MB is Gemini's per-PDF limit; the page budget keeps a question to a few manuals' worth
    pdf_limits = PdfLimits(max_bytes=50 * 1024 * 1024, max_pages=300, request_bytes=None)

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        # Retries cover 408/429/5xx and transient network errors with exponential backoff
        self.client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2.0, max_delay=15.0),
            ),
        )
        self._uploaded: dict[str, tuple[str, float]] = {}  # cache key -> (file uri, expiry)

    # ── Calls ────────────────────────────────────────────────────────────────

    async def generate(self, system: str, messages: list[Message], *, search: bool, label: str) -> Answer:
        """One answer; with search, grounded with Google Search and its sources appended."""
        contents = [types.Content(role=m.role, parts=await self._parts(m.parts)) for m in messages]
        # Temperature stays at the model default; Gemini 3 degrades below 1.0
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(google_search=types.GoogleSearch())] if search else None,
        )
        response = await self._call(contents, config, label)

        text = (response.text or '').strip()
        if not text:
            reason = response.candidates[0].finish_reason if response.candidates else None
            log.warning("Gemini %s returned no text (finish_reason=%s)", label, reason)
            raise EmptyResponse(label)

        text += sources_markdown(_grounding_sources(response))
        meta = response.candidates[0].grounding_metadata
        entry = meta.search_entry_point if meta else None
        return Answer(text, entry.rendered_content if entry else None)

    async def extract_json(self, system: str, prompt: str, schema: dict, label: str) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type='application/json',
            response_json_schema=schema,
        )
        response = await self._call(prompt, config, label)
        if not (response.text or '').strip():
            raise EmptyResponse(label)
        return json.loads(response.text)

    async def validate(self) -> None:
        # A token count costs nothing and accepts any servable name, including -latest aliases
        try:
            await self.client.aio.models.count_tokens(model=self.model, contents='ping')
        except errors.APIError as e:
            log.info("Gemini key check failed: %s", describe_error(e))
            if e.code == 404:
                raise ValidationFailed(f'Model "{self.model}" not found') from None
            if e.code in (400, 401, 403):
                raise ValidationFailed('Google rejected this key') from None
            if e.code == 429:
                raise ValidationFailed('Google rate limit or quota reached') from None
            raise ValidationFailed('Google could not check this key') from None
        except Exception as e:
            log.info("Gemini key check failed: %s", describe_error(e))
            raise ValidationFailed('Could not reach Google') from None

    async def _call(self, contents, config: types.GenerateContentConfig, label: str):
        t0 = time.monotonic()
        try:
            response = await self.client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as e:
            log.warning("Gemini %s call failed: %s", label, describe_error(e))
            raise
        log.info("Gemini %s completed in %.1fs", label, time.monotonic() - t0)
        return response

    # ── Inputs ───────────────────────────────────────────────────────────────

    async def _parts(self, parts: list) -> list[types.Part]:
        out = []
        for p in parts:
            if isinstance(p, str):
                out.append(types.Part.from_text(text=p))
            elif isinstance(p, Image):
                out.append(types.Part.from_bytes(data=p.data, mime_type=p.mime, media_resolution=_IMAGE_RESOLUTION))
            elif isinstance(p, PdfFile):
                out.append(await self._pdf_file_part(p))
            elif isinstance(p, PdfBytes):
                out.append(await self._pdf_bytes_part(p.data))
            else:
                raise TypeError(f'Unsupported part {type(p).__name__}')
        return out

    async def _upload_pdf(self, file) -> str:
        """Upload a PDF path or stream through the Files API and return its URI once active."""
        f = await self.client.aio.files.upload(file=file, config=types.UploadFileConfig(mime_type='application/pdf'))
        while f.state == types.FileState.PROCESSING:
            await asyncio.sleep(1)
            f = await self.client.aio.files.get(name=f.name)
        if f.state == types.FileState.FAILED:
            raise RuntimeError(f'Gemini could not process uploaded PDF {f.name}')
        return f.uri

    async def _pdf_file_part(self, pdf: PdfFile) -> types.Part:
        """Part for a whole PDF, uploaded once and reused until the upload expires."""
        hit = self._uploaded.get(pdf.cache_key)
        if hit and hit[1] > time.time():
            uri = hit[0]
        else:
            uri = await self._upload_pdf(pdf.path)
            self._uploaded[pdf.cache_key] = (uri, time.time() + _FILE_TTL)
        return types.Part.from_uri(file_uri=uri, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)

    async def _pdf_bytes_part(self, data: bytes) -> types.Part:
        if len(data) <= _INLINE_LIMIT:
            return types.Part.from_bytes(data=data, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)
        uri = await self._upload_pdf(io.BytesIO(data))
        return types.Part.from_uri(file_uri=uri, mime_type='application/pdf', media_resolution=_PDF_RESOLUTION)


def _grounding_sources(response) -> list[tuple[str, str]]:
    """(title, uri) pairs for the web pages Google Search grounding used."""
    sources: dict[str, str] = {}
    for cand in response.candidates or []:
        meta = cand.grounding_metadata
        for chunk in (meta.grounding_chunks or []) if meta else []:
            if chunk.web and chunk.web.uri:
                sources.setdefault(chunk.web.uri, chunk.web.title or chunk.web.domain or 'Source')
    return [(title, uri) for uri, title in sources.items()]
