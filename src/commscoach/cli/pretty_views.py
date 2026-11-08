"""Terminal (pretty-mode) renderings of feedback, goals, and reviews.

Each function mirrors a plain formatter in its command module and is only
called when :func:`commscoach.cli.style.pretty` is true, so the plain line
formats that scripts read stay untouched.
"""

from __future__ import annotations

from .. import db
from . import style


def feedback_human(feedback: db.Feedback) -> str:
    head = style.join(
        [
            style.heading(f"Feedback {feedback.id}"),
            f"on entry {feedback.entry_id}",
            style.relative(feedback.created_at),
        ]
    )
    return "\n".join([head, "", style.render_markdown(feedback.body, indent="  ")])


def goal_glyph(goal: db.Goal) -> str:
    if goal.status == "done":
        return style.good("✓")
    if goal.status == "dropped":
        return style.muted("–")
    return style.warn("◎")


def goal_age(goal: db.Goal) -> str:
    """``open 3 weeks`` for an active goal, ``done Nov 2`` for a closed one."""
    if goal.is_open:
        days = style.days_since(goal.created_at)
        if days is None:
            return ""
        return "new today" if days <= 0 else f"open {style.span(days)}"
    if goal.closed_at:
        return f"{goal.status} {style.relative(goal.closed_at)}"
    return goal.status


def goal_short(goal: db.Goal) -> str:
    parts = [f"{goal_glyph(goal)} {style.ident(goal.id)}  {goal.title}"]
    tail = []
    if goal.person is not None:
        tail.append(style.handle(goal.person.handle))
    tail.append(style.muted(goal_age(goal)))
    return parts[0] + "  " + style.join(tail)


def goal_human(goal: db.Goal) -> str:
    head = style.join([style.heading(f"Goal {goal.id}"), goal_glyph(goal) + " " + goal_age(goal)])
    lines = [head, "", "  " + style.paint(goal.title, "bold")]
    links = []
    if goal.person is not None:
        links.append("with " + style.handle(goal.person.handle))
    else:
        links.append("general")
    if goal.review is not None:
        links.append(f"from review {goal.review.id}")
    links.append(f"set {style.relative(goal.created_at)}")
    lines.append("  " + style.muted(style.SEP.join(_strip(link) for link in links)))
    if goal.detail is not None:
        lines.extend(("", style.render_markdown(goal.detail, indent="  ")))
    if goal.outcome is not None:
        lines.extend(("", "  " + style.heading("Outcome")))
        lines.append(style.render_markdown(goal.outcome, indent="  "))
    return "\n".join(lines)


def review_human(review: db.Review, scope: str) -> str:
    head = [style.heading(f"Review {review.id}"), scope, style.relative(review.created_at)]
    if review.entries:
        ids = ", ".join(str(entry.id) for entry in review.entries)
        head.append(f"covers {'entry' if len(review.entries) == 1 else 'entries'} {ids}")
    lines = [style.join(head)]
    if review.updated_at != review.created_at:
        lines.append(style.muted(f"edited {style.relative(review.updated_at)}"))
    lines.extend(("", style.render_markdown(review.body, indent="  ")))
    return "\n".join(lines)


def _strip(text: str) -> str:
    return style._ANSI_RE.sub("", text)


def _ranked(label: str, items: tuple[tuple[str, int], ...], render) -> list[str]:  # type: ignore[no-untyped-def]
    if not items:
        return []
    top = max(count for _, count in items) or 1
    name_w = max(len(name) for name, _ in items)
    lines = []
    for index, (name, count) in enumerate(items):
        lead = "  " + (style.muted(label.ljust(15)) if index == 0 else " " * 15)
        meter = style.paint("█" * max(1, round(count / top * 16)), "blue")
        lines.append(f"{lead}{style.pad(render(name), name_w + 1)} {meter} {count}")
    return lines


