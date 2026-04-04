# PitCrew - Project Car Manager

Garage-first project car manager with an AI layer for parts research, search grounding, and hands-free chat. Cars are the central context of the whole app - everything is scoped to whichever car you're working on. Built around a **2006 BMW E46 330ci MSport**.

## Stack

| Layer | Tech |
|-------|------|
| AI | Gemini 2.0 Flash - native `google_search` grounding |
| Backend | FastAPI |
| STT / VAD | faster-whisper + Silero |
| TTS | piper (local, voice) |
| Database | SQLite via aiosqlite |
| Frontend | Single-file HTML/CSS/JS - served directly by FastAPI |
| Deploy | Proxmox VM, Traefik reverse proxy, open on trusted LAN |

## Running

```
uvicorn backend.app:app --reload    → http://localhost:8000
```

FastAPI serves `index.html` at `/`. 

## Config

```
GEMINI_API_KEY=       - required
```

## Phases

1. **Garage UI** - Landing page with car cards, add-car flow, car selection routing to the dashboard
2. **Car dashboard** - Tile grid layout, car info view, basic car record (year, make, model, trim, engine, notes)
3. **Research tile** - Gemini streaming with `google_search` grounding, car-aware system prompt, handles both part lookups *and* general how-to questions
4. **Structured commands** - Gemini tool definitions: `log_progress`, `search_parts`, `add_to_cart`, `log_maintenance`
5. **Photo pins** - Upload car photos, tap to drop a pin, label + notes, trigger a Gemini part search scoped to that pin's context
6. **Cart + parts tracker** - Parts list with supplier, price, status; cart tab on dashboard
7. **Journal + Notes** - Browsable progress timeline, freeform notes scratchpad with AI cleanup on demand
8. **Chat tile** - Streaming live chat with full car + journal + notes context baked into the prompt
9. **Voice input** - faster-whisper STT + Silero VAD, iPhone audio → transcribe → chat pipeline
10. **Voice output** - piper TTS, full hands-free loop
11. **Enhanced intelligence** - ETK-style exploded diagrams as pinnable views, cross-session context
12. **Deploy** - Dockerfile, Proxmox, LAN

## File Structure

```
pitcrew/
  README.md
  Dockerfile
  backend/
    __init__.py         - makes backend a Python package
    app.py              - FastAPI: REST endpoints, static file serving
    db.py               - SQLite schema + queries
    prompt.py           - System prompt builder (assembled dynamically from car record)
    voice.py            - VAD + STT + TTS pipeline
    requirements.txt
  static/
    index.html          - Single-file frontend (plain HTML/CSS/JS)
    assets/
    uploads/originals/  - Full EXIF photos (local only, never sent to Google)
```

## System Prompt

The prompt is built dynamically per-car from the `cars` table - year, make, model, trim are injected at runtime. This means the assistant context stays accurate when switching between cars in garage mode without maintaining separate hardcoded prompts.

## How It Works

The **landing page is the Garage** - a grid of car cards. Click a car to open its manager, or tap the add card to register a new one. Everything in the app is scoped to the active car: prompts, searches, journal entries, parts, pins, notes.

### Inside a Car

A centered tile-based dashboard gives quick access to all the car's tools:

| Tile | Purpose |
|------|---------|
| **Car Info** | Year, make, model, trim, engine, build notes |
| **Journal** | Timeline of what's been done and what's next |
| **Research** | AI-grounded search - ask for part numbers *or* "how do I do this?" |
| **Photo Pins** | Upload images, drop pins on parts, trigger a part search from that pin |
| **Cart** | Running list of things to buy |
| **Notes** | Freeform scratchpad - AI can parse and clean it up on demand |
| **Chat** | Streaming live chat with full car + journal + notes context |

> **Research** covers both specific part ID lookups and general how-to questions - both go through Gemini with `google_search` grounding and return structured results with links.

## Photo Pins

Upload photos from any angle (exterior, engine bay, underside, interior). Tap the photo to drop a pin → add a label + notes → search that part. Results come back with links and prices, one tap saves to parts tracker.

**Pin → Search:** backend assembles car + view + pin context into a Gemini prompt with `google_search` grounding. Every search is logged for later reference.

**Photos:** originals stored locally with EXIF intact (useful for progress timeline). Before anything is sent to Google, Pillow re-renders pixel data only into a clean in-memory buffer - GPS, device info, and timestamps are never transmitted externally.

## Data Model

```
cars       - id, nickname, year, make, model, trim
messages   - id, car_id, role, content, citations, created_at
views      - id, car_id, name, angle, file_path, type (photo | diagram)
pins       - id, view_id, label, notes, status, x_pct, y_pct, created_at
parts      - id, pin_id (FK), name, part_number, url, price, status
searches   - id, pin_id, query_sent, result_summary, raw_response, created_at
```


