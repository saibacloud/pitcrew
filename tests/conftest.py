import asyncio
import os

import pytest

# Importing the app constructs the Gemini client, which needs a key; no AI is called in tests
os.environ.setdefault('GENAI_API_KEY', 'test-key')

from fastapi.testclient import TestClient  # noqa: E402

from backend import db  # noqa: E402
from backend.app import app  # noqa: E402
from backend.auth import require_session  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(db, '_db', None)
    monkeypatch.setattr(db, '_write_lock', asyncio.Lock())
    app.dependency_overrides[require_session] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def run(client):
    """Run a coroutine on the app's event loop, for direct DB setup in tests."""
    return client.portal.call