def stats_view(stats: db.Stats, *, weeks: int, window: str | None, until: str | None = None) -> str:
    """The ``stats`` dashboard: coverage bar, streak, weekly sparkline, ranked bars."""
    from datetime import date, datetime, timedelta

    title = "Your communication at a glance" + (f" · {window}" if window else "")
    lines = [style.heading(title), ""]

    def row(label: str, value: str) -> None:
        lines.append("  " + style.muted(label.ljust(15)) + value)

    waiting = stats.entry_count - stats.entries_with_feedback
    row(
        "Conversations",
        style.join(
            [
                f"{stats.entry_count} logged",
                f"{stats.people_count} {'person' if stats.people_count == 1 else 'people'}",
                f"{stats.entries_per_week:.1f} a week",
            ]
        ),
    )
    coverage = stats.entries_with_feedback / stats.entry_count if stats.entry_count else 0.0
    row(
        "Coaching",
        f"{style.bar(coverage)}  {stats.entries_with_feedback}/{stats.entry_count} coached"
        f" ({coverage:.0%})" + (style.sep() + style.warn(f"{waiting} waiting") if waiting else ""),
    )
    row(
        "Goals",
        style.join(
            [f"{stats.open_goal_count} active", f"{stats.closed_goal_count} closed"]
            + ([f"{stats.review_count} reviews"] if stats.review_count else [])
        ),
    )
    streak = stats.current_weekly_streak
    row(
        "Streak",
        (
            style.warn(f"🔥 {streak} weeks")
            if streak >= 2
            else f"{streak} week" + ("" if streak == 1 else "s")
        )
        + style.muted(f" (longest {stats.longest_weekly_streak})"),
    )
    buckets = stats.weekly_buckets[-weeks:] if weeks else ()
    if buckets:
        # Weeks run up to the last entry; fill the quiet weeks since then.
        counts = [count for _, count in buckets]
        last = date.fromisoformat(buckets[-1][0])
        end = date.today()
        if until is not None:
            end = min(end, datetime.fromisoformat(until).astimezone().date())
        this_monday = end - timedelta(days=end.weekday())
        while last < this_monday and len(counts) < max(weeks, len(buckets)) + 52:
            last += timedelta(weeks=1)
            counts.append(0)
        counts = counts[-weeks:]
        first = this_monday - timedelta(weeks=len(counts) - 1) if counts else this_monday
        lines.append("")
        row(
            "Weekly rhythm",
            style.paint(style.sparkline(counts), "green")
            + style.muted(
                f"  {first:%b %-d} → "
                + ("this week" if end == date.today() else f"{this_monday:%b %-d}")
                + f" · peak {max(counts)}"
            ),
        )
    lines.append("")
    lines.extend(_ranked("Top people", stats.top_people, style.handle))
    lines.extend(_ranked("Top tags", stats.top_tags, style.tag))
    lines.extend(_ranked("Channels", stats.top_sources, lambda name: name))
    span_parts = []
    if stats.first_entry:
        span_parts.append(f"first entry {style.relative(stats.first_entry)}")
    if stats.last_entry:
        span_parts.append(f"last {style.relative(stats.last_entry)}")
    span_parts.append(
        f"last review {style.relative(stats.last_review)}" if stats.last_review else "no review yet"
    )
    lines.extend(("", "  " + style.muted(style.SEP.join(span_parts))))
    return "\n".join(lines)


VERDICT_STYLE = {
    "advanced": ("✓", "green", "advanced"),
    "hurt": ("✗", "red", "slipped"),
    "neutral": ("·", "dim", "neutral"),
}


def verdict_glyph(verdict: str) -> str:
    glyph, hue, _ = VERDICT_STYLE[verdict]
    return style.paint(glyph, hue)


def verdict_strip(checks) -> str:  # type: ignore[no-untyped-def]
    return "".join(verdict_glyph(check.verdict) for check in checks)


def momentum(summary) -> str:  # type: ignore[no-untyped-def]
    """``advanced 3×, slipped 1×``, colored, naming only what happened."""
    parts = []
    for verdict, count in (
        ("advanced", summary.advanced),
        ("hurt", summary.hurt),
        ("neutral", summary.neutral),
    ):
        if count:
            _, hue, label = VERDICT_STYLE[verdict]
            parts.append(style.paint(f"{label} {count}×", hue))
    return ", ".join(parts)


def check_trail(checks, summary) -> str:  # type: ignore[no-untyped-def]
    """The goal's progress: a glyph strip, a win rate, then one line per check."""
    rate = summary.advanced / summary.total if summary.total else 0.0
    lines = [
        "",
        "  " + style.heading("Progress") + "  " + verdict_strip(checks) + "  " + momentum(summary),
        "  "
        + style.muted(
            f"advanced in {summary.advanced} of {summary.total} checked conversations ({rate:.0%})"
        ),
    ]
    for check in checks:
        _, hue, label = VERDICT_STYLE[check.verdict]
        line = (
            f"    {verdict_glyph(check.verdict)} {style.pad(style.relative(check.at), 18)}"
            f"{style.muted(f'entry {check.entry_id}')}  {style.paint(label, hue)}"
        )
        if check.note:
            line += style.muted(" — ") + check.note
        lines.append(line)
    return "\n".join(lines)


def journey(goal: db.Goal, summary) -> str:  # type: ignore[no-untyped-def]
    """The story of a closed goal, told once when it closes."""
    opened = style.days_since(goal.created_at) or 0
    closed = (style.days_since(goal.closed_at) or 0) if goal.closed_at else 0
    days = max(0, opened - closed)
    lead = "🎉 Done" if goal.status == "done" else "Dropped"
    after = "the same day" if days == 0 else f"after {style.span(days)}"
    conversations = f"{summary.total} checked conversation{'s' if summary.total != 1 else ''}"
    parts = [f"{lead} {after}", conversations, momentum(summary)]
    if summary.first_advanced_at is not None:
        parts.append(f"first win {style.relative(summary.first_advanced_at)}")
    return "\n" + style.join(parts)


def feedback_stored(feedback: db.Feedback, checks, goals) -> str:  # type: ignore[no-untyped-def]
    """One-line confirmation for ``feedback add`` plus each recorded goal check."""
    lines = [
        style.good("✓")
        + " "
        + style.join(
            [style.heading(f"Stored feedback {feedback.id}"), f"on entry {feedback.entry_id}"]
        )
    ]
    for check in checks:
        goal = goals.get(check.goal_id) if goals else None
        _, hue, label = VERDICT_STYLE[check.verdict]
        title = goal.title if goal is not None else f"goal {check.goal_id}"
        line = f"  {verdict_glyph(check.verdict)} {style.paint(label, hue)} {title}"
        if check.note:
            line += style.muted(" — " + check.note)
        lines.append(line)
    return "\n".join(lines)
