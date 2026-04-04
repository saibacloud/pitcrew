# Database
# SQLite schema + async CRUD via aiosqlite

import os
from typing import Optional
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'pitcrew.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname    TEXT NOT NULL,
    year        INTEGER,
    make        TEXT,
    model       TEXT,
    trim        TEXT,
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
    title       TEXT NOT NULL,
    body        TEXT,
    status      TEXT DEFAULT 'done',
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

CREATE TABLE IF NOT EXISTS pins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    view_id     INTEGER REFERENCES views(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    notes       TEXT,
    status      TEXT DEFAULT 'open',
    x_pct       REAL,
    y_pct       REAL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS parts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pin_id      INTEGER REFERENCES pins(id) ON DELETE SET NULL,
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
    pin_id          INTEGER REFERENCES pins(id) ON DELETE SET NULL,
    query_sent      TEXT NOT NULL,
    result_summary  TEXT,
    raw_response    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
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

_CAR_FIELDS = ('nickname', 'year', 'make', 'model', 'trim',
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
