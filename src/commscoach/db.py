"""SQLite connection, schema v2, idempotent init, and CRUD for commscoach.

This module owns the database foundation: connecting with foreign keys enabled,
applying schema v2 on demand, and stamping ``_meta.schema_version`` so callers
can reject stale databases until ``commscoach init`` upgrades them.

Schema invariants enforced at the DB level:

* ``people.handle`` is case-insensitive UNIQUE via an expression index on
  ``LOWER(handle)``.
* At most one row may have ``is_self = 1`` via a partial UNIQUE index.
* Cascading deletes: removing an entry removes its tag links, people links,
  attached feedback, and review-entry links. Removing a review removes its
  review-entry links. Removing a person removes their entry links.
* Stored review scopes currently support only ``scope_kind = 'latest'``.

Each public mutator opens its own transaction via ``with conn:`` so callers do
not need to manage transactions explicitly.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .models import (
    Entry,
    EntryContext,
    Feedback,
    Person,
    Review,
    ReviewContext,
    ReviewEntryContext,
)

SCHEMA_VERSION = 2

_EXPECTED_TABLES = frozenset(
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

_SCHEMA_V1_SQL = """
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

_SCHEMA_V2_SQL = """
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


# Sentinel for "argument not provided" in update_* APIs. Using a private
# object() means it can never collide with a real caller-supplied value.
_UNSET: Any = object()


class DBError(Exception):
    """Base class for CRUD errors the CLI surfaces as exit-code-1 failures."""


class EntryNotFoundError(DBError):
    def __init__(self, entry_id: int) -> None:
        super().__init__(f"entry {entry_id} not found")
        self.entry_id = entry_id


class PersonNotFoundError(DBError):
    def __init__(self, ref: int | str) -> None:
        super().__init__(f"person {ref!r} not found")
        self.ref = ref


class FeedbackNotFoundError(DBError):
    def __init__(self, feedback_id: int) -> None:
        super().__init__(f"feedback {feedback_id} not found")
        self.feedback_id = feedback_id


class ReviewNotFoundError(DBError):
    def __init__(self, review_id: int) -> None:
        super().__init__(f"review {review_id} not found")
        self.review_id = review_id


class DuplicateHandleError(DBError):
    def __init__(self, handle: str) -> None:
        super().__init__(f"handle {handle!r} already exists")
        self.handle = handle


class DuplicateSelfError(DBError):
    def __init__(self) -> None:
        super().__init__("another person is already marked self")


class MissingSelfError(DBError):
    def __init__(self) -> None:
        super().__init__("no self person configured")


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
    if version != SCHEMA_VERSION or not _EXPECTED_TABLES.issubset(tables):
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
    conn.executescript(_SCHEMA_V1_SQL)
    version = get_schema_version(conn)
    if version not in (None, 1, SCHEMA_VERSION):
        raise DBError(f"unsupported schema version {version}")
    conn.executescript(_SCHEMA_V2_SQL)
    _set_schema_version(conn, SCHEMA_VERSION)
    conn.commit()


def _utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for ``created_at``/``updated_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_tag(name: str) -> str:
    """Normalize a tag for storage: stripped, lowercased."""
    return name.strip().lower()


def _normalize_tag_list(tags: Iterable[str]) -> list[str]:
    """Normalize and de-duplicate a tag iterable, dropping empties."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = _normalize_tag(tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _row_to_person(row: sqlite3.Row) -> Person:
    return Person(
        id=row["id"],
        handle=row["handle"],
        name=row["name"],
        is_self=bool(row["is_self"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_feedback(row: sqlite3.Row) -> Feedback:
    return Feedback(
        id=row["id"],
        entry_id=row["entry_id"],
        body=row["body"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_entry_tags(conn: sqlite3.Connection, entry_id: int) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT t.name FROM tags t"
        " JOIN entry_tags et ON et.tag_id = t.id"
        " WHERE et.entry_id = ?"
        " ORDER BY t.name",
        (entry_id,),
    ).fetchall()
    return tuple(row["name"] for row in rows)


def _load_entry_people(conn: sqlite3.Connection, entry_id: int) -> tuple[Person, ...]:
    rows = conn.execute(
        "SELECT p.* FROM people p"
        " JOIN entry_people ep ON ep.person_id = p.id"
        " WHERE ep.entry_id = ?"
        " ORDER BY p.handle COLLATE NOCASE",
        (entry_id,),
    ).fetchall()
    return tuple(_row_to_person(row) for row in rows)


def _hydrate_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        body=row["body"],
        source=row["source"],
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        tags=_load_entry_tags(conn, row["id"]),
        people=_load_entry_people(conn, row["id"]),
    )


def _load_review_entries(conn: sqlite3.Connection, review_id: int) -> tuple[Entry, ...]:
    rows = conn.execute(
        "SELECT e.* FROM entries e"
        " JOIN review_entries re ON re.entry_id = e.id"
        " WHERE re.review_id = ?"
        " ORDER BY re.position ASC",
        (review_id,),
    ).fetchall()
    return tuple(_hydrate_entry(conn, row) for row in rows)


def _hydrate_review(conn: sqlite3.Connection, row: sqlite3.Row) -> Review:
    return Review(
        id=row["id"],
        body=row["body"],
        scope_kind=row["scope_kind"],
        scope_limit=row["scope_limit"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        entries=_load_review_entries(conn, row["id"]),
    )


def _gc_orphan_tags(conn: sqlite3.Connection) -> None:
    """Drop tag rows that no entry references anymore.

    Called after entry deletion or tag replacement so the ``tags`` table stays
    in sync with active links instead of accumulating dead names.
    """
    conn.execute(
        "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM entry_tags)"
    )


def _wrap_people_integrity_error(
    exc: sqlite3.IntegrityError, handle: str | None
) -> None:
    """Translate a people-table IntegrityError into a typed DBError. Always raises.

    SQLite reports the partial UNIQUE index on ``is_self`` as
    ``UNIQUE constraint failed: people.is_self`` rather than by the index
    name, so the matcher checks both forms.
    """
    msg = str(exc)
    if "idx_people_one_self" in msg or "people.is_self" in msg:
        raise DuplicateSelfError() from exc
    if "idx_people_handle_lower" in msg or "people.handle" in msg.lower():
        raise DuplicateHandleError(handle or "") from exc
    raise exc


def _resolve_person_handles(
    conn: sqlite3.Connection, handles: Iterable[str]
) -> list[Person]:
    """Resolve handles to ``Person`` rows preserving input order and de-duping.

    Raises ``PersonNotFoundError`` on the first unknown handle so entry mutation
    fails before any rows are written.
    """
    resolved: list[Person] = []
    seen: set[int] = set()
    for handle in handles:
        person = get_person_by_handle(conn, handle)
        if person is None:
            raise PersonNotFoundError(handle)
        if person.id in seen:
            continue
        seen.add(person.id)
        resolved.append(person)
    return resolved


def _resolve_review_entries(
    conn: sqlite3.Connection, entry_ids: Iterable[int] | None
) -> list[Entry]:
    """Resolve entry ids preserving caller order and de-duping first hits."""
    if entry_ids is None:
        raise DBError("review must cover at least one entry")
    resolved: list[Entry] = []
    seen: set[int] = set()
    for entry_id in entry_ids:
        entry = get_entry(conn, entry_id)
        if entry is None:
            raise EntryNotFoundError(entry_id)
        if entry.id in seen:
            continue
        seen.add(entry.id)
        resolved.append(entry)
    if not resolved:
        raise DBError("review must cover at least one entry")
    return resolved


def _upsert_tag(conn: sqlite3.Connection, name: str) -> int:
    """Insert tag if missing and return its id."""
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row["id"]


def create_person(
    conn: sqlite3.Connection,
    handle: str,
    *,
    name: str | None = None,
    is_self: bool = False,
) -> Person:
    """Create a person row and return the resulting ``Person``.

    Raises ``DuplicateHandleError`` on case-insensitive handle collision and
    ``DuplicateSelfError`` if ``is_self=True`` while another self row exists.
    """
    if not handle or not handle.strip():
        raise DBError("handle must be a non-empty string")
    now = _utcnow_iso()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (handle, name, 1 if is_self else 0, now, now),
            )
    except sqlite3.IntegrityError as exc:
        _wrap_people_integrity_error(exc, handle)
    person = get_person(conn, cur.lastrowid)
    assert person is not None
    return person


def update_person(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    handle: Any = _UNSET,
    name: Any = _UNSET,
) -> Person:
    """Update mutable person fields. Only non-``_UNSET`` arguments are written.

    Raises ``PersonNotFoundError`` if the row is missing and
    ``DuplicateHandleError`` if ``handle`` collides with another row.
    """
    person = get_person(conn, person_id)
    if person is None:
        raise PersonNotFoundError(person_id)

    fields: list[str] = []
    params: list[Any] = []
    if handle is not _UNSET:
        if not handle or not str(handle).strip():
            raise DBError("handle must be a non-empty string")
        fields.append("handle = ?")
        params.append(handle)
    if name is not _UNSET:
        fields.append("name = ?")
        params.append(name)
    if not fields:
        return person

    fields.append("updated_at = ?")
    params.append(_utcnow_iso())
    params.append(person_id)
    try:
        with conn:
            conn.execute(
                f"UPDATE people SET {', '.join(fields)} WHERE id = ?",
                params,
            )
    except sqlite3.IntegrityError as exc:
        _wrap_people_integrity_error(exc, handle if handle is not _UNSET else None)
    updated = get_person(conn, person_id)
    assert updated is not None
    return updated


def get_person(conn: sqlite3.Connection, person_id: int) -> Person | None:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        return None
    return _row_to_person(row)


def get_person_by_handle(conn: sqlite3.Connection, handle: str) -> Person | None:
    row = conn.execute(
        "SELECT * FROM people WHERE LOWER(handle) = LOWER(?)",
        (handle,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_person(row)


def list_people(conn: sqlite3.Connection) -> tuple[Person, ...]:
    """Return all people with the self row first, then handle-alphabetical."""
    rows = conn.execute(
        "SELECT * FROM people ORDER BY is_self DESC, handle COLLATE NOCASE"
    ).fetchall()
    return tuple(_row_to_person(row) for row in rows)


def delete_person(conn: sqlite3.Connection, person_id: int) -> bool:
    """Delete a person. Returns ``True`` if a row was deleted, ``False`` otherwise."""
    with conn:
        cur = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return cur.rowcount > 0


def get_self_person(conn: sqlite3.Connection) -> Person | None:
    row = conn.execute("SELECT * FROM people WHERE is_self = 1").fetchone()
    if row is None:
        return None
    return _row_to_person(row)


def set_self(conn: sqlite3.Connection, person_id: int) -> Person:
    """Promote ``person_id`` to self, demoting any prior self atomically.

    The partial UNIQUE index on ``is_self = 1`` would reject a concurrent two-row
    self state, so the demote-then-promote sequence runs inside a single
    ``with conn:`` transaction to keep the intermediate state private.
    """
    person = get_person(conn, person_id)
    if person is None:
        raise PersonNotFoundError(person_id)
    now = _utcnow_iso()
    with conn:
        conn.execute(
            "UPDATE people SET is_self = 0, updated_at = ?"
            " WHERE is_self = 1 AND id != ?",
            (now, person_id),
        )
        conn.execute(
            "UPDATE people SET is_self = 1, updated_at = ? WHERE id = ?",
            (now, person_id),
        )
    updated = get_person(conn, person_id)
    assert updated is not None
    return updated


def create_entry(
    conn: sqlite3.Connection,
    body: str,
    *,
    source: str | None = None,
    occurred_at: str | None = None,
    tags: Iterable[str] = (),
    people: Iterable[str] = (),
) -> Entry:
    """Create an entry plus its tag and people links in a single transaction.

    ``tags`` are normalized (stripped, lowercased) and de-duplicated before
    insertion. ``people`` are existing person handles; any unknown handle raises
    ``PersonNotFoundError`` *before* any row is inserted so the DB stays clean.
    """
    if not body or not body.strip():
        raise DBError("entry body must be a non-empty string")
    normalized_tags = _normalize_tag_list(tags)
    resolved_people = _resolve_person_handles(conn, people)
    now = _utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (body, source, occurred_at, now, now),
        )
        entry_id = cur.lastrowid
        for tag in normalized_tags:
            tag_id = _upsert_tag(conn, tag)
            conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_id),
            )
        for person in resolved_people:
            conn.execute(
                "INSERT OR IGNORE INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person.id),
            )
    entry = get_entry(conn, entry_id)
    assert entry is not None
    return entry


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    body: Any = _UNSET,
    source: Any = _UNSET,
    occurred_at: Any = _UNSET,
    tags: Any = _UNSET,
    people: Any = _UNSET,
) -> Entry:
    """Update an entry. ``tags`` and ``people``, when provided, fully replace the
    existing links (pass ``()`` to clear them). Other column args update only
    when not ``_UNSET``.
    """
    if get_entry(conn, entry_id) is None:
        raise EntryNotFoundError(entry_id)

    column_updates: list[str] = []
    params: list[Any] = []
    if body is not _UNSET:
        if not body or not str(body).strip():
            raise DBError("entry body must be a non-empty string")
        column_updates.append("body = ?")
        params.append(body)
    if source is not _UNSET:
        column_updates.append("source = ?")
        params.append(source)
    if occurred_at is not _UNSET:
        column_updates.append("occurred_at = ?")
        params.append(occurred_at)

    normalized_tags = _normalize_tag_list(tags) if tags is not _UNSET else None
    resolved_people = (
        _resolve_person_handles(conn, people) if people is not _UNSET else None
    )

    now = _utcnow_iso()
    with conn:
        if column_updates:
            column_updates.append("updated_at = ?")
            params.append(now)
            params.append(entry_id)
            conn.execute(
                f"UPDATE entries SET {', '.join(column_updates)} WHERE id = ?",
                params,
            )
        elif normalized_tags is not None or resolved_people is not None:
            conn.execute(
                "UPDATE entries SET updated_at = ? WHERE id = ?",
                (now, entry_id),
            )
        if normalized_tags is not None:
            conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
            for tag in normalized_tags:
                tag_id = _upsert_tag(conn, tag)
                conn.execute(
                    "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, tag_id),
                )
            _gc_orphan_tags(conn)
        if resolved_people is not None:
            conn.execute("DELETE FROM entry_people WHERE entry_id = ?", (entry_id,))
            for person in resolved_people:
                conn.execute(
                    "INSERT OR IGNORE INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                    (entry_id, person.id),
                )
    updated = get_entry(conn, entry_id)
    assert updated is not None
    return updated


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Entry | None:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_entry(conn, row)


def list_entries(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> tuple[Entry, ...]:
    """Return entries newest-first by ``created_at``, optionally bounded by ``limit``."""
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    sql = "SELECT * FROM entries ORDER BY created_at DESC, id DESC"
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(_hydrate_entry(conn, row) for row in rows)


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Delete an entry and GC orphan tags. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        if cur.rowcount == 0:
            return False
        _gc_orphan_tags(conn)
    return True


def search_entries(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    tag: str | None = None,
    person_handle: str | None = None,
    limit: int | None = None,
) -> tuple[Entry, ...]:
    """Filter entries by body substring, tag name, and/or person handle.

    All filters combine with AND. ``query`` is a case-insensitive LIKE against
    ``entries.body``; ``tag`` matches the normalized tag name;
    ``person_handle`` matches case-insensitively. Results are newest-first.
    """
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    where: list[str] = []
    params: list[Any] = []
    if query:
        where.append("e.body LIKE ? COLLATE NOCASE")
        params.append(f"%{query}%")
    if tag:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM entry_tags et"
            " JOIN tags t ON t.id = et.tag_id"
            " WHERE et.entry_id = e.id AND t.name = ?)"
        )
        params.append(_normalize_tag(tag))
    if person_handle:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM entry_people ep"
            " JOIN people p ON p.id = ep.person_id"
            " WHERE ep.entry_id = e.id AND LOWER(p.handle) = LOWER(?))"
        )
        params.append(person_handle)
    sql = "SELECT e.* FROM entries e"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.created_at DESC, e.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(_hydrate_entry(conn, row) for row in rows)


