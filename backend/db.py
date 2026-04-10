# Database
# SQLite schema + async CRUD via aiosqlite
# Single shared connection, WAL mode, foreign keys enforced

import logging
import os
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    'PITCREW_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'pitcrew.db')
)

# ── Allowed enum values ──────────────────────────────────────────────────────

VALID_JOURNAL_TYPES = {'research', 'note', 'converse', 'docsearch'}
VALID_PART_STATUSES = {'wishlist', 'ordered', 'received', 'installed'}
VALID_PART_CATEGORIES = {'Mechanical', 'Electrical', 'Body', 'Interior', 'Consumables'}
VALID_MANUAL_CATEGORIES = {'manual', 'reference', 'other'}
VALID_VIEW_ANGLES = {'front', 'sideD', 'sideP', 'rear', 'engine', 'underside', 'interior'}

# ── Schema ───────────────────────────────────────────────────────────────────

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
    deleted_at  TEXT,
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
    type        TEXT NOT NULL DEFAULT 'note'
                CHECK(type IN ('research', 'note', 'converse', 'docsearch')),
    title       TEXT NOT NULL,
    body        TEXT,
    deleted_at  TEXT,
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
    deleted_at  TEXT,
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
    deleted_at  TEXT,
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
    category    TEXT DEFAULT 'Mechanical'
                CHECK(category IN ('Mechanical', 'Electrical', 'Body', 'Interior', 'Consumables')),
    status      TEXT DEFAULT 'wishlist'
                CHECK(status IN ('wishlist', 'ordered', 'received', 'installed')),
    notes       TEXT,
    deleted_at  TEXT,
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
    category    TEXT DEFAULT 'manual'
                CHECK(category IN ('manual', 'reference', 'other')),
    deleted_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

# ── Shared connection ────────────────────────────────────────────────────────

_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.execute("PRAGMA busy_timeout=5000")
        log.info("Database connected: %s (WAL, FK on)", DB_PATH)
    return _db


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        log.info("Database connection closed")


# ── Init ─────────────────────────────────────────────────────────────────────

async def init_db():
    db = await get_db()
    await db.executescript(_SCHEMA)

    # Migrations — add columns that may not exist on older databases
    _migrations = [
        "ALTER TABLE cars ADD COLUMN photo_url TEXT",
        "ALTER TABLE cars ADD COLUMN deleted_at TEXT",
        "ALTER TABLE journal ADD COLUMN type TEXT NOT NULL DEFAULT 'note'",
        "ALTER TABLE journal ADD COLUMN deleted_at TEXT",
        "ALTER TABLE parts ADD COLUMN quantity INTEGER DEFAULT 1",
        "ALTER TABLE parts ADD COLUMN category TEXT DEFAULT 'Mechanical'",
        "ALTER TABLE parts ADD COLUMN notes TEXT",
        "ALTER TABLE parts ADD COLUMN deleted_at TEXT",
        "ALTER TABLE manuals ADD COLUMN category TEXT DEFAULT 'manual'",
        "ALTER TABLE manuals ADD COLUMN deleted_at TEXT",
        "ALTER TABLE photopins ADD COLUMN ai_summary TEXT",
        "ALTER TABLE photopins ADD COLUMN deleted_at TEXT",
        "ALTER TABLE views ADD COLUMN deleted_at TEXT",
    ]
    for stmt in _migrations:
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception:
            pass  # column already exists

    # Rename nickname -> options if old schema
    try:
        await db.execute("ALTER TABLE cars RENAME COLUMN nickname TO options")
        await db.commit()
    except Exception:
        pass

    log.info("Database initialised, migrations applied")


# ── Query helpers ────────────────────────────────────────────────────────────

async def _fetchall(sql: str, params: tuple = ()):
    db = await get_db()
    async with db.execute(sql, params) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _fetchone(sql: str, params: tuple = ()):
    db = await get_db()
    async with db.execute(sql, params) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def _execute(sql: str, params: tuple = ()) -> int:
    """Execute a write query. Returns rowcount."""
    db = await get_db()
    cur = await db.execute(sql, params)
    await db.commit()
    return cur.rowcount


