import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from google.genai import errors, types
from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_output_text import AnnotationURLCitation

from backend.services.ai import INVENTORY_SCHEMA
from backend.services.ai.base import EmptyResponse, Image, Message, PdfBytes, PdfFile, ValidationFailed
from backend.services.ai.gemini_adapter import GeminiAdapter
from backend.services.ai.openai_adapter import OpenAIAdapter

HISTORY = [Message('user', ['how do I bleed the brakes']), Message('model', ['Start at the rear right.']),
           Message('user', ['done, what next'])]


def run(coro):
    return asyncio.run(coro)


# ── Gemini fakes ─────────────────────────────────────────────────────────────

def gemini_response(text, sources=(), suggestions=None):
    meta = types.GroundingMetadata(
        grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri=u, title=t)) for t, u in sources],
        search_entry_point=types.SearchEntryPoint(rendered_content=suggestions) if suggestions else None,
    ) if sources or suggestions else None
    parts = [types.Part(text=text)] if text else []
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=parts), grounding_metadata=meta,
        finish_reason=types.FinishReason.STOP if text else types.FinishReason.SAFETY)])


class FakeGemini:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls, self.uploads = [], []

        async def generate_content(model, contents, config):
            self.calls.append({'model': model, 'contents': contents, 'config': config})
            return self.responses.pop(0)

        async def upload(file, config):
            self.uploads.append(file)
            return types.File(name=f'files/{len(self.uploads)}', uri=f'https://g/files/{len(self.uploads)}',
                              state=types.FileState.ACTIVE)

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                                   files=SimpleNamespace(upload=upload))


def gemini(*responses):
    fake = FakeGemini(*responses)
    return GeminiAdapter('AIza-test', 'gemini-test', client=fake), fake


# ── OpenAI fakes ─────────────────────────────────────────────────────────────

def openai_response(text, citations=()):
    content = [ResponseOutputText.model_construct(
        type='output_text', text=text,
        annotations=[AnnotationURLCitation(type='url_citation', url=u, title=t, start_index=0, end_index=1)
                     for t, u in citations])] if text else []
    return Response.model_construct(
        output=[ResponseOutputMessage.model_construct(type='message', role='assistant', content=content)],
        status='completed' if text else 'incomplete', incomplete_details=None)


class FakeOpenAI:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls, self.uploads = [], []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return self.queue.pop(0)

        async def upload(file, purpose, expires_after):
            self.uploads.append({'file': file, 'purpose': purpose, 'expires_after': expires_after})
            return SimpleNamespace(id=f'file-{len(self.uploads)}')

        self.responses = SimpleNamespace(create=create)
        self.files = SimpleNamespace(create=upload)


def openai(*responses):
    fake = FakeOpenAI(*responses)
    return OpenAIAdapter('sk-test', 'gpt-test', client=fake), fake


# ── History role mapping ─────────────────────────────────────────────────────

def test_gemini_keeps_user_and_model_roles():
    adapter, fake = gemini(gemini_response('Next, the front left.'))
    run(adapter.generate('sys', HISTORY, search=True, label='chat'))
    contents = fake.calls[0]['contents']
    assert [c.role for c in contents] == ['user', 'model', 'user']
    assert contents[1].parts[0].text == 'Start at the rear right.'
    assert fake.calls[0]['config'].system_instruction == 'sys'


def test_openai_maps_model_role_to_assistant():
    adapter, fake = openai(openai_response('Next, the front left.'))
    run(adapter.generate('sys', HISTORY, search=True, label='chat'))
    call = fake.calls[0]
    assert [m['role'] for m in call['input']] == ['user', 'assistant', 'user']
    assert call['input'][1]['content'] == 'Start at the rear right.'
    assert call['instructions'] == 'sys' and call['model'] == 'gpt-test'


# ── Web search sources ───────────────────────────────────────────────────────

def test_gemini_grounding_becomes_sources_markdown_and_suggestions():
    adapter, fake = gemini(gemini_response(
        'Use DOT 4.', sources=[('BMW TIS', 'https://a.example/tis'), ('Forum', 'https://b.example/f'),
                               ('Dup', 'https://a.example/tis')],
        suggestions='<div>chips</div>'))
    answer = run(adapter.generate('sys', [Message('user', ['fluid?'])], search=True, label='research'))
    assert answer.text == ('Use DOT 4.\n\n**Sources**\n- [BMW TIS](https://a.example/tis)\n'
                           '- [Forum](https://b.example/f)')
    assert answer.search_suggestions == '<div>chips</div>'
    assert fake.calls[0]['config'].tools[0].google_search is not None


def test_gemini_without_search_sends_no_tools():
    adapter, fake = gemini(gemini_response('From the manual.'))
    answer = run(adapter.generate('sys', [Message('user', ['q'])], search=False, label='doc search'))
    assert answer.text == 'From the manual.' and answer.search_suggestions is None
    assert not fake.calls[0]['config'].tools


