"""Rendering helpers for the ``entry`` subcommands."""

from __future__ import annotations

import argparse

from .. import db
from .common import (
    first_prose_line,
    format_stamp,
    print_json,
    truncate,
)
from .serializers import entry_to_dict


def format_entry_human(entry: db.Entry) -> str:
    header_parts = [f"[{entry.id}]", f"created {format_stamp(entry.created_at)}"]
    if entry.occurred_at:
        header_parts.append(f"occurred {format_stamp(entry.occurred_at)}")
    if entry.source:
        header_parts.append(f"source: {entry.source}")
    lines = [" ".join(header_parts)]
    if entry.people:
        lines.append("people: " + ", ".join(f"@{p.handle}" for p in entry.people))
    if entry.tags:
        lines.append("tags: " + ", ".join(entry.tags))
    lines.append("---")
    lines.append(entry.body)
    return "\n".join(lines)


def format_entry_short(
    entry: db.Entry,
    *,
    has_feedback: bool = False,
    excerpt: str | None = None,
) -> str:
    parts = [f"[{entry.id}]", format_stamp(entry.occurred_at or entry.created_at)]
    if entry.source:
        parts.append(" ".join(entry.source.split()))
    parts.append("has feedback" if has_feedback else "needs feedback")
    parts.append(excerpt if excerpt is not None else truncate(first_prose_line(entry.body)))
    if entry.tags:
        parts.append("#" + ",".join(entry.tags))
    if entry.people:
        parts.append("@" + ",".join(p.handle for p in entry.people))
    return " · ".join(parts)


def emit_entry(
    args: argparse.Namespace,
    entry: db.Entry,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(entry_to_dict(entry))
        return
    if human_prefix:
        print(f"{human_prefix} entry {entry.id}")
    print(format_entry_human(entry))


def emit_entries(
    args: argparse.Namespace,
    entries: tuple[db.Entry, ...],
    coached: set[int],
    *,
    excerpts: dict[int, str] | None = None,
) -> None:
    if args.json:
        print_json([entry_to_dict(e) for e in entries])
        return
    if not entries:
        print("(no entries)")
        return
    for entry in entries:
        print(
            format_entry_short(
                entry,
                has_feedback=entry.id in coached,
                excerpt=excerpts.get(entry.id) if excerpts is not None else None,
            )
        )
