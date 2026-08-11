"""CRUD for stored review syntheses and their covered entries."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ..models import Entry, Review
from . import common
from .entries import get_entry
from .errors import DBError, EntryNotFoundError
from .rows import hydrate_review


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
    now = common.utcnow_iso()
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
    return hydrate_review(conn, row)


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
    return tuple(hydrate_review(conn, row) for row in rows)


def delete_review(conn: sqlite3.Connection, review_id: int) -> bool:
    """Delete a stored review. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    return cur.rowcount > 0
