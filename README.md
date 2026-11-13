# PitCrew

Welcome to PitCrew, a garage workshop app for managing your project cars. 
Everything is scoped to your car, whatever you're working on, whether its researching, finding parts, uploading photos, adding in reference documents, PitCrew augments your capability by using an AI layer (Google Gemini or OpenAI) as a superpowered search engine, that with your project car context, allows you to ask questions and get answers specific to your year/make/model/trim.

PitCrew is a web app, however works as a PWA so you can save it to your phone home screen.

## Getting started

Copy `.env.example` to `.env` and fill in two values.

An Argon2id hash of the 6–12 digit PIN you'll unlock the app with. The prompt keeps the PIN itself out of your shell history — only the hash is ever stored:

```sh
python -m backend.auth
# PIN (6-12 digits): ******
# Confirm: ******
# $argon2id$v=19$m=65536,t=3,p=4$...
```

```
PITCREW_PIN_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
```

And an HMAC key for signing session cookies:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
PITCREW_SECRET_KEY=generated_value_here
```

If `PITCREW_PIN_HASH` is unset the app fails closed — nobody can log in. If `PITCREW_SECRET_KEY` is unset a random key is generated at boot, which works but logs everyone out on every restart and means no AI key can be stored.

The AI key is entered in the app: open **Settings** in the garage header and paste a Google AI Studio key (`AIza…`) or an OpenAI API key (`sk-…`). The provider is picked from the key, the model is optional (Gemini defaults to `gemini-flash-latest`, OpenAI to `gpt-6-astra`), and the key is checked with the provider before it is saved. It is stored encrypted with a key derived from `PITCREW_SECRET_KEY`, and only its last four characters are ever shown. If `PITCREW_SECRET_KEY` changes, the stored key can no longer be decrypted and has to be entered again.

With no key in Settings, `GENAI_API_KEY` (and optionally `GEMINI_MODEL`) in the environment is used as a Gemini key. With neither, the AI features answer "No AI API key set" and everything else works.

To check the configured provider end to end (one real call each for chat, web search, an image, a PDF and JSON extraction):

```sh
python -m backend.services.ai.check
```

Then run it however suits your setup:

```sh
# Local dev
uvicorn backend.app:app --reload

# Docker Compose
docker compose up --build
```

The image runs on any container runtime. It needs `PITCREW_PIN_HASH` and `PITCREW_SECRET_KEY` in its environment (`GENAI_API_KEY` is an optional fallback) and two volumes: `/app/data` for the SQLite database and `/app/static/uploads` for uploads. `docker-compose.yml` sets this up with the named volumes `pitcrew-data` and `pitcrew-uploads`, and publishes the container port on the host address in `BIND_IP` (default `127.0.0.1`) for a reverse proxy to serve over HTTPS.

The app serves on port 8600. Note that the session cookie is `Secure`, so PitCrew needs to be served over HTTPS in production — Chrome and Firefox make an exception for `http://localhost`, which is what makes local dev work.

## How it works

On first visit you'll get a PIN pad. The PIN is verified against the Argon2id hash in `PITCREW_PIN_HASH` and exchanged for an HttpOnly, `Secure`, `SameSite=strict` session cookie that lasts 12 hours — the PIN itself is never stored in the browser. The cookie holds a signed random session ID; the database keeps only its SHA-256, and the **Lock** button revokes it server-side, so a copied cookie stops working once you lock. Login is rate limited to 8 attempts per 15 minutes per IP.

The **Garage** is your landing page - a grid of your cars. Click one to open it up.

Once inside a car, you've got five sections to work with:

