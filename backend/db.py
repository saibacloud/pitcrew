# Database
# SQLite schema + async CRUD via aiosqlite
# Single shared connection, WAL mode, foreign keys enforced

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite

from backend import fitment

log = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    'PITCREW_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'pitcrew.db')
)

# ── Allowed enum values ──────────────────────────────────────────────────────

VALID_JOURNAL_TYPES = {'research', 'note', 'converse', 'docsearch', 'photo', 'service'}
VALID_PART_STATUSES = {'wishlist', 'ordered', 'received', 'installed'}
VALID_PART_CATEGORIES = {'Mechanical', 'Electrical', 'Body', 'Interior', 'Consumables'}
VALID_PART_SOURCES = {'owner', 'agent'}
VALID_PART_ORIGINS = {'oem', 'oe_supplier', 'aftermarket'}
VALID_MANUAL_CATEGORIES = {'manual', 'reference', 'other'}
VALID_MANUAL_TIERS = {'oem', 'catalogue', 'other'}
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

CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'note'
                CHECK(type IN ('research', 'note', 'converse', 'docsearch', 'photo', 'service')),
    title       TEXT NOT NULL,
    body        TEXT,
    photo_url   TEXT,
    odometer    INTEGER,
    search_suggestions TEXT,
    agent_key_id INTEGER,
    source_ids  TEXT,
    deleted_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
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
    search_suggestions TEXT,
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
    source      TEXT NOT NULL DEFAULT 'owner' CHECK(source IN ('owner', 'agent')),
    confidence  TEXT CHECK(confidence IN ('verified', 'conditional', 'unverified')),
    open_fields TEXT,
    fitment_json TEXT,
    needs_recheck INTEGER NOT NULL DEFAULT 0,
    override_at TEXT,
    brand       TEXT,
    origin      TEXT CHECK(origin IN ('oem', 'oe_supplier', 'aftermarket')),
    superseded_by TEXT,
    agent_key_id INTEGER,
    deleted_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    category    TEXT DEFAULT 'manual'
                CHECK(category IN ('manual', 'reference', 'other')),
    tier        TEXT NOT NULL DEFAULT 'other' CHECK(tier IN ('oem', 'catalogue', 'other')),
    deleted_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id_hash     TEXT PRIMARY KEY,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Vehicle identity used by the fitment gate. Changes supersede rows, never overwrite them.
CREATE TABLE IF NOT EXISTS build_fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id        INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    field         TEXT NOT NULL,
    value         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'owner'
                  CHECK(source IN ('owner', 'data_plate', 'catalogue', 'document')),
    source_id     INTEGER,
    agent_key_id  INTEGER,
    quote         TEXT,
    confirmed_at  TEXT,
    superseded_at TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS build_fields_current
    ON build_fields(car_id, field) WHERE superseded_at IS NULL;

-- Credentials for external mechanic agents. Only the SHA-256 of each token is stored.
CREATE TABLE IF NOT EXISTS agent_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    revoked_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Evidence pitcrew captured itself: pages it fetched, or manual page text
CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id         INTEGER REFERENCES cars(id) ON DELETE CASCADE,
    agent_key_id   INTEGER REFERENCES agent_keys(id),
    kind           TEXT NOT NULL CHECK(kind IN ('web_page', 'manual_page')),
    url            TEXT,
    domain         TEXT,
    title          TEXT,
    tier           TEXT NOT NULL CHECK(tier IN ('catalogue', 'oem', 'other')),
    extracted_by   TEXT NOT NULL CHECK(extracted_by IN ('text', 'ocr')),
    manual_id      INTEGER REFERENCES manuals(id),
    page           INTEGER,
    content        TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    deleted_at     TEXT,
    fetched_at     TEXT DEFAULT (datetime('now'))
);

-- An agent's fitment claim for a part, bound to one excerpt of one source
CREATE TABLE IF NOT EXISTS fitment_claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id       INTEGER REFERENCES parts(id) ON DELETE CASCADE,
    agent_key_id  INTEGER REFERENCES agent_keys(id),
    part_number   TEXT NOT NULL,
    claimed       TEXT NOT NULL CHECK(claimed IN ('verified', 'conditional', 'unverified')),
    depends_on    TEXT NOT NULL,
    applies_when  TEXT NOT NULL,
    source_id     INTEGER REFERENCES sources(id),
    excerpt       TEXT NOT NULL,
    superseded_at TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Blind second-agent checks of a claim. Stale once a build field it relied on changes.