async def _insert(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT. Returns lastrowid."""
    db = await get_db()
    cur = await db.execute(sql, params)
    await db.commit()
    return cur.lastrowid


# ── Cars ─────────────────────────────────────────────────────────────────────

_CAR_FIELDS = ('year', 'make', 'model', 'trim', 'options',
               'engine', 'color', 'vin', 'notes', 'photo_url')

_LIVE_CAR = "deleted_at IS NULL"


async def get_cars():
    return await _fetchall(f"SELECT * FROM cars WHERE {_LIVE_CAR} ORDER BY created_at DESC")


async def get_car(car_id: int):
    return await _fetchone(f"SELECT * FROM cars WHERE id = ? AND {_LIVE_CAR}", (car_id,))


async def create_car(data: dict) -> int:
    cols = [f for f in _CAR_FIELDS if f in data]
    placeholders = ', '.join('?' for _ in cols)
    values = [data[f] for f in cols]
    return await _insert(
        f"INSERT INTO cars ({', '.join(cols)}) VALUES ({placeholders})", tuple(values)
    )


async def update_car(car_id: int, data: dict) -> int:
    fields = {k: v for k, v in data.items() if k in _CAR_FIELDS}
    if not fields:
        return 0
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [car_id]
    return await _execute(
        f"UPDATE cars SET {set_clause} WHERE id = ? AND {_LIVE_CAR}", tuple(values)
    )


async def soft_delete_car(car_id: int) -> int:
    return await _execute(
        f"UPDATE cars SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_CAR}", (car_id,)
    )


async def update_car_photo(car_id: int, photo_url: Optional[str]) -> int:
    return await _execute(
        f"UPDATE cars SET photo_url = ? WHERE id = ? AND {_LIVE_CAR}", (photo_url, car_id)
    )


# ── Journal ──────────────────────────────────────────────────────────────────

_JOURNAL_FIELDS = ('type', 'title', 'body')
_LIVE_JOURNAL = "deleted_at IS NULL"


async def get_journals(car_id: int, *, limit: int = 50, offset: int = 0):
    return await _fetchall(
        f"SELECT * FROM journal WHERE car_id = ? AND {_LIVE_JOURNAL} "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (car_id, limit, offset)
    )


async def count_journals(car_id: int) -> int:
    row = await _fetchone(
        f"SELECT COUNT(*) as cnt FROM journal WHERE car_id = ? AND {_LIVE_JOURNAL}",
        (car_id,)
    )
    return row['cnt'] if row else 0


async def get_journal(journal_id: int):
    return await _fetchone(f"SELECT * FROM journal WHERE id = ? AND {_LIVE_JOURNAL}", (journal_id,))


async def create_journal(car_id: int, data: dict) -> int:
    j_type = data.get('type', 'note')
    if j_type not in VALID_JOURNAL_TYPES:
        raise ValueError(f"Invalid journal type: {j_type}")
    return await _insert(
        "INSERT INTO journal (car_id, type, title, body) VALUES (?, ?, ?, ?)",
        (car_id, j_type, data.get('title', ''), data.get('body'))
    )


async def update_journal(journal_id: int, data: dict) -> int:
    fields = {k: v for k, v in data.items() if k in _JOURNAL_FIELDS}
    if not fields:
        return 0
    if 'type' in fields and fields['type'] not in VALID_JOURNAL_TYPES:
        raise ValueError(f"Invalid journal type: {fields['type']}")
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [journal_id]
    return await _execute(
        f"UPDATE journal SET {set_clause} WHERE id = ? AND {_LIVE_JOURNAL}", tuple(values)
    )


async def soft_delete_journal(journal_id: int) -> int:
    return await _execute(
        f"UPDATE journal SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_JOURNAL}",
        (journal_id,)
    )


# ── Views (photo-pin backgrounds) ───────────────────────────────────────────

_LIVE_VIEW = "deleted_at IS NULL"


async def get_views(car_id: int):
    return await _fetchall(
        f"SELECT * FROM views WHERE car_id = ? AND {_LIVE_VIEW} ORDER BY angle",
        (car_id,)
    )


async def get_view(view_id: int):
    return await _fetchone(f"SELECT * FROM views WHERE id = ? AND {_LIVE_VIEW}", (view_id,))


async def get_view_by_angle(car_id: int, angle: str):
    return await _fetchone(
        f"SELECT * FROM views WHERE car_id = ? AND angle = ? AND {_LIVE_VIEW}",
        (car_id, angle)
    )


async def create_view(car_id: int, name: str, angle: str, file_path: str) -> int:
    if angle not in VALID_VIEW_ANGLES:
        raise ValueError(f"Invalid view angle: {angle}")
    return await _insert(
        "INSERT INTO views (car_id, name, angle, file_path) VALUES (?, ?, ?, ?)",
        (car_id, name, angle, file_path)
    )


async def soft_delete_view(view_id: int) -> int:
    return await _execute(
        f"UPDATE views SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_VIEW}",
        (view_id,)
    )


# ── Photo Pins ───────────────────────────────────────────────────────────────

_PHOTOPIN_FIELDS = ('label', 'notes', 'status', 'x_pct', 'y_pct', 'ai_summary')
_LIVE_PIN = "deleted_at IS NULL"


async def get_photopins(view_id: int):
    return await _fetchall(
        f"SELECT * FROM photopins WHERE view_id = ? AND {_LIVE_PIN} ORDER BY id ASC",
        (view_id,)
    )


async def get_photopin(photopin_id: int):
    return await _fetchone(f"SELECT * FROM photopins WHERE id = ? AND {_LIVE_PIN}", (photopin_id,))


async def create_photopin(view_id: int, data: dict) -> int:
    return await _insert(
        "INSERT INTO photopins (view_id, label, notes, x_pct, y_pct) VALUES (?, ?, ?, ?, ?)",
        (view_id, data.get('label', ''), data.get('notes'), data.get('x_pct'), data.get('y_pct'))
    )


async def update_photopin(photopin_id: int, data: dict) -> int:
    fields = {k: v for k, v in data.items() if k in _PHOTOPIN_FIELDS}
    if not fields:
        return 0
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [photopin_id]
    return await _execute(
        f"UPDATE photopins SET {set_clause} WHERE id = ? AND {_LIVE_PIN}", tuple(values)
    )


async def soft_delete_photopin(photopin_id: int) -> int:
    return await _execute(
        f"UPDATE photopins SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_PIN}",
        (photopin_id,)
    )


# ── Parts ────────────────────────────────────────────────────────────────────

_PART_FIELDS = ('name', 'part_number', 'supplier', 'url', 'price',
                'quantity', 'category', 'status', 'notes', 'pin_id')
_LIVE_PART = "deleted_at IS NULL"


async def get_parts(car_id: int, *, limit: int = 100, offset: int = 0):
    return await _fetchall(
        f"SELECT * FROM parts WHERE car_id = ? AND {_LIVE_PART} "
        f"ORDER BY category, created_at DESC LIMIT ? OFFSET ?",
        (car_id, limit, offset)
    )


async def count_parts(car_id: int) -> int:
    row = await _fetchone(
        f"SELECT COUNT(*) as cnt FROM parts WHERE car_id = ? AND {_LIVE_PART}",
        (car_id,)
    )
    return row['cnt'] if row else 0


async def get_part(part_id: int):
    return await _fetchone(f"SELECT * FROM parts WHERE id = ? AND {_LIVE_PART}", (part_id,))


async def create_part(car_id: int, data: dict) -> int:
    if 'category' in data and data['category'] not in VALID_PART_CATEGORIES:
        raise ValueError(f"Invalid part category: {data['category']}")
    if 'status' in data and data['status'] not in VALID_PART_STATUSES:
        raise ValueError(f"Invalid part status: {data['status']}")
    data_cols = [f for f in _PART_FIELDS if f in data and data[f] is not None]
    cols = ['car_id'] + data_cols
    placeholders = ', '.join('?' for _ in cols)
    values = [car_id] + [data[f] for f in data_cols]
    return await _insert(
        f"INSERT INTO parts ({', '.join(cols)}) VALUES ({placeholders})", tuple(values)
    )


async def update_part(part_id: int, data: dict) -> int:
    fields = {k: v for k, v in data.items() if k in _PART_FIELDS}
    if not fields:
        return 0
    if 'category' in fields and fields['category'] not in VALID_PART_CATEGORIES:
        raise ValueError(f"Invalid part category: {fields['category']}")
    if 'status' in fields and fields['status'] not in VALID_PART_STATUSES:
        raise ValueError(f"Invalid part status: {fields['status']}")
    set_clause = ', '.join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [part_id]
    return await _execute(
        f"UPDATE parts SET {set_clause} WHERE id = ? AND {_LIVE_PART}", tuple(values)
    )


async def soft_delete_part(part_id: int) -> int:
    return await _execute(
        f"UPDATE parts SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_PART}",
        (part_id,)
    )


# ── Manuals ──────────────────────────────────────────────────────────────────

_LIVE_MANUAL = "deleted_at IS NULL"


async def get_manuals(car_id: int):
    return await _fetchall(
        f"SELECT * FROM manuals WHERE car_id = ? AND {_LIVE_MANUAL} ORDER BY created_at DESC",
        (car_id,)
    )


async def get_manual(manual_id: int):
    return await _fetchone(f"SELECT * FROM manuals WHERE id = ? AND {_LIVE_MANUAL}", (manual_id,))


async def create_manual(car_id: int, title: str, file_path: str, category: str = 'manual') -> int:
    if category not in VALID_MANUAL_CATEGORIES:
        raise ValueError(f"Invalid manual category: {category}")
    return await _insert(
        "INSERT INTO manuals (car_id, title, file_path, category) VALUES (?, ?, ?, ?)",
        (car_id, title, file_path, category)
    )


async def soft_delete_manual(manual_id: int) -> int:
    return await _execute(
        f"UPDATE manuals SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_MANUAL}",
        (manual_id,)
    )
