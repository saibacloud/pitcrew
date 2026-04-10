# PitCrew

Welcome to PitCrew, a garage workshop app for managing your project cars. 
Everything is scoped to your car, whatever you're working on, whether its researching, finding parts, uploading photos, adding in reference documents, PitCrew augments your capability by using an AI layer (Gemini) as a superpowered search engine, that with your project car context, allows you to ask questions and get answers specific to your year/make/model/trim.

PitCrew is a web app, however works as a PWA so you can save it to your phone home screen.

## Getting started

Set your Gemini API key in `.env`:

```
GENAI_API_KEY=your_key_here
```

Then run it however suits your setup:

```sh
# Local dev
uvicorn backend.app:app --reload

# Docker (you will need to change image to build in the compose file)
docker compose up --build

# I personally deploy as a constrained node on my Docker Swarm
docker build -t pitcrew:latest .
docker stack deploy -c docker-compose.yml pitcrew
```

The app serves on port 8000.

## How it works

The **Garage** is your landing page - a grid of your cars. Click one to open it up.

Once inside a car, you've got five sections to work with:

- **Car Info** - the basics: year, make, model, trim, engine, VIN, colour/paint code, notes, and a photo. This is the context that feeds every AI query.
- **Journal** - your research log. Ask a question, Gemini answers it with your car's context baked in. You can also save freeform notes and document search results here. Everything is kept so you can come back to it.
- **Photo Pins** - upload photos from any angle (front, sides, rear, engine bay, underside, interior). Click anywhere on the photo to drop a pin, label the component, and ask AI to research that specific part. The answer sticks to the pin so you don't lose it.
- **Cart** - a running parts list. Name, part number, price, supplier link, category, and status tracking from wishlist through to installed.
- **Documents** - upload workshop manuals (PDF/DOCX), reference images, or any other files. Tick the ones you want, type a question, and the AI searches through them for the answer.

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, async Python |
| Database | SQLite via aiosqlite, WAL mode, soft deletes |
| AI | Gemini 2.0 Flash (google-genai) |
| Frontend | Vanilla JS with ES modules, no framework, no build step |
| Deploy | Docker, (I use) Traefik reverse proxy, single replica |

## Project structure

```
pitcrew/
├── backend/
│   ├── app.py              - app entry point, mounts routers, serves static files
│   ├── db.py               - database schema, migrations, all CRUD operations
│   ├── routes/
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
│   │   ├── app.js          - boot sequence, shared state, API helper, utilities
│   │   ├── garage.js       - car grid and add/remove car flow
│   │   ├── car.js          - car view, sidebar navigation, info form, photo upload
│   │   ├── journal.js      - research, notes, converse, and document search tabs
│   │   ├── pins.js         - photo grid, pin overlay, AI component research
│   │   ├── cart.js         - parts table, add/edit modals, status management
│   │   ├── manuals.js      - document grid, file uploads, AI document Q&A
│   │   └── dialogs.js      - custom prompt and confirm modals
│   ├── icons/              - PWA icons
│   └── uploads/            - car photos, view photos, uploaded documents
├── Dockerfile
├── docker-compose.yml
├── PLAN.md                 - roadmap and architectural decisions
└── .env                    - environment variables (hook ya Gemini key in here)
```

## Data and storage

The database is SQLite with WAL mode and foreign keys enabled. Every delete is a soft delete - nothing is permanently removed, records are filtered by `deleted_at IS NULL`. Enum fields (part status, category, journal type) are validated both in the schema and in application code.

Images are compressed on upload (max 2048px on the longest edge) and EXIF metadata is stripped before anything is sent to an external API.

Two Docker volumes keep your data safe across rebuilds:
- `pitcrew-data` - the SQLite database
- `pitcrew-uploads` - photos and documents

> **Note:** SQLite doesn't support concurrent writes from separate processes, so this runs as a single replica only. TLDR; single node in docker, hence the constraint to a single host in the compose file.
