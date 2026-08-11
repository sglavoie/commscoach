"""Row-to-model hydration shared by the CRUD submodules."""

from __future__ import annotations

import sqlite3

from ..models import Entry, Feedback, Person, Review


def row_to_person(row: sqlite3.Row) -> Person:
    return Person(
        id=row["id"],
        handle=row["handle"],
        name=row["name"],
        is_self=bool(row["is_self"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_feedback(row: sqlite3.Row) -> Feedback:
    return Feedback(
        id=row["id"],
        entry_id=row["entry_id"],
        body=row["body"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def load_entry_tags(conn: sqlite3.Connection, entry_id: int) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT t.name FROM tags t"
        " JOIN entry_tags et ON et.tag_id = t.id"
        " WHERE et.entry_id = ?"
        " ORDER BY t.name",
        (entry_id,),
    ).fetchall()
    return tuple(row["name"] for row in rows)


def load_entry_people(conn: sqlite3.Connection, entry_id: int) -> tuple[Person, ...]:
    rows = conn.execute(
        "SELECT p.* FROM people p"
        " JOIN entry_people ep ON ep.person_id = p.id"
        " WHERE ep.entry_id = ?"
        " ORDER BY p.handle COLLATE NOCASE",
        (entry_id,),
    ).fetchall()
    return tuple(row_to_person(row) for row in rows)


def hydrate_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        body=row["body"],
        source=row["source"],
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        tags=load_entry_tags(conn, row["id"]),
        people=load_entry_people(conn, row["id"]),
    )


def load_review_entries(conn: sqlite3.Connection, review_id: int) -> tuple[Entry, ...]:
    rows = conn.execute(
        "SELECT e.* FROM entries e"
        " JOIN review_entries re ON re.entry_id = e.id"
        " WHERE re.review_id = ?"
        " ORDER BY re.position ASC",
        (review_id,),
    ).fetchall()
    return tuple(hydrate_entry(conn, row) for row in rows)


def hydrate_review(conn: sqlite3.Connection, row: sqlite3.Row) -> Review:
    return Review(
        id=row["id"],
        body=row["body"],
        scope_kind=row["scope_kind"],
        scope_limit=row["scope_limit"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        entries=load_review_entries(conn, row["id"]),
    )
