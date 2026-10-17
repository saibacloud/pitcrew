# Fitment confidence gate
# Pure rules deciding how far an AI part claim can be trusted. No I/O.
# The agent claims a level; these rules compute a ceiling; the stored level is the lower.

import json
import re
from typing import NamedTuple, Optional

# ── Build sheet fields ───────────────────────────────────────────────────────

# Display order matters: the build sheet renders in this order
BUILD_FIELDS: dict[str, dict] = {
    'chassis_code':          {'label': 'Chassis code', 'kind': 'text', 'baseline': True},
    'body_style':            {'label': 'Body style', 'kind': 'text', 'baseline': True},
    'model':                 {'label': 'Model', 'kind': 'text', 'baseline': True},
    'engine_code':           {'label': 'Engine code', 'kind': 'text', 'baseline': True},
    'transmission':          {'label': 'Transmission', 'kind': 'text', 'baseline': True},
    'drivetrain':            {'label': 'Drivetrain', 'kind': 'enum', 'baseline': True,
                              'options': ['RWD', 'FWD', 'AWD', '4WD']},
    'production_month':      {'label': 'Production month', 'kind': 'month', 'baseline': True},
    'market':                {'label': 'Market', 'kind': 'text', 'baseline': True},
    'steering':              {'label': 'Steering', 'kind': 'enum', 'baseline': True,
                              'options': ['LHD', 'RHD']},
    'option_codes':          {'label': 'Option codes', 'kind': 'options', 'baseline': False},
    'option_codes_complete': {'label': 'Option list complete', 'kind': 'bool', 'baseline': False},
    'paint_code':            {'label': 'Paint code', 'kind': 'text', 'baseline': False},
    'upholstery_code':       {'label': 'Upholstery code', 'kind': 'text', 'baseline': False},
}

BASELINE_FIELDS = tuple(f for f, spec in BUILD_FIELDS.items() if spec['baseline'])
BUILD_SOURCES = {'owner', 'data_plate', 'catalogue', 'document'}

_MONTH = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
MAX_TEXT = 64
MAX_OPTIONS = 200


def encode_build_value(field: str, value) -> str:
    """Validate an incoming build-sheet value and return its stored text form."""
    spec = BUILD_FIELDS.get(field)
    if spec is None:
        raise ValueError(f'Unknown build field: {field}')
    kind = spec['kind']
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f'{spec["label"]} must be true or false')
        return '1' if value else '0'
    if kind == 'options':
        if not isinstance(value, list) or len(value) > MAX_OPTIONS:
            raise ValueError(f'{spec["label"]} must be a list of up to {MAX_OPTIONS} options')
        cleaned, seen = [], set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError('Each option needs a code')
            code = str(item.get('code') or '').strip().upper()
            desc = str(item.get('description') or '').strip()[:120]
            if not code or len(code) > 16:
                raise ValueError('Each option needs a code of up to 16 characters')
            if code not in seen:
                seen.add(code)
                cleaned.append({'code': code, 'description': desc})
        return json.dumps(cleaned)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{spec["label"]} needs a value')
    text = value.strip()
    if kind == 'month' and not _MONTH.match(text):
        raise ValueError(f'{spec["label"]} must be YYYY-MM')
    if kind == 'enum':
        text = text.upper()
        if text not in spec['options']:
            raise ValueError(f'{spec["label"]} must be one of {", ".join(spec["options"])}')
    if len(text) > MAX_TEXT:
        raise ValueError(f'{spec["label"]} must be {MAX_TEXT} characters or fewer')
    return text


def decode_build_value(field: str, stored: Optional[str]):
    if stored is None:
        return None
    kind = BUILD_FIELDS[field]['kind']
    if kind == 'bool':
        return stored == '1'
    if kind == 'options':
        return json.loads(stored)
    return stored


# ── Part number matching ─────────────────────────────────────────────────────

MIN_PART_NUMBER = 5
_PN_SEP = r'[ \-./]?'
_ALNUM = re.compile(r'[A-Z0-9]')


