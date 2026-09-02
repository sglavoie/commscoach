"""CRUD for the ``people`` table, including self promotion."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ..models import Person
from . import common
from .errors import (
    DBError,
    DuplicateHandleError,
    DuplicateSelfError,
    PersonNotFoundError,
)
from .rows import row_to_person


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
    now = common.utcnow_iso()
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
    handle: Any = common.UNSET,
    name: Any = common.UNSET,
) -> Person:
    """Update mutable person fields. Only non-``UNSET`` arguments are written.

    Raises ``PersonNotFoundError`` if the row is missing and
    ``DuplicateHandleError`` if ``handle`` collides with another row.

    ``reviews.scope_person_handle`` is denormalized text beside the
    ``scope_person_id`` foreign key, so a handle change is carried into every
    review still linked to this person, inside the same transaction. Without it
    a rename would strand that person's scoped review history under the old
    handle: ``list_reviews(person_handle=...)`` and the person-scoped context
    both match the denormalized column, not the FK. This mirrors ``rename_tag``,
    which carries a tag rename into ``reviews.scope_tag`` for the same reason.

    Reviews whose ``scope_person_id`` is already NULL -- the person was deleted
    -- are deliberately untouched: their handle is historical identity, not a
    live reference.
    """
    person = get_person(conn, person_id)
    if person is None:
        raise PersonNotFoundError(person_id)

    fields: list[str] = []
    params: list[Any] = []
    if handle is not common.UNSET:
        if not handle or not str(handle).strip():
            raise DBError("handle must be a non-empty string")
        fields.append("handle = ?")
        params.append(handle)
    if name is not common.UNSET:
        fields.append("name = ?")
        params.append(name)
    if not fields:
        return person

    fields.append("updated_at = ?")
    params.append(common.utcnow_iso())
    params.append(person_id)
    try:
        with conn:
            conn.execute(
                f"UPDATE people SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            if handle is not common.UNSET:
                conn.execute(
                    "UPDATE reviews SET scope_person_handle = ?"
                    " WHERE scope_person_id = ?",
                    (handle, person_id),
                )
    except sqlite3.IntegrityError as exc:
        _wrap_people_integrity_error(
            exc, handle if handle is not common.UNSET else None
        )
    updated = get_person(conn, person_id)
    assert updated is not None
    return updated


def get_person(conn: sqlite3.Connection, person_id: int) -> Person | None:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        return None
    return row_to_person(row)


def get_person_by_handle(conn: sqlite3.Connection, handle: str) -> Person | None:
    row = conn.execute(
        "SELECT * FROM people WHERE LOWER(handle) = LOWER(?)",
        (handle,),
    ).fetchone()
    if row is None:
        return None
    return row_to_person(row)


def list_people(conn: sqlite3.Connection) -> tuple[Person, ...]:
    """Return all people with the self row first, then handle-alphabetical."""
    rows = conn.execute(
        "SELECT * FROM people ORDER BY is_self DESC, handle COLLATE NOCASE"
    ).fetchall()
    return tuple(row_to_person(row) for row in rows)


def delete_person(conn: sqlite3.Connection, person_id: int) -> bool:
    """Delete a person. Returns ``True`` if a row was deleted, ``False`` otherwise."""
    with conn:
        cur = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return cur.rowcount > 0


def get_self_person(conn: sqlite3.Connection) -> Person | None:
    row = conn.execute("SELECT * FROM people WHERE is_self = 1").fetchone()
    if row is None:
        return None
    return row_to_person(row)


def set_self(conn: sqlite3.Connection, person_id: int) -> Person:
    """Promote ``person_id`` to self, demoting any prior self atomically.

    The partial UNIQUE index on ``is_self = 1`` would reject a concurrent two-row
    self state, so the demote-then-promote sequence runs inside a single
    ``with conn:`` transaction to keep the intermediate state private.
    """
    person = get_person(conn, person_id)
    if person is None:
        raise PersonNotFoundError(person_id)
    now = common.utcnow_iso()
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


def resolve_person_handles(
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
