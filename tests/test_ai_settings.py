import logging

import pytest

from backend import auth, db
from backend.services.ai import settings
from backend.services.ai.base import NoAIKey, ValidationFailed
from backend.services.ai.gemini_adapter import GeminiAdapter
from backend.services.ai.openai_adapter import OpenAIAdapter

GOOGLE_KEY = 'AIzaSyTESTgoogleKEY0000000000000000wxyz'
OPENAI_KEY = 'sk-proj-TESTopenaiKEY000000000000000000abcd'


@pytest.fixture
def ai_env(client, monkeypatch, caplog):
    """A configured secret, no env key, and fake adapters that record their key checks."""
    monkeypatch.setattr(auth, 'SECRET_KEY', 'secret-one')
    monkeypatch.setattr(auth, 'SECRET_CONFIGURED', True)
    monkeypatch.delenv('GENAI_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    monkeypatch.setattr(settings, '_cached', None)
    state = {'checks': [], 'fail': None}

    def fake(base):
        class Fake(base):
            def __init__(self, api_key, model, client=None):
                self.api_key, self.model = api_key, model

            async def validate(self):
                state['checks'].append((self.name, self.api_key, self.model))
                if state['fail']:
                    raise ValidationFailed(state['fail'])
        return Fake

    monkeypatch.setattr(settings, 'ADAPTERS', {'gemini': fake(GeminiAdapter), 'openai': fake(OpenAIAdapter)})
    caplog.set_level(logging.DEBUG)
    return state


def put(client, **body):
    return client.put('/api/settings/ai', json=body)


def assert_no_key(*texts):
    for text in texts:
        assert GOOGLE_KEY not in text and OPENAI_KEY not in text
        assert GOOGLE_KEY[4:-4] not in text and OPENAI_KEY[3:-4] not in text


# ── Encryption ───────────────────────────────────────────────────────────────

def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setattr(auth, 'SECRET_KEY', 'secret-one')
    token = settings.encrypt(OPENAI_KEY)
    assert OPENAI_KEY not in token
    assert settings.decrypt(token) == OPENAI_KEY
    assert settings.encrypt(OPENAI_KEY) != token  # Fernet tokens carry a random IV


def test_key_is_encrypted_at_rest(client, run, ai_env):
    assert put(client, api_key=OPENAI_KEY).status_code == 200
    row = run(db.get_ai_settings)
    assert OPENAI_KEY not in row['key_enc'] and row['key_last4'] == 'abcd'
    assert settings.decrypt(row['key_enc']) == OPENAI_KEY


# ── Saving ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('key, provider, model', [
    (GOOGLE_KEY, 'gemini', 'gemini-flash-latest'),
    (OPENAI_KEY, 'openai', 'gpt-6-astra'),
])
def test_provider_is_inferred_from_the_key_prefix(client, ai_env, key, provider, model):
    r = put(client, api_key=key)
    assert r.status_code == 200
    assert r.json() == {'provider': provider, 'model': model, 'key_last4': key[-4:], 'state': 'ok'}
    assert ai_env['checks'] == [(provider, key, model)]


def test_unrecognised_key_is_rejected(client, ai_env):
    r = put(client, api_key='xai-somethingelse1234')
    assert r.status_code == 400
    assert r.json()['detail'] == 'Unrecognised key. Use a Google AI Studio or OpenAI API key.'
    assert ai_env['checks'] == []
    assert client.get('/api/settings/ai').json()['state'] == 'none'


def test_the_key_never_appears_in_responses_or_logs(client, ai_env, caplog):
    responses = [put(client, api_key=OPENAI_KEY), client.get('/api/settings/ai'),
                 put(client, model='gpt-other'), client.get('/api/settings/ai')]
    ai_env['fail'] = 'OpenAI rejected this key'
    responses.append(put(client, api_key=OPENAI_KEY))
    responses.append(put(client, api_key=GOOGLE_KEY))
    responses.append(client.put('/api/settings/ai', json={'api_key': OPENAI_KEY, 'model': 5}))
    responses.append(client.put('/api/settings/ai', json={'api_key': OPENAI_KEY * 20}))
    responses.append(client.delete('/api/settings/ai'))
    assert_no_key(*(r.text for r in responses), caplog.text)


def test_failed_check_stores_nothing(client, ai_env):
    ai_env['fail'] = 'OpenAI rejected this key'
    r = put(client, api_key=OPENAI_KEY)
    assert r.status_code == 400 and r.json()['detail'] == 'OpenAI rejected this key'
    assert client.get('/api/settings/ai').json()['state'] == 'none'


def test_saving_is_refused_without_a_configured_secret(client, run, ai_env, monkeypatch):
    monkeypatch.setattr(auth, 'SECRET_CONFIGURED', False)
    r = put(client, api_key=OPENAI_KEY)
    assert r.status_code == 409 and r.json()['detail'] == 'Set PITCREW_SECRET_KEY to store an API key'
    assert run(db.get_ai_settings) is None and ai_env['checks'] == []