def normalise_part_number(pn: str) -> str:
    return ''.join(_ALNUM.findall((pn or '').upper()))


def part_number_in_text(pn: str, text: str) -> bool:
    """True when the part number appears as a whole token, allowing one separator between characters.

    A match directly after another digit group ("11 42 7 512 300" for 7512300) is rejected,
    so a short number never matches inside a longer spaced one.
    """
    norm = normalise_part_number(pn)
    if len(norm) < MIN_PART_NUMBER:
        return False
    body = _PN_SEP.join(re.escape(c) for c in norm)
    pattern = re.compile(rf'(?<![A-Z0-9])(?<![0-9][ \-./]){body}(?![A-Z0-9])', re.I)
    return bool(pattern.search(text or ''))


def _squash(text: str) -> str:
    return ' '.join((text or '').split()).casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """Verbatim containment, ignoring case and runs of whitespace."""
    q = _squash(quote)
    return bool(q) and q in _squash(text)


# Catalogue wording for a replaced number; the newest number must be resolved first
_SUPERSEDED = re.compile(r'supersed|replaced\s+by|replacement\s+for|ersetzt|no\s+longer\s+available', re.I)

# ── Gate ─────────────────────────────────────────────────────────────────────

LEVELS = ('unverified', 'conditional', 'verified')
VERIFIED_TIERS = {'catalogue', 'oem'}
PREDICATE_OPS = {'eq', 'in', 'range', 'has_option', 'lacks_option'}


class GateResult(NamedTuple):
    level: str
    reasons: list[str]
    open_fields: list[str]
    no_fit: list[str]


def _lower(a: str, b: str) -> str:
    return a if LEVELS.index(a) <= LEVELS.index(b) else b


def _option_codes(build: dict) -> set[str]:
    return {o['code'].upper() for o in build.get('option_codes') or []}


def evaluate_predicate(pred: dict, build: dict) -> Optional[bool]:
    """True/False against the build sheet, or None when the field it needs is unset."""
    op, field = pred.get('op'), pred.get('field')
    if op in ('has_option', 'lacks_option'):
        if build.get('option_codes') is None:
            return None
        code = str(pred.get('value') or '').upper()
        present = code in _option_codes(build)
        if op == 'has_option':
            return present
        # A partial option list cannot prove an option is absent
        if present:
            return False
        return True if build.get('option_codes_complete') is True else None
    value = build.get(field)
    if value is None:
        return None
    if op == 'eq':
        return _squash(str(value)) == _squash(str(pred.get('value')))
    if op == 'in':
        return _squash(str(value)) in {_squash(str(v)) for v in pred.get('values') or []}
    if op == 'range':
        lo, hi = pred.get('min'), pred.get('max')
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    raise ValueError(f'Unknown predicate op: {op}')


def validate_predicate(pred: dict) -> None:
    op, field = pred.get('op'), pred.get('field')
    if op not in PREDICATE_OPS:
        raise ValueError(f'Unknown predicate op: {op}')
    if op in ('has_option', 'lacks_option'):
        if field != 'option_codes' or not pred.get('value'):
            raise ValueError(f'{op} needs field option_codes and an option code value')
    elif field not in BUILD_FIELDS or BUILD_FIELDS[field]['kind'] == 'options':
        raise ValueError(f'Predicate field must be a build field: {field}')
    if op == 'range':
        if field != 'production_month':
            raise ValueError('range only applies to production_month')
        for bound in ('min', 'max'):
            if pred.get(bound) is not None and not _MONTH.match(str(pred[bound])):
                raise ValueError(f'range {bound} must be YYYY-MM')
        if pred.get('min') is None and pred.get('max') is None:
            raise ValueError('range needs min or max')
    if op == 'in' and not pred.get('values'):
        raise ValueError('in needs values')
    if not str(pred.get('quote') or '').strip():
        raise ValueError('Each predicate needs the quote it is read from')


