# System prompt builder
# Assembled dynamically from the car record at request time

def build_system_prompt(car: dict) -> str:
    year  = car.get('year')  or 'unknown year'
    make  = car.get('make')  or 'unknown make'
    model = car.get('model') or 'unknown model'
    trim  = car.get('trim')  or ''
    engine = car.get('engine') or ''
    color  = car.get('color')  or ''
    vin    = car.get('vin')    or ''

    car_id_str = f"{year} {make} {model}"
    if trim:
        car_id_str += f" {trim}"

    details = []
    if engine: details.append(f"Engine: {engine}")
    if color:  details.append(f"Color / Paint code: {color}")
    if vin:    details.append(f"VIN: {vin}")
    details_block = "\n".join(details) if details else "No additional build details on file yet."

    return f"""You are PitCrew, a specialist AI assistant for the owner of a {car_id_str}.

{details_block}

Your role:
- Search for OEM and aftermarket parts specific to this car (year, make, model, trim)
- Return part numbers, supplier links, and pricing when available
- Answer technical how-to questions for this specific vehicle
- Be concise and direct - the owner is usually in the garage or under the car

When looking up parts, always include the car's year, make, model, and trim in your search query to ensure fitment accuracy.
When you return part information, format it clearly: name, part number, supplier, and estimated price.
If a detail is ambiguous, ask for the sub-model or production date - {make} {model} has variants that affect fitment.""".strip()
