# Evidence fetcher
# Pitcrew fetches source pages itself so evidence text never comes from the agent.
# Only public https hosts are reachable; each redirect hop is re-checked.

import asyncio
import io
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_PDF_PAGES = 300
TIMEOUT = httpx.Timeout(15.0)
DEADLINE = 30.0  # whole fetch, so a slow-drip server cannot hold a request open
USER_AGENT = 'pitcrew-evidence/1.0'
_NAT64 = ipaddress.ip_network('64:ff9b::/96')  # is_global, but maps onto IPv4 space
_fetch_slots = asyncio.Semaphore(3)

class FetchError(Exception):
    pass


class Page(NamedTuple):
    url: str
    domain: str
    title: str
    text: str


async def _check_target(url: str) -> str:
    """Reject anything but https to a public address on port 443. Returns the host."""
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname:
        raise FetchError('Only https URLs can be fetched')
    if parts.username or parts.password:
        raise FetchError('URLs with credentials are not allowed')
    if parts.port not in (None, 443):
        raise FetchError('Only port 443 is allowed')
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError(f'Cannot resolve {parts.hostname}')
    for info in infos:
        if not _public(info[4][0]):
            raise FetchError(f'{parts.hostname} resolves to a non-public address')
    return parts.hostname.lower()


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%')[0])
    return ip.is_global and ip not in _NAT64


def _connected_public(resp: httpx.Response) -> bool:
    """The peer actually connected to is public; closes the gap a DNS rebind leaves after the check."""
    stream = resp.extensions.get('network_stream')
    addr = stream.get_extra_info('server_addr') if stream else None
    return bool(addr) and _public(addr[0])


class _TextExtractor(HTMLParser):
    """Visible text with block elements on their own lines and table cells split by ' | '."""

    _SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'head'}
    _VOID = {'br', 'img', 'input', 'meta', 'link', 'hr', 'wbr', 'source', 'col', 'area', 'base', 'embed'}
    _BLOCK = {'p', 'div', 'br', 'li', 'tr', 'table', 'section', 'article', 'header', 'footer',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dd', 'dt', 'ul', 'ol', 'form', 'main', 'nav'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ''
        self._skip = 0
        self._in_title = False

    @staticmethod
    def _hidden(attrs) -> bool:
        a = {k: (v or '').lower() for k, v in attrs}
        style = a.get('style', '').replace(' ', '')
        return 'hidden' in a or a.get('aria-hidden') == 'true' or \
            'display:none' in style or 'visibility:hidden' in style

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self._in_title = True
        # Hidden text is not what a reader of the catalogue sees, so it is never evidence
        elif self._skip or tag in self._SKIP or (tag not in self._VOID and self._hidden(attrs)):
            if tag not in self._VOID:
                self._skip += 1
        elif tag in self._BLOCK:
            self.out.append('\n')
        elif tag in ('td', 'th'):
            self.out.append(' | ')

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif self._skip:
            if tag not in self._VOID:
                self._skip -= 1
        elif tag in self._BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (' '.join(line.split()) for line in ''.join(parser.out).split('\n'))
    return ' '.join(parser.title.split()), '\n'.join(line for line in lines if line.strip(' |'))


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise FetchError(f'PDF has more than {MAX_PDF_PAGES} pages')
    return '\n\n'.join(f'[Page {i + 1}]\n{page.extract_text() or ""}' for i, page in enumerate(reader.pages))


_CHARSET = re.compile(r'charset=["\']?([\w-]+)', re.I)


async def fetch_page(url: str) -> Page:
    async with _fetch_slots:
        try:
            async with asyncio.timeout(DEADLINE):
                return await _fetch(url)
        except TimeoutError:
            raise FetchError(f'Fetch took longer than {DEADLINE:.0f}s')


async def _fetch(url: str) -> Page:
    # trust_env off: a proxy from the environment would bypass the address checks.
    # identity encoding: no decompression, so the size cap applies to what is read.
    headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False,
                                 headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            host = await _check_target(url)
            async with client.stream('GET', url) as resp:
                if not _connected_public(resp):
                    raise FetchError(f'{host} connected to a non-public address')
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get('location', ''))
                    continue
                if resp.status_code != 200:
                    raise FetchError(f'{host} returned HTTP {resp.status_code}')
                if resp.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise FetchError('Server sent a compressed response')
                body = bytearray()
                async for chunk in resp.aiter_raw():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError('Page is larger than 5 MB')
                ctype = resp.headers.get('content-type', '').lower()
                break
        else:
            raise FetchError('Too many redirects')

    if 'application/pdf' in ctype:
        text = await asyncio.to_thread(pdf_to_text, bytes(body))
        title = urlsplit(url).path.rsplit('/', 1)[-1]
    elif 'html' in ctype or 'text/plain' in ctype:
        match = _CHARSET.search(ctype)
        try:
            decoded = bytes(body).decode(match.group(1) if match else 'utf-8', errors='replace')
        except LookupError:
            decoded = bytes(body).decode('utf-8', errors='replace')
        title, text = html_to_text(decoded) if 'html' in ctype else ('', decoded)
    else:
        raise FetchError(f'Unsupported content type: {ctype or "unknown"}')
    if not text.strip():
        raise FetchError('Page has no readable text (it may need JavaScript)')
    return Page(url, host, title[:200], text)
