"""CRUD for coaching feedback notes attached to entries."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from ..models import Feedback
from . import common
from .entries import get_entry
from .errors import DBError, EntryNotFoundError, FeedbackNotFoundError, PersonNotFoundError
from .people import get_person_by_handle
from .rows import row_to_feedback


def feedback_entry_ids(conn: sqlite3.Connection, entry_ids: Iterable[int]) -> set[int]:
    """Find coached entries in batches without loading their feedback bodies."""
    ids = tuple(dict.fromkeys(entry_ids))
    coached: set[int] = set()
    for offset in range(0, len(ids), 500):
        batch = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        coached.update(
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT entry_id FROM feedback WHERE entry_id IN ({placeholders})",
                batch,
            )
        )
    return coached


@common.writes
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
    now = common.utcnow_iso()
    with common.write_transaction(conn):
        cur = conn.execute(
            "INSERT INTO feedback (entry_id, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
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
        "SELECT * FROM feedback WHERE entry_id = ? ORDER BY created_at ASC, id ASC",
        (entry_id,),
    ).fetchall()
    return tuple(row_to_feedback(row) for row in rows)


def get_feedback(conn: sqlite3.Connection, feedback_id: int) -> Feedback | None:
    row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    if row is None:
        return None
    return row_to_feedback(row)


def search_feedback(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    person_handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
    oldest_first: bool = False,
) -> tuple[Feedback, ...]:
    """Find literal body substrings, newest-first, by linked person or written date.

    Without ``query`` every note matching the other filters is returned, so
    feedback can be browsed across entries the way ``entry search`` can.
    """
    if limit < 0:
        raise DBError("limit must be non-negative")
    sql, params = _feedback_search_filter(conn, query, person_handle, since, until)
    direction = "ASC" if oldest_first else "DESC"
    return tuple(
        row_to_feedback(row)
        for row in conn.execute(
            f"SELECT f.*{sql} ORDER BY f.created_at {direction}, f.id {direction} LIMIT ?",
            [*params, limit],
        )
    )


def count_search_feedback(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    person_handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Count matching feedback notes before limiting search results."""
    sql, params = _feedback_search_filter(conn, query, person_handle, since, until)
    return conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]


def _feedback_search_filter(
    conn: sqlite3.Connection,
    query: str | None,
    person_handle: str | None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[str, list[object]]:
    sql = " FROM feedback f WHERE 1"
    params: list[object] = []
    if query is not None:
        if not query.strip():
            raise DBError("query must be a non-empty string")
        sql += " AND search_fold(f.body) LIKE ? ESCAPE '!'"
        params.append(common.contains_pattern(query))
    if person_handle is not None:
        person = get_person_by_handle(conn, person_handle)
        if person is None:
            raise PersonNotFoundError(person_handle)
        sql += (
            " AND EXISTS (SELECT 1 FROM entry_people ep"
            " WHERE ep.entry_id = f.entry_id AND ep.person_id = ?)"
        )
        params.append(person.id)
    common.validate_range_bounds(since, until, since_name="since", until_name="until")
    if since is not None:
        sql += " AND f.created_at >= ?"
        params.append(common.date_bound_timestamp(since, name="since"))
    if until is not None:
        sql += " AND f.created_at <= ?"
        params.append(common.date_bound_timestamp(until, name="until", upper=True))
    return sql, params


@common.writes
def update_feedback(
    conn: sqlite3.Connection,
    feedback_id: int,
    *,
    body: str,
) -> Feedback:
    """Replace a feedback note's body, advancing ``updated_at``.

    The note stays attached to its original entry: ``entry_id`` is part of what
    the note *is*, not editable content.
    """
    if not body or not body.strip():
        raise DBError("feedback body must be a non-empty string")
    current = get_feedback(conn, feedback_id)
    if current is None:
        raise FeedbackNotFoundError(feedback_id)
    if body == current.body:
        return current
    now = common.utcnow_iso()
    with common.write_transaction(conn):
        conn.execute(
            "UPDATE feedback SET body = ?, updated_at = ? WHERE id = ?",
            (body, now, feedback_id),
        )
    feedback = get_feedback(conn, feedback_id)
    assert feedback is not None
    return feedback


@common.writes
def delete_feedback(conn: sqlite3.Connection, feedback_id: int) -> bool:
    """Delete a feedback note. Returns ``True`` if a row was deleted.

    Nothing references ``feedback``, so there is no orphan collection to run.
    """
    with common.write_transaction(conn):
        cur = conn.execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))
    return cur.rowcount > 0
