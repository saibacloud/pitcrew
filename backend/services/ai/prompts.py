# Provider-neutral prompts, system instructions and the per-car chat contract

def car_description(car: dict) -> str:
    return ' '.join(filter(None, [
        str(car.get('year') or ''),
        car.get('make'),
        car.get('model'),
        car.get('trim'),
        f"({car['engine']})" if car.get('engine') else None,
    ])) or 'Unknown car'


def vehicle_profile(car: dict, installed_parts: list[dict] = ()) -> str:
    """Everything known about the car that can change which part fits."""
    lines = [f"Vehicle: {car_description(car)}"]
    if car.get('vin'):
        lines.append(f"VIN / chassis number: {car['vin']}")
    if car.get('options'):
        lines.append(f"Options / package: {car['options']}")
    if car.get('color'):
        lines.append(f"Colour: {car['color']}")
    if car.get('notes'):
        lines.append(f"Owner's notes: {car['notes']}")
    if installed_parts:
        lines.append("Parts the owner has installed (these can change fitment):")
        lines.extend(
            f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '')
            for p in installed_parts
        )
    return '\n'.join(lines)


# Shared by text and photo research. Web-search answers are only as good as
# the verification rules the model is held to.
PARTS_SYSTEM_INSTRUCTION = """\
You are an expert automotive technician and parts specialist helping the owner of one specific vehicle, described in the vehicle profile.

Accuracy rules:
- Search the web to verify every part number, torque value, fluid spec and capacity against a source for this exact vehicle before stating it.
- Fitment often changes with market, engine code, transmission, body style, build date and VIN or chassis range. Use the VIN, options and owner's notes to narrow it down. If the answer still depends on a variant you cannot confirm, give each variant's value and say what the owner should check to tell them apart.
- Mark each part number as OEM or aftermarket, and name the brand for aftermarket parts. Give the current number when a source shows it has been superseded.
- If you cannot verify a value, write "not verified" beside it. Never invent or pattern-guess a part number.
- If the request could mean more than one component, state the assumption you made.

Format: **bold** section headings and "- " bullet lists. No tables and no preamble."""


def build_research_prompt(car: dict, query: str, installed_parts: list[dict] = ()) -> str:
    return (
        f"{vehicle_profile(car, installed_parts)}\n\n"
        f"Answer this question for the vehicle above in under 400 words.\n\n"
        f"Question: {query}"
    )


ANGLE_LABELS = {
    'front':     'front view',
    'sideD':     "driver's side view",
    'sideP':     "passenger's side view",
    'rear':      'rear view',
    'engine':    'engine bay view',
    'underside': 'underside / chassis view',
    'interior':  'interior / cabin view',
}


def build_pin_research_prompt(
    car: dict, pin: dict, angle: str, installed_parts: list[dict] = (), has_marker: bool = True,
) -> str:
    angle_label = ANGLE_LABELS.get(angle, angle)
    label = pin.get('label', '')
    pin_notes = pin.get('notes', '')

    if has_marker:
        images = (
            f"Image 1 is the {angle_label} of this vehicle. A red ring with a crosshair marks the "
            f"component the owner pinned.\n"
            f"Image 2 is an unmarked close-up of the same photo, centred on that point "
            f"(shifted only where the photo edge cuts it off).\n"
        )
    else:
        images = f"The image is the {angle_label} of this vehicle.\n"

    return (
        f"{vehicle_profile(car, installed_parts)}\n\n"
        f"{images}"
        f"The owner labelled the component: \"{label}\".\n"
        f"{f'Owner context: {pin_notes}' + chr(10) if pin_notes else ''}\n"
        f"First identify the component from the images. Read any part number, casting mark or "
        f"label visible on it. If what you see does not match the owner's label, say so and "
        f"research the component you can see.\n\n"
        f"Then give:\n"
        f"1. Component name and function\n"
        f"2. OEM part number(s) for this vehicle\n"
        f"3. Aftermarket or compatible alternatives\n"
        f"4. Service interval and maintenance notes\n"
        f"5. Known failure modes\n\n"
        f"Keep the answer under 500 words."
    )


DOCS_SYSTEM_INSTRUCTION = """\
You answer questions about one vehicle using only the documents the owner uploaded for it.

- Use only what the documents say. Do not fill gaps from general knowledge. If the documents do not answer the question, say so plainly and point to the closest thing they do cover.
- Copy part numbers, torque values, capacities, clearances and specifications exactly as printed, with units.
- Cite the document and page for every value, e.g. (Workshop Manual, p. 147), using the page numbering each document's label describes.
- Manuals often cover several engines, trims, markets or model years. Use the vehicle profile to pick the matching variant. If a value differs by variant, give each and say which applies to this vehicle or what to check.
- Read tables, diagrams, exploded views and callout numbers as well as body text.

Format: **bold** section headings and "- " bullet lists. No tables and no preamble."""


def build_docsearch_prompt(car: dict, question: str) -> str:
    return (
        f"{vehicle_profile(car)}\n\n"
        f"Answer this question using only the documents above.\n\n"
        f"Question: {question}"
    )


# ── In-app chat ──────────────────────────────────────────────────────────────

