"""The ``commscoach export`` command: Markdown rendering of the whole DB."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import db, paths
from .common import open_db, print_err


def register(subparsers: argparse._SubParsersAction) -> None:
    export_parser = subparsers.add_parser(
        "export",
        help="Export all entries to Markdown (stdout by default).",
    )
    export_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write Markdown to this file with mode 0600. Default: stdout.",
    )
    export_parser.set_defaults(func=cmd_export)


def _format_person_export_label(person: db.Person) -> str:
    label = f"@{person.handle}"
    if person.name:
        label += f" ({person.name})"
    if person.is_self:
        label += " (self)"
    return label


def _format_markdown_entry(
    entry: db.Entry, feedback_items: tuple[db.Feedback, ...]
) -> str:
    lines: list[str] = [f"## Entry {entry.id}", ""]
    lines.append(f"- **Created**: {entry.created_at}")
    if entry.occurred_at:
        lines.append(f"- **Occurred**: {entry.occurred_at}")
    if entry.source:
        lines.append(f"- **Source**: {entry.source}")
    if entry.updated_at != entry.created_at:
        lines.append(f"- **Updated**: {entry.updated_at}")
    if entry.tags:
        lines.append("- **Tags**: " + ", ".join(entry.tags))
    if entry.people:
        people_labels = [_format_person_export_label(p) for p in entry.people]
        lines.append("- **People**: " + ", ".join(people_labels))
    lines.append("")
    lines.append(entry.body.rstrip("\n"))
    lines.append("")
    if feedback_items:
        lines.append("### Feedback")
        lines.append("")
        for feedback in feedback_items:
            lines.append(f"#### Feedback {feedback.id} — {feedback.created_at}")
            lines.append("")
            lines.append(feedback.body.rstrip("\n"))
            lines.append("")
    return "\n".join(lines)


def _format_markdown_review(review: db.Review) -> str:
    lines: list[str] = [f"### Review {review.id}", ""]
    lines.append(f"- **Created**: {review.created_at}")
    scope = review.scope_kind
    if review.scope_limit is not None:
        scope += f" (context limit {review.scope_limit})"
    lines.append(f"- **Scope**: {scope}")
    entry_ids = ", ".join(str(entry.id) for entry in review.entries)
    lines.append(f"- **Covered entry IDs**: {entry_ids or '(none)'}")
    lines.append("")
    lines.append(review.body.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def _render_markdown_export(
    entries: tuple[db.Entry, ...],
    feedback_by_entry: dict[int, tuple[db.Feedback, ...]],
    reviews: tuple[db.Review, ...],
) -> str:
    sections: list[str] = ["# commscoach export", ""]
    if reviews:
        sections.append("## Reviews")
        sections.append("")
        rendered_reviews = [_format_markdown_review(review) for review in reviews]
        sections.append("\n---\n\n".join(rendered_reviews).rstrip("\n"))
        sections.append("")
    if not entries:
        sections.append("_No entries._")
        sections.append("")
        return "\n".join(sections)
    rendered_entries: list[str] = []
    for entry in entries:
        rendered_entries.append(
            _format_markdown_entry(entry, feedback_by_entry[entry.id])
        )
    sections.append("\n---\n\n".join(rendered_entries))
    if not sections[-1].endswith("\n"):
        sections.append("")
    return "\n".join(sections)


def _write_export_file(out_path: Path, content: str) -> None:
    """Write the rendered Markdown to ``out_path`` with restricted permissions.

    Opens with ``O_CREAT | O_WRONLY | O_TRUNC`` and mode ``0o600`` so a fresh
    file is never world-readable, then calls ``paths.secure_file`` to enforce
    ``0o600`` even when overwriting a file that already existed with broader
    permissions.
    """
    fd = os.open(
        out_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, paths.DB_FILE_MODE
    )
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    paths.secure_file(out_path)


def cmd_export(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        reviews = db.list_reviews(conn)
        entries = db.list_entries(conn)
        feedback_by_entry = {
            entry.id: db.list_feedback(conn, entry.id) for entry in entries
        }
    rendered = _render_markdown_export(entries, feedback_by_entry, reviews)
    if not rendered.endswith("\n"):
        rendered += "\n"
    if args.out is None:
        sys.stdout.write(rendered)
        return 0
    out_path = Path(args.out).expanduser()
    try:
        _write_export_file(out_path, rendered)
    except OSError as exc:
        raise db.DBError(f"cannot write export to {out_path}: {exc}") from exc
    print_err(f"exported to {out_path}")
    return 0
