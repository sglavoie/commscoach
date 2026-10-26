"""Tag normalization, upsert, orphan collection, and vocabulary management."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from ..models import Tag
from . import common
from .errors import DBError, DuplicateTagError, TagNotFoundError


def normalize_tag(name: str) -> str:
    """Normalize a tag for storage: stripped, lowercased."""
    return name.strip().lower()


def normalize_tag_list(tags: Iterable[str]) -> list[str]:
    """Normalize and de-duplicate a tag iterable, dropping empties."""
    seen: set[str] = set()
    result: list[str] = []
    for value in tags:
        for tag in value.split(","):
            normalized = normalize_tag(tag)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
    return result


def upsert_tag(conn: sqlite3.Connection, name: str) -> int:
    """Insert tag if missing and return its id."""
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row["id"]


def gc_orphan_tags(conn: sqlite3.Connection) -> None:
    """Drop tag rows that no entry references anymore.

    Called after entry deletion or tag replacement so the ``tags`` table stays
    in sync with active links instead of accumulating dead names.
    """
    conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM entry_tags)")


def _require_name(value: str, *, what: str = "tag name") -> str:
    """Normalize a caller-supplied tag name, rejecting empties."""
    normalized = normalize_tag(value)
    if not normalized:
        raise DBError(f"{what} must be a non-empty string")
    return normalized


def _find_tag_id(conn: sqlite3.Connection, name: str) -> int | None:
    """Return the id of a normalized tag name, or ``None`` when absent."""
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return None if row is None else row["id"]


def get_tag(conn: sqlite3.Connection, name: str, *, hint: str | None = None) -> Tag:
    """Load a tag by name with its entry count, raising ``TagNotFoundError``."""
    tag_name = _require_name(name)
    tag_id = _find_tag_id(conn, tag_name)
    if tag_id is None:
        raise TagNotFoundError(tag_name, hint=hint)
    return _get_tag(conn, tag_id)


def count_tags(conn: sqlite3.Connection) -> int:
    """Return how many tags exist, for ``tag list --limit`` footers."""
    return conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]


def _get_tag(conn: sqlite3.Connection, tag_id: int) -> Tag:
    """Load one tag with its current entry count."""
    row = conn.execute(
        "SELECT t.id AS id, t.name AS name,"
        " COUNT(et.entry_id) AS entry_count"
        " FROM tags t LEFT JOIN entry_tags et ON et.tag_id = t.id"
        " WHERE t.id = ?"
        " GROUP BY t.id",
        (tag_id,),
    ).fetchone()
    assert row is not None
    return Tag(id=row["id"], name=row["name"], entry_count=row["entry_count"])


def list_tags(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> tuple[Tag, ...]:
    """Return every tag with its entry count, most-used first then by name.

    The ``LEFT JOIN`` keeps tags no entry references. Those are unreachable
    through normal entry CRUD (``gc_orphan_tags`` drops them) but a direct
    insert or an import can produce them, and a listing that hides rows would
    make ``tag rm`` look broken.
    """
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    sql = (
        "SELECT t.id AS id, t.name AS name,"
        " COUNT(et.entry_id) AS entry_count"
        " FROM tags t LEFT JOIN entry_tags et ON et.tag_id = t.id"
        " GROUP BY t.id"
        " ORDER BY entry_count DESC, t.name ASC"
    )
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(
        Tag(id=row["id"], name=row["name"], entry_count=row["entry_count"]) for row in rows
    )


@common.writes
def rename_tag(conn: sqlite3.Connection, old: str, new: str) -> Tag:
    """Rename a tag in place, keeping every entry link.

    Renaming onto a name that already exists is an error pointing at
    ``tag merge``: merging is destructive and is never done implicitly.

    ``reviews.scope_tag`` is denormalized text, not a foreign key, so the rename
    is carried into it in the same transaction; otherwise a rename would orphan
    the tag-scoped review history it is named after.
    """
    old_name = _require_name(old)
    new_name = _require_name(new, what="new tag name")
    if "," in new_name:
        # --tag splits on commas, so such a name could never be attached again.
        raise DBError(f"new tag name {new_name!r} must not contain a comma")
    tag_id = _find_tag_id(conn, old_name)
    if tag_id is None:
        raise TagNotFoundError(old_name)
    existing_id = _find_tag_id(conn, new_name)
    if existing_id is not None:
        if existing_id != tag_id:
            raise DuplicateTagError(new_name)
        # ``tags.name`` is COLLATE NOCASE, so a case-only rename resolves to
        # this very row rather than a conflicting one -- and ``normalize_tag``
        # lowercases anyway, so the stored name cannot actually change.
        assert old_name == new_name
        return _get_tag(conn, tag_id)
    with common.write_transaction(conn):
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
        conn.execute(
            "UPDATE reviews SET scope_tag = ? WHERE scope_tag = ?",
            (new_name, old_name),
        )
    return _get_tag(conn, tag_id)


@common.writes
def merge_tags(conn: sqlite3.Connection, source: str, dest: str) -> Tag:
    """Fold every ``source`` entry link into ``dest`` and drop ``source``.

    Both tags must already exist: a missing destination means the caller wanted
    ``tag rename``, and keeping the two commands non-overlapping is what makes
    "merges are never implicit" enforceable.

    Tag-scoped reviews follow the merge in the same transaction, for the same
    reason ``rename_tag`` carries the rename into ``reviews.scope_tag``.
    """
    source_name = _require_name(source, what="source tag name")
    dest_name = _require_name(dest, what="destination tag name")
    if source_name == dest_name:
        raise DBError("cannot merge a tag into itself")
    source_id = _find_tag_id(conn, source_name)
    if source_id is None:
        raise TagNotFoundError(source_name)
    dest_id = _find_tag_id(conn, dest_name)
    if dest_id is None:
        raise TagNotFoundError(
            dest_name,
            hint="use 'commscoach tag rename' to rename a tag to a new name",
        )
    with common.write_transaction(conn):
        # OR IGNORE is required: an entry carrying both tags already owns the
        # (entry_id, dest_id) primary key, and the re-point would collide.
        conn.execute(
            "UPDATE OR IGNORE entry_tags SET tag_id = ? WHERE tag_id = ?",
            (dest_id, source_id),
        )
        # Clears the links the UPDATE skipped, which are exactly the duplicates.
        conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (source_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
        conn.execute(
            "UPDATE reviews SET scope_tag = ? WHERE scope_tag = ?",
            (dest_name, source_name),
        )
    # No gc_orphan_tags here: the source row is deleted explicitly, and a blanket
    # sweep would also drop unrelated zero-entry tags, including the destination.
    return _get_tag(conn, dest_id)


@common.writes
def delete_tag(conn: sqlite3.Connection, name: str) -> int:
    """Unlink a tag from every entry and drop it. Returns the unlink count.

    ``reviews.scope_tag`` is deliberately left alone. A tag-scoped review is a
    historical record of a scope that existed when it was written, and clearing
    the name would both lose that and violate the
    ``(scope_kind = 'tag') = (scope_tag IS NOT NULL)`` CHECK. Deleting a tag
    therefore leaves reviews naming a tag the vocabulary no longer contains,
    which is the intended outcome -- unlike ``rename_tag`` and ``merge_tags``,
    where the tag itself survives under a different name.
    """
    tag_name = _require_name(name)
    tag_id = _find_tag_id(conn, tag_name)
    if tag_id is None:
        raise TagNotFoundError(tag_name)
    with common.write_transaction(conn):
        cur = conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
        unlinked = cur.rowcount
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    return unlinked
