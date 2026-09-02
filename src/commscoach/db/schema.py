"""SQLite connection, schema v4 DDL, and idempotent init.

Schema invariants enforced at the DB level:

* ``people.handle`` is case-insensitive UNIQUE via an expression index on
  ``LOWER(handle)``.
* At most one row may have ``is_self = 1`` via a partial UNIQUE index.
* Cascading deletes: removing an entry removes its tag links, people links,
  attached feedback, and review-entry links. Removing a review removes its
  review-entry links and clears goal references. Removing a person removes
  their entry links and note while clearing goal references.
* Stored review scopes are single-axis: ``scope_kind`` is one of ``latest``,
  ``person``, ``tag``, or ``range``, and CHECK constraints keep exactly the
  columns of that one axis populated. ``person`` requires
  ``scope_person_handle`` (the denormalized record of who the review was
  about, so ``person rm`` can null the ``scope_person_id`` link without
  breaking the row), ``tag`` requires ``scope_tag``, and ``range`` requires at
  least one of ``scope_since`` / ``scope_until`` — an unbounded range is just
  ``latest``.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .. import paths
from ..timeparse import TimeParseError, parse_when
from .errors import DBError

SCHEMA_VERSION = 4

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
        "goals",
        "person_notes",
    }
)

# The subset of EXPECTED_TABLES that has existed since schema v1, so it is
# present in every released version. This is what identifies a file as "a
# commscoach database" independently of which version it was stamped at.
_BASELINE_TABLES = frozenset({"_meta", "people", "entries"})

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

# Templated on the table name so the v4 rebuild builds ``reviews_new`` from the
# exact DDL a fresh database gets. Duplicating it would let the two drift.
_REVIEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    scope_kind TEXT NOT NULL
        CHECK (scope_kind IN ('latest', 'person', 'tag', 'range')),
    scope_limit INTEGER CHECK (scope_limit IS NULL OR scope_limit >= 0),
    scope_person_id INTEGER REFERENCES people (id) ON DELETE SET NULL,
    scope_person_handle TEXT,
    scope_tag TEXT,
    scope_since TEXT,
    scope_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((scope_kind = 'person') = (scope_person_handle IS NOT NULL)),
    CHECK ((scope_kind = 'tag') = (scope_tag IS NOT NULL)),
    CHECK (scope_person_id IS NULL OR scope_kind = 'person'),
    CHECK (scope_kind = 'range'
           OR (scope_since IS NULL AND scope_until IS NULL)),
    CHECK (scope_kind <> 'range'
           OR scope_since IS NOT NULL OR scope_until IS NOT NULL)
);
"""

# A table rebuild drops the table's indexes with it, so the rebuild replays
# these one by one; the cumulative DDL below runs the same list as a script.
_REVIEWS_INDEX_STATEMENTS = (
    (
        "CREATE INDEX IF NOT EXISTS idx_reviews_created_at"
        " ON reviews (created_at, id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_reviews_scope"
        " ON reviews (scope_kind, created_at)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_reviews_scope_person_id"
        " ON reviews (scope_person_id)"
    ),
)

