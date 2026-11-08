"""Rendering helpers for the ``entry`` subcommands."""

from __future__ import annotations

import argparse

from .. import db
from . import style
from .common import (
    first_prose_line,
    format_stamp,
    print_json,
    truncate,
)
from .insights import plain_inline
from .serializers import entry_to_dict


def format_entry_human(entry: db.Entry) -> str:
    if style.pretty():
        return _pretty_entry_human(entry)
    header_parts = [f"[{entry.id}]", f"created {format_stamp(entry.created_at)}"]
    if entry.occurred_at:
        header_parts.append(f"occurred {format_stamp(entry.occurred_at)}")
    if entry.source:
        header_parts.append(f"source: {entry.source}")
    lines = [" ".join(header_parts)]
    if entry.people:
        lines.append("people: " + ", ".join(f"@{p.handle}" for p in entry.people))
    if entry.tags:
        lines.append("tags: " + ", ".join(f"#{tag}" for tag in entry.tags))
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
        parts.append(", ".join(f"#{tag}" for tag in entry.tags))
    if entry.people:
        parts.append(", ".join(f"@{p.handle}" for p in entry.people))
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
        # The same coaching status the text rows show as has/needs feedback.
        print_json([{**entry_to_dict(e), "has_feedback": e.id in coached} for e in entries])
        return
    if not entries:
        print("(no entries)")
        return
    if style.pretty():
        for line in pretty_entry_rows(entries, coached, excerpts=excerpts):
            print(line)
        return
    for entry in entries:
        print(
            format_entry_short(
                entry,
                has_feedback=entry.id in coached,
                excerpt=excerpts.get(entry.id) if excerpts is not None else None,
            )
        )


def _pretty_entry_human(entry: db.Entry) -> str:
    when = entry.occurred_at or entry.created_at
    head = [style.heading(f"Entry {entry.id}"), style.relative(when)]
    if entry.source:
        head.append(" ".join(entry.source.split()))
    lines = [style.join(head)]
    meta = []
    if entry.people:
        meta.append("with " + ", ".join(style.handle(p.handle) for p in entry.people))
    if entry.tags:
        meta.append(" ".join(style.tag(t) for t in entry.tags))
    if meta:
        lines.append(style.join(meta))
    lines.append("")
    lines.append(style.render_markdown(entry.body, indent="  "))
    if entry.occurred_at:
        lines.append(style.muted(f"  logged {style.relative(entry.created_at)}"))
    return "\n".join(lines)


def pretty_entry_rows(
    entries: tuple[db.Entry, ...] | list[db.Entry],
    coached: set[int],
    *,
    excerpts: dict[int, str] | None = None,
) -> list[str]:
    """Aligned, glyph-led rows for a terminal: status, id, when, source, text, links."""
    width = style.term_width(cap=140)
    id_w = max(len(str(e.id)) for e in entries)
    whens = {e.id: style.short_when(e.occurred_at or e.created_at) for e in entries}
    when_w = max(len(w) for w in whens.values())
    sources = {e.id: " ".join((e.source or "").split()) for e in entries}
    source_w = min(12, max((len(s) for s in sources.values()), default=0))
    links = {
        e.id: " ".join(
            [*(style.handle(p.handle) for p in e.people), *(style.tag(t) for t in e.tags)]
        )
        for e in entries
    }
    texts = {
        e.id: (
            excerpts[e.id]
            if excerpts is not None and e.id in excerpts
            else plain_inline(first_prose_line(e.body))
        )
        for e in entries
    }
    fixed = 2 + id_w + 2 + when_w + 2 + (source_w + 2 if source_w else 0)
    links_w = max(style.visible_len(text) for text in links.values())
    # One text column for every row, so the people and tags line up after it.
    text_w = max(24, min(max(len(t) for t in texts.values()), width - fixed - links_w - 2))
    rows = []
    for entry in entries:
        glyph = style.good("✓") if entry.id in coached else style.warn("●")
        source = style.pad(style.muted(style.clip(sources[entry.id], source_w)), source_w)
        row = (
            f"{glyph} {style.ident(str(entry.id).rjust(id_w))}  "
            f"{style.pad(whens[entry.id], when_w)}  "
            + (f"{source}  " if source_w else "")
            + style.pad(style.clip(texts[entry.id], text_w), text_w)
        )
        if links[entry.id]:
            row += "  " + links[entry.id]
        rows.append(row.rstrip())
    return rows
