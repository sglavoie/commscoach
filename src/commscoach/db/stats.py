"""Windowed aggregate statistics for the communication journal."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from ..models import Stats
from .common import date_bound_timestamp, read_snapshot, validate_range_bounds
from .errors import DBError


def _where_window(
    expression: str,
    since: str | None,
    until: str | None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if since is not None:
        clauses.append(f"{expression} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{expression} <= ?")
        params.append(until)
    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def _count(conn: sqlite3.Connection, sql: str, params: list[Any]) -> int:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


def _ranked_counts(
    conn: sqlite3.Connection,
    sql: str,
    params: list[Any],
) -> tuple[tuple[str, int], ...]:
    return tuple((str(row[0]), int(row[1])) for row in conn.execute(sql, params))


def _local_day(value: str) -> date:
    """Return the system-local calendar day of a stored timestamp.

    Weeks are the user's weeks: a Sunday-evening entry west of UTC belongs to
    that Sunday's week, not to the following one. Converting each timestamp
    separately keeps this right across DST changes.
    """
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DBError(f"invalid effective entry timestamp: {value!r}") from exc
    if stamp.tzinfo is not None:
        try:
            stamp = stamp.astimezone()
        except (OverflowError, OSError):
            pass
    return stamp.date()


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _weekly_metrics(
    timestamps: list[str],
    *,
    anchor: date,
) -> tuple[float, int, int, tuple[tuple[str, int], ...]]:
    """Return cadence, current and longest weekly streaks, and week buckets.

    The current streak is the run of consecutive active weeks ending in the
    ``anchor`` week or the one before it (so a week not yet logged does not
    break it); a run that ended earlier is no longer current.
    """
    if not timestamps:
        return 0.0, 0, 0, ()

    counts = Counter(_monday(_local_day(timestamp)) for timestamp in timestamps)
    occupied = sorted(counts)
    first_monday, last_monday = occupied[0], occupied[-1]
    span_weeks = ((last_monday - first_monday).days // 7) + 1

    buckets: list[tuple[str, int]] = []
    cursor = first_monday
    while cursor <= last_monday:
        buckets.append((cursor.isoformat(), counts.get(cursor, 0)))
        cursor += timedelta(days=7)

    longest = 1
    run = 1
    for previous, current in pairwise(occupied):
        if (current - previous).days == 7:
            run += 1
        else:
            run = 1
        longest = max(longest, run)

    current_streak = 0
    if (_monday(anchor) - last_monday).days <= 7:
        current_streak = 1
        for previous, current in reversed(list(pairwise(occupied))):
            if (current - previous).days != 7:
                break
            current_streak += 1

    return len(timestamps) / span_weeks, current_streak, longest, tuple(buckets)


def _collect_stats(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    top: int = 5,
    today: date | None = None,
) -> Stats:
    """Return windowed counts, weekly cadence, streaks, and top dimensions.

    Weeks are local Monday-to-Sunday weeks. The current streak is anchored at
    ``until`` when given, otherwise at ``today`` (default: the local date).

    Entry cadence and linked-dimension counts use the entry's effective time,
    ``COALESCE(occurred_at, created_at)``. Feedback coverage is likewise based
    on entries in that window. Reviews use ``created_at``; active goals use
    ``created_at`` and closed goals use ``closed_at``.
    """
    if top < 0:
        raise DBError("top must be non-negative")
    validate_range_bounds(since, until, since_name="since", until_name="until")
    anchor = today if today is not None else date.today()
    if until is not None:
        anchor = min(anchor, _local_day(date_bound_timestamp(until, name="until", upper=True)))
    since = date_bound_timestamp(since, name="since") if since is not None else None
    until = date_bound_timestamp(until, name="until", upper=True) if until is not None else None
    entry_where, entry_params = _where_window("COALESCE(e.occurred_at, e.created_at)", since, until)
    review_where, review_params = _where_window("r.created_at", since, until)
    open_goal_where, open_goal_params = _where_window("g.created_at", since, until)
    closed_goal_where, closed_goal_params = _where_window("g.closed_at", since, until)

    entry_rows = conn.execute(
        "SELECT e.id, COALESCE(e.occurred_at, e.created_at) AS effective_at "
        f"FROM entries e WHERE {entry_where} "
        "ORDER BY effective_at ASC, e.id ASC",
        entry_params,
    ).fetchall()
    entry_count = len(entry_rows)
    timestamps = [str(row["effective_at"]) for row in entry_rows]
    entries_per_week, current_streak, longest_streak, buckets = _weekly_metrics(
        timestamps, anchor=anchor
    )

    feedback_count = _count(
        conn,
        f"SELECT COUNT(*) FROM feedback f JOIN entries e ON e.id = f.entry_id WHERE {entry_where}",
        entry_params,
    )
    entries_with_feedback = _count(
        conn,
        "SELECT COUNT(DISTINCT e.id) FROM entries e "
        "JOIN feedback f ON f.entry_id = e.id "
        f"WHERE {entry_where}",
        entry_params,
    )
    people_count = _count(
        conn,
        "SELECT COUNT(DISTINCT ep.person_id) FROM entry_people ep "
        "JOIN entries e ON e.id = ep.entry_id "
        f"WHERE {entry_where}",
        entry_params,
    )
    tag_count = _count(
        conn,
        "SELECT COUNT(DISTINCT et.tag_id) FROM entry_tags et "
        "JOIN entries e ON e.id = et.entry_id "
        f"WHERE {entry_where}",
        entry_params,
    )
    review_count = _count(
        conn,
        f"SELECT COUNT(*) FROM reviews r WHERE {review_where}",
        review_params,
    )
    last_review_row = conn.execute(
        "SELECT r.created_at FROM reviews r "
        f"WHERE {review_where} ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        review_params,
    ).fetchone()
    open_goal_count = _count(
        conn,
        f"SELECT COUNT(*) FROM goals g WHERE g.status = 'active' AND {open_goal_where}",
        open_goal_params,
    )
    closed_goal_count = _count(
        conn,
        f"SELECT COUNT(*) FROM goals g WHERE g.status != 'active' AND {closed_goal_where}",
        closed_goal_params,
    )

    top_tags = _ranked_counts(
        conn,
        "SELECT t.name, COUNT(*) AS uses FROM entry_tags et "
        "JOIN tags t ON t.id = et.tag_id JOIN entries e ON e.id = et.entry_id "
        f"WHERE {entry_where} GROUP BY t.id, t.name "
        "ORDER BY uses DESC, t.name COLLATE NOCASE ASC LIMIT ?",
        [*entry_params, top],
    )
    top_sources = _ranked_counts(
        conn,
        "SELECT e.source, COUNT(*) AS uses FROM entries e "
        f"WHERE {entry_where} AND e.source IS NOT NULL AND e.source != '' "
        "GROUP BY e.source ORDER BY uses DESC, e.source COLLATE NOCASE ASC LIMIT ?",
        [*entry_params, top],
    )
    top_people = _ranked_counts(
        conn,
        "SELECT p.handle, COUNT(*) AS uses FROM entry_people ep "
        "JOIN people p ON p.id = ep.person_id JOIN entries e ON e.id = ep.entry_id "
        f"WHERE {entry_where} GROUP BY p.id, p.handle "
        "ORDER BY uses DESC, p.handle COLLATE NOCASE ASC LIMIT ?",
        [*entry_params, top],
    )

    return Stats(
        entry_count=entry_count,
        people_count=people_count,
        tag_count=tag_count,
        feedback_count=feedback_count,
        review_count=review_count,
        entries_with_feedback=entries_with_feedback,
        open_goal_count=open_goal_count,
        closed_goal_count=closed_goal_count,
        first_entry=timestamps[0] if timestamps else None,
        last_entry=timestamps[-1] if timestamps else None,
        last_review=str(last_review_row[0]) if last_review_row is not None else None,
        entries_per_week=entries_per_week,
        current_weekly_streak=current_streak,
        longest_weekly_streak=longest_streak,
        weekly_buckets=buckets,
        top_tags=top_tags,
        top_sources=top_sources,
        top_people=top_people,
    )


def get_stats(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    top: int = 5,
    today: date | None = None,
) -> Stats:
    """Return windowed counts, weekly cadence, streaks, and top dimensions.

    All figures come from one snapshot; see :func:`_collect_stats`.
    """
    with read_snapshot(conn):
        return _collect_stats(conn, since=since, until=until, top=top, today=today)
