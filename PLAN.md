# PitCrew - Project Plan

A single-user, garage-first project car manager. Everything about a build lives here — specs, parts list, research notes, photo pins, uploaded documents, and an AI layer that knows your exact car.

The purpose is to cut through the noise of old forums, Facebook comments, and scattered niche fixes. Research happens in context — ask a question and the AI already knows the car.

---

## What's Built

### Backend (FastAPI + aiosqlite + SQLite)

Fully async API on port 8000. Serves the frontend as static files from the same process. No ORM — raw async SQL through `db.py` with `ALTER TABLE` migration guards on startup.

**Cars** — full CRUD. Fields: year, make, model, trim, options, engine, color, VIN, notes, photo (uploaded to `static/uploads/`).

**Parts / Cart** — full CRUD. Fields: name, part number, quantity, price (in $), URL, category (Mechanical / Electrical / Body / Interior / Consumables), status (wishlist → ordered → received → installed), notes, date added. Status is changed inline via a dropdown directly in the table.

**Journal** — CRUD endpoints for all three types: `research`, `note`, `converse`. Research entries store both the query (`title`) and the full AI response (`body`). The Research tab wires to `POST /api/cars/{id}/research` which calls Gemini 2.0 Flash with Google Search grounding.

**Photo Pins** — full system:
- `POST /api/cars/{id}/views/{angle}` — upload a photo to one of seven angle slots (front, sideD, sideP, rear, engine, underside, interior). EXIF is stripped via Pillow before anything is stored or sent externally.
- `POST /api/views/{id}/pins` — drop a pin at an x%/y% position with a label and optional notes.
- `POST /api/pins/{id}/research` — sends the photo + car context + pin label to Gemini 2.0 Flash and returns a component research summary. The response is persisted to `photopins.ai_summary` in the DB so it survives page reloads.
- Full delete for views and pins.

**Documents** — upload and serve files per car, in three categories:
- `manual` — PDFs and DOCX files
- `reference` — images (wiring diagrams, pinout charts, spec sheets)
- `other` — any file type (future AI ingestion target)

**Prompt builder** (`prompt.py`) — assembles a car-aware system prompt from the DB record. Used by the research endpoint.

**Schema** (`db.py`) — covers: `cars`, `messages`, `journal`, `notes`, `views`, `photopins`, `parts`, `searches`, `manuals`. All migrate-safe.

---

### Frontend (Vanilla JS SPA, no framework)

Single `index.html` + `style.css`. No build step. Two views: Garage and Car.

**Garage view** — grid of car cards. Add Car modal, remove per card.

**Car view** — sidebar nav with five sections:

- **Car Info** — photo upload/delete, full detail form, saves via `PATCH /api/cars/{id}`.
- **Journal** — Research tab (wired to Gemini, results stored + displayed with View Answer toggle), Note tab (wired), Converse tab (stub).
- **Photo Pins** — seven angle tabs. Each supports:
  - Click photo to drop a pin (label + optional notes, stored at x%/y%)
  - Numbered pin markers overlaid on the photo
  - Pin list below the photo with: Ask AI button, View Answer toggle (persisted, shown on load if previously asked), Remove button
  - Back to photos grid navigation
- **Cart** — category filter tabs, parts table with date added, $ price, inline status dropdown, add/edit/delete. `+ Add Part` in the table footer.
- **Documents** — three sub-tabs (Manuals, Reference, Other). Upload, list, delete, inline PDF viewer.

---

## Future / Nice to Have

- **Parts from Converse → Cart** — one-click to save an AI-suggested part directly to the cart
- **AI document reading** — pass Other-tab files to an AI so it can answer questions from your own uploaded docs
- **Export** — CSV/PDF parts list for the shop
- **Mobile** — responsive CSS is roughed in but needs work on narrow screens

---

## Architecture

FastAPI serves both API and frontend from one process. SQLite via `db.py` — no ORM, `executescript` on startup, `ALTER TABLE` guards per column. Frontend is a single HTML file, vanilla JS, `fetch` to the API. No bundler, no framework. One `uvicorn` command to run the whole thing.

`DB_PATH` is set via `PITCREW_DB_PATH` env var (defaults to `pitcrew.db` at project root; set to `/app/data/pitcrew.db` in Docker).

---

## Deployment (Docker / Proxmox)

Single image. Two named volumes:
- `pitcrew-data` → `/app/data/pitcrew.db` (database)
- `pitcrew-uploads` → `/app/static/uploads` (photos, documents)

> Do not run more than 1 replica — SQLite does not support concurrent writes from separate processes.

```sh
# Local
docker compose up --build

# Proxmox / swarm
docker build -t pitcrew:latest .
docker stack deploy -c docker-compose.yml pitcrew
```

**Traefik** labels are in `docker-compose.yml`. Assumes `--providers.docker=true` and `--providers.docker.exposedbydefault=false`. Add `pitcrew.lan` to your DNS resolver (Pi-hole / AdGuard / `/etc/hosts`).

**Environment:**
```
GENAI_API_KEY=your_gemini_api_key
```
`PITCREW_DB_PATH` is set in `docker-compose.yml` — no need to put it in `.env`.
