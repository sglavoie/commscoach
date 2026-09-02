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
class PersonNote:
    """Private coaching context stored separately from a person row."""

    person_id: int
    body: str
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
class Tag:
    """A normalized tag name plus how many entries currently carry it."""

    id: int
    name: str
    entry_count: int


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
class Goal:
    """A concrete coaching goal or short-horizon experiment."""

    id: int
    title: str
    detail: str | None
    status: str
    person: Person | None
    review: Review | None
    outcome: str | None
    closed_at: str | None
    created_at: str
    updated_at: str

    @property
    def is_open(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True, slots=True)
class Stats:
    """Windowed activity and coaching-coverage summary."""

    entry_count: int
    people_count: int
    tag_count: int
    feedback_count: int
    review_count: int
    entries_with_feedback: int
    open_goal_count: int
    closed_goal_count: int
    first_entry: str | None
    last_entry: str | None
    last_review: str | None
    entries_per_week: float
    current_weekly_streak: int
    longest_weekly_streak: int
    weekly_buckets: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    top_tags: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    top_sources: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    top_people: tuple[tuple[str, int], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PersonHistoryItem:
    """One prior entry involving a person plus its coaching feedback."""

    entry: Entry
    feedback: tuple[Feedback, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PersonContext:
    """A counterparty enriched with private notes and prior interactions."""

    person: Person
    notes: str | None
    history: tuple[PersonHistoryItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EntryContext:
    """Payload returned by ``commscoach context <entry-id>``.

    ``self_person`` is the global self. ``people`` enrich the entry's linked
    non-self counterparties with notes and history. ``prior_feedback`` is
    feedback already attached to this entry, ordered oldest-first by the DB
    layer. With the default limits this payload is roughly 6--10x larger than
    the original flat context; the CLI exposes each growth limit explicitly.
    """

    entry: Entry
    self_person: Person
    people: tuple[PersonContext, ...]
    prior_feedback: tuple[Feedback, ...]
    goals: tuple[Goal, ...] = field(default_factory=tuple)
    prior_reviews: tuple[Review, ...] = field(default_factory=tuple)


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
    goals: tuple[Goal, ...] = field(default_factory=tuple)
