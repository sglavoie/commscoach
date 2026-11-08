"""The home screen shown by a bare ``commscoach``: what deserves attention now.

It answers "what should I do next?" in one screen: the coaching backlog,
goals in play, relationships going quiet, an entry from this day in the past,
and the weekly rhythm. It only reads, never creates a database, and degrades to
an onboarding checklist on an empty or missing journal.
"""

from __future__ import annotations

import argparse
import shlex
from datetime import date, datetime, timedelta

from .. import db
from . import pretty_views, style
from .common import command_prefix, first_prose_line, open_db, parse_date_bound, plural

QUIET_DAYS = 28
QUIET_MIN_INTERACTIONS = 3
GOALS_SHOWN = 3
SPARK_WEEKS = 12


def _greeting(now: datetime) -> str:
    if now.hour < 5:
        return "Working late"
    if now.hour < 12:
        return "Good morning"
    if now.hour < 18:
        return "Good afternoon"
    return "Good evening"


def _cmd(args: argparse.Namespace, *words: str) -> str:
    return style.paint(shlex.join([*command_prefix(args), *words]), "cyan")


def _section(title: str) -> str:
    return "\n" + style.heading(title)


def _weekly_counts(stats: db.Stats, today: date, weeks: int) -> list[int]:
    """Entries per week for the last ``weeks`` weeks ending this week, zero-filled."""
    counts = dict(stats.weekly_buckets)
    monday = today - timedelta(days=today.weekday())
    return [
        counts.get((monday - timedelta(weeks=back)).isoformat(), 0)
        for back in range(weeks - 1, -1, -1)
    ]


def _on_this_day(conn, today: date) -> tuple[str, db.Entry] | None:  # type: ignore[no-untyped-def]
    """Pick one past entry from this calendar day: years ago first, then months."""
    candidates: list[tuple[str, date]] = []
    for years in range(1, 6):
        try:
            candidates.append(
                (f"{plural(years, 'year')} ago today", today.replace(year=today.year - years))
            )
        except ValueError:  # Feb 29
            continue
    for months, label in ((6, "six months ago"), (3, "three months ago"), (1, "a month ago")):
        year, month = divmod(today.month - 1 - months, 12)
        try:
            candidates.append(
                (f"{label} today", today.replace(year=today.year + year, month=month + 1))
            )
        except ValueError:
            continue
    for label, day in candidates:
        found = db.list_entries(
            conn,
            since=parse_date_bound(day.isoformat(), flag="--since"),
            until=parse_date_bound(day.isoformat(), flag="--until"),
            limit=1,
        )
        if found:
            return label.replace("1 year ago", "A year ago").capitalize(), found[0]
    return None


def _onboarding(args: argparse.Namespace, *, initialized: bool, has_self: bool, people: int) -> str:
    steps = [
        (initialized, "Create your journal", _cmd(args, "init")),
        (
            has_self,
            "Tell the coach who you are",
            _cmd(args, "person", "add", "me", "--self", "--name", "Your Name"),
        ),
        (
            people > 0,
            "Add someone you talk to",
            _cmd(args, "person", "add", "jdoe", "--name", "John Doe"),
        ),
        (
            False,
            "Log your first conversation",
            _cmd(args, "log", "1:1 with @jdoe: I led with the ask #1on1"),
        ),
    ]
    lines = [
        style.heading("Welcome to commscoach"),
        style.muted("Log conversations, get coaching on them, and watch your habits change."),
        "",
    ]
    for done, label, command in steps:
        mark = style.good("✓") if done else style.muted("○")
        lines.append(f"  {mark} {label}" + ("" if done else f"\n      {command}"))
    lines.append("")
    lines.append(style.muted(f"All commands: {shlex.join([*command_prefix(args), '--help'])}"))
    return "\n".join(lines)


