"""CRUD for stored review syntheses and their covered entries."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Iterable

from ..models import Entry, Person, Review
from . import common
from . import tags as tags_mod
from .entries import get_entry
from .errors import DBError, EntryNotFoundError, ReviewNotFoundError
from .people import resolve_person_handles
from .rows import hydrate_review

#: Every scope axis a review may cover, mirroring the ``scope_kind`` CHECK in
#: the ``reviews`` DDL. Re-exported through ``commscoach.db`` so the CLI can use
#: it as its ``--scope`` argparse ``choices`` without duplicating the list.
SCOPE_KINDS = ("latest", "person", "tag", "range")

# Which keyword argument names each kind owns. Anything not owned by the chosen
# kind must be absent: passing ``scope_tag`` with ``scope_kind='latest'`` is a
# caller bug, and a named Python error beats the raw CHECK failure it would
# otherwise become.
_SCOPE_AXES = {
    "latest": (),
    "person": ("scope_person_handle",),
    "tag": ("scope_tag",),
    "range": ("scope_since", "scope_until"),
}


def _validate_scope_date(value: Any, *, what: str) -> str:
    """Normalize an ISO date bound, rejecting anything else."""
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise DBError(f"{what} must be an ISO date (YYYY-MM-DD)") from exc


def _resolve_review_entries(
    conn: sqlite3.Connection, entry_ids: Iterable[int] | None
) -> list[Entry]:
    """Resolve entry ids preserving caller order and de-duping first hits."""
    if entry_ids is None:
        raise DBError("review must cover at least one entry")
    resolved: list[Entry] = []
    seen: set[int] = set()
    for entry_id in entry_ids:
        entry = get_entry(conn, entry_id)
        if entry is None:
            raise EntryNotFoundError(entry_id)
        if entry.id in seen:
            continue
        seen.add(entry.id)
        resolved.append(entry)
    if not resolved:
        raise DBError("review must cover at least one entry")
    return resolved


def _validate_scope(
    scope_kind: str,
    *,
    scope_person_handle: str | None,
    scope_tag: str | None,
    scope_since: str | None,
    scope_until: str | None,
) -> dict[str, Any]:
    """Check one scope axis against its kind and return the normalized axes.

    Rejects both a missing axis (a ``tag`` scope with no tag) and an axis
    belonging to another kind (a ``latest`` scope carrying a tag). The person
    handle is left untouched here; ``create_review`` resolves it separately so
    the lookup happens before any write.
    """
    if scope_kind not in SCOPE_KINDS:
        raise DBError(
            "review scope_kind must be one of: " + ", ".join(SCOPE_KINDS)
        )
    supplied = {
        "scope_person_handle": scope_person_handle,
        "scope_tag": scope_tag,
        "scope_since": scope_since,
        "scope_until": scope_until,
    }
    owned = _SCOPE_AXES[scope_kind]
    for name, value in supplied.items():
        if value is not None and name not in owned:
            raise DBError(
                f"review scope_kind '{scope_kind}' does not accept {name}"
            )

    axes: dict[str, Any] = {
        "scope_person_handle": None,
        "scope_tag": None,
        "scope_since": None,
        "scope_until": None,
    }
    if scope_kind == "person":
        if scope_person_handle is None or not str(scope_person_handle).strip():
            raise DBError("review scope_kind 'person' requires scope_person_handle")
        axes["scope_person_handle"] = scope_person_handle
    elif scope_kind == "tag":
        normalized = tags_mod.normalize_tag(scope_tag or "")
        if not normalized:
            raise DBError("review scope_tag must be a non-empty string")
        axes["scope_tag"] = normalized
    elif scope_kind == "range":
        if scope_since is None and scope_until is None:
            raise DBError(
                "review scope_kind 'range' requires scope_since or scope_until"
            )
        if scope_since is not None:
            axes["scope_since"] = _validate_scope_date(
                scope_since, what="review scope_since"
            )
        if scope_until is not None:
            axes["scope_until"] = _validate_scope_date(
                scope_until, what="review scope_until"
            )
        since, until = axes["scope_since"], axes["scope_until"]
        if since is not None and until is not None and since > until:
            raise DBError("review scope_since must not be after scope_until")
    return axes


def create_review(
    conn: sqlite3.Connection,
    body: str,
    entry_ids: Iterable[int],
    scope_kind: str = "latest",
    scope_limit: int | None = None,
    scope_person_handle: str | None = None,
    scope_tag: str | None = None,
    scope_since: str | None = None,
    scope_until: str | None = None,
) -> Review:
    """Store a review synthesis covering existing entries over one scope axis.

    ``scope_kind`` selects which axis applies and only that axis may be
    supplied. Everything is validated and resolved before the transaction opens
    so a bad handle, tag, or date leaves no partial write behind.
    """
    if not body or not body.strip():
        raise DBError("review body must be a non-empty string")
    if scope_limit is not None and scope_limit < 0:
        raise DBError("review scope_limit must be non-negative")
    axes = _validate_scope(
        scope_kind,
        scope_person_handle=scope_person_handle,
        scope_tag=scope_tag,
        scope_since=scope_since,
        scope_until=scope_until,
    )
    scope_person: Person | None = None
    if axes["scope_person_handle"] is not None:
        # Raises PersonNotFoundError before any insert, and canonicalizes the
        # stored handle so 'ALICE' is recorded as the person's own casing.
        (scope_person,) = resolve_person_handles(conn, [axes["scope_person_handle"]])
        axes["scope_person_handle"] = scope_person.handle
    entries = _resolve_review_entries(conn, entry_ids)
    now = common.utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO reviews"
            " (body, scope_kind, scope_limit, scope_person_id,"
            " scope_person_handle, scope_tag, scope_since, scope_until,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body,
                scope_kind,
                scope_limit,
                scope_person.id if scope_person is not None else None,
                axes["scope_person_handle"],
                axes["scope_tag"],
                axes["scope_since"],
                axes["scope_until"],
                now,
                now,
            ),
        )
        review_id = cur.lastrowid
        for position, entry in enumerate(entries):
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, ?)",
                (review_id, entry.id, position),
            )
    review = get_review(conn, review_id)
    assert review is not None
    return review


def get_review(conn: sqlite3.Connection, review_id: int) -> Review | None:
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    return hydrate_review(conn, row)


def list_reviews(
    conn: sqlite3.Connection,
    limit: int | None = None,
    *,
    scope_kind: str | None = None,
    person_handle: str | None = None,
    tag: str | None = None,
) -> tuple[Review, ...]:
    """Return stored reviews newest-first by creation time.

    The filters are additive and combine with AND; omitting them all preserves
    the original unfiltered listing. ``person_handle`` matches
    ``scope_person_handle`` case-insensitively rather than the ``scope_person_id``
    FK, so reviews about a since-deleted person are still findable. ``tag``
    matches the normalized ``scope_tag``.
    """
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    where: list[str] = []
    params: list[Any] = []
    if scope_kind is not None:
        if scope_kind not in SCOPE_KINDS:
            raise DBError(
                "review scope_kind must be one of: " + ", ".join(SCOPE_KINDS)
            )
        where.append("scope_kind = ?")
        params.append(scope_kind)
    if person_handle is not None:
        where.append("LOWER(scope_person_handle) = LOWER(?)")
        params.append(person_handle)
    if tag is not None:
        where.append("scope_tag = ?")
        params.append(tags_mod.normalize_tag(tag))
    sql = "SELECT * FROM reviews"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(hydrate_review(conn, row) for row in rows)


def update_review(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    body: Any = common.UNSET,
) -> Review:
    """Update a stored review's body, advancing ``updated_at``.

    Body only, deliberately. ``scope_kind``, ``scope_limit``, and the covered-entry
    list are facts about how the synthesis was produced, not editable content --
    letting them be rewritten would make the stored record lie about its own
    provenance. Do not add scope or entry editing here; store a new review
    instead.
    """
    current = get_review(conn, review_id)
    if current is None:
        raise ReviewNotFoundError(review_id)
    if body is common.UNSET:
        return current
    if not body or not str(body).strip():
        raise DBError("review body must be a non-empty string")
    now = common.utcnow_iso()
    with conn:
        conn.execute(
            "UPDATE reviews SET body = ?, updated_at = ? WHERE id = ?",
            (body, now, review_id),
        )
    review = get_review(conn, review_id)
    assert review is not None
    return review


def delete_review(conn: sqlite3.Connection, review_id: int) -> bool:
    """Delete a stored review. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    return cur.rowcount > 0