def fields_used(claim: dict) -> list[str]:
    """Build fields a claim's fitment rests on; a change to any of them forces a recheck."""
    used = list(BASELINE_FIELDS)
    for f in claim.get('depends_on') or []:
        if f in BUILD_FIELDS and f not in used:
            used.append(f)
    for pred in claim.get('applies_when') or []:
        extra = ['option_codes', 'option_codes_complete'] if pred.get('field') == 'option_codes' \
            else [pred.get('field')]
        used.extend(f for f in extra if f in BUILD_FIELDS and f not in used)
    return used


def gate(claim: dict, build: dict, evidence: Optional[dict], verification: Optional[dict]) -> GateResult:
    """Compute the confidence a part claim may carry.

    claim:        {part_number, claimed, agent_id, depends_on: [field], applies_when: [predicate]}
    build:        decoded build sheet, field -> value (unset fields absent or None)
    evidence:     {kind: web_page|manual_page, tier, excerpt, extracted_by: text|ocr} (bound excerpt)
    verification: {verdict: explicit_fit|ambiguous|no_fit, quote, agent_id} from a blind second agent
    """
    level, reasons, open_fields, no_fit = 'verified', [], [], []

    def cap(to: str, reason: str):
        nonlocal level
        level = _lower(level, to)
        reasons.append(reason)

    claimed = claim.get('claimed') if claim.get('claimed') in LEVELS else 'unverified'
    if claimed != 'verified':
        cap(claimed, f'agent claimed {claimed}')

    excerpt = (evidence or {}).get('excerpt') or ''
    if not evidence or not excerpt.strip():
        cap('unverified', 'no evidence excerpt')
    else:
        if not part_number_in_text(claim.get('part_number') or '', excerpt):
            cap('unverified', 'part number not found in the evidence excerpt')
        if evidence.get('kind') not in ('web_page', 'manual_page') or evidence.get('tier') not in VERIFIED_TIERS:
            cap('unverified', 'evidence is not from a catalogue or OEM source')
        if evidence.get('extracted_by') != 'text':
            cap('conditional', 'evidence text came from OCR')
        if _SUPERSEDED.search(excerpt):
            cap('conditional', 'evidence mentions a superseded number')

    preds = claim.get('applies_when') or []
    if not preds:
        cap('conditional', 'no applicability stated')
    for pred in preds:
        try:
            validate_predicate(pred)
        except ValueError as e:
            cap('unverified', str(e))
            continue
        if not quote_in_text(pred['quote'], excerpt):
            cap('unverified', f'applicability quote for {pred["field"]} not in the evidence excerpt')
            continue
        result = evaluate_predicate(pred, build)
        if result is False:
            no_fit.append(pred['field'])
            cap('unverified', f'does not fit: {pred["field"]}')
        elif result is None:
            need = 'option_codes_complete' if pred['op'] == 'lacks_option' and build.get('option_codes') is not None \
                else pred['field']
            if need not in open_fields:
                open_fields.append(need)

    for f in fields_used(claim):
        if f in ('option_codes', 'option_codes_complete'):
            continue  # judged by their predicates
        if build.get(f) is None and f not in open_fields:
            open_fields.append(f)
    if open_fields:
        cap('conditional', 'unconfirmed build fields: ' + ', '.join(open_fields))

    if not verification:
        cap('conditional', 'awaiting independent verification')
    elif not verification.get('agent_id') or verification.get('agent_id') == claim.get('agent_id'):
        cap('conditional', 'verification must come from a different agent')
    elif verification.get('verdict') == 'no_fit':
        cap('unverified', 'independent verification found no fit')
    elif verification.get('verdict') != 'explicit_fit':
        cap('conditional', 'independent verification was not conclusive')
    elif not quote_in_text(verification.get('quote') or '', excerpt):
        cap('conditional', 'verification quote not in the evidence excerpt')

    return GateResult(level, reasons, open_fields, no_fit)
