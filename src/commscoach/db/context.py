"""Aggregated read models consumed by the coaching skill."""

from __future__ import annotations

import sqlite3

from ..models import (
    EntryContext,
    PersonContext,
    PersonHistoryItem,
    ReviewContext,
    ReviewEntryContext,
)
from .entries import get_entry, list_entries, list_entries_with_person
from .errors import DBError, EntryNotFoundError, MissingSelfError
from .feedback import list_feedback
from .goals import list_goals
from .notes import get_person_notes
from .people import get_self_person
from .reviews import list_reviews


def get_review_context(
    conn: sqlite3.Connection,
    limit: int = 20,
    prior_review_limit: int = 3,
    *,
    goal_limit: int = 10,
) -> ReviewContext:
    """Aggregate recent entries and prior reviews for stored review synthesis."""
    if limit < 0:
        raise DBError("limit must be non-negative")
    if prior_review_limit < 0:
        raise DBError("prior_review_limit must be non-negative")
    if goal_limit < 0:
        raise DBError("goal_limit must be non-negative")
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
        goals=list_goals(conn, limit=goal_limit),
    )


def get_entry_context(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    history_limit: int = 3,
    prior_review_limit: int = 1,
) -> EntryContext:
    """Aggregate the data the coaching skill needs for one entry.

    With the default limits, the enriched payload is roughly 6--10x larger
    than the original flat context. This is intentional, and the history and
    prior-review limits are exposed as CLI flags. Raises ``EntryNotFoundError``
    if the entry is missing and ``MissingSelfError`` if no global self person
    is configured. Self is excluded from the returned ``people`` field;
    ``prior_feedback`` is oldest-first.
    """
    if history_limit < 0:
        raise DBError("history_limit must be non-negative")
    if prior_review_limit < 0:
        raise DBError("prior_review_limit must be non-negative")
    entry = get_entry(conn, entry_id)
    if entry is None:
        raise EntryNotFoundError(entry_id)
    self_person = get_self_person(conn)
    if self_person is None:
        raise MissingSelfError()
    non_self_people = tuple(p for p in entry.people if p.id != self_person.id)
    notes_by_person = get_person_notes(conn, (p.id for p in non_self_people))
    people = []
    for person in non_self_people:
        latest_history = list_entries_with_person(
            conn,
            person.id,
            exclude_entry_id=entry_id,
            limit=history_limit,
        )
        history = tuple(
            PersonHistoryItem(
                entry=history_entry,
                feedback=list_feedback(conn, history_entry.id),
            )
            for history_entry in reversed(latest_history)
        )
        note = notes_by_person.get(person.id)
        people.append(
            PersonContext(
                person=person,
                notes=note.body if note is not None else None,
                history=history,
            )
        )
    prior_feedback = list_feedback(conn, entry_id)
    latest_reviews = list_reviews(conn, limit=prior_review_limit)
    return EntryContext(
        entry=entry,
        self_person=self_person,
        people=tuple(people),
        prior_feedback=prior_feedback,
        goals=list_goals(conn),
        prior_reviews=tuple(reversed(latest_reviews)),
    )
