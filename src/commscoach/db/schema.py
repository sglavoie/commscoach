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
from datetime import datetime
from pathlib import Path

from .. import paths
from ..timeparse import TimeParseError, parse_when
from . import common
from .errors import DBError, InitRequiredError
from .people import renormalize_handles
from .schema_sql import (
    _FTS5_DROP_TRIGGER_SQL,
    _FTS5_TABLE_SQL,
    _FTS5_TRIGGER_SQL,
    _REVIEWS_INDEX_STATEMENTS,
    _REVIEWS_TABLE_SQL,
    SCHEMA_V1_SQL,
    SCHEMA_V2_SQL,
    SCHEMA_V3_SQL,
)
from .tags import renormalize_tag_names

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

_OCCURRED_AT_BACKFILL_KEY = "occurred_at_utc_backfill_v2"
_NAMES_BACKFILL_KEY = "unicode_names_backfill_v1"
_FTS5_META_KEY = "fts5"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with safe sync semantics and foreign keys enabled.

    ``foreign_keys`` is a per-connection PRAGMA in SQLite; it must be enabled
    every time a new connection is opened, not just at schema creation time.
    """
    if db_path is None:
        db_path = paths.resolve_db_path()
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.row_factory = sqlite3.Row
    common.register_functions(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    # DELETE leaves exactly one database file at rest. WAL's persistent -wal
    # and -shm files can be replicated independently by whole-file syncers,
    # producing a torn database when those files arrive out of order.
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn


def init_db(db_path: Path | str | None = None) -> Path:
    """Create the data dir, apply schema v4, and chmod the DB file.

    Idempotent: re-running ``init_db`` on an existing DB leaves data intact and
    reaffirms the DB file at ``0o600`` (and the default data directory at
    ``0o700``; see :func:`paths.ensure_data_dir`). Returns the resolved DB path.
    """
    resolved = Path(db_path).expanduser() if db_path is not None else paths.resolve_db_path()
    try:
        paths.ensure_data_dir(resolved)
    except OSError as exc:
        raise DBError(f"cannot create data directory for {resolved}: {exc}") from exc
    if resolved.exists():
        # Check read-only first: ``connect`` sets the journal mode, a lasting
        # change to a file that turns out to belong to another application.
        try:
            with closing(
                sqlite3.connect(f"{resolved.resolve().as_uri()}?mode=ro", uri=True)
            ) as probe:
                _refuse_foreign_database(probe)
        except sqlite3.Error:
            pass  # unreadable or not SQLite: the regular path reports it
    with closing(connect(resolved)) as conn:
        _apply_schema(conn)
    try:
        paths.secure_file(resolved)
    except OSError as exc:
        raise DBError(f"cannot secure database file {resolved}: {exc}") from exc
    return resolved


def validate_schema(conn: sqlite3.Connection) -> None:
    """Raise ``DBError`` unless ``conn`` points at the expected initialized DB."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in rows}
        version = get_schema_version(conn) if "_meta" in tables else None
    except (sqlite3.Error, ValueError) as exc:
        raise InitRequiredError() from exc
    if version != SCHEMA_VERSION or not EXPECTED_TABLES.issubset(tables):
        raise InitRequiredError()
    if not _fts5_usable(conn):
        raise InitRequiredError(
            reason="this database uses full-text search, which this SQLite build lacks"
            " (init switches it to substring search)"
        )


