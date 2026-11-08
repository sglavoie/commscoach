"""Goal checks: a feedback note's per-goal verdicts, and their progress trail.

The coaching rubric judges each relevant open goal as advanced, hurt, or
neutral. Storing those judgments beside the note, rather than only in its
prose, is what lets a goal show its trail. Checks are ordered by when the
checked interaction happened (its entry's occurrence time, falling back to its
creation time), then by when they were stored, so a backfilled note lands in
the trail where its conversation belongs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from ..models import GoalCheck, GoalCheckSummary
from . import common
from .errors import DBError, FeedbackNotFoundError, GoalNotFoundError

GOAL_CHECK_VERDICTS = ("advanced", "hurt", "neutral")

# One requested check: (goal_id, verdict, note or None).
GoalCheckSpec = tuple[int, str, str | None]

_SELECT = (
    "SELECT gc.id, gc.feedback_id, gc.goal_id, f.entry_id, gc.verdict, gc.note,"
    " COALESCE(e.occurred_at, e.created_at) AS at, gc.created_at"
    " FROM goal_checks gc"
    " JOIN feedback f ON f.id = gc.feedback_id"
    " JOIN entries e ON e.id = f.entry_id"
)
_CHRONOLOGICAL = " ORDER BY at ASC, gc.created_at ASC, gc.id ASC"
# Stays well under SQLite's host-parameter limit, which is 999 before 3.32.
_CHUNK = 500


def _row_to_goal_check(row: sqlite3.Row) -> GoalCheck:
    return GoalCheck(
        id=row["id"],
        feedback_id=row["feedback_id"],
        goal_id=row["goal_id"],
        entry_id=row["entry_id"],
        verdict=row["verdict"],
        note=row["note"],
        at=row["at"],
        created_at=row["created_at"],
    )


def _validate_verdict(verdict: Any) -> str:
    if verdict not in GOAL_CHECK_VERDICTS:
        raise DBError("goal check verdict must be 'advanced', 'hurt', or 'neutral'")
    return verdict


def _validate_note(note: Any) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str) or not note.strip():
        raise DBError("goal check note must be a non-empty string; omit it to leave it unset")
    if "\n" in note or "\r" in note:
        raise DBError("goal check note must be a single line")
    return note.strip()


def _validate_specs(
    conn: sqlite3.Connection, checks: Iterable[GoalCheckSpec]
) -> list[tuple[int, str, str | None]]:
    """Validate every check before any is written, naming the first problem."""
    validated: list[tuple[int, str, str | None]] = []
    seen: set[int] = set()
    for goal_id, verdict, note in checks:
        if goal_id in seen:
            raise DBError(f"goal {goal_id} is checked more than once; give one verdict per goal")
        seen.add(goal_id)
        verdict = _validate_verdict(verdict)
        note = _validate_note(note)
        if conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone() is None:
            raise GoalNotFoundError(goal_id)
        validated.append((goal_id, verdict, note))
    return validated


def validate_goal_checks(conn: sqlite3.Connection, checks: Iterable[GoalCheckSpec]) -> None:
    """Raise ``DBError`` unless every check could be stored as given.

    Lets a command reject a bad ``--goal-check`` before it opens an editor.
    """
    _validate_specs(conn, checks)


def _insert_checks(
    conn: sqlite3.Connection,
    feedback_id: int,
    checks: Sequence[tuple[int, str, str | None]],
    now: str,
) -> None:
    for goal_id, verdict, note in checks:
        conn.execute(
            "INSERT INTO goal_checks (feedback_id, goal_id, verdict, note, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (feedback_id, goal_id, verdict, note, now),
        )


@common.writes
def add_goal_checks(
    conn: sqlite3.Connection,
    feedback_id: int,
    checks: Iterable[GoalCheckSpec],
    *,
    now: str | None = None,
) -> tuple[GoalCheck, ...]:
    """Record ``(goal_id, verdict, note)`` checks on an existing feedback note.

    All or nothing: an unknown goal, an invalid verdict or note, a goal named
    twice, or a goal the note has already checked stores none of them.
    Returns the note's complete set of checks in trail order.
    """
    if conn.execute("SELECT 1 FROM feedback WHERE id = ?", (feedback_id,)).fetchone() is None:
        raise FeedbackNotFoundError(feedback_id)
    validated = _validate_specs(conn, checks)
    for goal_id, _verdict, _note in validated:
        existing = conn.execute(
            "SELECT 1 FROM goal_checks WHERE feedback_id = ? AND goal_id = ?",
            (feedback_id, goal_id),
        ).fetchone()
        if existing is not None:
            raise DBError(
                f"feedback {feedback_id} already checks goal {goal_id};"
                " replace its checks with 'commscoach feedback edit"
                f" {feedback_id} --goal-check ...'"
            )
    _insert_checks(conn, feedback_id, validated, now or common.utcnow_iso())
    return list_feedback_goal_checks(conn, (feedback_id,)).get(feedback_id, ())


@common.writes
def replace_goal_checks(
    conn: sqlite3.Connection,
    feedback_id: int,
    checks: Iterable[GoalCheckSpec],
    *,
    now: str | None = None,
) -> tuple[GoalCheck, ...]:
    """Replace a feedback note's checks with ``checks``; an empty list clears them.

    A goal whose verdict and note are unchanged keeps its stored row (and its
    ``created_at``), so re-submitting the same set is a no-op.
    """
    if conn.execute("SELECT 1 FROM feedback WHERE id = ?", (feedback_id,)).fetchone() is None:
        raise FeedbackNotFoundError(feedback_id)
    validated = _validate_specs(conn, checks)
    current = {
        row["goal_id"]: (row["verdict"], row["note"])
        for row in conn.execute(
            "SELECT goal_id, verdict, note FROM goal_checks WHERE feedback_id = ?",
            (feedback_id,),
        )
    }
    wanted = {goal_id: (verdict, note) for goal_id, verdict, note in validated}
    stale = [goal_id for goal_id, value in current.items() if wanted.get(goal_id) != value]
    for goal_id in stale:
        conn.execute(
            "DELETE FROM goal_checks WHERE feedback_id = ? AND goal_id = ?",
            (feedback_id, goal_id),
        )
    fresh = [check for check in validated if current.get(check[0]) != (check[1], check[2])]
    _insert_checks(conn, feedback_id, fresh, now or common.utcnow_iso())
    return list_feedback_goal_checks(conn, (feedback_id,)).get(feedback_id, ())


def list_goal_checks(conn: sqlite3.Connection, goal_id: int) -> tuple[GoalCheck, ...]:
    """Return one goal's checks in trail order (oldest interaction first)."""
    rows = conn.execute(f"{_SELECT} WHERE gc.goal_id = ?{_CHRONOLOGICAL}", (goal_id,)).fetchall()
    return tuple(_row_to_goal_check(row) for row in rows)