CREATE TABLE IF NOT EXISTS fitment_verifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id     INTEGER REFERENCES fitment_claims(id) ON DELETE CASCADE,
    agent_key_id INTEGER REFERENCES agent_keys(id),
    verdict      TEXT NOT NULL CHECK(verdict IN ('explicit_fit', 'ambiguous', 'no_fit')),
    quote        TEXT NOT NULL,
    stale_at     TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

-- Replay store for agent retries carrying an Idempotency-Key
CREATE TABLE IF NOT EXISTS agent_requests (
    agent_key_id  INTEGER NOT NULL,
    idem_key      TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response      TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (agent_key_id, idem_key)
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
        "ALTER TABLE journal ADD COLUMN odometer INTEGER",
        "ALTER TABLE journal ADD COLUMN search_suggestions TEXT",
        "ALTER TABLE photopins ADD COLUMN search_suggestions TEXT",
        "ALTER TABLE parts ADD COLUMN source TEXT NOT NULL DEFAULT 'owner' CHECK(source IN ('owner', 'agent'))",
        "ALTER TABLE parts ADD COLUMN confidence TEXT CHECK(confidence IN ('verified', 'conditional', 'unverified'))",
        "ALTER TABLE parts ADD COLUMN open_fields TEXT",
        "ALTER TABLE parts ADD COLUMN fitment_json TEXT",
        "ALTER TABLE parts ADD COLUMN needs_recheck INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE parts ADD COLUMN override_at TEXT",
        "ALTER TABLE parts ADD COLUMN brand TEXT",
        "ALTER TABLE parts ADD COLUMN origin TEXT CHECK(origin IN ('oem', 'oe_supplier', 'aftermarket'))",
        "ALTER TABLE parts ADD COLUMN superseded_by TEXT",
        "ALTER TABLE parts ADD COLUMN agent_key_id INTEGER",
        "ALTER TABLE journal ADD COLUMN agent_key_id INTEGER",
        "ALTER TABLE journal ADD COLUMN source_ids TEXT",
        "ALTER TABLE manuals ADD COLUMN tier TEXT NOT NULL DEFAULT 'other' CHECK(tier IN ('oem', 'catalogue', 'other'))",
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

    # Add photo_url column to journal (may already exist on fresh installs)
    try:
        await db.execute("ALTER TABLE journal ADD COLUMN photo_url TEXT")
        await db.commit()
    except Exception:
        pass

    # Legacy schema: cars.options was NOT NULL (renamed from nickname) — rebuild without it
    async with db.execute("PRAGMA table_info(cars)") as cur:
        car_cols = {row[1]: row for row in await cur.fetchall()}
    if car_cols.get('options') and car_cols['options'][3]:  # notnull flag
        # FK enforcement must be off during the rebuild — DROP TABLE cars would
        # otherwise cascade-delete every child row (journal, parts, manuals, ...)
        await db.commit()
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.executescript("""
            CREATE TABLE cars_new (
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
            INSERT INTO cars_new (id, year, make, model, trim, options, engine,
                                  color, vin, notes, photo_url, deleted_at, created_at)
                SELECT id, year, make, model, trim, options, engine,
                       color, vin, notes, photo_url, deleted_at, created_at FROM cars;
            DROP TABLE cars;
            ALTER TABLE cars_new RENAME TO cars;
        """)
        await db.commit()
        await db.execute("PRAGMA foreign_keys=ON")
        log.info("Migrated cars table: dropped NOT NULL on options")

    # Legacy parts default was 'needed' — not a valid status anymore
    await db.execute("UPDATE parts SET status = 'wishlist' WHERE status = 'needed'")
    await db.commit()

    # Expand journal type CHECK to include 'photo' and 'service'
    try:
        await db.execute(
            "INSERT INTO journal (car_id, type, title) VALUES (NULL, 'service', '__check_test__')"
        )
        await db.execute("DELETE FROM journal WHERE title = '__check_test__'")
        await db.commit()
    except Exception:
        # CHECK constraint too narrow — recreate table
        await db.executescript("""
            CREATE TABLE journal_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                car_id      INTEGER REFERENCES cars(id) ON DELETE CASCADE,
                type        TEXT NOT NULL DEFAULT 'note'
                            CHECK(type IN ('research', 'note', 'converse', 'docsearch', 'photo', 'service')),
                title       TEXT NOT NULL,
                body        TEXT,
                photo_url   TEXT,
                odometer    INTEGER,
                search_suggestions TEXT,
                agent_key_id INTEGER,
                source_ids  TEXT,
                deleted_at  TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO journal_new (id, car_id, type, title, body, photo_url, odometer,
                                     search_suggestions, agent_key_id, source_ids,
                                     deleted_at, created_at)
                SELECT id, car_id, type, title, body, photo_url, odometer,
                       search_suggestions, agent_key_id, source_ids,
                       deleted_at, created_at FROM journal;
            DROP TABLE journal;
            ALTER TABLE journal_new RENAME TO journal;
        """)
        await db.commit()
        log.info("Migrated journal table to expand type CHECK")

    # One-time cleanup: soft-delete pins whose parent view was hard-deleted
    # before views had soft deletes (their photos are gone from disk)
    await db.execute(
        "UPDATE photopins SET deleted_at = datetime('now') "
        "WHERE deleted_at IS NULL AND view_id NOT IN (SELECT id FROM views)"
    )
    await db.commit()

    # Tables from early schemas that nothing reads or writes. Dropped only when
    # empty, so any rows that do exist are left for a manual look.
    for table in ('messages', 'notes', 'searches'):
        async with db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ) as cur:
            if not await cur.fetchone():
                continue
        async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
            (rows,) = await cur.fetchone()
        if rows:
            log.warning("Unused table %s has %d rows; leaving it in place", table, rows)
        else:
            await db.execute(f"DROP TABLE {table}")
            await db.commit()
            log.info("Dropped unused empty table %s", table)

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