def _fts5_usable(conn: sqlite3.Connection) -> bool:
    """Report whether entry writes can run here, without writing anything.

    A database initialized where FTS5 exists keeps triggers that write to
    ``entries_fts``; on a build without FTS5 every entry write would fail with
    "no such module: fts5" until ``init`` drops them.
    """
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (_FTS5_META_KEY,)).fetchone()
    if row is None or row[0] != "1":
        return True
    try:
        conn.execute("SELECT 1 FROM entries_fts LIMIT 0").fetchall()
    except sqlite3.OperationalError:
        return False
    return True


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
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in rows}
        version = get_schema_version(conn) if "_meta" in tables else None
    except (sqlite3.Error, ValueError) as exc:
        raise InitRequiredError() from exc
    if version is None or version < 1 or not _BASELINE_TABLES.issubset(tables):
        raise InitRequiredError()
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
    """Turn every legacy occurrence value into a canonical UTC timestamp, once.

    Before occurrence times were parsed at input, ``--occurred-at`` stored raw
    text. Relative text ("yesterday") is read against the entry's own creation
    time, which is when it was written, rather than the time of the upgrade.
    Text that still cannot be parsed would break stats, date filters, and
    re-importing an export, so it moves into the end of the body (nothing is
    lost) and the entry falls back to its creation time.

    The v1 pass resolved relative text against the upgrade time and left
    unparsable text in place; v2 re-runs over any database, which is safe
    because canonical values are left untouched.
    """
    already_done = conn.execute(
        "SELECT 1 FROM _meta WHERE key = ?", (_OCCURRED_AT_BACKFILL_KEY,)
    ).fetchone()
    if already_done is not None:
        return

    rows = conn.execute(
        "SELECT id, body, occurred_at, created_at FROM entries WHERE occurred_at IS NOT NULL"
    ).fetchall()
    for row in rows:
        raw = row["occurred_at"]
        try:
            normalized = parse_when(raw, now=_local_reference(row["created_at"]))
        except TimeParseError:
            body = f"{row['body'].rstrip()}\n\n(Originally recorded occurrence time: {raw})"
            conn.execute(
                "UPDATE entries SET body = ?, occurred_at = NULL WHERE id = ?",
                (body, row["id"]),
            )
            continue
        if normalized != raw:
            conn.execute(
                "UPDATE entries SET occurred_at = ? WHERE id = ?",
                (normalized, row["id"]),
            )

    conn.execute(
        "INSERT INTO _meta (key, value) VALUES (?, '1')",
        (_OCCURRED_AT_BACKFILL_KEY,),
    )


def _normalize_names_once(conn: sqlite3.Connection) -> None:
    """Bring stored handles and tag names up to the current normalization, once.

    Earlier releases stored them as typed, so a decomposed accent or a leading
    ``#`` on a tag made a second record that looked like an existing one.
    """
    already_done = conn.execute(
        "SELECT 1 FROM _meta WHERE key = ?", (_NAMES_BACKFILL_KEY,)
    ).fetchone()
    if already_done is not None:
        return
    renormalize_handles(conn)
    renormalize_tag_names(conn)
    conn.execute("INSERT INTO _meta (key, value) VALUES (?, '1')", (_NAMES_BACKFILL_KEY,))