SCHEMA_V2_SQL = (
    _REVIEWS_TABLE_SQL.format(table="reviews")
    + "".join(f"\n{statement};\n" for statement in _REVIEWS_INDEX_STATEMENTS)
    + """
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

CREATE INDEX IF NOT EXISTS idx_entries_effective_at
    ON entries (COALESCE(occurred_at, created_at) DESC);
"""
)

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'done', 'dropped')),
    person_id INTEGER REFERENCES people (id) ON DELETE SET NULL,
    review_id INTEGER REFERENCES reviews (id) ON DELETE SET NULL,
    outcome TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((status = 'active') = (closed_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_goals_status
    ON goals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_goals_person_id ON goals (person_id);
CREATE INDEX IF NOT EXISTS idx_goals_review_id ON goals (review_id);

CREATE TABLE IF NOT EXISTS person_notes (
    person_id INTEGER PRIMARY KEY REFERENCES people (id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_OCCURRED_AT_BACKFILL_KEY = "occurred_at_utc_backfill_v1"
_FTS5_META_KEY = "fts5"

_FTS5_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    body,
    content='entries',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_FTS5_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS entries_fts_after_insert
AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_after_delete
AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, body)
    VALUES ('delete', old.id, old.body);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_after_update
AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, body)
    VALUES ('delete', old.id, old.body);
    INSERT INTO entries_fts(rowid, body) VALUES (new.id, new.body);
END;
"""

_FTS5_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS entries_fts_after_insert;
DROP TRIGGER IF EXISTS entries_fts_after_delete;
DROP TRIGGER IF EXISTS entries_fts_after_update;
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with safe sync semantics and foreign keys enabled.

    ``foreign_keys`` is a per-connection PRAGMA in SQLite; it must be enabled
    every time a new connection is opened, not just at schema creation time.
    """
    if db_path is None:
        db_path = paths.resolve_db_path()
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # DELETE leaves exactly one database file at rest. WAL's persistent -wal
    # and -shm files can be replicated independently by whole-file syncers,
    # producing a torn database when those files arrive out of order.
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn


def init_db(db_path: Path | str | None = None) -> Path:
    """Create the data dir, apply schema v4, and chmod the DB file.

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


def validate_backup_source(conn: sqlite3.Connection) -> int:
    """Raise ``DBError`` unless ``conn`` is a commscoach database of ANY version.

    ``validate_schema`` demands the current version because every read and
    write path is written against it. Copying a database is different: the
    online backup API works at the page level and does not care what the
    schema looks like, and the moment a snapshot matters most is precisely
    *before* the ``init`` that migrates it. Validating the post-migration
    schema in ``backup`` therefore made the protective command refuse the
    databases it exists to protect.

    The bar is only "this is recognizably ours": ``_meta`` carries an integer
    ``schema_version`` and the v1 baseline tables are present. Versions newer
    than :data:`SCHEMA_VERSION` are accepted too — refusing to protect data
    written by a later release would recreate the same bug in the other
    direction.

    Returns the stamped schema version.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in rows}
        version = get_schema_version(conn) if "_meta" in tables else None
    except (sqlite3.Error, ValueError) as exc:
        raise DBError("run 'commscoach init' first") from exc
    if version is None or version < 1 or not _BASELINE_TABLES.issubset(tables):
        raise DBError("run 'commscoach init' first")
    return version


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


def _normalize_occurred_at_once(conn: sqlite3.Connection) -> None:
    """Normalize parseable legacy occurrence timestamps once per database."""
    already_done = conn.execute(
        "SELECT 1 FROM _meta WHERE key = ?", (_OCCURRED_AT_BACKFILL_KEY,)
    ).fetchone()
    if already_done is not None:
        return

    has_values = conn.execute(
        "SELECT 1 FROM entries WHERE occurred_at IS NOT NULL LIMIT 1"
    ).fetchone()
    if has_values is not None:
        rows = conn.execute(
            "SELECT id, occurred_at FROM entries WHERE occurred_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                normalized = parse_when(row["occurred_at"])
            except TimeParseError:
                continue
            if normalized != row["occurred_at"]:
                conn.execute(
                    "UPDATE entries SET occurred_at = ? WHERE id = ?",
                    (normalized, row["id"]),
                )

    conn.execute(
        "INSERT INTO _meta (key, value) VALUES (?, '1')",
        (_OCCURRED_AT_BACKFILL_KEY,),
    )


def _reviews_need_scope_columns(conn: sqlite3.Connection) -> bool:
    """Report whether ``reviews`` exists in a pre-v4 shape and must be rebuilt.

    ``PRAGMA table_info`` returns no rows for a table that does not exist, so a
    fresh database answers ``False`` and lets the cumulative v2 DDL create the
    v4 shape outright.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)")}
    return bool(columns) and "scope_tag" not in columns


def _rebuild_reviews_table(conn: sqlite3.Connection) -> None:
    """Copy the pre-v4 ``reviews`` rows into a v4-shaped table and swap it in.

    Every pre-existing row is ``scope_kind = 'latest'``, so the five new scope
    columns stay NULL and every new CHECK holds.
    """
    conn.execute("DROP TABLE IF EXISTS reviews_new")
    conn.execute(_REVIEWS_TABLE_SQL.format(table="reviews_new"))
    conn.execute(
        "INSERT INTO reviews_new"
        " (id, body, scope_kind, scope_limit, created_at, updated_at)"
        " SELECT id, body, scope_kind, scope_limit, created_at, updated_at"
        " FROM reviews"
    )
    conn.execute("DROP TABLE reviews")
    conn.execute("ALTER TABLE reviews_new RENAME TO reviews")
    for statement in _REVIEWS_INDEX_STATEMENTS:
        conn.execute(statement)


def _verify_reviews_rebuild(conn: sqlite3.Connection) -> None:
    """Raise ``DBError`` unless the swapped-in table left the DB consistent."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise DBError(
            "schema v4 migration would leave foreign-key violations"
            f" ({len(violations)} rows); no changes were applied"
        )
    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise DBError(
            f"schema v4 migration failed the integrity check: {integrity[0]};"
            " no changes were applied"
        )


def _migrate_reviews_scope_v4(conn: sqlite3.Connection) -> None:
    """Rebuild a pre-v4 ``reviews`` table so it carries the scope columns.

    ``ALTER TABLE`` cannot widen a CHECK constraint, so this is the table
    rebuild the SQLite documentation prescribes. It is a no-op on a fresh
    database and on an already-migrated one, so ``init_db`` stays idempotent.

    Foreign keys must be off across the swap: with them on, ``DROP TABLE
    reviews`` cascade-deletes every ``review_entries`` row and nulls every
    ``goals.review_id``, silently destroying the coaching loop. ``PRAGMA
    foreign_keys`` is a silent no-op inside a transaction, so any implicit one
    is committed first; the previous setting is restored in a ``finally`` and
    the whole rebuild rolls back as one on any error.
    """
    if not _reviews_need_scope_columns(conn):
        return

    conn.commit()
    (foreign_keys,) = conn.execute("PRAGMA foreign_keys").fetchone()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        try:
            _rebuild_reviews_table(conn)
            _verify_reviews_rebuild(conn)
        except BaseException:
            conn.rollback()
            raise
        conn.execute("COMMIT")
    finally:
        conn.execute(
            "PRAGMA foreign_keys = ON" if foreign_keys else "PRAGMA foreign_keys = OFF"
        )


def _probe_fts5(conn: sqlite3.Connection) -> bool:
    """Create the FTS table when the active SQLite library supports FTS5."""
    try:
        conn.execute(_FTS5_TABLE_SQL)
    except sqlite3.OperationalError:
        return False
    return True


def _rebuild_fts5(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild')")


def _configure_fts5(conn: sqlite3.Connection) -> None:
    """Probe and configure optional FTS5 support for this database."""
    enabled = _probe_fts5(conn)
    if enabled:
        conn.executescript(_FTS5_TRIGGER_SQL)
        entry_count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        indexed_count = conn.execute(
            "SELECT COUNT(*) FROM entries_fts_docsize"
        ).fetchone()[0]
        if indexed_count != entry_count:
            _rebuild_fts5(conn)
    else:
        # A database may move between Python/SQLite builds. Keep writes working
        # when the current build cannot load an FTS table created elsewhere.
        conn.executescript(_FTS5_DROP_TRIGGER_SQL)

    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
        (_FTS5_META_KEY, "1" if enabled else "0"),
    )


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Apply schema v4 idempotently, migrating v1/v2/v3 databases in place."""
    conn.executescript(SCHEMA_V1_SQL)
    version = get_schema_version(conn)
    if version is not None and not 1 <= version <= SCHEMA_VERSION:
        raise DBError(f"unsupported schema version {version}")
    conn.execute("DROP INDEX IF EXISTS idx_entries_occurred_at")
    _normalize_occurred_at_once(conn)
    # Must precede SCHEMA_V2_SQL: that script now creates
    # idx_reviews_scope_person_id, which cannot be built against a pre-v4
    # reviews table.
    _migrate_reviews_scope_v4(conn)
    conn.executescript(SCHEMA_V2_SQL)
    conn.executescript(SCHEMA_V3_SQL)
    _configure_fts5(conn)
    _set_schema_version(conn, SCHEMA_VERSION)
    conn.commit()
