"""CRUD for coaching feedback notes attached to entries."""

from __future__ import annotations

import sqlite3

from ..models import Feedback
from . import common
from .entries import get_entry
from .errors import DBError, EntryNotFoundError
from .rows import row_to_feedback


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
    return tuple(row_to_feedback(row) for row in rows)


def get_feedback(conn: sqlite3.Connection, feedback_id: int) -> Feedback | None:
    row = conn.execute(
        "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
    ).fetchone()
    if row is None:
        return None
    return row_to_feedback(row)