def list_feedback_goal_checks(
    conn: sqlite3.Connection, feedback_ids: Iterable[int]
) -> dict[int, tuple[GoalCheck, ...]]:
    """Return each listed note's checks, ordered by goal id; notes without any are absent."""
    unique = list(dict.fromkeys(feedback_ids))
    grouped: dict[int, list[GoalCheck]] = {}
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        placeholders = ", ".join("?" * len(chunk))
        for row in conn.execute(
            f"{_SELECT} WHERE gc.feedback_id IN ({placeholders})"
            " ORDER BY gc.feedback_id, gc.goal_id",
            chunk,
        ):
            check = _row_to_goal_check(row)
            grouped.setdefault(check.feedback_id, []).append(check)
    return {feedback_id: tuple(checks) for feedback_id, checks in grouped.items()}


def goal_check_summaries(
    conn: sqlite3.Connection, goal_ids: Iterable[int]
) -> dict[int, GoalCheckSummary]:
    """Summarize the checks of each listed goal in one pass per chunk.

    Every requested id gets a summary; a goal with no checks (or an unknown id)
    gets the empty one, whose ``total`` is zero, so callers can index the result
    without a membership test.
    """
    unique = list(dict.fromkeys(goal_ids))
    counts: dict[int, dict[str, int]] = {}
    last: dict[int, tuple[str, str]] = {}
    first_advanced: dict[int, str] = {}
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        placeholders = ", ".join("?" * len(chunk))
        for row in conn.execute(
            f"{_SELECT} WHERE gc.goal_id IN ({placeholders}){_CHRONOLOGICAL}", chunk
        ):
            goal_id, verdict, at = row["goal_id"], row["verdict"], row["at"]
            tally = counts.setdefault(goal_id, dict.fromkeys(GOAL_CHECK_VERDICTS, 0))
            tally[verdict] += 1
            last[goal_id] = (verdict, at)
            if verdict == "advanced":
                first_advanced.setdefault(goal_id, at)
    summaries: dict[int, GoalCheckSummary] = {}
    for goal_id in unique:
        if goal_id not in counts:
            summaries[goal_id] = GoalCheckSummary()
            continue
        tally = counts[goal_id]
        summaries[goal_id] = GoalCheckSummary(
            advanced=tally["advanced"],
            hurt=tally["hurt"],
            neutral=tally["neutral"],
            last_verdict=last[goal_id][0],
            last_at=last[goal_id][1],
            first_advanced_at=first_advanced.get(goal_id),
        )
    return summaries
