"""The ``commscoach feedback`` subcommands."""

from __future__ import annotations

import argparse
import sys
import textwrap

from .. import db
from .common import (
    confirm,
    format_stamp,
    non_negative_int,
    open_db,
    print_json,
    prose_lines,
    read_body_arg,
    search_excerpt,
)
from .editor import compose_in_editor
from .serializers import entry_to_dict, feedback_to_dict


FEEDBACK_PREAMBLE = (
    "Write the feedback body below. Lines beginning with #> are ignored."
)


def register(subparsers: argparse._SubParsersAction) -> None:
    feedback_parser = subparsers.add_parser(
        "feedback",
        help="Manage coaching feedback notes attached to entries.",
    )
    feedback_sub = feedback_parser.add_subparsers(
        dest="feedback_command", metavar="SUBCOMMAND"
    )

    def _show_feedback_help(_args: argparse.Namespace) -> int:
        feedback_parser.print_help()
        return 0

    feedback_parser.set_defaults(func=_show_feedback_help)

    add = feedback_sub.add_parser(
        "add", help="Attach a feedback note to an existing entry."
    )
    add.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    add.add_argument(
        "body",
        nargs="?",
        default=None,
        help=(
            "Feedback body (literal text, '-' for stdin, '@PATH' for a UTF-8"
            " file, or omit to open $EDITOR)."
        ),
    )
    add.set_defaults(func=cmd_feedback_add)

    lst = feedback_sub.add_parser(
        "list", help="List feedback notes for an entry, oldest first."
    )
    lst.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    lst.set_defaults(func=cmd_feedback_list)

    search = feedback_sub.add_parser("search", help="Search stored coaching text.")
    search.add_argument(
        "--query", required=True, metavar="TEXT",
        help="Literal case-insensitive substring to find in feedback bodies.",
    )
    search.add_argument("--person", default=None, metavar="HANDLE")
    search.add_argument(
        "--limit", type=non_negative_int, default=20, metavar="N",
        help="Maximum results (default: 20); text output reports omitted matches.",
    )
    search.set_defaults(func=cmd_feedback_search)

    show = feedback_sub.add_parser("show", help="Show one feedback note by id.")
    show.add_argument("feedback_id", type=int, metavar="FEEDBACK_ID")
    show.add_argument(
        "--with-entry", action="store_true",
        help="Include the linked entry's full current text and metadata.",
    )
    show.set_defaults(func=cmd_feedback_show)

    edit = feedback_sub.add_parser("edit", help="Rewrite a feedback note body.")
    edit.add_argument("feedback_id", type=int, metavar="FEEDBACK_ID")
    edit.add_argument(
        "--body",
        nargs="?",
        const="",
        default=None,
        help=(
            "Replace the body (text, '-', or '@PATH'); omit the value to open"
            " $EDITOR with the current body."
        ),
    )
    edit.set_defaults(func=cmd_feedback_edit)

    rm = feedback_sub.add_parser("rm", help="Delete one feedback note.")
    rm.add_argument("feedback_id", type=int, metavar="FEEDBACK_ID")
    rm.set_defaults(func=cmd_feedback_rm)


def _format_feedback_human(feedback: db.Feedback) -> str:
    header = (
        f"[{feedback.id}] entry {feedback.entry_id}"
        f" created {feedback.created_at}"
    )
    return "\n".join([header, "---", feedback.body])


PREVIEW_WIDTH = 76
PREVIEW_LINES = 3


def _format_feedback_short(feedback: db.Feedback, *, query: str | None = None) -> str:
    """Render a listing block: an id/time/entry header over indented prose."""
    header = (
        f"[{feedback.id}] {format_stamp(feedback.created_at)}"
        f" · entry {feedback.entry_id}"
    )
    preview = (
        search_excerpt(feedback.body, query) if query is not None
        else " · ".join(prose_lines(feedback.body))
    )
    wrapped = textwrap.wrap(preview, PREVIEW_WIDTH)
    if query is None and len(wrapped) > PREVIEW_LINES:
        wrapped = wrapped[:PREVIEW_LINES]
        wrapped[-1] += "…"
    return "\n".join([header, *(f"    {line}" for line in wrapped)])


def _emit_feedback(
    args: argparse.Namespace,
    feedback: db.Feedback,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(feedback_to_dict(feedback))
        return
    if human_prefix:
        print(f"{human_prefix} feedback {feedback.id}")
    print(_format_feedback_human(feedback))


def _emit_feedback_list(
    args: argparse.Namespace, items: tuple[db.Feedback, ...], *, query: str | None = None,
) -> None:
    if args.json:
        print_json([feedback_to_dict(f) for f in items])
        return
    if not items:
        print("(no feedback)")
        return
    for index, feedback in enumerate(items):
        if index:
            print()
        print(_format_feedback_short(feedback, query=query))


def cmd_feedback_add(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        body = read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=FEEDBACK_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        feedback = db.create_feedback(conn, args.entry_id, body)
    _emit_feedback(args, feedback, human_prefix="created")
    return 0


def cmd_feedback_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        items = db.list_feedback(conn, args.entry_id)
    _emit_feedback_list(args, items)
    return 0


def cmd_feedback_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        feedback = db.get_feedback(conn, args.feedback_id)
        if feedback is None:
            raise db.FeedbackNotFoundError(args.feedback_id)
        entry = db.get_entry(conn, feedback.entry_id) if args.with_entry else None
        if args.with_entry and entry is None:
            raise db.EntryNotFoundError(feedback.entry_id)
    if entry is not None:
        if args.json:
            print_json({**feedback_to_dict(feedback), "entry": entry_to_dict(entry)})
            return 0
        # Entry commands also use feedback renderers, so import at call time.
        from .entries_format import format_entry_human

        print("Linked entry (current text):")
        print(format_entry_human(entry))
        print("\nFeedback:")
    _emit_feedback(args, feedback)
    return 0


def cmd_feedback_search(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        items = db.search_feedback(
            conn, query=args.query, person_handle=args.person, limit=args.limit,
        )
        total = (
            db.count_search_feedback(conn, query=args.query, person_handle=args.person)
            if not args.json else len(items)
        )
    _emit_feedback_list(args, items, query=args.query)
    if not args.json and total > len(items):
        print(f"Showing {len(items)} of {total} matching feedback notes.")
    return 0


def cmd_feedback_edit(args: argparse.Namespace) -> int:
    if args.body is None:
        raise db.DBError("feedback edit requires --body")
    body = (
        read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=FEEDBACK_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        if args.body != ""
        else None
    )
    with open_db(args) as conn:
        if body is None:
            current = db.get_feedback(conn, args.feedback_id)
            if current is None:
                raise db.FeedbackNotFoundError(args.feedback_id)
            body = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=FEEDBACK_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        feedback = db.update_feedback(conn, args.feedback_id, body=body)
    _emit_feedback(args, feedback, human_prefix="updated")
    return 0


def cmd_feedback_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_feedback(conn, args.feedback_id) is None:
            raise db.FeedbackNotFoundError(args.feedback_id)
        if not confirm(
            f"Delete feedback {args.feedback_id}?", assume_yes=args.yes
        ):
            raise db.DBError("aborted")
        deleted = db.delete_feedback(conn, args.feedback_id)
    if not deleted:
        raise db.FeedbackNotFoundError(args.feedback_id)
    if args.json:
        print_json({"deleted": True, "id": args.feedback_id})
    else:
        print(f"deleted feedback {args.feedback_id}")
    return 0