def render_home(args: argparse.Namespace, *, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    try:
        with open_db(args) as conn:
            return _render(args, conn, now)
    except db.InitRequiredError:
        return _onboarding(args, initialized=False, has_self=False, people=0)


def _render(args: argparse.Namespace, conn, now: datetime) -> str:  # type: ignore[no-untyped-def]
    today = now.date()
    me = db.get_self_person(conn)
    people = [p for p in db.list_people(conn) if not p.is_self]
    stats = db.get_stats(conn, today=today)
    if stats.entry_count == 0:
        return _onboarding(args, initialized=True, has_self=me is not None, people=len(people))

    lines: list[str] = []
    who = (me.name.split()[0] if me and me.name else me.handle) if me else None
    lines.append(style.heading(f"{_greeting(now)}{', ' + who if who else ''}"))

    weekly = _weekly_counts(stats, today, SPARK_WEEKS)
    rhythm = []
    if stats.current_weekly_streak >= 2:
        rhythm.append(style.warn(f"🔥 {stats.current_weekly_streak}-week streak"))
    rhythm.append(f"{plural(weekly[-1], 'entry', 'entries')} this week")
    rhythm.append(
        style.paint(style.sparkline(weekly), "green") + style.muted(f" last {SPARK_WEEKS} weeks")
    )
    lines.append(style.join(rhythm))
    if me is None:
        lines.append(
            style.warn("! No self person yet; coaching needs one: ")
            + _cmd(args, "person", "add", "me", "--self")
        )

    # Coaching backlog
    waiting = db.count_search_entries(conn, without_feedback=True)
    lines.append(_section("Waiting for coaching"))
    if waiting:
        oldest = db.search_entries(conn, without_feedback=True, oldest_first=True, limit=1)[0]
        with_whom = (
            " with " + ", ".join(style.handle(p.handle) for p in oldest.people if not p.is_self)
            if any(not p.is_self for p in oldest.people)
            else ""
        )
        lines.append(
            f"  {style.warn('●')} {plural(waiting, 'entry', 'entries')} · oldest "
            f"{style.relative(oldest.occurred_at or oldest.created_at)}{with_whom}: "
            f"{style.muted(style.clip(first_prose_line(oldest.body), 50))}"
        )
        lines.append(f"    Ask your coach: {style.paint(f'/commscoach {oldest.id}', 'cyan')}")
    else:
        coverage = f"{stats.entries_with_feedback}/{stats.entry_count}"
        lines.append(f"  {style.good('✓')} All caught up · {coverage} entries coached")

    # Goals
    goals = db.list_goals(conn, status="active", oldest_first=True)
    lines.append(_section("Goals in play"))
    if goals:
        summaries = _goal_summaries(conn, [goal.id for goal in goals])
        for goal in goals[:GOALS_SHOWN]:
            line = "  " + pretty_views.goal_short(goal)
            summary = summaries.get(goal.id)
            if summary is not None:
                line += style.sep() + _momentum(conn, goal.id, summary)
            lines.append(line)
        if len(goals) > GOALS_SHOWN:
            lines.append(
                style.muted(f"    and {len(goals) - GOALS_SHOWN} more: ")
                + _cmd(args, "goal", "list")
            )
    else:
        lines.append(
            f"  {style.muted('○')} No active goal. Pick one habit to practice: "
            + _cmd(args, "goal", "add", "Lead with the ask")
        )

    # Relationships going quiet
    activity = db.get_people_activity(conn)
    quiet = []
    for person in people:
        act = activity.get(person.id)
        if act is None or act.interaction_count < QUIET_MIN_INTERACTIONS:
            continue
        days = style.days_since(act.last_logged_interaction, now=now)
        if days is not None and days >= QUIET_DAYS:
            quiet.append((days, person, act))
    if quiet:
        lines.append(_section("Gone quiet"))
        for days, person, act in sorted(quiet, key=lambda item: -item[0])[:3]:
            label = person.name or person.handle
            lines.append(
                f"  {style.paint('↻', 'blue')} {style.handle(person.handle)}"
                + (f" ({label})" if person.name else "")
                + style.muted(
                    f" · last talked {style.span(days)} ago · {plural(act.interaction_count, 'conversation')}"
                )
            )

    # On this day
    memory = _on_this_day(conn, today)
    if memory is not None:
        label, entry = memory
        lines.append(_section(label))
        lines.append(
            f"  {style.ident(f'[{entry.id}]')} "
            + style.paint(
                style.clip(first_prose_line(entry.body), style.term_width() - 10), "italic"
            )
        )

    lines.append("")
    lines.append(
        style.muted("Log a conversation: ")
        + _cmd(args, "log", "Pushed back on the deadline with @jdoe #standup")
    )
    return "\n".join(lines)


def _goal_summaries(conn, goal_ids: list[int]) -> dict:  # type: ignore[no-untyped-def]
    """Goal-check summaries for goals that have at least one check."""
    if not goal_ids:
        return {}
    return {
        goal_id: summary
        for goal_id, summary in db.goal_check_summaries(conn, goal_ids).items()
        if summary.total
    }


def _momentum(conn, goal_id: int, summary) -> str:  # type: ignore[no-untyped-def]
    """The last few verdicts as glyphs, then the counts: ``✓✓✗✓ advanced 3×, slipped 1×``."""
    recent = db.list_goal_checks(conn, goal_id)[-8:]
    return pretty_views.verdict_strip(recent) + " " + pretty_views.momentum(summary)
