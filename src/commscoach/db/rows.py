"""Row-to-model hydration shared by the CRUD submodules."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from ..models import Entry, Feedback, Goal, Person, Review


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


# Stays well under SQLite's host-parameter limit, which is 999 before 3.32.
_CHUNK = 500


def _rows_by_ids(conn: sqlite3.Connection, sql: str, ids: Iterable[int]) -> list[sqlite3.Row]:
    """Run ``sql`` (with an ``{ids}`` placeholder list) over ``ids`` in chunks."""
    unique = list(dict.fromkeys(ids))
    rows: list[sqlite3.Row] = []
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        rows.extend(conn.execute(sql.format(ids=", ".join("?" * len(chunk))), chunk))
    return rows


def _people_by_id(conn: sqlite3.Connection, ids: Iterable[Any]) -> dict[int, Person]:
    rows = _rows_by_ids(
        conn, "SELECT * FROM people WHERE id IN ({ids})", (i for i in ids if i is not None)
    )
    return {row["id"]: row_to_person(row) for row in rows}


def hydrate_entries(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> tuple[Entry, ...]:
    """Build ``Entry`` models, loading every row's tags and people in two queries.

    Loading them per entry made each listing cost two extra queries per row.
    """
    ids = [row["id"] for row in rows]
    tags: dict[int, list[str]] = {}
    for link in _rows_by_ids(
        conn,
        "SELECT et.entry_id, t.name FROM tags t JOIN entry_tags et ON et.tag_id = t.id"
        " WHERE et.entry_id IN ({ids}) ORDER BY search_fold(t.name), t.name",
        ids,
    ):
        tags.setdefault(link["entry_id"], []).append(link["name"])
    people: dict[int, list[Person]] = {}
    for link in _rows_by_ids(
        conn,
        "SELECT ep.entry_id, p.* FROM people p JOIN entry_people ep ON ep.person_id = p.id"
        " WHERE ep.entry_id IN ({ids})"
        " ORDER BY search_fold(p.handle), handle_key(p.handle), p.handle",
        ids,
    ):
        people.setdefault(link["entry_id"], []).append(row_to_person(link))
    return tuple(
        Entry(
            id=row["id"],
            body=row["body"],
            source=row["source"],
            occurred_at=row["occurred_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tags=tuple(tags.get(row["id"], ())),
            people=tuple(people.get(row["id"], ())),
        )
        for row in rows
    )


def hydrate_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> Entry:
    return hydrate_entries(conn, [row])[0]


def _load_review_entries(
    conn: sqlite3.Connection, review_ids: Sequence[int]
) -> dict[int, tuple[Entry, ...]]:
    """Return each review's covered entries in stored order, in three queries."""
    rows = _rows_by_ids(
        conn,
        "SELECT re.review_id AS review_id, e.* FROM entries e"
        " JOIN review_entries re ON re.entry_id = e.id"
        " WHERE re.review_id IN ({ids})"
        " ORDER BY re.review_id, re.position ASC",
        review_ids,
    )
    grouped: dict[int, list[Entry]] = {}
    for row, entry in zip(rows, hydrate_entries(conn, rows), strict=True):
        grouped.setdefault(row["review_id"], []).append(entry)
    return {review_id: tuple(entries) for review_id, entries in grouped.items()}


def hydrate_reviews(
    conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], *, with_entries: bool = True
) -> tuple[Review, ...]:
    """Build ``Review`` models, batching scope people and covered entries.

    ``scope_person`` is ``None`` both when the scope is not person-kind and when
    the person has since been deleted (``ON DELETE SET NULL`` clears the FK).
    ``scope_person_handle`` is read straight from the row and needs no lookup --
    the denormalized column is what survives that deletion.
    """
    people = _people_by_id(conn, (row["scope_person_id"] for row in rows))
    entries = _load_review_entries(conn, [row["id"] for row in rows]) if with_entries else {}
    return tuple(
        Review(
            id=row["id"],
            body=row["body"],
            scope_kind=row["scope_kind"],
            scope_limit=row["scope_limit"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            scope_person=people.get(row["scope_person_id"]),
            scope_person_handle=row["scope_person_handle"],
            scope_tag=row["scope_tag"],
            scope_since=row["scope_since"],
            scope_until=row["scope_until"],
            entries=entries.get(row["id"], ()),
        )
        for row in rows
    )


def hydrate_review(
    conn: sqlite3.Connection, row: sqlite3.Row, *, with_entries: bool = True
) -> Review:
    return hydrate_reviews(conn, [row], with_entries=with_entries)[0]


def hydrate_goals(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> tuple[Goal, ...]:
    """Build ``Goal`` models; each ``review`` is the source review without covered entries.

    Goals only reference their review, and loading every covered entry (with
    tags and people) for each goal made goal listings and contexts cost
    O(goals x review size) queries. Use ``get_review`` for the full review.
    People and reviews are loaded for every row at once.
    """
    people = _people_by_id(conn, (row["person_id"] for row in rows))
    review_rows = _rows_by_ids(
        conn,
        "SELECT * FROM reviews WHERE id IN ({ids})",
        (row["review_id"] for row in rows if row["review_id"] is not None),
    )
    reviews = {
        review.id: review for review in hydrate_reviews(conn, review_rows, with_entries=False)
    }
    return tuple(
        Goal(
            id=row["id"],
            title=row["title"],
            detail=row["detail"],
            status=row["status"],
            person=people.get(row["person_id"]),
            review=reviews.get(row["review_id"]),
            outcome=row["outcome"],
            closed_at=row["closed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    )


def hydrate_goal(conn: sqlite3.Connection, row: sqlite3.Row) -> Goal:
    return hydrate_goals(conn, [row])[0]
