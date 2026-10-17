import json

from backend import db


def make_car(client):
    return client.post('/api/cars', json={'make': 'BMW', 'model': '330Ci'}).json()['id']


def test_build_sheet_starts_with_all_baseline_missing(client):
    car = make_car(client)
    sheet = client.get(f'/api/cars/{car}/build').json()
    assert sheet['missing_baseline'] == list(db.fitment.BASELINE_FIELDS)
    assert all(f['value'] is None for f in sheet['fields'])


def test_set_and_clear_field_keeps_history(client, run):
    car = make_car(client)
    r = client.put(f'/api/cars/{car}/build/engine_code', json={'value': 'M54B30'})
    assert r.status_code == 200
    row = next(f for f in r.json()['fields'] if f['field'] == 'engine_code')
    assert row['value'] == 'M54B30' and row['source'] == 'owner'
    client.put(f'/api/cars/{car}/build/engine_code', json={'value': 'M54B25'})
    client.delete(f'/api/cars/{car}/build/engine_code')
    rows = run(db._fetchall, 'SELECT value, superseded_at FROM build_fields WHERE car_id = ?', (car,))
    assert [r['value'] for r in rows] == ['M54B30', 'M54B25']
    assert all(r['superseded_at'] for r in rows)


def test_invalid_value_is_400(client):
    car = make_car(client)
    assert client.put(f'/api/cars/{car}/build/production_month', json={'value': '05/2004'}).status_code == 400
    assert client.put(f'/api/cars/{car}/build/bogus', json={'value': 'x'}).status_code == 400


def test_unknown_car_is_404(client):
    assert client.get('/api/cars/999/build').status_code == 404


def agent_part(run, car, **kw):
    cols = {'car_id': car, 'name': 'Brake disc', 'part_number': '34116855000', 'source': 'agent',
            'confidence': 'conditional', 'fitment_json': json.dumps({'production_month': '2004-05'}), **kw}
    keys = ', '.join(cols)
    return run(db._insert, f'INSERT INTO parts ({keys}) VALUES ({", ".join("?" for _ in cols)})',
               tuple(cols.values()))


def test_unverified_agent_part_cannot_leave_wishlist_without_override(client, run):
    car = make_car(client)
    pid = agent_part(run, car)
    r = client.patch(f'/api/parts/{pid}', json={'status': 'ordered'})
    assert r.status_code == 409
    assert client.get(f'/api/cars/{car}/parts').json()['items'][0]['status'] == 'wishlist'
    r = client.patch(f'/api/parts/{pid}', json={'status': 'ordered', 'override_fitment': True})
    assert r.status_code == 200
    assert r.json()['status'] == 'ordered' and r.json()['override_at']
    # Already out of the wishlist: later moves are not gated again
    assert client.patch(f'/api/parts/{pid}', json={'status': 'received'}).status_code == 200


def test_gate_applies_when_status_set_straight_to_installed(client, run):
    car = make_car(client)
    pid = agent_part(run, car)
    assert client.patch(f'/api/parts/{pid}', json={'status': 'installed'}).status_code == 409


def test_verified_agent_part_moves_freely(client, run):
    car = make_car(client)
    pid = agent_part(run, car, confidence='verified')
    assert client.patch(f'/api/parts/{pid}', json={'status': 'ordered'}).status_code == 200


def test_editing_part_number_drops_verification(client, run):
    car = make_car(client)
    pid = agent_part(run, car, confidence='verified')
    r = client.patch(f'/api/parts/{pid}', json={'part_number': '34116855001'})
    assert r.json()['confidence'] == 'unverified' and r.json()['fitment_json'] is None
    assert client.patch(f'/api/parts/{pid}', json={'status': 'ordered'}).status_code == 409


def test_reformatting_part_number_keeps_verification(client, run):
    car = make_car(client)
    pid = agent_part(run, car, confidence='verified')
    r = client.patch(f'/api/parts/{pid}', json={'part_number': '34 11 6 855 000'})
    assert r.json()['confidence'] == 'verified'


def test_owner_parts_are_never_gated(client):
    car = make_car(client)
    pid = client.post(f'/api/cars/{car}/parts', json={'name': 'Oil filter'}).json()['id']
    assert client.patch(f'/api/parts/{pid}', json={'status': 'ordered'}).status_code == 200


def test_owner_cannot_mark_part_as_agent_or_verified(client):
    car = make_car(client)
    part = client.post(f'/api/cars/{car}/parts',
                       json={'name': 'x', 'source': 'agent', 'confidence': 'verified'}).json()
    assert part['source'] == 'owner' and part['confidence'] is None


def test_build_change_flags_dependent_parts_for_recheck(client, run):
    car = make_car(client)
    client.put(f'/api/cars/{car}/build/production_month', json={'value': '2004-05'})
    dependent = agent_part(run, car, confidence='verified')
    other = agent_part(run, car, confidence='verified', fitment_json=json.dumps({'engine_code': 'M54B30'}))
    client.put(f'/api/cars/{car}/build/production_month', json={'value': '2004-06'})
    parts = {p['id']: p for p in client.get(f'/api/cars/{car}/parts').json()['items']}
    assert parts[dependent]['needs_recheck'] == 1 and parts[dependent]['confidence'] == 'conditional'
    assert parts[other]['needs_recheck'] == 0 and parts[other]['confidence'] == 'verified'


def test_saving_same_value_does_not_flag_recheck(client, run):
    car = make_car(client)
    client.put(f'/api/cars/{car}/build/production_month', json={'value': '2004-05'})
    pid = agent_part(run, car, confidence='verified')
    client.put(f'/api/cars/{car}/build/production_month', json={'value': '2004-05'})
    assert client.get(f'/api/cars/{car}/parts').json()['items'][0]['confidence'] == 'verified'
