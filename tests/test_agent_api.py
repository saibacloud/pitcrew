import pytest

from backend import db, fetch
from backend.agent_keys import hash_token
from backend.fetch import Page, FetchError, _check_target, html_to_text
from backend.routes import agent as agent_routes

ROW = '03 | Brake disc, ventilated | 34116855000 | 2 | E46 330Ci Coupe M54 from 03/2003'
PAGE_TEXT = 'Front brake\n02 | Brake pad set | 34116761244 | 1 | E46 all\n' + ROW
BUILD = {'chassis_code': 'E46', 'body_style': 'Coupe', 'model': '330Ci', 'engine_code': 'M54B30',
         'transmission': 'GS6-37BZ', 'drivetrain': 'RWD', 'production_month': '2004-05',
         'market': 'AUS', 'steering': 'RHD'}
CLAIM = {
    'part_number': '34116855000', 'claimed': 'verified', 'source_id': None, 'excerpt': ROW,
    'applies_when': [
        {'field': 'model', 'op': 'eq', 'value': '330Ci', 'quote': 'E46 330Ci Coupe'},
        {'field': 'production_month', 'op': 'range', 'min': '2003-03', 'quote': 'from 03/2003'},
    ],
}


@pytest.fixture
def agents(client, run, monkeypatch):
    tokens = {}
    for name in ('mech', 'verifier'):
        token = f'pc_test_{name}'
        run(db.create_agent_key, name, hash_token(token))
        tokens[name] = {'Authorization': f'Bearer {token}'}

    async def fake_fetch(url):
        return Page('https://parts.example.com/e46/brakes', 'parts.example.com', 'Front brake', PAGE_TEXT)
    monkeypatch.setattr(agent_routes, 'fetch_page', fake_fetch)
    monkeypatch.setattr(fetch, 'CATALOGUE_DOMAINS', {'example.com'})
    return tokens


@pytest.fixture
def car(client):
    car_id = client.post('/api/cars', json={'make': 'BMW', 'model': '330Ci'}).json()['id']
    for field, value in BUILD.items():
        assert client.put(f'/api/cars/{car_id}/build/{field}', json={'value': value}).status_code == 200
    return car_id


def add_source(client, agents, car):
    r = client.post(f'/api/agent/cars/{car}/sources', json={'url': 'https://parts.example.com/x'},
                    headers=agents['mech'])
    assert r.status_code == 201, r.text
    return r.json()['id']


def add_part(client, agents, car, source_id, **claim):
    body = {'name': 'Brake disc', 'category': 'Mechanical', 'brand': 'BMW', 'origin': 'oem',
            'claim': {**CLAIM, 'source_id': source_id, **claim}}
    return client.post(f'/api/agent/cars/{car}/parts', json=body, headers=agents['mech'])


def test_agent_routes_need_a_bearer_token(client):
    assert client.get('/api/agent/cars').status_code == 401
    assert client.get('/api/agent/cars', headers={'Authorization': 'Bearer pc_nope'}).status_code == 401


def test_revoked_key_is_rejected(client, run, agents):
    run(db.revoke_agent_key, 1)
    assert client.get('/api/agent/cars', headers=agents['mech']).status_code == 401


def test_llms_txt_is_public(client):
    r = client.get('/llms.txt')
    assert r.status_code == 200 and 'text/plain' in r.headers['content-type']


def test_full_claim_and_blind_verification_flow(client, agents, car):
    sid = add_source(client, agents, car)
    r = add_part(client, agents, car, sid)
    assert r.status_code == 201, r.text
    part, gate = r.json()['part'], r.json()['gate']
    assert part['source'] == 'agent' and part['status'] == 'wishlist'
    assert gate['level'] == 'conditional' and 'awaiting independent verification' in gate['reasons']

    # The claimant cannot verify its own claim
    r = verify(client, agents['mech'], part['id'], 'explicit_fit', 'E46 330Ci Coupe M54 from 03/2003')
    assert r.status_code == 409

    packet = client.get(f'/api/agent/parts/{part["id"]}/verification-packet', headers=agents['verifier']).json()
    assert packet['excerpt'] == ROW and 'applies_when' not in packet and 'claimed' not in packet
    r = verify(client, agents['verifier'], part['id'], 'explicit_fit', 'E46 330Ci Coupe M54 from 03/2003')
    assert r.json()['gate']['level'] == 'verified', r.json()
    r = client.patch(f'/api/agent/parts/{part["id"]}', json={'status': 'ordered'}, headers=agents['mech'])
    assert r.status_code == 200 and r.json()['status'] == 'ordered'


