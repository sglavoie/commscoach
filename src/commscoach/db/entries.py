"""CRUD and search for the ``entries`` table and its tag/people links."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ..models import Entry
from . import common, tags as tags_mod
from .errors import DBError, EntryNotFoundError
from .people import resolve_person_handles
from .rows import hydrate_entry


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
    normalized_tags = tags_mod.normalize_tag_list(tags)
    resolved_people = resolve_person_handles(conn, people)
    now = common.utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (body, source, occurred_at, now, now),
        )
        entry_id = cur.lastrowid
        for tag in normalized_tags:
            tag_id = tags_mod.upsert_tag(conn, tag)
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
    body: Any = common.UNSET,
    source: Any = common.UNSET,
    occurred_at: Any = common.UNSET,
    tags: Any = common.UNSET,
    people: Any = common.UNSET,
) -> Entry:
    """Update an entry. ``tags`` and ``people``, when provided, fully replace the
    existing links (pass ``()`` to clear them). Other column args update only
    when not ``UNSET``.
    """
    if get_entry(conn, entry_id) is None:
        raise EntryNotFoundError(entry_id)

    column_updates: list[str] = []
    params: list[Any] = []
    if body is not common.UNSET:
        if not body or not str(body).strip():
            raise DBError("entry body must be a non-empty string")
        column_updates.append("body = ?")
        params.append(body)
    if source is not common.UNSET:
        column_updates.append("source = ?")
        params.append(source)
    if occurred_at is not common.UNSET:
        column_updates.append("occurred_at = ?")
        params.append(occurred_at)

    normalized_tags = (
        tags_mod.normalize_tag_list(tags) if tags is not common.UNSET else None
    )
    resolved_people = (
        resolve_person_handles(conn, people) if people is not common.UNSET else None
    )

    now = common.utcnow_iso()
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
                tag_id = tags_mod.upsert_tag(conn, tag)
                conn.execute(
                    "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, tag_id),
                )
            tags_mod.gc_orphan_tags(conn)
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
    return hydrate_entry(conn, row)


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
    return tuple(hydrate_entry(conn, row) for row in rows)


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Delete an entry and GC orphan tags. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        if cur.rowcount == 0:
            return False
        tags_mod.gc_orphan_tags(conn)
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
        params.append(tags_mod.normalize_tag(tag))
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
    return tuple(hydrate_entry(conn, row) for row in rows)
