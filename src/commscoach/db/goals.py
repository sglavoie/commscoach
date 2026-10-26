"""CRUD for coaching goals and short-horizon experiments."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Goal, Person, Review
from . import common
from .errors import DBError, GoalNotFoundError, ReviewNotFoundError
from .people import resolve_person_handles
from .reviews import get_review
from .rows import hydrate_goal

GOAL_STATUSES = frozenset({"active", "done", "dropped"})


def _validate_title(title: Any) -> str:
    if not isinstance(title, str) or not title.strip():
        raise DBError("goal title must be a non-empty string")
    return title


def _validate_status(status: Any) -> str:
    if status not in GOAL_STATUSES:
        raise DBError("goal status must be 'active', 'done', or 'dropped'")
    return status


def _resolve_person(conn: sqlite3.Connection, person_handle: str | None) -> Person | None:
    if person_handle is None:
        return None
    return resolve_person_handles(conn, (person_handle,))[0]


def _resolve_review(conn: sqlite3.Connection, review_id: int | None) -> Review | None:
    if review_id is None:
        return None
    review = get_review(conn, review_id)
    if review is None:
        raise ReviewNotFoundError(review_id)
    return review


def create_goal(
    conn: sqlite3.Connection,
    title: str,
    *,
    detail: str | None = None,
    person_handle: str | None = None,
    review_id: int | None = None,
    now: str | None = None,
) -> Goal:
    """Create an active goal after resolving all references."""
    title = _validate_title(title)
    person = _resolve_person(conn, person_handle)
    review = _resolve_review(conn, review_id)
    timestamp = now if now is not None else common.utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO goals"
            " (title, detail, status, person_id, review_id, outcome, closed_at,"
            " created_at, updated_at)"
            " VALUES (?, ?, 'active', ?, ?, NULL, NULL, ?, ?)",
            (
                title,
                detail,
                person.id if person is not None else None,
                review.id if review is not None else None,
                timestamp,
                timestamp,
            ),
        )
    goal = get_goal(conn, cur.lastrowid)
    assert goal is not None
    return goal


def update_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    *,
    title: Any = common.UNSET,
    detail: Any = common.UNSET,
    outcome: Any = common.UNSET,
    person_handle: Any = common.UNSET,
    review_id: Any = common.UNSET,
    status: Any = common.UNSET,
    now: str | None = None,
) -> Goal:
    """Update supplied goal fields, explicitly maintaining ``closed_at``."""
    current = get_goal(conn, goal_id)
    if current is None:
        raise GoalNotFoundError(goal_id)

    if title is not common.UNSET:
        title = _validate_title(title)
    if status is not common.UNSET:
        status = _validate_status(status)
    person = (
        _resolve_person(conn, person_handle) if person_handle is not common.UNSET else common.UNSET
    )
    review = _resolve_review(conn, review_id) if review_id is not common.UNSET else common.UNSET

    updates: list[str] = []
    params: list[Any] = []
    for column, value in (("title", title), ("detail", detail), ("outcome", outcome)):
        if value is not common.UNSET:
            updates.append(f"{column} = ?")
            params.append(value)
    if person is not common.UNSET:
        updates.append("person_id = ?")
        params.append(person.id if person is not None else None)
    if review is not common.UNSET:
        updates.append("review_id = ?")
        params.append(review.id if review is not None else None)

    status_changed = status is not common.UNSET and status != current.status
    if not updates and not status_changed:
        return current

    timestamp = now if now is not None else common.utcnow_iso()
    if status_changed:
        updates.extend(("status = ?", "closed_at = ?"))
        params.extend((status, None if status == "active" else timestamp))
    updates.append("updated_at = ?")
    params.append(timestamp)
    params.append(goal_id)
    with conn:
        conn.execute(
            f"UPDATE goals SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    updated = get_goal(conn, goal_id)
    assert updated is not None
    return updated


def close_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    *,
    outcome: str | None = None,
    dropped: bool = False,
    now: str | None = None,
) -> Goal:
    """Close a goal, preserving its outcome when none is supplied.

    Repeating the same closure is a no-op. Correcting only the outcome keeps
    the original closure time; changing status records a new closure time.
    """
    current = get_goal(conn, goal_id)
    if current is None:
        raise GoalNotFoundError(goal_id)
    status = "dropped" if dropped else "done"
    outcome = current.outcome if outcome is None else outcome
    if current.status == status and current.outcome == outcome:
        return current
    timestamp = now if now is not None else common.utcnow_iso()
    closed_at = current.closed_at if current.status == status else timestamp
    with conn:
        conn.execute(
            "UPDATE goals SET status = ?, outcome = ?, closed_at = ?, updated_at = ? WHERE id = ?",
            (status, outcome, closed_at, timestamp, goal_id),
        )
    goal = get_goal(conn, goal_id)
    assert goal is not None
    return goal


def get_goal(conn: sqlite3.Connection, goal_id: int) -> Goal | None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        return None
    return hydrate_goal(conn, row)


def list_goals(
    conn: sqlite3.Connection,
    *,
    status: str | None = "active",
    person_handle: str | None = None,
    limit: int | None = None,
    oldest_first: bool = False,
    include_general: bool = False,
    review_id: int | None = None,
    query: str | None = None,
    prioritize_person_ids: tuple[int, ...] = (),
) -> tuple[Goal, ...]:
    """List goals by creation time; all-status lists put active goals first.

    With a person filter, include_general also selects unlinked goals.
    A review filter selects only goals originating from that review.
    Query matches literal substrings in titles, details, and outcomes.
    An unknown person handle is an error, even when the limit is zero.
    Optional person priorities put their goals first, then general goals, then
    other goals, preserving chronological order within each group.
    """
    if limit is not None and limit < 0:
        raise DBError("limit must be non-negative")
    filters, params = _goal_filters(
        conn,
        status=status,
        person_handle=person_handle,
        include_general=include_general,
        review_id=review_id,
        query=query,
    )
    sql = "SELECT * FROM goals" + filters
    direction = "ASC" if oldest_first else "DESC"
    sql += " ORDER BY "
    if status is None:
        sql += "CASE status WHEN 'active' THEN 0 ELSE 1 END, "
    if prioritize_person_ids:
        placeholders = ", ".join("?" for _ in prioritize_person_ids)
        sql += (
            f"CASE WHEN person_id IN ({placeholders}) THEN 0 "
            "WHEN person_id IS NULL THEN 1 ELSE 2 END, "
        )
        params.extend(prioritize_person_ids)
    sql += f"created_at {direction}, id {direction}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return tuple(hydrate_goal(conn, row) for row in rows)


def count_goals(
    conn: sqlite3.Connection,
    *,
    status: str | None = "active",
    person_handle: str | None = None,
    include_general: bool = False,
    review_id: int | None = None,
    query: str | None = None,
) -> int:
    """Count goals using the same filters as list_goals, without a limit."""
    filters, params = _goal_filters(
        conn,
        status=status,
        person_handle=person_handle,
        include_general=include_general,
        review_id=review_id,
        query=query,
    )
    return conn.execute("SELECT COUNT(*) FROM goals" + filters, params).fetchone()[0]


def _goal_filters(
    conn: sqlite3.Connection,
    *,
    status: str | None,
    person_handle: str | None,
    include_general: bool,
    review_id: int | None,
    query: str | None,
) -> tuple[str, list[Any]]:
    if status is not None:
        status = _validate_status(status)
    if include_general and person_handle is None:
        raise DBError("include_general requires person_handle")
    where: list[str] = []
    params: list[Any] = []
    if query is not None:
        if not query.strip():
            raise DBError("query must be a non-empty string")
        literal = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        where.append(
            "("
            + " OR ".join(
                f"{column} LIKE ? ESCAPE '!' COLLATE NOCASE"
                for column in ("title", "detail", "outcome")
            )
            + ")"
        )
        params.extend([f"%{literal}%"] * 3)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if person_handle is not None:
        person = _resolve_person(conn, person_handle)
        person_filter = "goals.person_id = ?"
        where.append(
            f"({person_filter} OR goals.person_id IS NULL)" if include_general else person_filter
        )
        params.append(person.id)
    if review_id is not None:
        where.append("review_id = ?")
        params.append(review_id)
    return (" WHERE " + " AND ".join(where) if where else ""), params


def delete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return cur.rowcount > 0
