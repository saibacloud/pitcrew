# PitCrew

Welcome to PitCrew, a garage workshop app for managing your project cars. 
Everything is scoped to your car, whatever you're working on, whether its researching, finding parts, uploading photos, adding in reference documents, PitCrew augments your capability by using an AI layer (Gemini) as a superpowered search engine, that with your project car context, allows you to ask questions and get answers specific to your year/make/model/trim.

PitCrew is a web app, however works as a PWA so you can save it to your phone home screen.

## Getting started

Copy `.env.example` to `.env` and fill in three values.

Your Gemini API key:

```
GENAI_API_KEY=your_key_here
```

An Argon2id hash of the access code you'll unlock the app with. The prompt keeps the code itself out of your shell history — only the hash is ever stored:

```sh
python -m backend.auth
# Access code: ****
# Confirm: ****
# $argon2id$v=19$m=65536,t=3,p=4$...
```

```
PITCREW_CODE_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
```

And an HMAC key for signing session cookies:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
PITCREW_SECRET_KEY=generated_value_here
```

If `PITCREW_CODE_HASH` is unset the app fails closed — nobody can log in. If `PITCREW_SECRET_KEY` is unset a random key is generated at boot, which works but logs everyone out on every restart.

Then run it however suits your setup:

```sh
# Local dev
uvicorn backend.app:app --reload

# Docker Compose
docker compose up --build
```

I personally deploy via Coolify, which pulls from Forgejo and builds the image straight from the `Dockerfile`. `GENAI_API_KEY`, `PITCREW_CODE_HASH` and `PITCREW_SECRET_KEY` are set in the Coolify environment UI, traffic is fronted by NPM, and the two named volumes (`pitcrew-data`, `pitcrew-uploads`) persist the SQLite database and uploads across rebuilds.

The app serves on port 8000. Note that the session cookie is `Secure`, so PitCrew needs to be served over HTTPS in production — Chrome and Firefox make an exception for `http://localhost`, which is what makes local dev work.

## How it works

On first visit you'll get an unlock screen asking for your access code. It's verified against the Argon2id hash in `PITCREW_CODE_HASH` and exchanged for an HttpOnly, `Secure`, `SameSite=strict` session cookie that lasts 12 hours — the code itself is never stored in the browser. Login is rate limited to 8 attempts per 15 minutes per IP. The **Lock** button in the garage header ends the session.

The **Garage** is your landing page - a grid of your cars. Click one to open it up.

Once inside a car, you've got five sections to work with:

- **Car Info** - the basics: year, make, model, trim, engine, VIN, colour/paint code, notes, and a photo. This is the context that feeds every AI query.
- **Journal** - your research log. Ask a question, Gemini answers it (grounded via Google Search) with your car's context baked in. Also holds freeform notes, a service log (date, odometer, work done, details), photos, and every document search answer - doc answers are saved automatically so nothing is lost. Car Info notes are fed into every AI prompt, so mileage/history/mods written there make answers more specific.
- **Photo Pins** - upload photos from any angle (front, sides, rear, engine bay, underside, interior). Click anywhere on the photo to drop a pin, label the component, and ask AI to research that specific part. The answer sticks to the pin so you don't lose it.
- **Cart** - a running parts list. Name, part number, price, supplier link, category, and status tracking from wishlist through to installed.
- **Documents** - upload workshop manuals (PDF/DOCX), reference images, or any other files. Tick the ones you want, type a question, and the AI searches through them for the answer.

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, async Python |
| Auth | Argon2id access code, signed session cookie (itsdangerous), per-IP rate limits on login (8/15min) and AI endpoints (10/min) |
| Database | SQLite via aiosqlite, WAL mode, foreign keys on, soft deletes |
| AI | `gemini-flash-latest` (google-genai) with Google Search grounding |
| Frontend | Vanilla JS with ES modules, no framework, no build step |
| Deploy | Docker image built by Coolify from Forgejo, fronted by NPM |

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
│   │   └── manuals.py      - document uploads and AI document search
│   └── services/
│       └── gemini.py       - all Gemini API calls, prompt building, image processing
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
│   │   └── dialogs.js      - custom prompt and confirm modals
│   ├── icons/              - PWA icons
│   └── uploads/            - car photos, view photos, manuals, originals
├── Dockerfile
├── docker-compose.yml
├── PLAN.md                 - roadmap and architectural decisions
└── .env                    - GENAI_API_KEY, PITCREW_CODE_HASH, PITCREW_SECRET_KEY
```

## Data and storage

The database is SQLite with WAL mode and foreign keys enabled. Every delete is a soft delete - nothing is permanently removed, records are filtered by `deleted_at IS NULL`. Enum fields (part status, category, journal type, view angle) are validated both in the schema (CHECK constraints) and in application code before any write. Startup migrations handle legacy schemas (table rebuilds run with foreign keys off so `DROP TABLE` can't cascade into child rows).

Images are compressed on upload (max 2048px on the longest edge) and EXIF metadata is stripped before anything is stored or sent to an external API.

Two Docker volumes keep your data safe across rebuilds:
- `pitcrew-data` - the SQLite database (with its WAL/SHM siblings)
- `pitcrew-uploads` - car photos, view photos, manuals, and originals



