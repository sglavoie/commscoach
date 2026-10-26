"""CRUD and search for the ``entries`` table and its tag/people links."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any, Literal

from ..models import Entry
from ..timeparse import TimeParseError, parse_when
from . import common
from . import tags as tags_mod
from .errors import DBError, EntryNotFoundError
from .people import resolve_person_handles
from .rows import hydrate_entry


def _validate_occurred_at(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DBError("occurred_at must be a string or None")
    try:
        return parse_when(value)
    except TimeParseError as exc:
        raise DBError(f"invalid occurred_at: {exc}") from exc


def create_entry(
    conn: sqlite3.Connection,
    body: str,
    *,
    source: str | None = None,
    occurred_at: str | None = None,
    tags: Iterable[str] = (),
    people: Iterable[str] = (),
) -> Entry:
    """Create an entry plus its tag and people links in a single transaction.

    ``tags`` are normalized (stripped, lowercased) and de-duplicated before
    insertion. ``people`` are existing person handles; any unknown handle raises
    ``PersonNotFoundError`` *before* any row is inserted so the DB stays clean.
    """
    if not body or not body.strip():
        raise DBError("entry body must be a non-empty string")
    occurred_at = _validate_occurred_at(occurred_at)
    normalized_tags = tags_mod.normalize_tag_list(tags)
    resolved_people = resolve_person_handles(conn, people)
    now = common.utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (body, source, occurred_at, now, now),
        )
        entry_id = cur.lastrowid
        for tag in normalized_tags:
            tag_id = tags_mod.upsert_tag(conn, tag)
            conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_id),
            )
        for person in resolved_people:
            conn.execute(
                "INSERT OR IGNORE INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person.id),
            )
    entry = get_entry(conn, entry_id)
    assert entry is not None
    return entry


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    body: Any = common.UNSET,
    source: Any = common.UNSET,
    occurred_at: Any = common.UNSET,
    tags: Any = common.UNSET,
    people: Any = common.UNSET,
) -> Entry:
    """Update an entry. ``tags`` and ``people``, when provided, fully replace the
    existing links (pass ``()`` to clear them). Other column args update only
    when not ``UNSET``.
    """
    if get_entry(conn, entry_id) is None:
        raise EntryNotFoundError(entry_id)

    column_updates: list[str] = []
    params: list[Any] = []
    if body is not common.UNSET:
        if not body or not str(body).strip():
            raise DBError("entry body must be a non-empty string")
        column_updates.append("body = ?")
        params.append(body)
    if source is not common.UNSET:
        column_updates.append("source = ?")
        params.append(source)
    if occurred_at is not common.UNSET:
        occurred_at = _validate_occurred_at(occurred_at)
        column_updates.append("occurred_at = ?")
        params.append(occurred_at)

    normalized_tags = tags_mod.normalize_tag_list(tags) if tags is not common.UNSET else None
    resolved_people = resolve_person_handles(conn, people) if people is not common.UNSET else None

    now = common.utcnow_iso()
    with conn:
        if column_updates:
            column_updates.append("updated_at = ?")
            params.append(now)
            params.append(entry_id)
            conn.execute(
                f"UPDATE entries SET {', '.join(column_updates)} WHERE id = ?",
                params,
            )
        elif normalized_tags is not None or resolved_people is not None:
            conn.execute(
                "UPDATE entries SET updated_at = ? WHERE id = ?",
                (now, entry_id),
            )
        if normalized_tags is not None:
            conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
            for tag in normalized_tags:
                tag_id = tags_mod.upsert_tag(conn, tag)
                conn.execute(
                    "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, tag_id),
                )
            tags_mod.gc_orphan_tags(conn)
        if resolved_people is not None:
            conn.execute("DELETE FROM entry_people WHERE entry_id = ?", (entry_id,))
            for person in resolved_people:
                conn.execute(
                    "INSERT OR IGNORE INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                    (entry_id, person.id),
                )
    updated = get_entry(conn, entry_id)
    assert updated is not None
    return updated


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Entry | None:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    return hydrate_entry(conn, row)


def list_entries(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> tuple[Entry, ...]:
    """Return entries newest-first, optionally bounded by date and count."""
    return search_entries(
        conn,
        since=since,
        until=until,
        limit=limit,
    )


def list_entries_with_person(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    exclude_entry_id: int,
    limit: int,
    before: str | None = None,
) -> tuple[Entry, ...]:
    """Return other entries newest-first, optionally strictly before an instant."""
    if limit < 0:
        raise DBError("limit must be non-negative")
    where, params = _person_history_filter(person_id, exclude_entry_id, before)
    sql = "SELECT e.* FROM entries e" + where
    sql += " ORDER BY COALESCE(e.occurred_at, e.created_at) DESC, e.id DESC LIMIT ?"
    rows = conn.execute(sql, [*params, limit]).fetchall()
    return tuple(hydrate_entry(conn, row) for row in rows)


def _person_history_filter(
    person_id: int,
    exclude_entry_id: int,
    before: str | None,
) -> tuple[str, list[Any]]:
    sql = " JOIN entry_people ep ON ep.entry_id = e.id WHERE ep.person_id = ? AND e.id != ?"
    params: list[Any] = [person_id, exclude_entry_id]
    if before is not None:
        sql += " AND COALESCE(e.occurred_at, e.created_at) < ?"
        params.append(common.date_bound_timestamp(before, name="before"))
    return sql, params


def count_entries_with_person(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    exclude_entry_id: int,
    before: str | None = None,
) -> int:
    """Count the same eligible history as ``list_entries_with_person``."""
    where, params = _person_history_filter(person_id, exclude_entry_id, before)
    return conn.execute("SELECT COUNT(*) FROM entries e" + where, params).fetchone()[0]


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Delete an entry and GC orphan tags. Returns ``True`` if a row was deleted."""
    with conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        if cur.rowcount == 0:
            return False
        tags_mod.gc_orphan_tags(conn)
    return True


