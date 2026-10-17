import pytest

from backend.services import gemini

ANSWER_1 = 'You will need a 14 mm socket, a drain pan and brake and parts cleaner to degrease the housing.'


@pytest.fixture
def ai(monkeypatch):
    """Scripted Gemini: queue answers and extractions; record what each call received."""
    state = {'answers': [], 'extractions': [], 'chat_calls': [], 'extract_calls': []}

    async def chat(contract, history, message):
        state['chat_calls'].append({'contract': contract, 'history': list(history), 'message': message})
        result = state['answers'].pop(0)
        if isinstance(result, Exception):
            raise result
        return gemini.Answer(result, '<div>suggestions</div>')

    async def extract(previous_answer, message, inventory):
        state['extract_calls'].append({'previous': previous_answer, 'message': message})
        result = state['extractions'].pop(0) if state['extractions'] else []
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(gemini, 'chat', chat)
    monkeypatch.setattr(gemini, 'extract_inventory', extract)
    return state


@pytest.fixture
def car(client):
    car_id = client.post('/api/cars', json={'year': 2004, 'make': 'BMW', 'model': '330Ci', 'color': 'Titan Silver',
                                            'vin': 'WBABD52040JX12345'}).json()['id']
    client.put(f'/api/cars/{car_id}/build/transmission', json={'value': 'GS6-37BZ'})
    return car_id


def thread(client, car):
    return client.post(f'/api/cars/{car}/chats').json()['id']


def send(client, tid, text):
    return client.post(f'/api/chats/{tid}/messages', json={'text': text})


def change(**kw):
    return {'action': 'add', 'kind': 'tool', 'detail': None, 'basis': 'stated', 'step_quote': None, **kw}


def test_every_answer_gets_the_car_contract(client, ai, car):
    client.post('/api/inventory', json={'name': 'torque wrench', 'kind': 'tool'})
    ai['answers'] = ['Use the specified ATF.']
    tid = thread(client, car)
    assert send(client, tid, 'what oil do I need for my transmission').status_code == 201
    contract = ai['chat_calls'][0]['contract']
    for expected in ('WBABD52040JX12345', 'Titan Silver', '330Ci', 'transmission: GS6-37BZ', 'torque wrench'):
        assert expected in contract
    # The fixed rules ride along in the system instruction
    assert 'MUST follow these rules' in gemini.CHAT_SYSTEM_INSTRUCTION


def test_unconfirmed_build_values_are_marked_in_the_contract(client, run, ai, car):
    from backend import db
    run(db.set_build_field, car, 'steering', 'RHD')
    run(db._execute, "UPDATE build_fields SET confirmed_at = NULL WHERE field = 'steering'")
    ai['answers'] = ['ok']
    send(client, thread(client, car), 'hi')
    contract = ai['chat_calls'][0]['contract']
    assert 'unconfirmed, do not rely on' in contract and 'steering: RHD' in contract.split('unconfirmed')[1]


def test_completing_a_step_infers_its_tool(client, ai, car):
    tid = thread(client, car)
    ai['answers'] = [ANSWER_1, 'Next, refill the coolant.']
    send(client, tid, 'how do I change the thermostat')
    ai['extractions'] = [[change(name='socket', detail='14 mm', basis='inferred', quote="I've done that part",
                                 step_quote='14 mm socket')]]
    r = send(client, tid, "okay I've done that part, what next")
    assert ai['extract_calls'][1]['previous'] == ANSWER_1
    added = r.json()['user']['inventory_changes']
    assert [(c['name'], c['detail'], c['basis']) for c in added] == [('socket', '14 mm', 'inferred')]
    assert client.get('/api/inventory').json()['items'][0]['basis'] == 'inferred'
    # The second answer saw the first exchange as history
    assert [m['role'] for m in ai['chat_calls'][1]['history']] == ['user', 'model']