def create_feedback(
    conn: sqlite3.Connection,
    entry_id: int,
    body: str,
) -> Feedback:
    """Attach a feedback note to an existing entry."""
    if not body or not body.strip():
        raise DBError("feedback body must be a non-empty string")
    if get_entry(conn, entry_id) is None:
        raise EntryNotFoundError(entry_id)
    now = _utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO feedback (entry_id, body, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (entry_id, body, now, now),
        )
    feedback = get_feedback(conn, cur.lastrowid)
    assert feedback is not None
    return feedback


def list_feedback(
    conn: sqlite3.Connection,
    entry_id: int,
) -> tuple[Feedback, ...]:
    """Return all feedback for an entry, oldest-first."""
    rows = conn.execute(
        "SELECT * FROM feedback WHERE entry_id = ?"
        " ORDER BY created_at ASC, id ASC",
        (entry_id,),
    ).fetchall()
    return tuple(_row_to_feedback(row) for row in rows)


def get_feedback(conn: sqlite3.Connection, feedback_id: int) -> Feedback | None:
    row = conn.execute(
        "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_feedback(row)


def create_review(
    conn: sqlite3.Connection,
    body: str,
    entry_ids: Iterable[int],
    scope_kind: str = "latest",
    scope_limit: int | None = None,
) -> Review:
    """Store a review synthesis covering existing entries."""
    if not body or not body.strip():
        raise DBError("review body must be a non-empty string")
    if scope_kind != "latest":
        raise DBError("review scope_kind must be 'latest'")
    if scope_limit is not None and scope_limit < 0:
        raise DBError("review scope_limit must be non-negative")
    entries = _resolve_review_entries(conn, entry_ids)
    now = _utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (body, scope_kind, scope_limit, now, now),
        )
        review_id = cur.lastrowid
        for position, entry in enumerate(entries):
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, ?)",
                (review_id, entry.id, position),
            )
    review = get_review(conn, review_id)
    assert review is not None
    return review


