# PitCrew - Project Plan

A single-user, garage-first project car manager. This app holds everything about a build - the car's specs, your parts list, your research notes, and eventually an AI layer that knows your exact car and can search for parts, torque specs, and how-tos. 
The purpose is to simplify the research element in old cards, finding gold in old forums, niche fixes in Facebook comments, and general best practices for maintaining well documnted cars.

---

## What's Built

### Backend (FastAPI + aiosqlite + SQLite)

The API is fully async, runs locally on port 8000, and serves the frontend as static files from the same process.

**Cars** - full CRUD. Create, read, update, delete a car. A car has: nickname, year, make, model, trim, engine, color/paint code, VIN, notes, and a photo (uploaded to `static/uploads/`, stored as a URL path in the DB).

**Parts / Cart** - full CRUD. Parts belong to a car. Fields: name, part number, quantity, price, URL, category (Mechanical / Electrical / Body / Interior / Consumables), status (wishlist / ordered / received / installed), notes. The status field is designed for a flow - wishlist → ordered → received → installed - though status cycling isn't wired in the UI yet.

**Schema** is in `db.py` and covers more than what's wired up yet: `messages`, `journal`, `notes`, `views`, `pins`, `searches` tables are all defined and migrate-safe, they just don't have live endpoints or UI yet.

**Prompt builder** (`prompt.py`) is ready: it assembles a dynamic system prompt from the car record (year, make, model, trim, engine, color, VIN) for use with an LLM. It's not wired to anything yet.

**Chat stub** - `POST /api/chat` exists but just echoes the message back. The Gemini integration hasn't been plugged in.

---

### Frontend (Vanilla JS SPA, no framework)

Single `index.html` + `style.css`. Two views: Garage and Car. Everything runs off `fetch()` calls to the API, a shared `activeCar` state variable, and direct DOM manipulation.

**Garage view** - grid of car cards. Each card shows nickname + spec line. Add a Car opens a modal. Remove button on each card.

**Car view** - sidebar nav with four sections:

- **Car Info** - photo upload/delete (displayed in grayscale), and a form for all the car's details. Saves via `PATCH /api/cars/{id}`.
- **Journal** - three tabs (Research, Converse, Note) - structure is in the HTML but none of them are wired to the backend yet.
- **Photo Pins** - four angle tabs (Front, Driver Side, Passenger Side, Rear) - structure is in the HTML, the actual pin-dropping functionality isn't built.
- **Cart** - fully working. Category filter tabs, a table of parts pulled from the DB, inline Edit (prefills the modal) and Delete per row. Add Part modal covers all fields. The `+ Add Part` button lives in the table footer row.

---

## What's Not Done Yet

### Journal
The DB tables (`journal`, `notes`, `messages`) exist but there are zero API endpoints for them and the frontend tabs are empty stubs. Needs:
- Backend: CRUD endpoints for journal entries and the sticky note
- Frontend: wire the Research tab to journal entries (list + add + delete), the Note tab to the single notes record (auto-save textarea), and the Converse tab to the chat endpoint once AI is live

### Photo Pins
The `views` and `pins` tables are in the schema but nothing is wired. The original idea here is good: upload a photo of the car from an angle, then click on the photo to drop a pin, label it, attach notes and a status. That's a meaningful chunk of work - image upload to an angle-specific record, an SVG/canvas overlay for pin placement, and a sidebar or popover to manage each pin.

### AI / Gemini (Phase 3)
`prompt.py` is ready. The chat stub exists. What's needed:
- Wire `POST /api/chat` to the Gemini 2.0 Flash API with `google_search` grounding turned on
- Pass `car_id` → look up the car → call `build_system_prompt()` → send to Gemini with the user's message
- Store the exchange in the `messages` table
- Display the response (with citations) in the Converse tab

### Status cycling on parts
The status field has a defined flow (wishlist → ordered → received → installed) but you can only set it via the Edit modal right now. A click-to-cycle button directly in the table row would be cleaner.

### Parts ↔ Pins link
The `parts` table has a `pin_id` foreign key - the idea being a part can be linked to a specific pin on a photo view. This is future state and depends on Photo Pins being built first.

---

## Future State (What This Could Grow Into)

- **AI-assisted parts search** - you're in the Converse tab, you ask "what's the torque spec for the front control arm bolts" or "find me an OEM alternator part number" and it searches the web, knows it's a 2006 E46 330ci MSport, and gives you an actual answer with links
- **Parts from chat → Cart** - if the AI returns a part, there should be a one-click "Add to Cart" that populates the form
- **Searches table** - log every AI search query and result so you can go back and review what was found without asking again
- **Multi-car garage** - it already supports multiple cars, but the Cart and Journal sections are per-car which is exactly right
- **Export** - a simple parts list export to CSV or PDF for when you're actually at the shop
- **Mobile** - the responsive CSS is roughed in but the sidebar collapses aggressively on small screens, the table overflows, and the photo upload area needs work on mobile

---

## Architecture in One Paragraph

FastAPI serves both the API and the frontend from one process. The DB is a local SQLite file (`pitcrew.db`) managed entirely through `db.py` with raw async SQL - no ORM, no migrations framework, just `executescript` on startup and `ALTER TABLE` guards for new columns. The frontend is a single HTML file with vanilla JS talking to the API over `fetch`. There's no build step, no bundler, no framework. The whole thing runs with one `uvicorn` command. That simplicity is intentional - this is a local tool for one person, and every layer of complexity would need to justify itself.