def _scope_filters(
    *,
    tag: str | None,
    person_handle: str | None,
    since: str | None,
    until: str | None,
) -> tuple[list[str], list[Any]]:
    """Share scope predicates between entry retrieval and completeness counts."""
    common.validate_range_bounds(since, until, since_name="since", until_name="until")
    where: list[str] = []
    params: list[Any] = []
    if tag:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM entry_tags et"
            " JOIN tags t ON t.id = et.tag_id"
            " WHERE et.entry_id = e.id AND t.name = ?)"
        )
        params.append(tags_mod.normalize_tag(tag))
    if person_handle:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM entry_people ep"
            " JOIN people p ON p.id = ep.person_id"
            " WHERE ep.entry_id = e.id AND LOWER(p.handle) = LOWER(?))"
        )
        params.append(person_handle)
    if since is not None:
        where.append("COALESCE(e.occurred_at, e.created_at) >= ?")
        params.append(common.date_bound_timestamp(since, name="since"))
    if until is not None:
        where.append("COALESCE(e.occurred_at, e.created_at) <= ?")
        params.append(common.date_bound_timestamp(until, name="until", upper=True))
    return where, params


def count_scoped_entries(
    conn: sqlite3.Connection,
    *,
    tag: str | None = None,
    person_handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Count a review's matching entries without hydrating the omitted rows."""
    where, params = _scope_filters(
        tag=tag,
        person_handle=person_handle,
        since=since,
        until=until,
    )
    sql = "SELECT COUNT(*) FROM entries e"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, params).fetchone()[0]


def search_entries(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    person_handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
    without_feedback: bool = False,
    limit: int | None = None,
    mode: Literal["auto", "like", "fts"] = "auto",
    oldest_first: bool = False,
) -> tuple[Entry, ...]:
    """Filter entries by content, relationships, effective date, and feedback.

    All filters combine with AND. ``query`` uses FTS5 when ``mode`` is ``auto``
    and the initialized database advertises support, while ``like`` forces a
    case-insensitive substring match and ``fts`` explicitly requests FTS5.
    Malformed automatic FTS queries silently retry with LIKE. ``source`` is a
    case-insensitive exact match on the stored channel. ``tag`` matches
    the normalized tag name; ``person_handle`` matches case-insensitively.
    ``since`` and ``until`` are
    inclusive UTC dates or timezone-aware timestamps applied to ``COALESCE(occurred_at, created_at)``;
    ``without_feedback`` selects the coaching backlog. Results are newest-first
    unless ``oldest_first`` is set; ordering is applied before the limit.
    """
    rows = _search_entry_rows(
        conn,
        query=query,
        source=source,
        tag=tag,
        person_handle=person_handle,
        since=since,
        until=until,
        without_feedback=without_feedback,
        limit=limit,
        mode=mode,
        oldest_first=oldest_first,
    )
    return tuple(hydrate_entry(conn, row) for row in rows)


