import pytest

from backend import fitment
from backend.fitment import gate, part_number_in_text

FULL_BUILD = {
    'chassis_code': 'E46', 'body_style': 'Coupe', 'model': '330Ci', 'engine_code': 'M54B30',
    'transmission': 'GS6-37BZ', 'drivetrain': 'RWD', 'production_month': '2004-05',
    'market': 'AUS', 'steering': 'RHD',
    'option_codes': [{'code': 'S337', 'description': 'M Sport package'}],
}

EXCERPT = ('02 Disc brake front 11427512300 x - not used\n'
           '03 Brake disc, ventilated 34116855000 2 E46 330Ci Coupe M54 from 03/2003')

PREDICATES = [
    {'field': 'model', 'op': 'eq', 'value': '330Ci', 'quote': 'E46 330Ci Coupe'},
    {'field': 'production_month', 'op': 'range', 'min': '2003-03', 'quote': 'from 03/2003'},
]


def claim(**kw):
    base = {'part_number': '34116855000', 'claimed': 'verified', 'agent_id': 'mech-1',
            'depends_on': [], 'applies_when': PREDICATES}
    return {**base, **kw}


def evidence(**kw):
    return {'kind': 'web_page', 'tier': 'catalogue', 'excerpt': EXCERPT, 'extracted_by': 'text', **kw}


VERIFY = {'verdict': 'explicit_fit', 'source_ok': True, 'quote': 'E46 330Ci Coupe M54 from 03/2003',
          'agent_id': 'verifier-1'}


def test_all_conditions_met_is_verified():
    r = gate(claim(), FULL_BUILD, evidence(), VERIFY)
    assert r.level == 'verified', r.reasons


@pytest.mark.parametrize('pn,text,expected', [
    ('11 42 7 512 300', 'part 11427512300 qty 1', True),
    ('11427512300', 'part 11 42 7 512 300 1', True),
    ('7512300', '11 42 7 512 300', False),          # short number inside a spaced long one
    ('1142751230', '11427512300', False),            # prefix of a longer number
    ('11427512300', 'Part No 11427512300', True),
    ('123', 'item 123', False),                      # too short to match safely
])
def test_part_number_matching(pn, text, expected):
    assert part_number_in_text(pn, text) is expected


def test_number_absent_from_excerpt_is_unverified():
    assert gate(claim(part_number='34116855001'), FULL_BUILD, evidence(), VERIFY).level == 'unverified'


def test_non_catalogue_tier_is_unverified():
    assert gate(claim(), FULL_BUILD, evidence(tier='forum'), VERIFY).level == 'unverified'


def test_ocr_caps_conditional():
    assert gate(claim(), FULL_BUILD, evidence(extracted_by='ocr'), VERIFY).level == 'conditional'


def test_superseded_marker_caps_conditional():
    ev = evidence(excerpt=EXCERPT + ' superseded by 34116864906')
    assert gate(claim(), FULL_BUILD, ev, VERIFY).level == 'conditional'


def test_empty_depends_on_still_requires_every_baseline_field():
    build = {k: v for k, v in FULL_BUILD.items() if k != 'steering'}
    r = gate(claim(depends_on=[]), build, evidence(), VERIFY)
    assert r.level == 'conditional'
    assert 'steering' in r.open_fields


def test_predicate_quote_must_be_in_excerpt():
    preds = [{'field': 'model', 'op': 'eq', 'value': '330Ci', 'quote': 'fits all E46'}]
    assert gate(claim(applies_when=preds), FULL_BUILD, evidence(), VERIFY).level == 'unverified'


def test_false_predicate_is_no_fit():
    build = {**FULL_BUILD, 'production_month': '2002-01'}
    r = gate(claim(), build, evidence(), VERIFY)
    assert r.level == 'unverified'
    assert r.no_fit == ['production_month']


def test_no_applicability_caps_conditional():
    assert gate(claim(applies_when=[]), FULL_BUILD, evidence(), VERIFY).level == 'conditional'


def test_lacks_option_needs_complete_option_list():
    ev = evidence(excerpt=EXCERPT + ' except S704A')
    preds = PREDICATES + [{'field': 'option_codes', 'op': 'lacks_option', 'value': 'S704A', 'quote': 'except S704A'}]
    r = gate(claim(applies_when=preds), FULL_BUILD, ev, {**VERIFY})
    assert r.level == 'conditional'
    assert 'option_codes_complete' in r.open_fields
    r = gate(claim(applies_when=preds), {**FULL_BUILD, 'option_codes_complete': True}, ev, VERIFY)
    assert r.level == 'verified', r.reasons


def test_has_absent_option_is_no_fit():
    ev = evidence(excerpt=EXCERPT + ' with S704A')
    preds = PREDICATES + [{'field': 'option_codes', 'op': 'has_option', 'value': 'S704A', 'quote': 'with S704A'}]
    assert gate(claim(applies_when=preds), FULL_BUILD, ev, VERIFY).no_fit == ['option_codes']


@pytest.mark.parametrize('verification,expected', [
    (None, 'conditional'),
    ({**VERIFY, 'agent_id': 'mech-1'}, 'conditional'),     # same agent cannot verify itself
    ({**VERIFY, 'verdict': 'ambiguous'}, 'conditional'),
    ({**VERIFY, 'verdict': 'no_fit'}, 'unverified'),
    ({**VERIFY, 'quote': 'made up text'}, 'conditional'),
    ({**VERIFY, 'source_ok': False}, 'unverified'),          # verifier rejects the source itself
])
def test_verification_rules(verification, expected):
    assert gate(claim(), FULL_BUILD, evidence(), verification).level == expected


def test_claim_lower_than_ceiling_wins():
    assert gate(claim(claimed='conditional'), FULL_BUILD, evidence(), VERIFY).level == 'conditional'


def test_unknown_claim_level_is_unverified():
    assert gate(claim(claimed='certain'), FULL_BUILD, evidence(), VERIFY).level == 'unverified'


def test_fields_used_includes_baseline_and_predicate_fields():
    used = fitment.fields_used(claim(depends_on=['paint_code']))
    assert set(fitment.BASELINE_FIELDS) <= set(used)
    assert 'paint_code' in used


@pytest.mark.parametrize('field,value', [
    ('production_month', '2004-13'),
    ('steering', 'middle'),
    ('option_codes', [{'description': 'no code'}]),
    ('option_codes_complete', 'yes'),
    ('nonsense', 'x'),
    ('model', '   '),
])
def test_invalid_build_values_rejected(field, value):
    with pytest.raises(ValueError):
        fitment.encode_build_value(field, value)


def test_build_value_round_trip():
    stored = fitment.encode_build_value('option_codes', [{'code': 's337', 'description': 'M Sport'}])
    assert fitment.decode_build_value('option_codes', stored) == [{'code': 'S337', 'description': 'M Sport'}]
    assert fitment.encode_build_value('steering', 'rhd') == 'RHD'