@pytest.mark.parametrize('bad', [
    change(name='socket', quote='I have a socket set'),                                   # quote not in message
    change(name='torque wrench', quote='done that part'),                                 # quote does not name it
    change(name='socket', basis='inferred', quote='done that part', step_quote='19 mm socket'),  # not in answer
    change(name='jack', basis='inferred', quote='done that part', step_quote='14 mm socket'),    # item not in step
    change(name='socket', kind='weapon', quote='done that part'),
])
def test_unsupported_extractions_are_dropped(client, ai, car, bad):
    tid = thread(client, car)
    ai['answers'] = [ANSWER_1, 'ok']
    send(client, tid, 'how do I change the thermostat')
    ai['extractions'] = [[bad]]
    r = send(client, tid, 'done that part')
    assert r.json()['user']['inventory_changes'] == []
    assert client.get('/api/inventory').json()['items'] == []


def test_stated_items_and_removals(client, ai, car):
    tid = thread(client, car)
    ai['answers'] = ['ok', 'ok']
    ai['extractions'] = [[change(name='degreaser', kind='consumable', quote='bought some degreaser')]]
    send(client, tid, 'I bought some degreaser today')
    ai['extractions'] = [[change(action='remove', name='degreaser', kind='consumable', quote='ran out of degreaser')]]
    r = send(client, tid, 'ran out of degreaser')
    assert r.json()['user']['inventory_changes'][0]['action'] == 'remove'
    assert client.get('/api/inventory').json()['items'] == []
    chat = client.get(f'/api/chats/{tid}').json()
    first, second = chat['messages'][0], chat['messages'][2]
    assert first['inventory_changes'][0]['action'] == 'add' and first['inventory_changes'][0]['held'] is False
    assert second['inventory_changes'][0]['action'] == 'remove'


def test_duplicate_add_is_skipped(client, ai, car):
    client.post('/api/inventory', json={'name': 'Socket', 'kind': 'tool', 'detail': '14 MM'})
    ai['answers'] = ['ok']
    ai['extractions'] = [[change(name='socket', detail='14 mm', quote='have a 14 mm socket')]]
    r = send(client, thread(client, car), 'I have a 14 mm socket')
    assert r.json()['user']['inventory_changes'] == []
    assert len(client.get('/api/inventory').json()['items']) == 1


def test_failed_answer_stores_nothing(client, ai, car):
    tid = thread(client, car)
    ai['answers'] = [RuntimeError('gemini down')]
    assert send(client, tid, 'hello').status_code == 502
    assert client.get(f'/api/chats/{tid}').json()['messages'] == []


def test_failed_extraction_still_logs_the_answer(client, ai, car):
    tid = thread(client, car)
    ai['answers'] = ['answer']
    ai['extractions'] = [RuntimeError('bad json')]
    assert send(client, tid, 'hello').status_code == 201
    chat = client.get(f'/api/chats/{tid}').json()
    assert [m['role'] for m in chat['messages']] == ['user', 'model']
    assert chat['thread']['title'] == 'hello'


def test_inventory_owner_crud_and_restore(client):
    item = client.post('/api/inventory', json={'name': 'jack stands', 'kind': 'tool', 'detail': '3 t'}).json()
    assert item['basis'] == 'owner'
    assert client.patch(f'/api/inventory/{item["id"]}', json={'detail': '2 t'}).json()['detail'] == '2 t'
    assert client.post('/api/inventory', json={'name': 'x', 'kind': 'weapon'}).status_code == 400
    assert client.delete(f'/api/inventory/{item["id"]}').status_code == 204
    assert client.post(f'/api/inventory/{item["id"]}/restore').json()['name'] == 'jack stands'


def test_chat_threads_list_and_soft_delete(client, ai, car):
    tid = thread(client, car)
    assert client.get(f'/api/cars/{car}/chats').json()['items'][0]['id'] == tid
    assert client.delete(f'/api/chats/{tid}').status_code == 204
    assert client.get(f'/api/cars/{car}/chats').json()['items'] == []
    assert client.get(f'/api/chats/{tid}').status_code == 404