def test_build_change_makes_verification_stale(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003')
    client.put(f'/api/cars/{car}/build/market', json={'value': 'EU'})
    part = db_part(client, car, pid)
    assert part['confidence'] == 'conditional' and part['needs_recheck'] == 1
    # A fresh verification against the new build sheet restores it
    r = verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003')
    assert r.json()['gate']['level'] == 'verified'
    assert r.json()['part']['needs_recheck'] == 0


def verify(client, headers, pid, verdict, quote):
    packet = client.get(f'/api/agent/parts/{pid}/verification-packet', headers=headers).json()
    return client.post(f'/api/agent/parts/{pid}/verifications',
                       json={'claim_id': packet['claim_id'], 'verdict': verdict, 'quote': quote}, headers=headers)


def db_part(client, car, pid):
    return next(p for p in client.get(f'/api/cars/{car}/parts').json()['items'] if p['id'] == pid)


def test_part_that_does_not_fit_is_rejected_and_not_stored(client, agents, car):
    client.put(f'/api/cars/{car}/build/production_month', json={'value': '2002-01'})
    sid = add_source(client, agents, car)
    r = add_part(client, agents, car, sid)
    assert r.status_code == 422 and r.json()['detail']['gate']['no_fit'] == ['production_month']
    assert client.get(f'/api/cars/{car}/parts').json()['total'] == 0


def test_excerpt_must_come_from_the_source(client, agents, car):
    sid = add_source(client, agents, car)
    r = add_part(client, agents, car, sid, excerpt='34116855000 fits every E46')
    assert r.status_code == 400


def test_number_from_another_row_on_the_page_does_not_verify(client, agents, car):
    sid = add_source(client, agents, car)
    r = add_part(client, agents, car, sid, part_number='34116761244')
    assert r.json()['gate']['level'] == 'unverified'


def test_purchase_url_must_be_a_fetched_page_showing_the_number(client, agents, car):
    sid = add_source(client, agents, car)
    body = {'name': 'Brake disc', 'url': 'https://shop.example.com/disc',
            'claim': {**CLAIM, 'source_id': sid}}
    assert client.post(f'/api/agent/cars/{car}/parts', json=body, headers=agents['mech']).status_code == 400
    body['url'] = 'https://parts.example.com/e46/brakes'
    assert client.post(f'/api/agent/cars/{car}/parts', json=body, headers=agents['mech']).status_code == 201


def test_agent_cannot_order_unverified_part(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    r = client.patch(f'/api/agent/parts/{pid}', json={'status': 'ordered'}, headers=agents['mech'])
    assert r.status_code == 409


def test_new_claim_drops_old_verification(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003')
    r = client.post(f'/api/agent/parts/{pid}/claims', json={**CLAIM, 'source_id': sid}, headers=agents['mech'])
    assert r.json()['gate']['level'] == 'conditional'


def test_idempotent_retry_replays_and_rejects_reuse(client, agents, car):
    sid = add_source(client, agents, car)
    headers = {**agents['mech'], 'Idempotency-Key': 'part-1'}
    body = {'name': 'Brake disc', 'claim': {**CLAIM, 'source_id': sid}}
    first = client.post(f'/api/agent/cars/{car}/parts', json=body, headers=headers)
    again = client.post(f'/api/agent/cars/{car}/parts', json=body, headers=headers)
    assert first.json()['part']['id'] == again.json()['part']['id']
    assert client.get(f'/api/cars/{car}/parts').json()['total'] == 1
    body['name'] = 'Other'
    assert client.post(f'/api/agent/cars/{car}/parts', json=body, headers=headers).status_code == 422


def test_catalogue_build_value_needs_a_quote_from_the_source(client, agents, car):
    sid = add_source(client, agents, car)
    url = f'/api/agent/cars/{car}/build/engine_code'
    assert client.put(url, json={'value': 'M54B30', 'source': 'catalogue'}, headers=agents['mech']).status_code == 400
    bad = {'value': 'M54B30', 'source': 'catalogue', 'source_id': sid, 'quote': 'engine M54B30'}
    assert client.put(url, json=bad, headers=agents['mech']).status_code == 400
    assert client.put(url, json={**bad, 'quote': 'Coupe M54 from'}, headers=agents['mech']).status_code == 400
    ok = {'value': 'M54', 'source': 'catalogue', 'source_id': sid, 'quote': 'Coupe M54 from'}
    r = client.put(url, json=ok, headers=agents['mech'])
    assert r.status_code == 200
    row = next(f for f in r.json()['fields'] if f['field'] == 'engine_code')
    assert row['source'] == 'catalogue' and row['confirmed'] is False


def test_manual_page_source_takes_the_owner_set_tier(client, run, agents, car, monkeypatch):
    mid = run(db.create_manual, car, 'Parts catalogue', '/static/uploads/manuals/x.pdf')
    assert client.patch(f'/api/manuals/{mid}', json={'tier': 'oem'}).json()['tier'] == 'oem'

    async def pages(manual):
        return {147: ROW}
    monkeypatch.setattr(agent_routes, '_manual_pages', pages)
    r = client.post(f'/api/agent/cars/{car}/sources', json={'manual_id': mid, 'page': 147}, headers=agents['mech'])
    assert r.status_code == 201 and r.json()['tier'] == 'oem'
    r = client.post(f'/api/agent/cars/{car}/sources', json={'manual_id': mid, 'page': 9}, headers=agents['mech'])
    assert r.status_code == 422


def test_journal_entry_records_agent_and_sources(client, agents, car):
    sid = add_source(client, agents, car)
    r = client.post(f'/api/agent/cars/{car}/journal',
                    json={'type': 'service', 'title': 'Oil change', 'odometer': 182000, 'source_ids': [sid]},
                    headers=agents['mech'])
    assert r.status_code == 201 and r.json()['agent_key_id'] == 1 and r.json()['source_ids'] == f'[{sid}]'
    bad = {'type': 'note', 'title': 'x', 'source_ids': [999]}
    assert client.post(f'/api/agent/cars/{car}/journal', json=bad, headers=agents['mech']).status_code == 400


@pytest.mark.parametrize('url', [
    'http://example.com/', 'https://127.0.0.1/', 'https://localhost/', 'https://10.0.0.1/',
    'https://example.com:8443/', 'https://user:pw@example.com/', 'https://[::1]/',
])
def test_fetch_rejects_non_public_targets(url, run):
    with pytest.raises(FetchError):
        run(_check_target, url)


def test_html_to_text_keeps_rows_and_drops_scripts():
    title, text = html_to_text('<html><head><meta charset="utf-8"><title>Brakes</title></head><script>evil()</script>'
                               '<table><tr><td>03</td><td>34116855000</td></tr></table>')
    assert title == 'Brakes' and 'evil' not in text and '03 | 34116855000' in text


def test_agent_build_values_wait_for_owner_confirmation(client, agents, car):
    url = f'/api/agent/cars/{car}/build/steering'
    sheet = client.put(url, json={'value': 'LHD', 'source': 'owner'}, headers=agents['mech']).json()
    assert 'steering' in sheet['pending'] and 'steering' in sheet['missing_baseline']
    sheet = client.post(f'/api/cars/{car}/build/steering/confirm').json()
    assert sheet['pending'] == [] and 'steering' not in sheet['missing_baseline']


def test_unconfirmed_agent_value_does_not_satisfy_the_gate(client, agents, car):
    client.delete(f'/api/cars/{car}/build/model')
    client.put(f'/api/agent/cars/{car}/build/model', json={'value': '330Ci', 'source': 'owner'}, headers=agents['mech'])
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    r = verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003')
    assert r.json()['gate']['level'] == 'conditional' and 'model' in r.json()['gate']['open_fields']


@pytest.mark.parametrize('excerpt', [
    '34116761244 | 1 | E46 all\n03 | Brake disc',          # splices two rows
    '03 | Brake disc, ventilated | 34116855000 | 2 | E46 330Ci Coupe',  # trims the row
])
def test_excerpt_must_be_one_whole_row(client, agents, car, excerpt):
    sid = add_source(client, agents, car)
    assert add_part(client, agents, car, sid, excerpt=excerpt).status_code == 400


def test_quotes_must_state_the_predicate_value(client, agents, car):
    sid = add_source(client, agents, car)
    preds = [{'field': 'model', 'op': 'eq', 'value': '330Ci', 'quote': 'Brake disc'}]
    r = add_part(client, agents, car, sid, applies_when=preds)
    assert r.json()['gate']['level'] == 'unverified'
    preds = [{'field': 'model', 'op': 'eq', 'value': '330Ci', 'quote': 'e'}]
    assert add_part(client, agents, car, sid, applies_when=preds).json()['gate']['level'] == 'unverified'


def test_verification_is_bound_to_the_claim_read(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    packet = client.get(f'/api/agent/parts/{pid}/verification-packet', headers=agents['verifier']).json()
    assert packet['context'] == {'before': ['Front brake', '02 | Brake pad set | 34116761244 | 1 | E46 all'],
                                 'after': []}
    client.post(f'/api/agent/parts/{pid}/claims', json={**CLAIM, 'source_id': sid}, headers=agents['mech'])
    r = client.post(f'/api/agent/parts/{pid}/verifications',
                    json={'claim_id': packet['claim_id'], 'verdict': 'explicit_fit', 'quote': 'from 03/2003'},
                    headers=agents['verifier'])
    assert r.status_code == 409


def test_one_verdict_per_key_and_doubt_wins(client, run, agents, car):
    run(db.create_agent_key, 'third', hash_token('pc_test_third'))
    third = {'Authorization': 'Bearer pc_test_third'}
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    assert verify(client, agents['verifier'], pid, 'no_fit', 'from 03/2003').json()['gate']['level'] == 'unverified'
    assert verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003').status_code == 409
    assert verify(client, third, pid, 'explicit_fit', 'from 03/2003').json()['gate']['level'] == 'unverified'


def test_owner_number_edit_retires_the_claim(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    client.patch(f'/api/parts/{pid}', json={'part_number': '34116855999'})
    r = client.get(f'/api/agent/parts/{pid}/verification-packet', headers=agents['verifier'])
    assert r.status_code == 404
    assert db_part(client, car, pid)['confidence'] == 'unverified'


def test_only_the_creating_key_can_replace_a_claim(client, agents, car):
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid).json()['part']['id']
    r = client.post(f'/api/agent/parts/{pid}/claims', json={**CLAIM, 'source_id': sid}, headers=agents['verifier'])
    assert r.status_code == 409


def test_page_echoing_its_url_cannot_verify(client, agents, car, monkeypatch):
    row = '34116855000 E46 330Ci Coupe from 03/2003'

    async def search_page(url):
        return Page('https://parts.example.com/search?q=34116855000%20E46%20330Ci%20Coupe%20from%2003/2003',
                    'parts.example.com', 'Search', 'Results for\n' + row)
    monkeypatch.setattr(agent_routes, 'fetch_page', search_page)
    sid = add_source(client, agents, car)
    pid = add_part(client, agents, car, sid, excerpt=row).json()['part']['id']
    r = verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003')
    assert r.json()['gate']['level'] == 'unverified'


def test_lowering_a_manual_tier_regates_its_parts(client, run, agents, car, monkeypatch):
    mid = run(db.create_manual, car, 'Parts catalogue', '/static/uploads/manuals/x.pdf')
    client.patch(f'/api/manuals/{mid}', json={'tier': 'oem'})

    async def pages(manual):
        return {147: ROW}
    monkeypatch.setattr(agent_routes, '_manual_pages', pages)
    sid = client.post(f'/api/agent/cars/{car}/sources', json={'manual_id': mid, 'page': 147},
                      headers=agents['mech']).json()['id']
    pid = add_part(client, agents, car, sid).json()['part']['id']
    assert verify(client, agents['verifier'], pid, 'explicit_fit', 'from 03/2003').json()['gate']['level'] == 'verified'
    client.patch(f'/api/manuals/{mid}', json={'tier': 'other'})
    assert db_part(client, car, pid)['confidence'] == 'unverified'


def test_hidden_html_text_is_dropped():
    _, text = html_to_text('<div>Brake disc</div><div style="display: none">34116855000 E46 330Ci</div>'
                           '<span hidden>fits all</span><p aria-hidden="true">x <b>y</b></p><p>Visible<br>row</p>')
    assert '34116855000' not in text and 'fits all' not in text and 'y' not in text.split()
    assert 'Brake disc' in text and 'Visible' in text and 'row' in text


def test_connected_address_check_reads_the_real_peer(run):
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    import httpx
    from backend.fetch import _connected_public

    class Ok(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')

        def log_message(self, *a):
            pass

    server = HTTPServer(('127.0.0.1', 0), Ok)
    threading.Thread(target=server.handle_request, daemon=True).start()

    async def peer_ok():
        async with httpx.AsyncClient(trust_env=False) as c:
            async with c.stream('GET', f'http://127.0.0.1:{server.server_port}/') as resp:
                return _connected_public(resp), resp.extensions['network_stream'].get_extra_info('server_addr')
    public, addr = run(peer_ok)
    server.server_close()
    assert addr[0] == '127.0.0.1' and public is False