def get_review(conn: sqlite3.Connection, review_id: int) -> Review | None:
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_review(conn, row)


def list_reviews(
    conn: sqlite3.Connection,
    limit: int | None = None,
) -> tuple[Review, ...]:
    """Return stored reviews newest-first by creation time."""
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    sql = "SELECT * FROM reviews ORDER BY created_at DESC, id DESC"
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(_hydrate_review(conn, row) for row in rows)


def delete_review(conn: sqlite3.Connection, review_id: int) -> bool:
    """Delete a stored review. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    return cur.rowcount > 0


def get_review_context(
    conn: sqlite3.Connection,
    limit: int = 20,
    prior_review_limit: int = 3,
) -> ReviewContext:
    """Aggregate recent entries and prior reviews for stored review synthesis."""
    if limit < 0:
        raise DBError("limit must be non-negative")
    if prior_review_limit < 0:
        raise DBError("prior_review_limit must be non-negative")
    self_person = get_self_person(conn)
    if self_person is None:
        raise MissingSelfError()

    latest_entries = list_entries(conn, limit=limit)
    entry_contexts = tuple(
        ReviewEntryContext(entry=entry, feedback=list_feedback(conn, entry.id))
        for entry in reversed(latest_entries)
    )
    latest_reviews = list_reviews(conn, limit=prior_review_limit)
    return ReviewContext(
        self_person=self_person,
        scope_kind="latest",
        scope_limit=limit,
        entries=entry_contexts,
        prior_reviews=tuple(reversed(latest_reviews)),
    )


def get_entry_context(
    conn: sqlite3.Connection,
    entry_id: int,
) -> EntryContext:
    """Aggregate the data the coaching skill needs for one entry.

    Raises ``EntryNotFoundError`` if the entry is missing and
    ``MissingSelfError`` if no global self person is configured. Self is
    excluded from the returned ``people`` field; ``prior_feedback`` is
    oldest-first.
    """
    entry = get_entry(conn, entry_id)
    if entry is None:
        raise EntryNotFoundError(entry_id)
    self_person = get_self_person(conn)
    if self_person is None:
        raise MissingSelfError()
    non_self_people = tuple(p for p in entry.people if p.id != self_person.id)
    prior_feedback = list_feedback(conn, entry_id)
    return EntryContext(
        entry=entry,
        self_person=self_person,
        people=non_self_people,
        prior_feedback=prior_feedback,
    )
