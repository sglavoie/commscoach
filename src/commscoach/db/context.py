"""Aggregated read models consumed by the coaching skill."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import (
    EntryContext,
    Person,
    PersonContext,
    PersonHistoryItem,
    ReviewContext,
    ReviewEntryContext,
)
from . import tags as tags_mod
from .entries import (
    count_scoped_entries,
    get_entry,
    list_entries_with_person,
    search_entries,
)
from .errors import DBError, EntryNotFoundError, MissingSelfError, PersonNotFoundError
from .feedback import list_feedback
from .goals import list_goals
from .notes import get_person_note, get_person_notes
from .people import get_person_by_handle, get_self_person
from .reviews import (
    _SCOPE_AXES,
    SCOPE_KINDS,
    list_reviews,
    validate_range_bounds,
)

# ``get_review_context`` names its scope axes without the ``scope_`` prefix the
# stored-review API uses, because they map one-for-one onto ``search_entries``
# keywords. The per-kind ownership table is derived from ``reviews._SCOPE_AXES``
# rather than restated, so the single-axis rule lives in exactly one place; a
# scope kind added there with an axis missing below fails loudly at import.
_AXIS_ARG_NAMES = {
    "scope_person_handle": "person_handle",
    "scope_tag": "tag",
    "scope_since": "since",
    "scope_until": "until",
}
_SCOPE_ARGS = {
    kind: tuple(_AXIS_ARG_NAMES[axis] for axis in axes)
    for kind, axes in _SCOPE_AXES.items()
}


def _validate_context_scope(scope_kind: str, supplied: dict[str, Any]) -> None:
    """Check one scope axis against its kind, mirroring ``create_review``.

    Rejects an unknown kind, an axis belonging to another kind (``tag`` with
    ``scope_kind='latest'``), a kind whose own axis is missing, and a closed
    range whose bounds are reversed. An empty *result* is fine; an incoherent
    *request* is not -- and ``since`` after ``until`` is the second kind, since
    it can never select anything and ``create_review`` rejects it outright.
    """
    if scope_kind not in SCOPE_KINDS:
        raise DBError("scope_kind must be one of: " + ", ".join(SCOPE_KINDS))
    owned = _SCOPE_ARGS[scope_kind]
    for name, value in supplied.items():
        if value is not None and name not in owned:
            raise DBError(f"scope_kind '{scope_kind}' does not accept {name}")
    if scope_kind == "person" and not str(supplied["person_handle"] or "").strip():
        raise DBError("scope_kind 'person' requires person_handle")
    if scope_kind == "tag" and not tags_mod.normalize_tag(supplied["tag"] or ""):
        raise DBError("scope_kind 'tag' requires a non-empty tag")
    if scope_kind == "range":
        if supplied["since"] is None and supplied["until"] is None:
            raise DBError("scope_kind 'range' requires since or until")
        validate_range_bounds(
            supplied["since"],
            supplied["until"],
            since_name="since",
            until_name="until",
        )


def get_review_context(
    conn: sqlite3.Connection,
    limit: int = 20,
    prior_review_limit: int = 3,
    *,
    goal_limit: int = 10,
    scope_kind: str = "latest",
    person_handle: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> ReviewContext:
    """Aggregate entries and prior reviews for stored review synthesis.

    ``scope_kind`` selects which axis narrows the entries and only that axis may
    be supplied, mirroring ``create_review``. Entries come from
    ``search_entries``, so ``tag`` is matched normalized and ``since``/``until``
    are inclusive UTC dates or timestamps on ``COALESCE(occurred_at, created_at)``. ``limit``
    caps the entries under every scope and is reported back as ``scope_limit``.
    A scope that matches nothing returns ``entries=()`` rather than raising --
    "no entries with @alice yet" is an answer, not an error. An unknown
    ``person_handle`` does raise ``PersonNotFoundError``, because that is a typo
    rather than a finding. A reversed closed range (``since`` after ``until``)
    raises for the same reason: it is the one empty result that can only be a
    mistake, and ``create_review`` rejects it, so accepting it here would let a
    user build a context from flags that then fail to store.

    Two deliberate asymmetries, both load-bearing:

    * **Prior reviews are scope-filtered for ``person`` and ``tag`` only.** A
      review of @alice should be measured against previous reviews of @alice,
      not against unrelated general syntheses. ``latest`` and ``range`` get the
      unfiltered listing instead: a date window is a one-off slice with no
      natural predecessor, so filtering it to "prior reviews of the same range"
      would return nothing useful.
    * **Goals are never scope-filtered**, only capped by ``goal_limit``. The
      skill judges per-goal relevance itself, and restricting them to one
      person's or one tag's goals would hide the general goals a relationship
      or theme review should still be measured against.

    Do not "fix" either one into uniform filtering.
    """
    if limit < 0:
        raise DBError("limit must be non-negative")
    if prior_review_limit < 0:
        raise DBError("prior_review_limit must be non-negative")
    if goal_limit < 0:
        raise DBError("goal_limit must be non-negative")
    _validate_context_scope(
        scope_kind,
        {
            "person_handle": person_handle,
            "tag": tag,
            "since": since,
            "until": until,
        },
    )
    self_person = get_self_person(conn)
    if self_person is None:
        raise MissingSelfError()

    scope_person: Person | None = None
    if scope_kind == "person":
        scope_person = get_person_by_handle(conn, person_handle)
        if scope_person is None:
            raise PersonNotFoundError(person_handle)
    scope_tag = tags_mod.normalize_tag(tag) if tag is not None else None

    scoped_entries = search_entries(
        conn,
        tag=scope_tag,
        person_handle=person_handle,
        since=since,
        until=until,
        limit=limit,
    )
    entry_contexts = tuple(
        ReviewEntryContext(entry=entry, feedback=list_feedback(conn, entry.id))
        for entry in reversed(scoped_entries)
    )
    if scope_person is not None:
        latest_reviews = list_reviews(
            conn,
            limit=prior_review_limit,
            scope_kind="person",
            person_handle=scope_person.handle,
        )
    elif scope_kind == "tag":
        latest_reviews = list_reviews(
            conn,
            limit=prior_review_limit,
            scope_kind="tag",
            tag=scope_tag,
        )
    else:
        latest_reviews = list_reviews(conn, limit=prior_review_limit)
    self_note = get_person_note(conn, self_person.id)
    scope_person_note = (
        get_person_note(conn, scope_person.id) if scope_person is not None else None
    )
    return ReviewContext(
        self_person=self_person,
        self_notes=self_note.body if self_note is not None else None,
        scope_kind=scope_kind,
        scope_limit=limit,
        scope_person=scope_person,
        scope_person_notes=(
            scope_person_note.body if scope_person_note is not None else None
        ),
        scope_tag=scope_tag,
        scope_since=since,
        scope_until=until,
        entries=entry_contexts,
        prior_reviews=tuple(reversed(latest_reviews)),
        goals=list_goals(conn, limit=goal_limit),
        total_entry_count=count_scoped_entries(
            conn, tag=scope_tag, person_handle=person_handle, since=since, until=until,
        ),
        total_goal_count=conn.execute(
            "SELECT COUNT(*) FROM goals WHERE status = 'active'"
        ).fetchone()[0],
        goal_limit=goal_limit,
    )


def get_entry_context(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    history_limit: int = 3,
    prior_review_limit: int = 1,
    goal_limit: int = 10,
) -> EntryContext:
    """Aggregate the data the coaching skill needs for one entry.

    With the default limits, the enriched payload is roughly 6--10x larger
    than the original flat context. This is intentional, and the history and
    prior-review and goal limits are exposed as CLI flags. Raises ``EntryNotFoundError``
    if the entry is missing and ``MissingSelfError`` if no global self person
    is configured. Self is excluded from the returned ``people`` field;
    ``prior_feedback`` is oldest-first. Person history includes only entries
    strictly before the target's occurrence time (falling back to creation time).
    Goals and prior reviews remain current coaching context, not historical snapshots.
    """
    if history_limit < 0:
        raise DBError("history_limit must be non-negative")
    if prior_review_limit < 0:
        raise DBError("prior_review_limit must be non-negative")
    if goal_limit < 0:
        raise DBError("goal_limit must be non-negative")
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
            before=entry.occurred_at or entry.created_at,
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
    self_note = get_person_note(conn, self_person.id)
    return EntryContext(
        entry=entry,
        self_person=self_person,
        self_notes=self_note.body if self_note is not None else None,
        people=tuple(people),
        prior_feedback=prior_feedback,
        goals=list_goals(conn, limit=goal_limit),
        prior_reviews=tuple(reversed(latest_reviews)),
        total_goal_count=conn.execute(
            "SELECT COUNT(*) FROM goals WHERE status = 'active'"
        ).fetchone()[0],
        goal_limit=goal_limit,
    )