def test_model_only_update_rechecks_the_stored_key(client, ai_env):
    put(client, api_key=OPENAI_KEY)
    r = put(client, model='gpt-6.1-sol')
    assert r.status_code == 200 and r.json()['model'] == 'gpt-6.1-sol' and r.json()['key_last4'] == 'abcd'
    assert ai_env['checks'][-1] == ('openai', OPENAI_KEY, 'gpt-6.1-sol')
    # A new key for the same provider keeps the chosen model; a new provider starts on its default
    assert put(client, api_key='sk-another-key-9999').json()['model'] == 'gpt-6.1-sol'
    assert put(client, api_key=GOOGLE_KEY).json()['model'] == 'gemini-flash-latest'


def test_model_only_update_needs_a_stored_key(client, ai_env):
    assert put(client, model='gpt-6.1-sol').status_code == 400
    assert put(client).status_code == 400


def test_failed_model_change_keeps_the_old_model(client, ai_env):
    put(client, api_key=OPENAI_KEY)
    ai_env['fail'] = 'Model "nope" not found'
    r = put(client, model='nope')
    assert r.status_code == 400 and r.json()['detail'] == 'Model "nope" not found'
    assert client.get('/api/settings/ai').json()['model'] == 'gpt-6-astra'


# ── Reading back ─────────────────────────────────────────────────────────────

def test_changed_secret_reads_as_unreadable(client, ai_env, monkeypatch):
    put(client, api_key=OPENAI_KEY)
    monkeypatch.setattr(auth, 'SECRET_KEY', 'secret-two')
    r = client.get('/api/settings/ai')
    assert r.status_code == 200
    assert r.json() == {'provider': 'openai', 'model': 'gpt-6-astra', 'key_last4': 'abcd', 'state': 'unreadable'}
    r = put(client, model='gpt-6.1-sol')
    assert r.status_code == 400 and r.json()['detail'] == "Stored key can't be decrypted. Enter it again."
    # An unreadable key counts as no stored key, so the env fallback applies
    monkeypatch.setenv('GENAI_API_KEY', 'AIza-env-key')
    adapter = client.portal.call(settings.resolve)
    assert adapter.name == 'gemini' and adapter.api_key == 'AIza-env-key'


def test_env_key_is_the_fallback(client, ai_env, monkeypatch):
    monkeypatch.setenv('GENAI_API_KEY', 'AIza-env-key-1234')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-env-model')
    assert client.get('/api/settings/ai').json() == {
        'provider': 'gemini', 'model': 'gemini-env-model', 'key_last4': '1234', 'state': 'env'}
    adapter = client.portal.call(settings.resolve)
    assert (adapter.name, adapter.api_key, adapter.model) == ('gemini', 'AIza-env-key-1234', 'gemini-env-model')
    # A stored key takes over from the env key without a restart
    put(client, api_key=OPENAI_KEY)
    adapter = client.portal.call(settings.resolve)
    assert (adapter.name, adapter.api_key) == ('openai', OPENAI_KEY)


def test_adapter_is_reused_until_the_key_changes(client, ai_env):
    put(client, api_key=OPENAI_KEY)
    first = client.portal.call(settings.resolve)
    put(client, model='gpt-6.1-sol')
    second = client.portal.call(settings.resolve)
    assert second is first and second.model == 'gpt-6.1-sol'
    put(client, api_key='sk-another-key-9999')
    assert client.portal.call(settings.resolve) is not first


def test_delete_removes_the_key(client, run, ai_env):
    put(client, api_key=OPENAI_KEY)
    r = client.delete('/api/settings/ai')
    assert r.status_code == 200 and r.json()['state'] == 'none'
    assert run(db.get_ai_settings) is None
    with pytest.raises(NoAIKey):
        client.portal.call(settings.resolve)


# ── No key ───────────────────────────────────────────────────────────────────

def test_ai_endpoints_return_503_without_a_key(client, ai_env):
    car_id = client.post('/api/cars', json={'make': 'BMW', 'model': '330Ci'}).json()['id']
    tid = client.post(f'/api/cars/{car_id}/chats').json()['id']
    for method, path, body in [
        ('POST', f'/api/cars/{car_id}/research', {'query': 'brake fluid'}),
        ('POST', f'/api/chats/{tid}/messages', {'text': 'hello'}),
        ('POST', f'/api/cars/{car_id}/manuals/ask', {'question': 'q', 'manual_ids': [1]}),
        ('POST', '/api/pins/1/research', None),
    ]:
        r = client.request(method, path, json=body)
        assert r.status_code == 503, path
        assert r.json()['detail'] == 'No AI API key set'


def test_settings_need_a_session(client):
    from backend.app import app
    app.dependency_overrides.pop(auth.require_session)
    assert client.get('/api/settings/ai').status_code == 401
    assert client.put('/api/settings/ai', json={'api_key': OPENAI_KEY}).status_code == 401
    assert client.delete('/api/settings/ai').status_code == 401
