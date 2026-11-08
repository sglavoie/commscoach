"""What the coach notices right after you log something.

These short lines turn a write into a moment: how often you have talked to
someone lately, which open goal applies, milestones, and the one command that
closes the loop. They print only in pretty mode (a terminal, not ``--json``),
so scripts and the skill see exactly the output they always did.
"""

from __future__ import annotations

import argparse
import shlex
from datetime import date, datetime, timedelta

from .. import db
from . import coach, insights, style
from .common import parse_date_bound, plural

ENTRY_MILESTONES = (1, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
COACHED_MILESTONES = (1, 10, 25, 50, 100, 250, 500, 1000)


def enabled(args: argparse.Namespace) -> bool:
    return not getattr(args, "json", False) and style.pretty()


def _month_start(today: date) -> str:
    return parse_date_bound(today.replace(day=1).isoformat(), flag="--since")


def entry_logged(
    conn, entry: db.Entry, *, today: date | None = None, prefix: list[str] | None = None
) -> list[str]:  # type: ignore[no-untyped-def]
    """Confirmation plus nudges for a freshly created entry."""
    today = today or datetime.now().date()
    head = [style.good("✓") + " " + style.heading(f"Logged entry {entry.id}")]
    head.append(style.relative(entry.occurred_at or entry.created_at))
    if entry.source:
        head.append(" ".join(entry.source.split()))
    links = [style.handle(p.handle) for p in entry.people] + [style.tag(t) for t in entry.tags]
    if links:
        head.append(" ".join(links))
    lines = [style.join(head)]

    others = [p for p in entry.people if not p.is_self]
    for person in others[:3]:
        total = db.count_search_entries(conn, person_handle=person.handle)
        if total == 1:
            lines.append(
                f"  {style.paint('★', 'yellow')} First conversation logged with {style.handle(person.handle)}"
            )
            continue
        this_month = db.count_search_entries(
            conn, person_handle=person.handle, since=_month_start(today)
        )
        lines.append(
            f"  {style.muted('↳')} {style.ordinal(total)} conversation with {style.handle(person.handle)}"
            + style.muted(f" · {plural(this_month, 'this month', 'this month')}")
        )

    for person in others[:1]:
        plan = insights.latest_plan(conn, person_handle=person.handle, before_entry=entry.id)
        if plan is not None:
            room = style.term_width() - 30
            lines.append(
                f"  {style.paint('↳', 'yellow')} Last time you planned: "
                + style.paint(style.clip(plan.items[0], room), "italic")
            )

    goals = db.list_goals(
        conn,
        status="active",
        prioritize_person_ids=tuple(p.id for p in others),
    )
    relevant = [g for g in goals if g.person is None or any(g.person.id == p.id for p in others)]
    for goal in relevant[:2]:
        scope = f" with {style.handle(goal.person.handle)}" if goal.person else ""
        lines.append(
            f"  {style.warn('◎')} Goal {goal.id}{scope}: {style.paint(goal.title, 'bold')}"
        )

    lines.extend(_entry_milestones(conn, entry, today))
    ask = (
        shlex.join([*(prefix or ["commscoach"]), "coach", str(entry.id)])
        if coach.available()
        else f"/commscoach {entry.id}"
    )
    lines.append(f"  {style.muted('→')} Ask your coach: {style.paint(ask, 'cyan')}")
    return lines


def _entry_milestones(conn, entry: db.Entry, today: date) -> list[str]:  # type: ignore[no-untyped-def]
    stats = db.get_stats(conn, today=today, top=0)
    out = []
    if stats.entry_count in ENTRY_MILESTONES and stats.entry_count > 1:
        out.append(f"  🎉 That's your {style.ordinal(stats.entry_count)} logged conversation")
    # The first entry of a week that continues a run is the moment a streak grows.
    monday = (today - timedelta(days=today.weekday())).isoformat()
    this_week = dict(stats.weekly_buckets).get(monday, 0)
    if this_week == 1 and stats.current_weekly_streak >= 2:
        out.append(f"  🔥 {stats.current_weekly_streak}-week streak, kept alive")
    return out


def feedback_stored(conn, feedback: db.Feedback) -> list[str]:  # type: ignore[no-untyped-def]
    """Milestones and backlog status after a feedback note is stored."""
    stats = db.get_stats(conn, top=0)
    out = []
    coached = stats.entries_with_feedback
    first_on_entry = len(db.list_feedback(conn, feedback.entry_id)) == 1
    if first_on_entry and coached in COACHED_MILESTONES:
        out.append(f"🎉 {style.ordinal(coached)} coached conversation")
    waiting = stats.entry_count - coached
    if waiting == 0:
        out.append(style.good("✓ All caught up: every entry has coaching"))
    elif first_on_entry:
        out.append(style.muted(f"{plural(waiting, 'entry', 'entries')} still waiting for coaching"))
    return out
