import asyncio
import json

from backend import db
from backend.services.ai import check
from backend.services.ai.base import Answer, Image, PdfBytes

KEY = 'sk-proj-TESTopenaiKEY000000000000000000abcd'


class FakeAdapter:
    """Answers each check correctly, and records what it was sent."""
    name = 'openai'

    def __init__(self, fail_search=False):
        self.fail_search = fail_search
        self.calls = []

    async def generate(self, system, messages, *, search, label):
        parts = messages[0].parts
        self.calls.append((label, search, [type(p).__name__ for p in parts]))
        if search:
            if self.fail_search:
                raise RuntimeError(f'boom {KEY}')
            return Answer('Python 3.14\n\n**Sources**\n- [python.org](https://www.python.org)')
        if any(isinstance(p, Image) for p in parts):
            return Answer('Red')
        if any(isinstance(p, PdfBytes) for p in parts):
            return Answer('PITCREW-4827')
        return Answer('pong')

    async def extract_json(self, system, prompt, schema, label):
        return json.loads('{"changes": [{"name": "socket"}]}')


def run_check(monkeypatch, tmp_path, capsys, adapter):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'check.db'))
    monkeypatch.setattr(db, '_db', None)
    monkeypatch.setattr(db, '_write_lock', asyncio.Lock())

    async def resolve():
        return adapter

    async def status():
        return {'provider': 'openai', 'model': 'gpt-test', 'key_last4': 'abcd', 'state': 'ok'}

    monkeypatch.setattr(check, 'resolve', resolve)
    monkeypatch.setattr(check, 'status', status)
    code = asyncio.run(check.main())
    return code, capsys.readouterr().out


def test_check_passes_every_capability(monkeypatch, tmp_path, capsys):
    adapter = FakeAdapter()
    code, out = run_check(monkeypatch, tmp_path, capsys, adapter)
    assert code == 0
    for name in ('chat', 'web search', 'image', 'pdf', 'json extraction'):
        assert f'PASS  {name}' in out
    assert 'key: ...abcd' in out and KEY not in out
    assert [(label, search) for label, search, _ in adapter.calls] == [
        ('check chat', False), ('check search', True), ('check image', False), ('check pdf', False)]


def test_check_reports_failures_without_the_key(monkeypatch, tmp_path, capsys):
    code, out = run_check(monkeypatch, tmp_path, capsys, FakeAdapter(fail_search=True))
    assert code == 1
    assert 'FAIL  web search: RuntimeError' in out and 'PASS  chat' in out
    assert KEY not in out and 'boom' not in out