# Every write holds this lock: the connection is shared, so a commit from one
# coroutine would otherwise commit another coroutine's half-finished transaction.
_write_lock = asyncio.Lock()


async def _execute(sql: str, params: tuple = ()) -> int:
    """Execute a write query. Returns rowcount."""
    db = await get_db()
    async with _write_lock:
        cur = await db.execute(sql, params)
        await db.commit()
    return cur.rowcount


async def _insert(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT. Returns lastrowid."""
    db = await get_db()
    async with _write_lock:
        cur = await db.execute(sql, params)
        await db.commit()
    return cur.lastrowid


@asynccontextmanager
async def transaction():
    """Run several writes atomically. Use the yielded connection directly, not _execute/_insert."""
    db = await get_db()
    async with _write_lock:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        await db.commit()


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

_JOURNAL_FIELDS = ('type', 'title', 'body', 'photo_url', 'odometer', 'search_suggestions')
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
        "INSERT INTO journal (car_id, type, title, body, photo_url, odometer, search_suggestions, "
        "agent_key_id, source_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (car_id, j_type, data.get('title', ''), data.get('body'), data.get('photo_url'),
         data.get('odometer'), data.get('search_suggestions'),
         data.get('agent_key_id'), data.get('source_ids'))
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
    # Cascade: soft-delete pins belonging to this view
    await _execute(
        f"UPDATE photopins SET deleted_at = datetime('now') WHERE view_id = ? AND {_LIVE_PIN}",
        (view_id,)
    )
    return await _execute(
        f"UPDATE views SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_VIEW}",
        (view_id,)
    )


# ── Photo Pins ───────────────────────────────────────────────────────────────

_PHOTOPIN_FIELDS = ('label', 'notes', 'status', 'x_pct', 'y_pct', 'ai_summary', 'search_suggestions')
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
# Columns an agent may set on its parts; status, confidence and fitment are server-owned
AGENT_PART_FIELDS = ('name', 'supplier', 'url', 'price', 'quantity', 'category', 'notes',
                      'brand', 'origin', 'superseded_by')


async def get_parts(car_id: int, *, limit: int = 100, offset: int = 0):
    return await _fetchall(
        f"SELECT * FROM parts WHERE car_id = ? AND {_LIVE_PART} "
        f"ORDER BY category, created_at DESC LIMIT ? OFFSET ?",
        (car_id, limit, offset)
    )


async def get_installed_parts(car_id: int, limit: int = 40):
    return await _fetchall(
        f"SELECT name, part_number FROM parts WHERE car_id = ? AND status = 'installed' "
        f"AND {_LIVE_PART} ORDER BY created_at DESC LIMIT ?",
        (car_id, limit)
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


class FitmentGateError(Exception):
    """An agent-sourced part cannot leave the wishlist without verified fitment or an owner override."""


async def update_part(part_id: int, data: dict, *, override_fitment: bool = False,
                      allowed: tuple = _PART_FIELDS) -> int:
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return 0
    if 'category' in fields and fields['category'] not in VALID_PART_CATEGORIES:
        raise ValueError(f"Invalid part category: {fields['category']}")
    if 'origin' in fields and fields['origin'] not in VALID_PART_ORIGINS:
        raise ValueError(f"Invalid part origin: {fields['origin']}")
    if 'status' in fields and fields['status'] not in VALID_PART_STATUSES:
        raise ValueError(f"Invalid part status: {fields['status']}")

    async with transaction() as db:
        async with db.execute(f"SELECT * FROM parts WHERE id = ? AND {_LIVE_PART}", (part_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0
        part = dict(row)
        stamp_override = False
        if part['source'] == 'agent':
            # Editing the number invalidates whatever evidence verified the old one
            new_pn = fields.get('part_number', part['part_number'])
            if fitment.normalise_part_number(new_pn) != fitment.normalise_part_number(part['part_number']):
                fields.update(confidence='unverified', fitment_json=None, needs_recheck=0)
                await db.execute(
                    "UPDATE fitment_claims SET superseded_at = datetime('now') "
                    "WHERE part_id = ? AND superseded_at IS NULL", (part_id,)
                )
            confidence = fields.get('confidence', part['confidence'])
            leaving_wishlist = part['status'] == 'wishlist' and fields.get('status', 'wishlist') != 'wishlist'
            if leaving_wishlist and confidence != 'verified':
                if not override_fitment:
                    raise FitmentGateError(f"Fitment is {confidence or 'unverified'}, not verified")
                stamp_override = True
        set_clause = ', '.join(f"{k} = ?" for k in fields)
        if stamp_override:
            set_clause += ", override_at = datetime('now')"
        values = list(fields.values()) + [part_id]
        cur = await db.execute(
            f"UPDATE parts SET {set_clause} WHERE id = ? AND {_LIVE_PART}", tuple(values)
        )
        return cur.rowcount


async def soft_delete_part(part_id: int) -> int:
    return await _execute(
        f"UPDATE parts SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_PART}",
        (part_id,)
    )


# ── Build sheet ──────────────────────────────────────────────────────────────

_CURRENT_BUILD = "superseded_at IS NULL"


async def get_build_rows(car_id: int) -> dict[str, dict]:
    rows = await _fetchall(
        f"SELECT field, value, source, source_id, agent_key_id, confirmed_at, created_at FROM build_fields "
        f"WHERE car_id = ? AND {_CURRENT_BUILD}",
        (car_id,)
    )
    return {r['field']: r for r in rows if r['field'] in fitment.BUILD_FIELDS}


async def get_build(car_id: int) -> dict:
    """The build sheet the gate sees: owner-confirmed values only, decoded, unset fields absent."""
    return {f: fitment.decode_build_value(f, r['value'])
            for f, r in (await get_build_rows(car_id)).items() if r['confirmed_at']}


async def _flag_recheck(db, car_id: int, field: str) -> None:
    """Agent parts whose fitment rested on this field drop from verified and need a recheck."""
    async with db.execute(
        f"SELECT id, fitment_json FROM parts WHERE car_id = ? AND source = 'agent' "
        f"AND fitment_json IS NOT NULL AND {_LIVE_PART}",
        (car_id,)
    ) as cur:
        rows = await cur.fetchall()
    ids = [r['id'] for r in rows if field in json.loads(r['fitment_json'])]
    for part_id in ids:
        await db.execute(
            "UPDATE parts SET needs_recheck = 1, confidence = CASE confidence "
            "WHEN 'verified' THEN 'conditional' ELSE confidence END WHERE id = ?",
            (part_id,)
        )
        # The verifier judged the old build sheet, so its verdict no longer counts
        await db.execute(
            "UPDATE fitment_verifications SET stale_at = datetime('now') WHERE stale_at IS NULL "
            "AND claim_id IN (SELECT id FROM fitment_claims WHERE part_id = ?)",
            (part_id,)
        )
        await _regate(db, part_id, clear_recheck=False)


async def set_build_field(car_id: int, field: str, value, *, source: str = 'owner',
                          source_id: Optional[int] = None, agent_key_id: Optional[int] = None,
                          quote: Optional[str] = None) -> None:
    """Owner writes are confirmed at once; agent writes wait for the owner to confirm them."""
    stored = fitment.encode_build_value(field, value)
    if source not in fitment.BUILD_SOURCES:
        raise ValueError(f"Invalid build field source: {source}")
    confirmed = agent_key_id is None
    async with transaction() as db:
        async with db.execute(
            f"SELECT value, source, agent_key_id, confirmed_at FROM build_fields "
            f"WHERE car_id = ? AND field = ? AND {_CURRENT_BUILD}",
            (car_id, field)
        ) as cur:
            current = await cur.fetchone()
        if current and current['value'] == stored and current['source'] == source \
                and bool(current['confirmed_at']) == confirmed:
            return
        if current:
            await db.execute(
                f"UPDATE build_fields SET superseded_at = datetime('now') "
                f"WHERE car_id = ? AND field = ? AND {_CURRENT_BUILD}",
                (car_id, field)
            )
        await db.execute(
            "INSERT INTO build_fields (car_id, field, value, source, source_id, agent_key_id, quote, "
            "confirmed_at) VALUES (?, ?, ?, ?, ?, ?, ?, " + ("datetime('now')" if confirmed else "NULL") + ")",
            (car_id, field, stored, source, source_id, agent_key_id, quote)
        )
        # The gate's view changes when a confirmed value goes, or a new value is confirmed
        if (current and current['confirmed_at']) or confirmed:
            await _flag_recheck(db, car_id, field)


async def confirm_build_field(car_id: int, field: str) -> int:
    async with transaction() as db:
        cur = await db.execute(
            f"UPDATE build_fields SET confirmed_at = datetime('now') "
            f"WHERE car_id = ? AND field = ? AND confirmed_at IS NULL AND {_CURRENT_BUILD}",
            (car_id, field)
        )
        if cur.rowcount:
            await _flag_recheck(db, car_id, field)
        return cur.rowcount


async def clear_build_field(car_id: int, field: str) -> int:
    if field not in fitment.BUILD_FIELDS:
        raise ValueError(f"Unknown build field: {field}")
    async with transaction() as db:
        cur = await db.execute(
            f"UPDATE build_fields SET superseded_at = datetime('now') "
            f"WHERE car_id = ? AND field = ? AND {_CURRENT_BUILD}",
            (car_id, field)
        )
        if cur.rowcount:
            await _flag_recheck(db, car_id, field)
        return cur.rowcount


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


async def update_manual_tier(manual_id: int, tier: str) -> int:
    if tier not in VALID_MANUAL_TIERS:
        raise ValueError(f"Invalid manual tier: {tier}")
    async with transaction() as db:
        cur = await db.execute(
            f"UPDATE manuals SET tier = ? WHERE id = ? AND {_LIVE_MANUAL}", (tier, manual_id)
        )
        # Parts whose evidence is a page of this manual now carry its new tier
        for r in await _fetchall(
            "SELECT DISTINCT c.part_id FROM fitment_claims c JOIN sources s ON s.id = c.source_id "
            "WHERE s.manual_id = ? AND c.superseded_at IS NULL", (manual_id,)
        ):
            await _regate(db, r['part_id'], clear_recheck=False)
        return cur.rowcount


async def soft_delete_manual(manual_id: int) -> int:
    return await _execute(
        f"UPDATE manuals SET deleted_at = datetime('now') WHERE id = ? AND {_LIVE_MANUAL}",
        (manual_id,)
    )


# ── Sessions ─────────────────────────────────────────────────────────────────

async def create_session(id_hash: str, ttl_seconds: int) -> None:
    await _execute(
        "INSERT INTO sessions (id_hash, expires_at) VALUES (?, datetime('now', ?))",
        (id_hash, f'+{int(ttl_seconds)} seconds')
    )


async def session_active(id_hash: str) -> bool:
    row = await _fetchone(
        "SELECT 1 FROM sessions WHERE id_hash = ? AND revoked_at IS NULL "
        "AND expires_at > datetime('now')",
        (id_hash,)
    )
    return row is not None


async def revoke_session(id_hash: str) -> int:
    return await _execute(
        "UPDATE sessions SET revoked_at = datetime('now') WHERE id_hash = ? AND revoked_at IS NULL",
        (id_hash,)
    )


async def prune_sessions() -> int:
    # Expired rows can never authenticate again, so nothing depends on them
    return await _execute("DELETE FROM sessions WHERE expires_at < datetime('now', '-30 days')")


# ── Agents ───────────────────────────────────────────────────────────────────

class NoFitError(Exception):
    """The evidence shows the part does not fit this car."""

    def __init__(self, result):
        super().__init__('Evidence shows this part does not fit: ' + ', '.join(result.no_fit))
        self.result = result


async def create_agent_key(label: str, token_hash: str) -> int:
    return await _insert("INSERT INTO agent_keys (label, token_hash) VALUES (?, ?)", (label, token_hash))


async def get_agent_key(token_hash: str):
    return await _fetchone(
        "SELECT id, label FROM agent_keys WHERE token_hash = ? AND revoked_at IS NULL", (token_hash,)
    )


async def list_agent_keys():
    return await _fetchall("SELECT id, label, revoked_at, created_at FROM agent_keys ORDER BY id")


async def revoke_agent_key(key_id: int) -> int:
    return await _execute(
        "UPDATE agent_keys SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL", (key_id,)
    )


async def create_source(car_id: int, agent_key_id: int, data: dict) -> int:
    return await _insert(
        "INSERT INTO sources (car_id, agent_key_id, kind, url, domain, title, tier, extracted_by, "
        "manual_id, page, content, content_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (car_id, agent_key_id, data['kind'], data.get('url'), data.get('domain'), data.get('title'),
         data['tier'], data['extracted_by'], data.get('manual_id'), data.get('page'),
         data['content'], data['content_sha256'])
    )


async def get_web_sources(car_id: int, url: str):
    return await _fetchall(
        "SELECT * FROM sources WHERE car_id = ? AND kind = 'web_page' AND url = ? AND deleted_at IS NULL",
        (car_id, url)
    )


async def get_source(source_id: int):
    return await _fetchone("SELECT * FROM sources WHERE id = ? AND deleted_at IS NULL", (source_id,))


async def get_current_claim(part_id: int):
    return await _fetchone(
        "SELECT * FROM fitment_claims WHERE part_id = ? AND superseded_at IS NULL", (part_id,)
    )


_VERDICT_CAUTION = {'no_fit': 0, 'ambiguous': 1, 'explicit_fit': 2}


async def _current_tier(source: dict) -> str:
    """Tier as configured now, so a downgraded manual or catalogue domain stops verifying."""
    from backend.fetch import domain_tier
    if source['kind'] == 'web_page':
        return domain_tier(source['domain'])
    manual = await get_manual(source['manual_id'])
    return manual['tier'] if manual else 'other'


async def _regate(db, part_id: int, *, clear_recheck: bool = True):
    """Recompute a part's confidence from its current claim; call inside a transaction."""
    part = await _fetchone("SELECT * FROM parts WHERE id = ?", (part_id,))
    claim = await get_current_claim(part_id)
    if not part or not claim:
        return None
    source = await get_source(claim['source_id'])
    active_keys = {k['id'] for k in await _fetchall("SELECT id FROM agent_keys WHERE revoked_at IS NULL")}
    live = [v for v in await _fetchall(
        "SELECT verdict, quote, agent_key_id FROM fitment_verifications "
        "WHERE claim_id = ? AND stale_at IS NULL ORDER BY id",
        (claim['id'],)
    ) if v['agent_key_id'] in active_keys]
    # Any live doubt outweighs any number of positive verdicts
    verification = min(live, key=lambda v: _VERDICT_CAUTION[v['verdict']]) if live else None
    claim_d = {
        'part_number': claim['part_number'],
        'claimed': claim['claimed'] if claim['agent_key_id'] in active_keys else 'unverified',
        'agent_id': claim['agent_key_id'],
        'depends_on': json.loads(claim['depends_on']),
        'applies_when': json.loads(claim['applies_when']),
    }
    evidence = {
        'kind': source['kind'], 'tier': await _current_tier(source), 'url': source['url'],
        'extracted_by': source['extracted_by'], 'excerpt': claim['excerpt'],
    } if source else None
    verif_d = {
        'verdict': verification['verdict'], 'quote': verification['quote'],
        'agent_id': verification['agent_key_id'],
    } if verification else None
    build = await get_build(part['car_id'])
    result = fitment.gate(claim_d, build, evidence, verif_d)
    snapshot = {f: build.get(f) for f in fitment.fields_used(claim_d)}
    await db.execute(
        "UPDATE parts SET confidence = ?, open_fields = ?, fitment_json = ?"
        + (", needs_recheck = 0" if clear_recheck else "") + " WHERE id = ?",
        (result.level, json.dumps(result.open_fields), json.dumps(snapshot), part_id)
    )
    return result


async def regate_all() -> None:
    """Re-run the gate for every claimed part, e.g. after catalogue domains change at startup."""
    rows = await _fetchall(
        f"SELECT DISTINCT p.id FROM parts p JOIN fitment_claims c ON c.part_id = p.id "
        f"WHERE c.superseded_at IS NULL AND p.{_LIVE_PART}"
    )
    async with transaction() as db:
        for r in rows:
            await _regate(db, r['id'], clear_recheck=False)


async def _insert_claim(db, part_id: int, agent_key_id: int, claim: dict) -> None:
    await db.execute(
        "UPDATE fitment_claims SET superseded_at = datetime('now') WHERE part_id = ? AND superseded_at IS NULL",
        (part_id,)
    )
    await db.execute(
        "INSERT INTO fitment_claims (part_id, agent_key_id, part_number, claimed, depends_on, "
        "applies_when, source_id, excerpt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (part_id, agent_key_id, claim['part_number'], claim['claimed'], json.dumps(claim['depends_on']),
         json.dumps(claim['applies_when']), claim['source_id'], claim['excerpt'])
    )


