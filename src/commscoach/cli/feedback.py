"""The ``commscoach feedback`` subcommands."""

from __future__ import annotations

import argparse
import sys

from .. import db
from .common import open_db, print_json, read_body_arg
from .editor import compose_in_editor
from .serializers import feedback_to_dict


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

    get = feedback_sub.add_parser("get", help="Show one feedback note by id.")
    get.add_argument("feedback_id", type=int, metavar="FEEDBACK_ID")
    get.set_defaults(func=cmd_feedback_get)


def _format_feedback_human(feedback: db.Feedback) -> str:
    header = (
        f"[{feedback.id}] entry {feedback.entry_id}"
        f" created {feedback.created_at}"
    )
    return "\n".join([header, "---", feedback.body])


def _format_feedback_short(feedback: db.Feedback) -> str:
    first_line = feedback.body.strip().split("\n", 1)[0]
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    return f"[{feedback.id}] {feedback.created_at} {first_line}"


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
    args: argparse.Namespace, items: tuple[db.Feedback, ...]
) -> None:
    if args.json:
        print_json([feedback_to_dict(f) for f in items])
        return
    if not items:
        print("(no feedback)")
        return
    for feedback in items:
        print(_format_feedback_short(feedback))


def cmd_feedback_add(args: argparse.Namespace) -> int:
    body = read_body_arg(
        args.body,
        what="body",
        template="",
        preamble=FEEDBACK_PREAMBLE,
        isatty=sys.stdin.isatty,
        compose=compose_in_editor,
    )
    with open_db(args) as conn:
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


def cmd_feedback_get(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        feedback = db.get_feedback(conn, args.feedback_id)
    if feedback is None:
        raise db.FeedbackNotFoundError(args.feedback_id)
    _emit_feedback(args, feedback)
    return 0
