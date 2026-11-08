"""``commscoach recap``: the past week (or month) told as a short story.

Who you talked to, how each goal moved, the best moment the coach recorded,
and one line of advice worth carrying forward, compared with the period
before. It only reads.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import db
from . import insights, pretty_views, style
from .common import open_db, parse_date_bound, plural, print_json
from .serializers import entry_to_dict

PERIODS = {"week": 7, "month": 30}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "recap",
        help="Recap the past week or month: people, goal movement, best moment.",
        description=(
            "Recap the last 7 (week) or 30 (month) days, ending today, "
            "against the same span before it."
        ),
    )
    parser.add_argument(
        "period",
        nargs="?",
        choices=tuple(PERIODS),
        default="week",
        help="week (last 7 days, the default) or month (last 30 days).",
    )
    parser.set_defaults(func=cmd_recap)


@dataclass
class GoalMove:
    goal: db.Goal | None
    goal_id: int
    checks: list[db.GoalCheck] = field(default_factory=list)

    @property
    def advanced(self) -> int:
        return sum(1 for c in self.checks if c.verdict == "advanced")

    @property
    def hurt(self) -> int:
        return sum(1 for c in self.checks if c.verdict == "hurt")


@dataclass
class Recap:
    period: str
    start: date
    end: date
    entries: tuple[db.Entry, ...]
    previous_count: int
    coached: set[int]
    people: list[tuple[db.Person, int]]
    new_people: list[db.Person]
    goals: list[GoalMove]
    best: tuple[db.GoalCheck, db.Goal | None] | None
    advice: tuple[str, db.Entry] | None
    streak: int


def gather(conn, period: str, *, today: date | None = None) -> Recap:  # type: ignore[no-untyped-def]
    today = today or datetime.now().date()
    days = PERIODS[period]
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    since = parse_date_bound(start.isoformat(), flag="--since")
    until = parse_date_bound(today.isoformat(), flag="--until")
    entries = db.search_entries(conn, since=since, until=until)
    previous = db.count_search_entries(
        conn,
        since=parse_date_bound(prev_start.isoformat(), flag="--since"),
        until=parse_date_bound((start - timedelta(days=1)).isoformat(), flag="--until"),
    )
    coached = db.feedback_entry_ids(conn, (e.id for e in entries))

    counts: Counter[int] = Counter()
    by_id: dict[int, db.Person] = {}
    for entry in entries:
        for person in entry.people:
            if not person.is_self:
                counts[person.id] += 1
                by_id[person.id] = person
    people = [(by_id[pid], n) for pid, n in counts.most_common()]
    times = db.get_people_interaction_times(conn)
    new_people = [by_id[pid] for pid in counts if times.get(pid) and _day(times[pid][0]) >= start]

    checks = db.list_goal_checks_between(conn, since=since, until=until)
    moves: dict[int, GoalMove] = {}
    for check in checks:
        move = moves.setdefault(
            check.goal_id, GoalMove(db.get_goal(conn, check.goal_id), check.goal_id)
        )
        move.checks.append(check)
    advanced = [c for c in checks if c.verdict == "advanced"]
    best_check = next((c for c in reversed(advanced) if c.note), advanced[-1] if advanced else None)
    best = (best_check, moves[best_check.goal_id].goal) if best_check else None

    advice = None
    for entry in entries:  # newest first
        for note in reversed(db.list_feedback(conn, entry.id)):
            line = insights.key_advice(note.body)
            if line:
                advice = (line, entry)
                break
        if advice:
            break

    stats = db.get_stats(conn, today=today, top=0)
    return Recap(
        period=period,
        start=start,
        end=today,
        entries=entries,
        previous_count=previous,
        coached=coached,
        people=people,
        new_people=new_people,
        goals=sorted(moves.values(), key=lambda m: (-m.advanced, m.goal_id)),
        best=best,
        advice=advice,
        streak=stats.current_weekly_streak,
    )


def _day(iso: str) -> date:
    parsed = datetime.fromisoformat(iso)
    return (parsed.astimezone() if parsed.tzinfo else parsed).date()


def to_dict(recap: Recap) -> dict:
    return {
        "period": recap.period,
        "start": recap.start.isoformat(),
        "end": recap.end.isoformat(),
        "entry_count": len(recap.entries),
        "previous_entry_count": recap.previous_count,
        "coached_count": len(recap.coached),
        "people": [{"handle": p.handle, "name": p.name, "count": n} for p, n in recap.people],
        "new_people": [p.handle for p in recap.new_people],
        "goals": [
            {
                "goal_id": move.goal_id,
                "title": move.goal.title if move.goal else None,
                "verdicts": [c.verdict for c in move.checks],
            }
            for move in recap.goals
        ],
        "best_moment": (
            {
                "goal_id": recap.best[0].goal_id,
                "entry_id": recap.best[0].entry_id,
                "note": recap.best[0].note,
            }
            if recap.best
            else None
        ),
        "advice": (
            {"text": recap.advice[0], "entry": entry_to_dict(recap.advice[1])}
            if recap.advice
            else None
        ),
        "weekly_streak": recap.streak,
    }


def _trend(now: int, before: int) -> str:
    if before == 0:
        return "" if now == 0 else "up from none"
    delta = now - before
    if delta == 0:
        return "same as before"
    return f"{'up' if delta > 0 else 'down'} {abs(delta)} from {before}"


def render_plain(recap: Recap) -> str:
    lines = [
        f"Recap: {recap.start.isoformat()} to {recap.end.isoformat()} ({recap.period})",
        f"conversations: {len(recap.entries)} (previous {recap.period}: {recap.previous_count})",
        f"coached: {len(recap.coached)}/{len(recap.entries)}",
        "people: " + (", ".join(f"@{p.handle} ({n})" for p, n in recap.people) or "(none)"),
    ]
    if recap.new_people:
        lines.append("new: " + ", ".join(f"@{p.handle}" for p in recap.new_people))
    for move in recap.goals:
        title = move.goal.title if move.goal else f"goal {move.goal_id}"
        verdicts = ", ".join(c.verdict for c in move.checks)
        lines.append(f"goal {move.goal_id} {title}: {verdicts}")
    if recap.best:
        check, _ = recap.best
        lines.append(
            f"best moment: entry {check.entry_id}" + (f": {check.note}" if check.note else "")
        )
    if recap.advice:
        lines.append(f"carry forward: {recap.advice[0]}")
    return "\n".join(lines)


def render_pretty(recap: Recap) -> str:
    width = style.term_width()
    span = f"{recap.start:%b %-d} → {recap.end:%b %-d}"
    title = "Your week" if recap.period == "week" else "Your month"
    lines = [style.heading(title) + style.muted(f"  {span}"), ""]
    label_w = 15

    def row(label: str, value: str) -> None:
        lines.append("  " + style.muted(label.ljust(label_w)) + value)

    if not recap.entries:
        lines.append(
            "  "
            + style.muted("A quiet " + recap.period + ": nothing logged. ")
            + "One line is enough to start again."
        )
        return "\n".join(lines)

    talks = plural(len(recap.entries), "conversation")
    trend = _trend(len(recap.entries), recap.previous_count)
    arrow = ""
    if recap.previous_count and len(recap.entries) != recap.previous_count:
        arrow = style.good("▲ ") if len(recap.entries) > recap.previous_count else style.warn("▼ ")
    row("Talked", style.join([talks, arrow + style.muted(trend) if trend else ""]))
    row(
        "Coached",
        f"{style.bar(len(recap.coached) / len(recap.entries), width=10)}  "
        f"{len(recap.coached)}/{len(recap.entries)}",
    )
    if recap.people:
        top = max(n for _, n in recap.people)
        shown = recap.people[:5]
        name_w = max(len(p.handle) + 1 for p, _ in shown)
        for index, (person, n) in enumerate(shown):
            star = (
                "  " + style.paint("★ new", "yellow")
                if any(p.id == person.id for p in recap.new_people)
                else ""
            )
            meter = style.paint("█" * max(1, round(n / top * 12)), "blue")
            lines.append(
                "  "
                + (style.muted("With".ljust(label_w)) if index == 0 else " " * label_w)
                + style.pad(style.handle(person.handle), name_w + 1)
                + f" {meter} {n}{star}"
            )
        if len(recap.people) > len(shown):
            lines.append(
                " " * (label_w + 2) + style.muted(f"and {len(recap.people) - len(shown)} more")
            )

    if recap.goals:
        lines.append("")
        for index, move in enumerate(recap.goals):
            title = move.goal.title if move.goal else f"goal {move.goal_id}"
            strip = pretty_views.verdict_strip(move.checks)
            verdict = (
                style.good("moving")
                if move.advanced > move.hurt
                else style.bad("slipping")
                if move.hurt > move.advanced
                else style.muted("mixed")
            )
            lines.append(
                "  "
                + (style.muted("Goals".ljust(label_w)) if index == 0 else " " * label_w)
                + f"{strip}  {style.clip(title, width - label_w - 24)}  {verdict}"
            )

    if recap.best:
        check, goal = recap.best
        lines.append("")
        what = check.note or (f"advanced {goal.title}" if goal else "a goal advanced")
        row("Best moment", "✨ " + style.paint(style.clip(what, width - label_w - 8), "italic"))
        lines.append(
            " " * (label_w + 2)
            + style.muted(
                f"entry {check.entry_id}, {style.relative(check.at)}"
                + (f" · {goal.title}" if goal and check.note else "")
            )
        )
    if recap.advice:
        text, entry = recap.advice
        lines.append("")
        row("Carry forward", style.paint("→ ", "yellow") + style.clip(text, width - label_w - 6))
        lines.append(" " * (label_w + 2) + style.muted(f"from coaching on entry {entry.id}"))

    if recap.streak >= 2:
        lines.extend(("", "  " + style.warn(f"🔥 {recap.streak}-week streak")))
    return "\n".join(lines)


def cmd_recap(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        recap = gather(conn, args.period)
    if args.json:
        print_json(to_dict(recap))
    elif style.pretty():
        print(render_pretty(recap))
    else:
        print(render_plain(recap))
    return 0
