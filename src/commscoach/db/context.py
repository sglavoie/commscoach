"""Aggregated read models consumed by the coaching skill."""

from __future__ import annotations

import sqlite3

from ..models import EntryContext, ReviewContext, ReviewEntryContext
from .entries import get_entry, list_entries
from .errors import DBError, EntryNotFoundError, MissingSelfError
from .feedback import list_feedback
from .people import get_self_person
from .reviews import list_reviews


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