async def create_agent_part(car_id: int, agent_key_id: int, data: dict, claim: dict):
    """Insert an agent part on the wishlist with its claim. Rolls back if the evidence shows no fit."""
    cols = [f for f in AGENT_PART_FIELDS if data.get(f) is not None]
    values = [data[f] for f in cols]
    async with transaction() as db:
        cur = await db.execute(
            f"INSERT INTO parts (car_id, source, status, agent_key_id, part_number, {', '.join(cols)}) "
            f"VALUES (?, 'agent', 'wishlist', ?, ?, {', '.join('?' for _ in cols)})",
            (car_id, agent_key_id, claim['part_number'], *values)
        )
        part_id = cur.lastrowid
        await _insert_claim(db, part_id, agent_key_id, claim)
        result = await _regate(db, part_id)
        if result.no_fit:
            raise NoFitError(result)
    return part_id, result


async def replace_claim(part_id: int, agent_key_id: int, claim: dict):
    """New claim for an existing agent part; earlier verifications no longer apply."""
    async with transaction() as db:
        part = await _fetchone("SELECT agent_key_id FROM parts WHERE id = ?", (part_id,))
        if part['agent_key_id'] != agent_key_id:
            raise ValueError('Only the agent key that added this part can replace its claim')
        await db.execute("UPDATE parts SET part_number = ? WHERE id = ?", (claim['part_number'], part_id))
        await _insert_claim(db, part_id, agent_key_id, claim)
        result = await _regate(db, part_id)
        if result.no_fit:
            raise NoFitError(result)
    return result


