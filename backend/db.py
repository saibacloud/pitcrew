# Database
# SQLite schema + async CRUD via aiosqlite

import os
from typing import Optional
import aiosqlite

DB_PATH = os.environ.get(
    'PITCREW_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'pitcrew.db')
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        INTEGER,
    make        TEXT,
    model       TEXT,
    trim        TEXT,
    options     TEXT,
    engine      TEXT,
    color       TEXT,
    vin         TEXT,
    notes       TEXT,
    photo_url   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    citations   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'note',
    title       TEXT NOT NULL,
    body        TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    angle       TEXT,
    file_path   TEXT NOT NULL,
    type        TEXT DEFAULT 'photo',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photopins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    view_id     INTEGER REFERENCES views(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    notes       TEXT,
    status      TEXT DEFAULT 'open',
    x_pct       REAL,
    y_pct       REAL,
    ai_summary  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS parts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pin_id      INTEGER REFERENCES photopins(id) ON DELETE SET NULL,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    part_number TEXT,
    supplier    TEXT,
    url         TEXT,
    price       REAL,
    quantity    INTEGER DEFAULT 1,
    category    TEXT DEFAULT 'Mechanical',
    status      TEXT DEFAULT 'wishlist',
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id          INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    pin_id          INTEGER REFERENCES photopins(id) ON DELETE SET NULL,
    query_sent      TEXT NOT NULL,
    result_summary  TEXT,
    raw_response    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    category    TEXT DEFAULT 'manual',
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

# ── Init ──────────────────────────────────────────────────────────────────────


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        # Migrate existing DB: add photo_url if missing
        try:
            await db.execute("ALTER TABLE cars ADD COLUMN photo_url TEXT")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migrate: rename nickname -> options
        try:
            await db.execute("ALTER TABLE cars RENAME COLUMN nickname TO options")
            await db.commit()
        except Exception:
            pass  # already renamed or column doesn't exist
        # Migrate parts: add quantity, category, notes
        for stmt in [
            "ALTER TABLE parts ADD COLUMN quantity INTEGER DEFAULT 1",
            "ALTER TABLE parts ADD COLUMN category TEXT DEFAULT 'Mechanical'",
            "ALTER TABLE parts ADD COLUMN notes TEXT",
        ]:
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                pass  # column already exists
        # Migrate journal: add type column
        try:
            await db.execute("ALTER TABLE journal ADD COLUMN type TEXT NOT NULL DEFAULT 'note'")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migrate manuals: add category column
        try:
            await db.execute("ALTER TABLE manuals ADD COLUMN category TEXT DEFAULT 'manual'")
            await db.commit()
        except Exception:
            pass  # column already exists
        # Migrate photopins: add ai_summary column
        try:
            await db.execute("ALTER TABLE photopins ADD COLUMN ai_summary TEXT")
            await db.commit()
        except Exception:
            pass  # column already exists

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _fetchall(sql: str, params: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _fetchone(sql: str, params: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

# ── Cars ──────────────────────────────────────────────────────────────────────

_CAR_FIELDS = ('year', 'make', 'model', 'trim', 'options', 
               'engine', 'color', 'vin', 'notes', 'photo_url')


async def get_cars():
    return await _fetchall("SELECT * FROM cars ORDER BY created_at DESC")


async def get_car(car_id: int):
    return await _fetchone("SELECT * FROM cars WHERE id = ?", (car_id,))


async def create_car(data: dict) -> int:
    cols = [f for f in _CAR_FIELDS if f in data]
    placeholders = ', '.join('?' for _ in cols)
    values = [data[f] for f in cols]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"INSERT INTO cars ({', '.join(cols)}) VALUES ({placeholders})", values
        )
        await db.commit()
        return cur.lastrowid


async def update_car(car_id: int, data: dict):
    fields = {k: v for k, v in data.items() if k in _CAR_FIELDS}
    if not fields:
        return
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [car_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE cars SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_car(car_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        await db.commit()


async def update_car_photo(car_id: int, photo_url: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE cars SET photo_url = ? WHERE id = ?", (photo_url, car_id))
        await db.commit()


# ── Journal ────────────────────────────────────────────────────────────────────


_JOURNAL_FIELDS = ('type', 'title', 'body')


async def get_journals(car_id: int):
    return await _fetchall(
        "SELECT * FROM journal WHERE car_id = ? ORDER BY created_at DESC",
        (car_id,)
    )


async def get_journal(journal_id: int):
    return await _fetchone("SELECT * FROM journal WHERE id = ?", (journal_id,))


async def create_journal(car_id: int, data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO journal (car_id, type, title, body) VALUES (?, ?, ?, ?)",
            (car_id, data.get('type', 'note'), data.get('title', ''), data.get('body'))
        )
        await db.commit()
        return cur.lastrowid


async def update_journal(journal_id: int, data: dict):
    fields = {k: v for k, v in data.items() if k in _JOURNAL_FIELDS}
    if not fields:
        return
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [journal_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE journal SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_journal(journal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM journal WHERE id = ?", (journal_id,))
        await db.commit()



# ── Views (photo-pin backgrounds) ────────────────────────────────────────────

async def get_views(car_id: int):
    return await _fetchall(
        "SELECT * FROM views WHERE car_id = ? ORDER BY angle",
        (car_id,)
    )


async def get_view(view_id: int):
    return await _fetchone("SELECT * FROM views WHERE id = ?", (view_id,))


async def get_view_by_angle(car_id: int, angle: str):
    return await _fetchone(
        "SELECT * FROM views WHERE car_id = ? AND angle = ?",
        (car_id, angle)
    )


async def create_view(car_id: int, name: str, angle: str, file_path: str) -> int:
    """Allow multiple photos per angle per car."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO views (car_id, name, angle, file_path) VALUES (?, ?, ?, ?)",
            (car_id, name, angle, file_path)
        )
        await db.commit()
        return cur.lastrowid


async def delete_view(view_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM views WHERE id = ?", (view_id,))
        await db.commit()


# ── Photo Pins ─────────────────────────────────────────────────────────────────

_PHOTOPIN_FIELDS = ('label', 'notes', 'status', 'x_pct', 'y_pct', 'ai_summary')


async def get_photopins(view_id: int):
    return await _fetchall(
        "SELECT * FROM photopins WHERE view_id = ? ORDER BY id ASC",
        (view_id,)
    )


async def get_photopin(photopin_id: int):
    return await _fetchone("SELECT * FROM photopins WHERE id = ?", (photopin_id,))


async def create_photopin(view_id: int, data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO photopins (view_id, label, notes, x_pct, y_pct) VALUES (?, ?, ?, ?, ?)",
            (view_id, data.get('label', ''), data.get('notes'), data.get('x_pct'), data.get('y_pct'))
        )
        await db.commit()
        return cur.lastrowid


async def update_photopin(photopin_id: int, data: dict):
    fields = {k: v for k, v in data.items() if k in _PHOTOPIN_FIELDS}
    if not fields:
        return
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [photopin_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE photopins SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_photopin(photopin_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM photopins WHERE id = ?", (photopin_id,))
        await db.commit()

# ── Parts ─────────────────────────────────────────────────────────────────────

_PART_FIELDS = ('name', 'part_number', 'supplier', 'url', 'price',
                'quantity', 'category', 'status', 'notes', 'pin_id')


async def get_parts(car_id: int):
    return await _fetchall(
        "SELECT * FROM parts WHERE car_id = ? ORDER BY category, created_at DESC",
        (car_id,)
    )


async def get_part(part_id: int):
    return await _fetchone("SELECT * FROM parts WHERE id = ?", (part_id,))


async def create_part(car_id: int, data: dict) -> int:
    data_cols = [f for f in _PART_FIELDS if f in data and data[f] is not None]
    cols = ['car_id'] + data_cols
    placeholders = ', '.join('?' for _ in cols)
    values = [car_id] + [data[f] for f in data_cols]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"INSERT INTO parts ({', '.join(cols)}) VALUES ({placeholders})", values
        )
        await db.commit()
        return cur.lastrowid


async def update_part(part_id: int, data: dict):
    fields = {k: v for k, v in data.items() if k in _PART_FIELDS}
    if not fields:
        return
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [part_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE parts SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_part(part_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM parts WHERE id = ?", (part_id,))
        await db.commit()

# ── Manuals ────────────────────────────────────────────────────────────────────

async def get_manuals(car_id: int):
    return await _fetchall(
        "SELECT * FROM manuals WHERE car_id = ? ORDER BY created_at DESC",
        (car_id,)
    )


async def get_manual(manual_id: int):
    return await _fetchone("SELECT * FROM manuals WHERE id = ?", (manual_id,))


async def create_manual(car_id: int, title: str, file_path: str, category: str = 'manual') -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO manuals (car_id, title, file_path, category) VALUES (?, ?, ?, ?)",
            (car_id, title, file_path, category)
        )
        await db.commit()
        return cur.lastrowid


async def delete_manual(manual_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM manuals WHERE id = ?", (manual_id,))
        await db.commit()
