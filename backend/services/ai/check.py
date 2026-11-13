# Live check of the configured AI provider: python -m backend.services.ai.check
# Runs one real call per capability and prints pass or fail; the key is never printed.

import asyncio
import io
import os
import sys

from dotenv import load_dotenv

# PITCREW_SECRET_KEY must be in the environment before backend.auth reads it
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

from PIL import Image as PILImage  # noqa: E402

from backend import db  # noqa: E402
from backend.services.ai import prompts  # noqa: E402
from backend.services.ai.base import Image, Message, PdfBytes, describe_error  # noqa: E402
from backend.services.ai.settings import resolve, status  # noqa: E402


def _red_square() -> bytes:
    buf = io.BytesIO()
    PILImage.new('RGB', (64, 64), (220, 20, 20)).save(buf, format='JPEG')
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """A one-page PDF showing the given text."""
    stream = f'BT /F1 24 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    out, offsets = io.BytesIO(), []
    out.write(b'%PDF-1.4\n')
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n' % i + body + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    out.writelines(b'%010d 00000 n \n' % o for o in offsets)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref))
    return out.getvalue()


async def _checks(adapter) -> list[tuple[str, bool, str]]:
    def user(*parts):
        return [Message('user', list(parts))]

    async def plain():
        a = await adapter.generate('Answer in one word.', user('Reply with the word pong.'), search=False, label='check chat')
        return 'pong' in a.text.lower(), a.text[:60]

    async def search():
        a = await adapter.generate('Answer briefly and cite sources.',
                                   user('What is the latest stable Python release? Search the web.'),
                                   search=True, label='check search')
        return '**Sources**' in a.text, 'sources listed' if '**Sources**' in a.text else 'no sources'

    async def image():
        a = await adapter.generate('Answer in one word.', user(Image(_red_square()), 'What colour is this image?'),
                                   search=False, label='check image')
        return 'red' in a.text.lower(), a.text[:60]

    async def pdf():
        a = await adapter.generate('Answer with the code only.',
                                   user(PdfBytes(_pdf('Check code PITCREW-4827')), 'What check code does the PDF show?'),
                                   search=False, label='check pdf')
        return '4827' in a.text, a.text[:60]

    async def extraction():
        result = await adapter.extract_json(
            prompts.INVENTORY_SYSTEM_INSTRUCTION,
            "Current inventory:\n- none\n\nAssistant's previous message:\n(none)\n\n"
            "Owner's latest message:\nI just bought a 14 mm socket",
            prompts.INVENTORY_SCHEMA, 'check extraction')
        names = [c.get('name', '') for c in result.get('changes') or []]
        return any('socket' in n for n in names), ', '.join(names) or 'no changes'

    results = []
    for name, check in (('chat', plain), ('web search', search), ('image', image), ('pdf', pdf),
                        ('json extraction', extraction)):
        try:
            ok, detail = await check()
        except Exception as e:
            ok, detail = False, describe_error(e)
        results.append((name, ok, detail))
    return results


async def main() -> int:
    await db.init_db()
    try:
        state = await status()
        key = f"...{state['key_last4']}" if state['key_last4'] else 'none'
        print(f"Provider: {state['provider']}  model: {state['model']}  key: {key}  state: {state['state']}")
        try:
            adapter = await resolve()
        except Exception as e:
            print(f'FAIL  no provider: {e}')
            return 1
        results = await _checks(adapter)
    finally:
        await db.close_db()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