def _local_reference(created_at: str) -> datetime | None:
    """Return ``created_at`` as naive system-local time, the parser's "now"."""
    try:
        stamp = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    return stamp


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
    columns stay NULL and every new CHECK holds. ``DROP TABLE`` also discards
    the table's ``sqlite_sequence`` row, so the high-water mark is carried
    over; otherwise the ids of reviews deleted before the upgrade (still named
    in old exports and notes) would be handed out again.
    """
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'reviews'").fetchone()
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
    if sequence is not None:
        updated = conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'reviews'", (sequence[0],)
        )
        if updated.rowcount == 0:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('reviews', ?)", (sequence[0],)
            )
    for statement in _REVIEWS_INDEX_STATEMENTS:
        conn.execute(statement)


def _verify_reviews_rebuild(conn: sqlite3.Connection) -> None:
    """Raise ``DBError`` unless the swapped-in table left the DB consistent.

    Only the tables that reference ``reviews`` are checked, so an unrelated
    pre-existing violation is not misreported as a migration failure.
    """
    violations = [
        *conn.execute("PRAGMA foreign_key_check(review_entries)").fetchall(),
        *conn.execute("PRAGMA foreign_key_check(goals)").fetchall(),
    ]
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
    ``goals.review_id``, silently destroying the coaching loop.
    :func:`_apply_schema` turns them off before opening its transaction
    (``PRAGMA foreign_keys`` is a silent no-op inside one) and rolls the whole
    upgrade back on any error.
    """
    if not _reviews_need_scope_columns(conn):
        return
    _rebuild_reviews_table(conn)
    _verify_reviews_rebuild(conn)


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
    previous = conn.execute("SELECT value FROM _meta WHERE key = ?", (_FTS5_META_KEY,)).fetchone()
    enabled = _probe_fts5(conn)
    if enabled:
        _execute_script(conn, _FTS5_TRIGGER_SQL)
        entry_count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        indexed_count = conn.execute("SELECT COUNT(*) FROM entries_fts_docsize").fetchone()[0]
        # A build without FTS5 drops the sync triggers, so entries may have
        # been edited there without the index noticing; equal counts cannot
        # detect an edited body, hence the unconditional rebuild after one.
        if indexed_count != entry_count or (previous is not None and previous[0] != "1"):
            _rebuild_fts5(conn)
    else:
        # A database may move between Python/SQLite builds. Keep writes working
        # when the current build cannot load an FTS table created elsewhere.
        _execute_script(conn, _FTS5_DROP_TRIGGER_SQL)

    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
        (_FTS5_META_KEY, "1" if enabled else "0"),
    )


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Run ``script`` statement by statement inside the current transaction.

    ``Connection.executescript`` commits any pending transaction first, which
    would split :func:`_apply_schema` into many separate commits.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        conn.execute(statement)


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Apply schema v4 idempotently, migrating v1/v2/v3 databases in place.

    The whole upgrade runs in one transaction, so a failure or crash part-way
    leaves the database exactly as it was.
    """
    conn.commit()
    (foreign_keys,) = conn.execute("PRAGMA foreign_keys").fetchone()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        try:
            _apply_schema_steps(conn)
        except BaseException:
            conn.rollback()
            raise
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON" if foreign_keys else "PRAGMA foreign_keys = OFF")


def _refuse_foreign_database(conn: sqlite3.Connection) -> None:
    """Raise ``DBError`` when an unstamped ``conn`` holds tables that are not ours.

    Every release stamps ``_meta.schema_version``; an unstamped file holding
    only commscoach tables is a pre-release or interrupted store and is
    finished in place. Any other table means the file belongs to another
    application, and adding ours to it (and making it owner-only) would
    quietly alter data commscoach does not own.
    """
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if "_meta" in tables and get_schema_version(conn) is not None:
        return
    foreign = sorted(
        name
        for name in tables
        if name not in EXPECTED_TABLES and not name.startswith("entries_fts")
    )
    if foreign:
        raise DBError(
            "refusing to initialize a SQLite database that commscoach did not create"
            f" (it has no schema version and contains table {foreign[0]!r});"
            " choose a new database path"
        )


def _apply_schema_steps(conn: sqlite3.Connection) -> None:
    _refuse_foreign_database(conn)
    _execute_script(conn, SCHEMA_V1_SQL)
    version = get_schema_version(conn)
    if version is not None and not 1 <= version <= SCHEMA_VERSION:
        raise DBError(f"unsupported schema version {version}")
    conn.execute("DROP INDEX IF EXISTS idx_entries_occurred_at")
    # The source and stored-handle filters moved to handle_key(), which these
    # ASCII-only indexes cannot serve.
    conn.execute("DROP INDEX IF EXISTS idx_entries_source_nocase")
    conn.execute("DROP INDEX IF EXISTS idx_reviews_scope_person_handle_lower")
    # Must precede SCHEMA_V2_SQL: that script now creates
    # idx_reviews_scope_person_id, which cannot be built against a pre-v4
    # reviews table.
    _migrate_reviews_scope_v4(conn)
    _execute_script(conn, SCHEMA_V2_SQL)
    _execute_script(conn, SCHEMA_V3_SQL)
    _normalize_names_once(conn)
    _configure_fts5(conn)
    # After the FTS5 step: this rewrites entry bodies, which fires the sync
    # triggers, and those must already be repaired (or dropped on a build
    # without FTS5) for the very database doctor tells to run init.
    _normalize_occurred_at_once(conn)
    _set_schema_version(conn, SCHEMA_VERSION)
