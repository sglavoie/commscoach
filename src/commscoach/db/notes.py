"""CRUD for private coaching notes attached to people."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from ..models import PersonNote
from . import common
from .errors import DBError, PersonNotFoundError
from .people import get_person


def _row_to_person_note(row: sqlite3.Row) -> PersonNote:
    return PersonNote(
        person_id=row["person_id"],
        body=row["body"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@common.writes
def set_person_note(
    conn: sqlite3.Connection,
    person_id: int,
    body: str,
    *,
    now: str | None = None,
) -> PersonNote:
    """Create or replace a person's note while preserving its creation time."""
    if not isinstance(body, str) or not body.strip():
        raise DBError("person notes must be a non-empty string")
    if get_person(conn, person_id) is None:
        raise PersonNotFoundError(person_id)
    timestamp = now if now is not None else common.utcnow_iso()
    with common.write_transaction(conn):
        conn.execute(
            "INSERT INTO person_notes (person_id, body, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(person_id) DO UPDATE SET"
            " body = excluded.body, updated_at = excluded.updated_at",
            (person_id, body, timestamp, timestamp),
        )
    note = get_person_note(conn, person_id)
    assert note is not None
    return note


@common.writes
def clear_person_note(conn: sqlite3.Connection, person_id: int) -> bool:
    """Delete a person's note, returning whether a row was removed."""
    with common.write_transaction(conn):
        cur = conn.execute("DELETE FROM person_notes WHERE person_id = ?", (person_id,))
    return cur.rowcount > 0


def get_person_note(conn: sqlite3.Connection, person_id: int) -> PersonNote | None:
    """Return one person's note, if present."""
    row = conn.execute("SELECT * FROM person_notes WHERE person_id = ?", (person_id,)).fetchone()
    return _row_to_person_note(row) if row is not None else None


def get_person_notes(conn: sqlite3.Connection, person_ids: Iterable[int]) -> dict[int, PersonNote]:
    """Bulk-fetch notes as a mapping keyed by person ID."""
    ids = tuple(dict.fromkeys(person_ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM person_notes WHERE person_id IN ({placeholders})",
        ids,
    ).fetchall()
    return {note.person_id: note for note in (_row_to_person_note(row) for row in rows)}
