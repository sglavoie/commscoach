"""SQLite connection, schema v2 DDL, and idempotent init.

Schema invariants enforced at the DB level:

* ``people.handle`` is case-insensitive UNIQUE via an expression index on
  ``LOWER(handle)``.
* At most one row may have ``is_self = 1`` via a partial UNIQUE index.
* Cascading deletes: removing an entry removes its tag links, people links,
  attached feedback, and review-entry links. Removing a review removes its
  review-entry links. Removing a person removes their entry links.
* Stored review scopes currently support only ``scope_kind = 'latest'``.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .. import paths
from .errors import DBError

SCHEMA_VERSION = 2

EXPECTED_TABLES = frozenset(
    {
        "_meta",
        "people",
        "entries",
        "tags",
        "entry_tags",
        "entry_people",
        "feedback",
        "reviews",
        "review_entries",
    }
)

SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    name TEXT,
    is_self INTEGER NOT NULL DEFAULT 0 CHECK (is_self IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_handle_lower
    ON people (LOWER(handle));

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_one_self
    ON people (is_self) WHERE is_self = 1;

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    source TEXT,
    occurred_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_occurred_at ON entries (occurred_at);
CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries (created_at);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_tags_tag_id ON entry_tags (tag_id);

CREATE TABLE IF NOT EXISTS entry_people (
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_people_person_id
    ON entry_people (person_id);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_entry_id ON feedback (entry_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback (created_at);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('latest')),
    scope_limit INTEGER CHECK (scope_limit IS NULL OR scope_limit >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_created_at
    ON reviews (created_at, id);

CREATE TABLE IF NOT EXISTS review_entries (
    review_id INTEGER NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (review_id, entry_id),
    UNIQUE (review_id, position)
);

CREATE INDEX IF NOT EXISTS idx_review_entries_entry_id
    ON review_entries (entry_id);

CREATE INDEX IF NOT EXISTS idx_review_entries_review_position
    ON review_entries (review_id, position);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with row factory and foreign keys enabled.

    ``foreign_keys`` is a per-connection PRAGMA in SQLite; it must be enabled
    every time a new connection is opened, not just at schema creation time.
    """
    if db_path is None:
        db_path = paths.resolve_db_path()
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str | None = None) -> Path:
    """Create the data dir, apply schema v2, and chmod the DB file.

    Idempotent: re-running ``init_db`` on an existing DB leaves data intact and
    reaffirms the DB parent directory at ``0o700`` and the DB file at ``0o600``.
    Returns the resolved DB path.
    """
    resolved = Path(db_path).expanduser() if db_path is not None else paths.resolve_db_path()
    paths.ensure_data_dir(resolved)
    with closing(connect(resolved)) as conn:
        _apply_schema(conn)
    paths.secure_file(resolved)
    return resolved


def validate_schema(conn: sqlite3.Connection) -> None:
    """Raise ``DBError`` unless ``conn`` points at the expected initialized DB."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in rows}
        version = get_schema_version(conn) if "_meta" in tables else None
    except (sqlite3.Error, ValueError) as exc:
        raise DBError("run 'commscoach init' first") from exc
    if version != SCHEMA_VERSION or not EXPECTED_TABLES.issubset(tables):
        raise DBError("run 'commscoach init' first")


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Return the integer schema version stamped in ``_meta``, or ``None``."""
    cur = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    if row is None:
        return None
    return int(row[0])


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Apply schema v2 idempotently, migrating v1 databases in place."""
    conn.executescript(SCHEMA_V1_SQL)
    version = get_schema_version(conn)
    if version not in (None, 1, SCHEMA_VERSION):
        raise DBError(f"unsupported schema version {version}")
    conn.executescript(SCHEMA_V2_SQL)
    _set_schema_version(conn, SCHEMA_VERSION)
    conn.commit()