def test_openai_url_citations_become_sources_markdown():
    adapter, fake = openai(openai_response(
        'Use DOT 4.', citations=[('BMW TIS', 'https://a.example/tis'), ('Again', 'https://a.example/tis'),
                                 ('Forum', 'https://b.example/f')]))
    answer = run(adapter.generate('sys', [Message('user', ['fluid?'])], search=True, label='research'))
    assert answer.text == ('Use DOT 4.\n\n**Sources**\n- [BMW TIS](https://a.example/tis)\n'
                           '- [Forum](https://b.example/f)')
    assert answer.search_suggestions is None
    assert fake.calls[0]['tools'] == [{'type': 'web_search'}]


def test_openai_without_search_sends_no_tools():
    adapter, fake = openai(openai_response('From the manual.'))
    run(adapter.generate('sys', [Message('user', ['q'])], search=False, label='doc search'))
    assert 'tools' not in fake.calls[0]


# ── Images ───────────────────────────────────────────────────────────────────

def test_gemini_image_input():
    adapter, fake = gemini(gemini_response('A brake caliper.'))
    run(adapter.generate('sys', [Message('user', [Image(b'jpegbytes'), 'what is this'])], search=True, label='img'))
    image, text = fake.calls[0]['contents'][0].parts
    assert image.inline_data.data == b'jpegbytes' and image.inline_data.mime_type == 'image/jpeg'
    assert image.media_resolution.level == types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH
    assert text.text == 'what is this'


def test_openai_image_input():
    adapter, fake = openai(openai_response('A brake caliper.'))
    run(adapter.generate('sys', [Message('user', [Image(b'pngbytes', 'image/png'), 'what is this'])],
                         search=True, label='img'))
    image, text = fake.calls[0]['input'][0]['content']
    assert image == {'type': 'input_image', 'detail': 'high',
                     'image_url': 'data:image/png;base64,' + base64.b64encode(b'pngbytes').decode()}
    assert text == {'type': 'input_text', 'text': 'what is this'}


# ── PDFs ─────────────────────────────────────────────────────────────────────

def test_gemini_inline_pdf():
    adapter, fake = gemini(gemini_response('p. 4'))
    run(adapter.generate('sys', [Message('user', [PdfBytes(b'%PDF-small'), 'q'])], search=False, label='docs'))
    part = fake.calls[0]['contents'][0].parts[0]
    assert part.inline_data.data == b'%PDF-small' and part.inline_data.mime_type == 'application/pdf'
    assert fake.uploads == []


def test_gemini_file_upload_pdf_is_cached(tmp_path):
    pdf = tmp_path / 'manual.pdf'
    pdf.write_bytes(b'%PDF-whole')
    adapter, fake = gemini(gemini_response('p. 1'), gemini_response('p. 2'))
    for _ in range(2):
        run(adapter.generate('sys', [Message('user', [PdfFile(str(pdf), 'k1'), 'q'])], search=False, label='docs'))
    assert fake.uploads == [str(pdf)]
    part = fake.calls[1]['contents'][0].parts[0]
    assert part.file_data.file_uri == 'https://g/files/1' and part.file_data.mime_type == 'application/pdf'


def test_gemini_large_pdf_bytes_upload(monkeypatch):
    from backend.services.ai import gemini_adapter
    monkeypatch.setattr(gemini_adapter, '_INLINE_LIMIT', 4)
    adapter, fake = gemini(gemini_response('p. 9'))
    run(adapter.generate('sys', [Message('user', [PdfBytes(b'%PDF-large'), 'q'])], search=False, label='docs'))
    assert len(fake.uploads) == 1
    assert fake.calls[0]['contents'][0].parts[0].file_data.file_uri == 'https://g/files/1'