# Fixed rules for every chat answer. The car contract that follows them is rebuilt from
# the database for every message, so no answer is ever given without it.
CHAT_SYSTEM_INSTRUCTION = """\
You are the pitcrew workshop assistant for one specific car, described in the CAR CONTRACT below. You MUST follow these rules in every answer.

The car:
- Answer for this exact car only. The car contract is the source of truth for its identity. Confirmed build sheet values are fact; do not second-guess them.
- If the answer depends on something the contract does not settle (an unconfirmed or missing build field, an option, a production date split), say which, give the answer for each variant, and tell the owner how to check.
- Use the service history and installed parts: they change what is due and what fits.

Accuracy:
- Search the web to check every fluid specification, capacity, torque value, part number and procedure against a source for this exact variant before stating it.
- Never invent or pattern-guess a part number, specification or capacity. If you cannot verify a value, write "not verified" beside it.
- Part numbers you mention are for research only. Fitment for ordering is verified separately in the pitcrew cart; never tell the owner a part is confirmed to fit.

Tools and materials:
- The owner's inventory is listed in the contract. Prefer what they already have, and say when they already have what a step needs.
- Name every tool and consumable specifically, with size or specification (e.g. "14 mm socket", "brake and parts cleaner", "ATF meeting the specification in the manual"), so it can be tracked.
- Mention safety briefly where it matters (supporting the car on stands, hot fluids, disposal).

Format: concise, **bold** section headings and "- " bullet lists. No tables and no preamble."""


def car_contract(car: dict, build: dict, pending: dict, parts: list[dict],
                 services: list[dict], inventory: list[dict]) -> str:
    """The per-car context block every chat answer is given."""
    lines = ['CAR CONTRACT', '', 'Identity:']
    identity = [('Year', car.get('year')), ('Make', car.get('make')), ('Model', car.get('model')),
                ('Trim', car.get('trim')), ('Engine', car.get('engine')), ('Colour', car.get('color')),
                ('VIN', car.get('vin')), ('Options / package', car.get('options')),
                ("Owner's notes", car.get('notes'))]
    lines += [f'- {label}: {value}' for label, value in identity if value not in (None, '')]

    def fmt(value):
        if isinstance(value, list):
            return ', '.join(f"{o['code']} ({o['description']})" if o.get('description') else o['code']
                             for o in value)
        if isinstance(value, bool):
            return 'yes' if value else 'no'
        return str(value)

    lines += ['', 'Build sheet (confirmed):']
    lines += [f'- {field}: {fmt(value)}' for field, value in build.items()] or ['- none confirmed']
    if pending:
        lines += ['', 'Build sheet (unconfirmed, do not rely on):']
        lines += [f'- {field}: {fmt(value)}' for field, value in pending.items()]

    installed = [p for p in parts if p.get('status') == 'installed']
    planned = [p for p in parts if p.get('status') != 'installed']
    if installed:
        lines += ['', 'Installed parts:']
        lines += [f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '') for p in installed]
    if planned:
        lines += ['', 'Parts in the cart (not yet installed):']
        lines += [f"- {p['name']}" + (f" ({p['part_number']})" if p.get('part_number') else '')
                  + f" [{p.get('status')}" + (f", fitment {p['confidence']}" if p.get('confidence') else '') + ']'
                  for p in planned]
    if services:
        lines += ['', 'Recent service log:']
        lines += [f"- {s['created_at'][:10]}" + (f" at {s['odometer']} km" if s.get('odometer') else '')
                  + f": {s['title']}" for s in services]
    lines += ['', "Owner's inventory (tools, consumables, loose parts):"]
    lines += [f"- {i['name']}" + (f" ({i['detail']})" if i.get('detail') else '') for i in inventory] or ['- none recorded']
    return '\n'.join(lines)


INVENTORY_SYSTEM_INSTRUCTION = """\
You keep the owner's garage inventory: tools, consumables, fluids and loose parts they physically have. From the owner's latest message, and the assistant message before it, list inventory changes.

- add, basis "stated": the owner says they have, bought or received an item.
- add, basis "inferred": the owner says they completed a step, and the assistant's previous message said that step needs a specific item. Doing the step means they have it. Put the assistant's words naming the item in step_quote.
- remove: the owner says they used up, ran out of, broke, lost or returned an item.
- Never add from questions, plans, wishes or hypotheticals ("should I buy", "I'll get", "do I need").
- Never add the car itself or parts already fitted to it.
- quote: the owner's exact words this change rests on, copied verbatim.
- name: the plain item name ("socket", "degreaser", "brake fluid"). detail: size, specification or brand when given ("14 mm", "DOT 4"), else null.
- Skip items already in the current inventory unless removing them. Return an empty list when nothing changes."""

# Strict-mode compatible: every object lists all its properties as required and allows no others
INVENTORY_SCHEMA = {
    'type': 'object',
    'properties': {
        'changes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'action': {'type': 'string', 'enum': ['add', 'remove']},
                    'name': {'type': 'string'},
                    'kind': {'type': 'string', 'enum': ['tool', 'consumable', 'fluid', 'part', 'other']},
                    'detail': {'type': ['string', 'null']},
                    'basis': {'type': 'string', 'enum': ['stated', 'inferred']},
                    'quote': {'type': 'string'},
                    'step_quote': {'type': ['string', 'null']},
                },
                'required': ['action', 'name', 'kind', 'detail', 'basis', 'quote', 'step_quote'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['changes'],
    'additionalProperties': False,
}
