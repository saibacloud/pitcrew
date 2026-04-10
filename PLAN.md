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

## Roadmap

### Phase 1 — Hardening (do before adding more data)

**SQLite robustness**
- Enable WAL mode (`PRAGMA journal_mode=WAL`) on startup — dramatically better read performance and prevents "database is locked" errors under concurrent async queries.
- Enable `PRAGMA foreign_keys=ON` per connection — foreign keys are defined in the schema but SQLite ignores them unless you opt in. Cascading deletes (`ON DELETE CASCADE`) don't actually fire right now.
- Wrap every write function in a try/except that catches `aiosqlite.IntegrityError` and `aiosqlite.OperationalError`, returning meaningful errors to the caller instead of silently passing or letting 500s propagate.
- Validate update fields against allowed values — `parts.status` should reject anything outside `wishlist|ordered|received|installed`, `parts.category` should reject unknown categories, `journal.type` should only accept `research|note|converse|docsearch`. Add CHECK constraints to the schema and validate in `db.py` before executing.
- Confirm rows were actually affected after UPDATE/DELETE — check `cursor.rowcount` and return 404 if 0 rows matched. Currently a `PATCH /api/parts/999999` silently succeeds.

**Connection management**
- Stop opening a new `aiosqlite.connect()` per query. Create a single long-lived connection (or a small pool) at startup and share it across requests. This also ensures WAL mode and `foreign_keys=ON` stay active.

**Pagination**
- Add `?limit=50&offset=0` to `GET /api/cars/{id}/journal`, `/parts`, `/manuals`, and the frontend. Without this, hundreds of journal entries or parts will bog down both the DB and the DOM.

**Logging**
- Add `logging.getLogger(__name__)` to `app.py` and `db.py`. Log AI calls (model, token count, latency), DB errors, and slow queries. Right now a failed Gemini call or corrupt DB returns a generic error with zero debug info.

**Background text extraction**
- Move PDF/DOCX text extraction on manual upload to `fastapi.BackgroundTasks`. A 200-page PDF currently blocks the upload request until extraction finishes.

---

### Phase 2 — Split the backend

The goal is reducing cognitive overhead, not building a framework. Three modules:

```
backend/
├── app.py          → FastAPI app, lifespan, static mount, top-level error handler
├── db.py           → connection management, schema, migrations, raw CRUD
├── routes/
│   ├── cars.py     → car CRUD + photo upload
│   ├── journal.py  → journal CRUD + research endpoint
│   ├── views.py    → photo views + pins + pin research
│   ├── parts.py    → parts/cart CRUD
│   └── manuals.py  → manual upload/delete + document search
├── services/
│   └── gemini.py   → all Gemini API calls, prompt building (absorbs prompt.py)
└── prompt.py       → (remove, fold into services/gemini.py)
```

Each route file is a `fastapi.APIRouter`. `app.py` just mounts them. `services/gemini.py` owns all AI interaction — prompt construction, EXIF stripping before send, error handling, token logging.

---

### Phase 3 — Media & storage

- **Image compression on upload** — resize to max 2048px on longest edge via Pillow, generate a 400px thumbnail for grid views. Saves disk and speeds up frontend rendering.
- **Upload size limits** — enforce max file size in the route (e.g. 20MB photos, 100MB manuals) before writing to disk.
- **Better document chunking** — current 8000-char fixed chunks with keyword overlap will miss relevant content in long manuals. Switch to overlapping chunks (2000 chars, 500 char overlap) and send more of them.

---

### Phase 4 — Frontend restructure

Split the monolithic `index.html` (~1550 lines) and `style.css` (~1620 lines) into logical modules. No framework — just ES modules and separate CSS files, loaded by the same `index.html`.

```
static/
├── index.html          → shell: HTML structure only, no <script> body
├── style.css           → base: reset, variables, layout, header, buttons, modals, toast
├── css/
│   ├── garage.css      → car grid, add-car card
│   ├── car-info.css    → photo upload, info form
│   ├── journal.css     → journal tabs, research answers, add forms
│   ├── pins.css        → view grid, thumbnails, pin overlay, pin list, research results
│   ├── cart.css         → parts table, status selects
│   ├── manuals.css     → manual grid, cards, PDF viewer
│   └── responsive.css  → all @media blocks in one place
├── js/
│   ├── app.js          → boot, api(), toast(), escapeHtml(), renderMarkdown(), shared state
│   ├── garage.js       → loadGarage(), removeCar(), add-car modal
│   ├── car.js          → showCar(), showSection(), populateCarInfoForm(), saveCarInfo()
│   ├── journal.js      → loadJournal(), renderJournalTab(), runResearch(), addJournalEntry()
│   ├── pins.js         → loadViews(), renderViewTab(), handlePinDrop(), researchPin()
│   ├── cart.js          → loadCartParts(), renderCartParts(), submitAddPart()
│   ├── manuals.js      → loadManuals(), renderManualGrid(), askDocuments()
│   └── dialogs.js      → pitcrewPrompt(), pitcrewConfirm()
```

Each JS module exports its public functions, `app.js` imports and wires them. HTML uses `<script type="module" src="/static/js/app.js">` — no bundler needed, native ES modules work in all modern browsers.

CSS files are loaded via `<link>` tags in `index.html`. Keeping `responsive.css` separate means all breakpoint overrides live in one file instead of scattered across sections.

---

### Phase 5 — Nice to have

- **Parts from Converse → Cart** — one-click to save an AI-suggested part directly to the cart
- **Export** — CSV/PDF parts list for the shop
- **Mobile** — responsive CSS is roughed in but needs work on narrow screens
- **Streaming AI responses** — SSE for research queries so results appear incrementally
- **Search across cars** — global search for journal entries, parts, pins by keyword

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