- **Car Info** - the basics: year, make, model, trim, engine, VIN, colour/paint code, notes, and a photo. This is the context that feeds every AI query. The VIN is sent to the AI provider because fitment often splits by VIN or build range.
- **Journal** - your research log. Ask a question, the AI answers it (with web search) with your car's context and installed parts baked in, and lists the web sources it used so part numbers can be checked. Also holds freeform notes, a service log (date, odometer, work done, details), photos, and every document search answer - doc answers are saved automatically so nothing is lost. Car Info notes are fed into every AI prompt, so mileage/history/mods written there make answers more specific.
- **Photo Pins** - upload photos from any angle (front, sides, rear, engine bay, underside, interior). Click anywhere on the photo to drop a pin, label the component, and ask AI to research that specific part. The AI gets the photo with the pin marked plus a close-up of that spot, and flags it when the label doesn't match what it sees. With Gemini, search-grounded answers show Google's Search Suggestions underneath, as the Gemini API terms require. The answer sticks to the pin so you don't lose it.
- **Cart** - a running parts list. Name, part number, price, supplier link, category, and status tracking from wishlist through to installed.
- **Documents** - upload workshop manuals (PDF/DOCX; save old `.doc` files as `.docx` first), reference images, or any other files. Tick the ones you want, type a question, and the AI answers from those documents only, citing pages. PDFs up to 300 pages in total (including scanned ones, within the provider's PDF size limit of 50 MB per file, and for OpenAI 50 MB per question) are read whole, tables and diagrams included. For longer manuals, the best-matching pages are picked from the text layer and sent.

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, async Python |
| Auth | Argon2id PIN, signed session cookie (itsdangerous) backed by a revocable `sessions` table, login limit 8/15min per IP, AI limit 10/min per IP |
| Database | SQLite via aiosqlite, WAL mode, foreign keys on, soft deletes |
| AI | Google Gemini (google-genai, Google Search grounding) or OpenAI (Responses API, `web_search` tool), chosen by the key entered in Settings; `GENAI_API_KEY` as a Gemini fallback |
| Frontend | Vanilla JS with ES modules, no framework, no build step |
| Deploy | Docker image on any container runtime, behind an HTTPS reverse proxy |

## Project structure

```
pitcrew/
├── backend/
│   ├── app.py              - app entry point, mounts routers, serves static files
│   ├── auth.py             - access-code hashing, session cookies, per-IP rate limiters
│   ├── db.py               - database schema, migrations, all CRUD operations
│   ├── routes/
│   │   ├── auth.py         - login, logout, session probe
│   │   ├── cars.py         - car CRUD and photo upload
│   │   ├── journal.py      - journal entries and AI research queries
│   │   ├── views.py        - photo views, pins, and pin-level AI research
│   │   ├── parts.py        - parts list / cart
│   │   ├── manuals.py      - document uploads and AI document search
│   │   └── settings.py     - AI key and model settings
│   └── services/ai/
│       ├── __init__.py     - provider-neutral AI calls used by the routes
│       ├── settings.py     - encrypted key storage and per-call provider resolution
│       ├── gemini_adapter.py - Google Gemini calls
│       ├── openai_adapter.py - OpenAI calls
│       ├── prompts.py      - prompts, system instructions, the chat car contract
│       ├── images.py       - image prep for AI calls and storage
│       ├── ranking.py      - BM25 ranking of manual pages and passages
│       └── check.py        - live check of the configured provider
├── static/
│   ├── index.html          - HTML shell, no inline JavaScript
│   ├── style.css           - all styling
│   ├── manifest.json       - PWA manifest for home screen install
│   ├── sw.js               - service worker for offline shell caching
│   ├── js/
│   │   ├── app.js          - boot sequence, shared state, API helpers, session gate
│   │   ├── login.js        - login view, access-code submission
│   │   ├── garage.js       - car grid and add/remove car flow
│   │   ├── car.js          - car view, sidebar navigation, info form, photo upload
│   │   ├── journal.js      - research, notes, converse, and document search tabs
│   │   ├── pins.js         - photo grid, pin overlay, AI component research
│   │   ├── cart.js         - parts table, add/edit modals, status management
│   │   ├── manuals.js      - document grid, file uploads, AI document Q&A
│   │   ├── settings.js     - AI settings dialog
│   │   └── dialogs.js      - custom prompt and confirm modals
│   ├── icons/              - PWA icons
│   └── uploads/            - car photos, view photos, manuals, originals (served only with a valid session)
├── Dockerfile
├── docker-compose.yml
├── PLAN.md                 - roadmap and architectural decisions
└── .env                    - PITCREW_PIN_HASH, PITCREW_SECRET_KEY, optional GENAI_API_KEY
```

## Data and storage

The database is SQLite with WAL mode and foreign keys enabled. Every delete is a soft delete - nothing is permanently removed, records are filtered by `deleted_at IS NULL`. Enum fields (part status, category, journal type, view angle) are validated both in the schema (CHECK constraints) and in application code before any write. Startup migrations handle legacy schemas (table rebuilds run with foreign keys off so `DROP TABLE` can't cascade into child rows).

Images are compressed on upload (max 2048px on the longest edge) and EXIF metadata is stripped before anything is stored or sent to an external API.

Two Docker volumes keep your data safe across rebuilds:
- `pitcrew-data` - the SQLite database (with its WAL/SHM siblings)
- `pitcrew-uploads` - car photos, view photos, manuals, and originals



