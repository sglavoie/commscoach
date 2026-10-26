"""The ``commscoach export`` command: Markdown or lossless JSON output."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .. import db, paths
from ..db.common import read_snapshot
from .common import format_scope, open_db, print_err, print_json, resolve_db_path


def register(subparsers: argparse._SubParsersAction) -> None:
    export_parser = subparsers.add_parser(
        "export",
        help="Export the database as lossy Markdown or restorable JSON.",
        description=(
            "Export the database. Markdown is human-readable and lossy; "
            "JSON is complete and restorable."
        ),
    )
    export_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default=None,
        help=(
            "Export format: Markdown is readable/lossy; JSON is complete/restorable"
            " (default: markdown; the global --json selects json unless --format is given)."
        ),
    )
    export_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write output to this file (not a directory) with mode 0600. Default: stdout.",
    )
    export_parser.set_defaults(func=cmd_export)


def _format_person_export_label(person: db.Person) -> str:
    label = f"@{person.handle}"
    if person.name:
        label += f" ({person.name})"
    if person.is_self:
        label += " (self)"
    return label


def _format_markdown_entry(entry: db.Entry, feedback_items: tuple[db.Feedback, ...]) -> str:
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
    """Render one review as a Markdown section, naming its scope axis.

    The scope line reads ``person @alice`` / ``tag conflict`` /
    ``range 2026-07-01..2026-09-01`` and keeps the existing
    ``(context limit N)`` suffix. ``format_scope`` is shared with the ``review``
    listings, so a person whose row was later deleted is still named from the
    denormalized ``scope_person_handle`` -- the Markdown export is lossy, but
    it does not have to be amnesiac.
    """
    lines: list[str] = [f"### Review {review.id}", ""]
    lines.append(f"- **Created**: {review.created_at}")
    scope = format_scope(review, separator=" ")
    if review.scope_limit is not None:
        scope += f" (context limit {review.scope_limit})"
    lines.append(f"- **Scope**: {scope}")
    entry_ids = ", ".join(str(entry.id) for entry in review.entries)
    lines.append(f"- **Covered entry IDs**: {entry_ids or '(none)'}")
    lines.append("")
    lines.append(review.body.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def _format_markdown_goal(goal: db.Goal) -> str:
    lines: list[str] = [f"### Goal {goal.id} — {goal.title}", ""]
    lines.append(f"- **Status**: {goal.status}")
    lines.append(f"- **Created**: {goal.created_at}")
    if goal.closed_at is not None:
        lines.append(f"- **Closed**: {goal.closed_at}")
    if goal.person is not None:
        lines.append(f"- **Person**: @{goal.person.handle}")
    if goal.review is not None:
        lines.append(f"- **Review**: {goal.review.id}")
    if goal.detail is not None:
        lines.extend(("", goal.detail.rstrip("\n")))
    if goal.outcome is not None:
        lines.extend(("", "#### Outcome", "", goal.outcome.rstrip("\n")))
    lines.append("")
    return "\n".join(lines)


def _render_markdown_export(
    entries: tuple[db.Entry, ...],
    feedback_by_entry: dict[int, tuple[db.Feedback, ...]],
    reviews: tuple[db.Review, ...],
    goals: tuple[db.Goal, ...] = (),
) -> str:
    sections: list[str] = ["# commscoach export", ""]
    if reviews:
        sections.append("## Reviews")
        sections.append("")
        rendered_reviews = [_format_markdown_review(review) for review in reviews]
        sections.append("\n---\n\n".join(rendered_reviews).rstrip("\n"))
        sections.append("")
    if goals:
        sections.append("## Goals")
        sections.append("")
        rendered_goals = [_format_markdown_goal(goal) for goal in goals]
        sections.append("\n---\n\n".join(rendered_goals).rstrip("\n"))
        sections.append("")
    if not entries:
        sections.append("_No entries._")
        sections.append("")
        return "\n".join(sections)
    rendered_entries: list[str] = []
    for entry in entries:
        rendered_entries.append(_format_markdown_entry(entry, feedback_by_entry[entry.id]))
    sections.append("\n---\n\n".join(rendered_entries))
    if not sections[-1].endswith("\n"):
        sections.append("")
    return "\n".join(sections)


def _write_export_file(out_path: Path, content: str) -> None:
    """Replace the destination only after a complete, private sibling is written."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temporary = Path(fh.name)
            paths.secure_file(temporary)
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, out_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_export_destination(out_path: Path, source_path: Path) -> None:
    """Protect the active store and SQLite sidecars, including filesystem aliases."""
    if out_path.is_dir():
        raise db.DBError(f"export destination {out_path} is a directory; give a file path")
    source = source_path.resolve()
    protected = [source, *(Path(f"{source}{suffix}") for suffix in ("-wal", "-shm", "-journal"))]
    for path in protected:
        if out_path.resolve() == path or (
            out_path.exists() and path.exists() and out_path.samefile(path)
        ):
            raise db.DBError(
                "export destination must differ from the source database and its SQLite sidecars"
            )


def cmd_export(args: argparse.Namespace) -> int:
    # With --out, --json only shapes the status line, so any --format can be written.
    if args.json and args.format == "markdown" and args.out is None:
        raise db.DBError("--json conflicts with --format markdown unless --out is given")
    export_format = args.format or ("json" if args.json else "markdown")
    out_path = Path(args.out).expanduser() if args.out is not None else None
    if out_path is not None:
        try:
            _validate_export_destination(out_path, resolve_db_path(args))
        except OSError as exc:
            raise db.DBError(f"cannot write export to {out_path}: {exc}") from exc
    with open_db(args) as conn:
        if export_format == "json":
            rendered = json.dumps(
                db.dump_to_dict(conn),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        else:
            # One snapshot, as the JSON dump uses, so the sections agree.
            with read_snapshot(conn):
                reviews = db.list_reviews(conn)
                goals = db.list_goals(conn, status=None)
                entries = db.list_entries(conn)
                feedback_by_entry = {
                    entry.id: db.list_feedback(conn, entry.id) for entry in entries
                }
            rendered = _render_markdown_export(entries, feedback_by_entry, reviews, goals)
    if not rendered.endswith("\n"):
        rendered += "\n"
    if out_path is None:
        sys.stdout.write(rendered)
        return 0
    try:
        _write_export_file(out_path, rendered)
    except OSError as exc:
        raise db.DBError(f"cannot write export to {out_path}: {exc}") from exc
    if args.json:
        # stdout carries no export here, so report the write like backup does.
        print_json({"format": export_format, "path": str(out_path)})
    else:
        print_err(f"exported to {out_path}")
    return 0
