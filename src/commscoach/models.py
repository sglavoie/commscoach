"""Frozen in-process data contracts shared across the DB and CLI layers.

These dataclasses are intentionally minimal: they describe the rows the CLI
hands to and receives from ``db.py``. Timestamps are stored as ISO-8601 strings
to match SQLite ``TEXT`` columns without an explicit conversion layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Person:
    """A counterparty or the global self."""

    id: int
    handle: str
    name: str | None
    is_self: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Entry:
    """A communication entry: something said, sent, or observed."""

    id: int
    body: str
    source: str | None
    occurred_at: str | None
    created_at: str
    updated_at: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    people: tuple[Person, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Feedback:
    """A coaching note attached to an entry."""

    id: int
    entry_id: int
    body: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Review:
    """A stored synthesis covering one or more communication entries."""

    id: int
    body: str
    scope_kind: str
    scope_limit: int | None
    created_at: str
    updated_at: str
    entries: tuple[Entry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EntryContext:
    """Payload returned by ``commscoach context <entry-id>``.

    ``self_person`` is the global self. ``people`` are the entry's linked
    non-self counterparties. ``prior_feedback`` is feedback already attached
    to this entry, ordered oldest-first by the DB layer.
    """

    entry: Entry
    self_person: Person
    people: tuple[Person, ...]
    prior_feedback: tuple[Feedback, ...]


@dataclass(frozen=True, slots=True)
class ReviewEntryContext:
    """One entry plus its existing feedback for review synthesis."""

    entry: Entry
    feedback: tuple[Feedback, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Payload for generating a stored review across recent entries."""

    self_person: Person
    scope_kind: str
    scope_limit: int | None
    entries: tuple[ReviewEntryContext, ...] = field(default_factory=tuple)
    prior_reviews: tuple[Review, ...] = field(default_factory=tuple)
