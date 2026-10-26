"""The ``commscoach stats`` activity summary command."""

from __future__ import annotations

import argparse
import shlex

from .. import db
from .common import (
    add_date_bounds,
    command_prefix,
    format_stamp,
    non_negative_int,
    open_db,
    parse_date_bounds,
    print_err,
    print_json,
)
from .serializers import stats_to_dict


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "stats",
        help="Summarize journal activity, coaching coverage, and weekly cadence.",
    )
    add_date_bounds(parser, noun="activity")
    parser.add_argument(
        "--top",
        type=non_negative_int,
        default=5,
        metavar="N",
        help="Show at most N tags, sources, and people (default: 5).",
    )
    parser.add_argument(
        "--weeks",
        type=non_negative_int,
        default=12,
        metavar="N",
        help="Show at most N recent weekly buckets (default: 12).",
    )
    parser.set_defaults(func=cmd_stats)


def _format_ranked(title: str, items: tuple[tuple[str, int], ...]) -> list[str]:
    lines = [f"{title}:"]
    lines.extend(f"  {name}: {count}" for name, count in items)
    if not items:
        lines.append("  (none)")
    return lines


def _stamp(value: str | None) -> str:
    return format_stamp(value) if value else "(none)"


def _format_human(stats: db.Stats, *, weeks: int) -> str:
    coverage = stats.entries_with_feedback / stats.entry_count if stats.entry_count else 0.0
    buckets = stats.weekly_buckets[-weeks:] if weeks else ()
    lines = [
        f"Entries: {stats.entry_count}",
        f"People: {stats.people_count}",
        f"Tags: {stats.tag_count}",
        f"Feedback: {stats.feedback_count}",
        f"Entries awaiting feedback: {stats.entry_count - stats.entries_with_feedback}",
        (f"Feedback coverage: {stats.entries_with_feedback}/{stats.entry_count} ({coverage:.1%})"),
        f"Reviews: {stats.review_count}",
        f"Goals: {stats.open_goal_count} open, {stats.closed_goal_count} closed",
        f"First entry: {_stamp(stats.first_entry)}",
        f"Last entry: {_stamp(stats.last_entry)}",
        f"Last review: {_stamp(stats.last_review)}",
        f"Entries/week: {stats.entries_per_week:.2f}",
        f"Weekly streak: {stats.current_weekly_streak} current, "
        f"{stats.longest_weekly_streak} longest",
    ]
    lines.extend(_format_ranked("Top tags", stats.top_tags))
    lines.extend(_format_ranked("Top sources", stats.top_sources))
    lines.extend(
        _format_ranked("Top people", tuple((f"@{name}", n) for name, n in stats.top_people))
    )
    lines.append("Recent weeks:")
    lines.extend(f"  {week}: {count}" for week, count in buckets)
    if not buckets:
        lines.append("  (none)")
    return "\n".join(lines)


def cmd_stats(args: argparse.Namespace) -> int:
    since, until = parse_date_bounds(args)
    with open_db(args) as conn:
        summary = db.get_stats(conn, since=since, until=until, top=args.top)
    if args.json:
        print_json(stats_to_dict(summary, weeks=args.weeks))
    else:
        print(_format_human(summary, weeks=args.weeks))
        if summary.entry_count > summary.entries_with_feedback:
            command = command_prefix(args)
            command.extend(("entry", "search", "--no-feedback", "--oldest-first", "--limit", "5"))
            # Repeat the bounds as typed: "2026-10-01", not its UTC instant.
            for flag, bound in (("--since", args.since), ("--until", args.until)):
                if bound is not None:
                    command.extend((flag, bound))
            print_err(f"\nCoach the oldest first: {shlex.join(command)}")
    return 0
