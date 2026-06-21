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
