# Provider-neutral inputs, results and errors shared by the adapters

from typing import NamedTuple


class Image(NamedTuple):
    data: bytes
    mime: str = 'image/jpeg'


class PdfFile(NamedTuple):
    """A whole PDF on disk; uploaded once per cache key and reused while the upload lives."""
    path: str
    cache_key: str


class PdfBytes(NamedTuple):
    """A PDF built per request; inlined when small enough, otherwise uploaded uncached."""
    data: bytes


# A part is plain text (str), an Image, a PdfFile or a PdfBytes
Part = str | Image | PdfFile | PdfBytes


class Message(NamedTuple):
    role: str  # 'user' or 'model'
    parts: list


class PdfLimits(NamedTuple):
    max_bytes: int                # per PDF
    max_pages: int                # PDF pages sent whole per request, across all PDFs
    request_bytes: int | None     # all PDFs in one request, None when unbounded


class Answer(NamedTuple):
    text: str
    # Google's Search Suggestions HTML; the Gemini API terms require showing it with grounded answers
    search_suggestions: str | None = None


class EmptyResponse(Exception):
    """The provider returned no answer text (safety block, recitation stop or empty output)."""


class NoAIKey(Exception):
    """No usable AI API key is configured."""


class ValidationFailed(Exception):
    """A key or model failed its check call; the message is safe to show and never holds the key."""


def sources_markdown(sources: list[tuple[str, str]]) -> str:
    """The **Sources** list appended to web-search answers, from (title, url) pairs."""
    if not sources:
        return ''
    return '\n\n**Sources**\n' + '\n'.join(f'- [{title}]({url})' for title, url in sources)


def describe_error(exc: BaseException) -> str:
    """Exception class and status for logs; SDK messages can quote part of the key, so they are left out."""
    status = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
    return f'{type(exc).__name__}' + (f' ({status})' if status else '')