async def add_verification(part_id: int, claim_id: int, agent_key_id: int, verdict: str, quote: str):
    async with transaction() as db:
        claim = await get_current_claim(part_id)
        if not claim:
            raise ValueError('Part has no fitment claim to verify')
        if claim['id'] != claim_id:
            raise ValueError('The claim changed since the verification packet was read; read it again')
        if claim['agent_key_id'] == agent_key_id:
            raise ValueError('A claim must be verified by a different agent key than the one that made it')
        if await _fetchone(
            "SELECT 1 FROM fitment_verifications WHERE claim_id = ? AND agent_key_id = ? AND stale_at IS NULL",
            (claim_id, agent_key_id)
        ):
            raise ValueError('This agent key has already given its verdict on this claim')
        await db.execute(
            "INSERT INTO fitment_verifications (claim_id, agent_key_id, verdict, quote) VALUES (?, ?, ?, ?)",
            (claim['id'], agent_key_id, verdict, quote)
        )
        return await _regate(db, part_id)


async def get_idempotent(agent_key_id: int, idem_key: str):
    return await _fetchone(
        "SELECT request_hash, status_code, response FROM agent_requests WHERE agent_key_id = ? AND idem_key = ?",
        (agent_key_id, idem_key)
    )


async def store_idempotent(agent_key_id: int, idem_key: str, request_hash: str,
                           status_code: int, response: str) -> None:
    await _execute(
        "INSERT OR IGNORE INTO agent_requests (agent_key_id, idem_key, request_hash, status_code, response) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_key_id, idem_key, request_hash, status_code, response)
    )