def test_openai_inline_pdf():
    adapter, fake = openai(openai_response('p. 4'))
    run(adapter.generate('sys', [Message('user', [PdfBytes(b'%PDF-small'), 'q'])], search=False, label='docs'))
    part = fake.calls[0]['input'][0]['content'][0]
    assert part == {'type': 'input_file', 'filename': 'document.pdf',
                    'file_data': 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-small').decode()}
    assert fake.uploads == []


def test_openai_file_upload_pdf_is_cached(tmp_path):
    pdf = tmp_path / 'manual.pdf'
    pdf.write_bytes(b'%PDF-whole')
    adapter, fake = openai(openai_response('p. 1'), openai_response('p. 2'))
    for _ in range(2):
        run(adapter.generate('sys', [Message('user', [PdfFile(str(pdf), 'k1'), 'q'])], search=False, label='docs'))
    assert len(fake.uploads) == 1
    upload = fake.uploads[0]
    assert upload['file'] == ('manual.pdf', b'%PDF-whole') and upload['purpose'] == 'user_data'
    assert upload['expires_after']['seconds'] > 47 * 3600
    assert fake.calls[1]['input'][0]['content'][0] == {'type': 'input_file', 'file_id': 'file-1'}


def test_openai_large_pdf_bytes_upload(monkeypatch):
    from backend.services.ai import openai_adapter
    monkeypatch.setattr(openai_adapter, '_INLINE_LIMIT', 4)
    adapter, fake = openai(openai_response('p. 9'))
    run(adapter.generate('sys', [Message('user', [PdfBytes(b'%PDF-large'), 'q'])], search=False, label='docs'))
    assert fake.uploads[0]['file'] == ('document.pdf', b'%PDF-large')
    assert fake.calls[0]['input'][0]['content'][0] == {'type': 'input_file', 'file_id': 'file-1'}


# ── JSON extraction ──────────────────────────────────────────────────────────

CHANGES = {'changes': [{'action': 'add', 'name': 'socket', 'kind': 'tool', 'detail': '14 mm', 'basis': 'stated',
                        'quote': 'bought a 14 mm socket', 'step_quote': None}]}


def test_gemini_json_extraction():
    adapter, fake = gemini(gemini_response(json.dumps(CHANGES)))
    assert run(adapter.extract_json('sys', 'prompt', INVENTORY_SCHEMA, 'inventory extraction')) == CHANGES
    config = fake.calls[0]['config']
    assert config.response_mime_type == 'application/json' and config.response_json_schema == INVENTORY_SCHEMA


def test_openai_strict_json_extraction():
    adapter, fake = openai(openai_response(json.dumps(CHANGES)))
    assert run(adapter.extract_json('sys', 'prompt', INVENTORY_SCHEMA, 'inventory extraction')) == CHANGES
    fmt = fake.calls[0]['text']['format']
    assert fmt == {'type': 'json_schema', 'name': 'inventory_extraction', 'schema': INVENTORY_SCHEMA, 'strict': True}


def test_inventory_schema_is_strict_mode_compatible():
    def objects(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                yield node
            for v in node.values():
                yield from objects(v)
    for obj in objects(INVENTORY_SCHEMA):
        assert obj['additionalProperties'] is False
        assert set(obj['required']) == set(obj['properties'])


# ── Empty responses ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('make', [gemini, openai])
def test_empty_answer_raises(make):
    empty = gemini_response('') if make is gemini else openai_response('')
    adapter, _ = make(empty)
    with pytest.raises(EmptyResponse):
        run(adapter.generate('sys', [Message('user', ['q'])], search=True, label='chat'))


@pytest.mark.parametrize('make', [gemini, openai])
def test_empty_extraction_raises(make):
    empty = gemini_response('') if make is gemini else openai_response('')
    adapter, _ = make(empty)
    with pytest.raises(EmptyResponse):
        run(adapter.extract_json('sys', 'prompt', INVENTORY_SCHEMA, 'inventory extraction'))


# ── Gemini key check ─────────────────────────────────────────────────────────

def gemini_checker(error=None):
    calls = []

    async def count_tokens(model, contents):
        calls.append({'model': model, 'contents': contents})
        if error:
            raise error
        return types.CountTokensResponse(total_tokens=1)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(count_tokens=count_tokens)))
    return GeminiAdapter('AIza-test', 'gemini-flash-latest', client=client), calls


def test_gemini_key_check_counts_tokens_on_the_alias():
    adapter, calls = gemini_checker()
    run(adapter.validate())
    assert calls == [{'model': 'gemini-flash-latest', 'contents': 'ping'}]


@pytest.mark.parametrize('error, reason', [
    (errors.ClientError(404, {'error': {'code': 404, 'message': 'x', 'status': 'NOT_FOUND'}}),
     'Model "gemini-flash-latest" not found'),
    (errors.ClientError(400, {'error': {'code': 400, 'message': 'API key not valid', 'status': 'INVALID_ARGUMENT'}}),
     'Google rejected this key'),
    (errors.ClientError(403, {'error': {'code': 403, 'message': 'x', 'status': 'PERMISSION_DENIED'}}),
     'Google rejected this key'),
    (errors.ClientError(429, {'error': {'code': 429, 'message': 'x', 'status': 'RESOURCE_EXHAUSTED'}}),
     'Google rate limit or quota reached'),
    (errors.ServerError(503, {'error': {'code': 503, 'message': 'x', 'status': 'UNAVAILABLE'}}),
     'Google could not check this key'),
    (OSError('connection refused'), 'Could not reach Google'),
])
def test_gemini_key_check_error_mapping(error, reason):
    adapter, _ = gemini_checker(error)
    with pytest.raises(ValidationFailed) as exc:
        run(adapter.validate())
    assert str(exc.value) == reason