def count_search_entries(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    person_handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
    without_feedback: bool = False,
    mode: Literal["auto", "like", "fts"] = "auto",
) -> int:
    """Count all search matches using the same predicates and FTS fallback."""
    rows = _search_entry_rows(
        conn,
        query=query,
        source=source,
        tag=tag,
        person_handle=person_handle,
        since=since,
        until=until,
        without_feedback=without_feedback,
        mode=mode,
        count_only=True,
    )
    return rows[0][0]


def _search_entry_rows(
    conn: sqlite3.Connection,
    *,
    query: str | None,
    source: str | None,
    tag: str | None,
    person_handle: str | None,
    since: str | None,
    until: str | None,
    without_feedback: bool,
    mode: Literal["auto", "like", "fts"],
    limit: int | None = None,
    oldest_first: bool = False,
    count_only: bool = False,
) -> list[sqlite3.Row]:
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    if mode not in {"auto", "like", "fts"}:
        raise DBError("mode must be one of: auto, like, fts")
    where, params = _scope_filters(
        tag=tag,
        person_handle=person_handle,
        since=since,
        until=until,
    )
    if source is not None:
        where.append("e.source = ? COLLATE NOCASE")
        params.append(source)
    use_fts = False
    if query:
        fts_row = conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()
        fts_enabled = fts_row is not None and fts_row[0] == "1"
        if mode == "fts" and not fts_enabled:
            raise DBError("FTS5 search is unavailable")
        use_fts = mode in {"auto", "fts"} and fts_enabled
        if use_fts:
            where.append("entries_fts MATCH ?")
            params.append(query)
        else:
            where.append("e.body LIKE ? ESCAPE '!' COLLATE NOCASE")
            literal_query = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            params.append(f"%{literal_query}%")
    if without_feedback:
        where.append("NOT EXISTS (SELECT 1 FROM feedback f WHERE f.entry_id = e.id)")
    sql = "SELECT " + ("COUNT(*)" if count_only else "e.*") + " FROM entries e"
    if use_fts:
        sql += " JOIN entries_fts ON entries_fts.rowid = e.id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if not count_only:
        direction = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY COALESCE(e.occurred_at, e.created_at) {direction}, e.id {direction}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        if not use_fts or mode != "auto":
            raise
        return _search_entry_rows(
            conn,
            query=query,
            source=source,
            tag=tag,
            person_handle=person_handle,
            since=since,
            until=until,
            without_feedback=without_feedback,
            limit=limit,
            mode="like",
            oldest_first=oldest_first,
            count_only=count_only,
        )
    return rows


def search_entry_snippets(
    conn: sqlite3.Connection,
    query: str,
    entry_ids: Iterable[int],
) -> dict[int, str]:
    """Return FTS excerpts for selected search results, keyed by entry ID.

    Let SQLite locate matches so phrases, prefixes, boolean queries, and
    diacritics follow the same tokenizer as search. Missing FTS support or an
    invalid query returns no snippets, matching auto search's LIKE fallback.
    Literal callers should use their own substring excerpts instead.
    """
    ids = tuple(dict.fromkeys(entry_ids))
    if not query or not ids:
        return {}
    enabled = conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()
    if enabled is None or enabled[0] != "1":
        return {}
    snippets: dict[int, str] = {}
    try:
        # Bound placeholders even when the caller searches without a limit.
        for start in range(0, len(ids), 500):
            batch = ids[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                "SELECT rowid, snippet(entries_fts, 0, '', '', '…', 32)"
                " FROM entries_fts WHERE entries_fts MATCH ?"
                f" AND rowid IN ({placeholders})",
                (query, *batch),
            ).fetchall()
            snippets.update((row[0], row[1]) for row in rows)
    except sqlite3.OperationalError:
        return {}
    return snippets
