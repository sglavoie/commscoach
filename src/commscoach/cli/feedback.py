"""The ``commscoach feedback`` subcommands."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import textwrap

from .. import db
from ..db.common import write_transaction
from . import nudges, pretty_views, style
from .common import (
    EDIT_IN_EDITOR,
    UsageError,
    add_date_bounds,
    add_oldest_first,
    confirm,
    edit_verb,
    format_stamp,
    handle_arg,
    non_negative_int,
    open_db,
    parse_date_bounds,
    print_json,
    prose_lines,
    read_body_arg,
    read_replacement_body,
    row_id,
    search_excerpt,
)
from .editor import compose_in_editor
from .serializers import entry_to_dict, feedback_to_dict

FEEDBACK_PREAMBLE = "Write the feedback body below. Lines beginning with #> are ignored."
GOAL_CHECK_HELP = (
    "Record this note's verdict on a goal as GOAL_ID=VERDICT, where VERDICT is"
    " advanced, hurt, or neutral; append ':NOTE' for a one-line reason."
    " Repeat for each goal."
)


def goal_check_arg(value: str) -> tuple[int, str, str | None]:
    """Parse ``GOAL_ID=VERDICT`` or ``GOAL_ID=VERDICT:short note``."""
    goal_part, equals, rest = value.partition("=")
    if not equals:
        raise argparse.ArgumentTypeError(f"expected GOAL_ID=VERDICT[:NOTE], got {value!r}")
    try:
        goal_id = row_id(goal_part.strip())
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError(
            f"goal id must be a positive integer, got {goal_part.strip()!r}"
        ) from exc
    verdict_part, colon, note = rest.partition(":")
    verdict = verdict_part.strip().lower()
    if verdict not in db.GOAL_CHECK_VERDICTS:
        raise argparse.ArgumentTypeError(
            f"verdict must be advanced, hurt, or neutral, got {verdict_part.strip()!r}"
        )
    if not colon:
        return goal_id, verdict, None
    if not note.strip():
        raise argparse.ArgumentTypeError(
            f"note after ':' must not be empty in {value!r}; omit ':' to leave it unset"
        )
    return goal_id, verdict, note.strip()


def _reject_duplicate_goals(checks: list[tuple[int, str, str | None]]) -> None:
    seen: set[int] = set()
    for goal_id, _verdict, _note in checks:
        if goal_id in seen:
            raise UsageError(f"--goal-check names goal {goal_id} more than once")
        seen.add(goal_id)


def register(subparsers: argparse._SubParsersAction) -> None:
    feedback_parser = subparsers.add_parser(
        "feedback",
        help="Manage coaching feedback notes attached to entries.",
    )
    feedback_sub = feedback_parser.add_subparsers(dest="feedback_command", metavar="SUBCOMMAND")

    def _show_feedback_help(_args: argparse.Namespace) -> int:
        feedback_parser.print_help()
        return 0

    feedback_parser.set_defaults(func=_show_feedback_help)

    add = feedback_sub.add_parser("add", help="Attach a feedback note to an existing entry.")
    add.add_argument("entry_id", type=row_id, metavar="ENTRY_ID")
    add.add_argument(
        "body",
        nargs="?",
        default=None,
        help=(
            "Feedback body (literal text, '-' for stdin, '@PATH' for a UTF-8"
            " file, or omit to open $EDITOR)."
        ),
    )
    add.add_argument(
        "--goal-check",
        action="append",
        default=[],
        type=goal_check_arg,
        metavar="GOAL_ID=VERDICT[:NOTE]",
        help=GOAL_CHECK_HELP,
    )
    add.set_defaults(func=cmd_feedback_add)

    lst = feedback_sub.add_parser("list", help="List feedback notes for an entry, oldest first.")
    lst.add_argument("entry_id", type=row_id, metavar="ENTRY_ID")
    lst.set_defaults(func=cmd_feedback_list)

    search = feedback_sub.add_parser(
        "search", help="Search stored coaching text, or browse it across entries."
    )
    search.add_argument(
        "--query",
        default=None,
        metavar="TEXT",
        help=(
            "Literal substring (ignoring case and accents) to find in feedback bodies;"
            " omit it to list every note matching the other filters."
        ),
    )
    search.add_argument(
        "--person",
        default=None,
        type=handle_arg,
        metavar="HANDLE",
        help="Only feedback on entries linked to this person.",
    )
    search.add_argument(
        "--limit",
        type=non_negative_int,
        default=20,
        metavar="N",
        help="Maximum results (default: 20); text output reports omitted matches.",
    )
    add_date_bounds(search, noun="feedback written")
    add_oldest_first(search, noun="matching feedback")
    search.set_defaults(func=cmd_feedback_search)

    show = feedback_sub.add_parser("show", help="Show one feedback note by id.")
    show.add_argument("feedback_id", type=row_id, metavar="FEEDBACK_ID")
    show.add_argument(
        "--with-entry",
        action="store_true",
        help="Include the linked entry's full current text and metadata.",
    )
    show.set_defaults(func=cmd_feedback_show)

    edit = feedback_sub.add_parser("edit", help="Rewrite a feedback note body.")
    edit.add_argument("feedback_id", type=row_id, metavar="FEEDBACK_ID")
    edit.add_argument(
        "--body",
        nargs="?",
        const=EDIT_IN_EDITOR,
        default=None,
        help=(
            "Replace the body (text, '-', or '@PATH'); omit the value to open"
            " $EDITOR with the current body."
        ),
    )
    edit.add_argument(
        "--goal-check",
        action="append",
        default=None,
        type=goal_check_arg,
        metavar="GOAL_ID=VERDICT[:NOTE]",
        help=GOAL_CHECK_HELP.replace("Repeat for each goal.", "Replaces the note's checks."),
    )
    edit.add_argument(
        "--clear-goal-checks",
        action="store_true",
        help="Remove every goal check from the note.",
    )
    edit.set_defaults(func=cmd_feedback_edit)

    rm = feedback_sub.add_parser("rm", help="Delete one feedback note.")
    rm.add_argument("feedback_id", type=row_id, metavar="FEEDBACK_ID")
    rm.set_defaults(func=cmd_feedback_rm)


def _format_feedback_human(feedback: db.Feedback) -> str:
    if style.pretty():
        return pretty_views.feedback_human(feedback)
    header = (
        f"[{feedback.id}] entry {feedback.entry_id} created {format_stamp(feedback.created_at)}"
    )
    return "\n".join([header, "---", feedback.body])


PREVIEW_WIDTH = 76
PREVIEW_LINES = 3


def _format_feedback_short(feedback: db.Feedback, *, query: str | None = None) -> str:
    """Render a listing block: an id/time/entry header over indented prose."""
    header = f"[{feedback.id}] {format_stamp(feedback.created_at)} · entry {feedback.entry_id}"
    preview = (
        search_excerpt(feedback.body, query)
        if query is not None
        else " · ".join(prose_lines(feedback.body))
    )
    wrapped = textwrap.wrap(preview, PREVIEW_WIDTH)
    if query is None and len(wrapped) > PREVIEW_LINES:
        wrapped = wrapped[:PREVIEW_LINES]
        wrapped[-1] += "…"
    return "\n".join([header, *(f"    {line}" for line in wrapped)])


def _format_check_given(check: db.GoalCheck, goal: db.Goal | None) -> str:
    """Render a stored check for ``feedback add``: ``goal 3 advanced: Title — note``."""
    line = f"goal {check.goal_id} {check.verdict}"
    if goal is not None:
        line += f": {goal.title}"
    return f"{line} — {check.note}" if check.note is not None else line


def _emit_feedback(
    args: argparse.Namespace,
    feedback: db.Feedback,
    checks: tuple[db.GoalCheck, ...],
    *,
    human_prefix: str | None = None,
    goals: dict[int, db.Goal] | None = None,
) -> None:
    """Print one note; ``goals`` (the checked goals) adds one line per check."""
    if args.json:
        print_json(feedback_to_dict(feedback, checks))
        return
    if human_prefix:
        print(f"{human_prefix} feedback {feedback.id}")
    print(_format_feedback_human(feedback))
    if goals is not None:
        for check in checks:
            print(_format_check_given(check, goals.get(check.goal_id)))


def _checked_goals(
    conn: sqlite3.Connection, checks: tuple[db.GoalCheck, ...]
) -> dict[int, db.Goal]:
    goals = {}
    for check in checks:
        goal = db.get_goal(conn, check.goal_id)
        if goal is not None:
            goals[goal.id] = goal
    return goals


def _emit_feedback_list(
    args: argparse.Namespace,
    items: tuple[db.Feedback, ...],
    checks: dict[int, tuple[db.GoalCheck, ...]],
    *,
    query: str | None = None,
) -> None:
    if args.json:
        print_json([feedback_to_dict(f, checks.get(f.id, ())) for f in items])
        return
    if not items:
        print("(no feedback)")
        return
    for index, feedback in enumerate(items):
        if index:
            print()
        print(_format_feedback_short(feedback, query=query))


def cmd_feedback_add(args: argparse.Namespace) -> int:
    _reject_duplicate_goals(args.goal_check)
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        db.validate_goal_checks(conn, args.goal_check)
        body = read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=FEEDBACK_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        feedback = db.create_feedback(conn, args.entry_id, body, goal_checks=args.goal_check)
        reaction = nudges.feedback_stored(conn, feedback) if nudges.enabled(args) else None
        checks = db.list_feedback_goal_checks(conn, [feedback.id]).get(feedback.id, ())
        goals = _checked_goals(conn, checks) if checks else None
    if reaction is not None:
        print(pretty_views.feedback_stored(feedback, checks, goals))
        print("\n".join(reaction))
        return 0
    _emit_feedback(args, feedback, checks, human_prefix="created", goals=goals)
    return 0


def cmd_feedback_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        items = db.list_feedback(conn, args.entry_id)
        checks = db.list_feedback_goal_checks(conn, (f.id for f in items))
    _emit_feedback_list(args, items, checks)
    return 0


def cmd_feedback_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        feedback = db.get_feedback(conn, args.feedback_id)
        if feedback is None:
            raise db.FeedbackNotFoundError(args.feedback_id)
        entry = db.get_entry(conn, feedback.entry_id) if args.with_entry else None
        if args.with_entry and entry is None:
            raise db.EntryNotFoundError(feedback.entry_id)
        checks = db.list_feedback_goal_checks(conn, [feedback.id]).get(feedback.id, ())
    if entry is not None:
        if args.json:
            print_json({**feedback_to_dict(feedback, checks), "entry": entry_to_dict(entry)})
            return 0
        # Entry commands also use feedback renderers, so import at call time.
        from .entries_format import format_entry_human

        print("Linked entry (current text):")
        print(format_entry_human(entry))
        print("\nFeedback:")
    _emit_feedback(args, feedback, checks)
    return 0


def cmd_feedback_search(args: argparse.Namespace) -> int:
    since, until = parse_date_bounds(args)
    with open_db(args) as conn:
        items = db.search_feedback(
            conn,
            query=args.query,
            person_handle=args.person,
            since=since,
            until=until,
            limit=args.limit,
            oldest_first=args.oldest_first,
        )
        total = db.count_search_feedback(
            conn, query=args.query, person_handle=args.person, since=since, until=until
        )
        checks = db.list_feedback_goal_checks(conn, (f.id for f in items))
    _emit_feedback_list(args, items, checks, query=args.query)
    if total > len(items):
        noun = "matching feedback notes" if args.query is not None else "feedback notes"
        print(f"Showing {len(items)} of {total} {noun}.", file=sys.stderr)
    return 0


def cmd_feedback_edit(args: argparse.Namespace) -> int:
    """Rewrite a note's body, replace its goal checks, or both, in one transaction."""
    if args.goal_check is not None and args.clear_goal_checks:
        raise UsageError("--clear-goal-checks cannot be combined with --goal-check")
    new_checks = [] if args.clear_goal_checks else args.goal_check
    if new_checks is not None:
        _reject_duplicate_goals(new_checks)
    if args.body is None and new_checks is None:
        raise db.DBError("feedback edit requires --body, --goal-check, or --clear-goal-checks")
    body: str | None = None
    edit_body = False
    if args.body is not None:
        body = read_replacement_body(args.body, command="feedback edit")
        edit_body = body is None
    with open_db(args) as conn:
        current = db.get_feedback(conn, args.feedback_id)
        if current is None:
            raise db.FeedbackNotFoundError(args.feedback_id)
        if new_checks is not None:
            db.validate_goal_checks(conn, new_checks)
        if edit_body:
            body = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=FEEDBACK_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
                flag="--body",
            )
        with write_transaction(conn):
            if body is not None:
                db.update_feedback(conn, args.feedback_id, body=body)
            if new_checks is not None:
                db.replace_goal_checks(conn, args.feedback_id, new_checks)
        feedback = db.get_feedback(conn, args.feedback_id)
        assert feedback is not None
        checks = db.list_feedback_goal_checks(conn, [feedback.id]).get(feedback.id, ())
        goals = _checked_goals(conn, checks) if new_checks else None
    _emit_feedback(
        args,
        feedback,
        checks,
        human_prefix=edit_verb(current.updated_at, feedback.updated_at),
        goals=goals,
    )
    return 0


def cmd_feedback_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_feedback(conn, args.feedback_id) is None:
            raise db.FeedbackNotFoundError(args.feedback_id)
        if not confirm(f"Delete feedback {args.feedback_id}?", assume_yes=args.yes):
            raise db.DBError("aborted; pass --yes to confirm without a prompt")
        deleted = db.delete_feedback(conn, args.feedback_id)
    if not deleted:
        raise db.FeedbackNotFoundError(args.feedback_id)
    if args.json:
        print_json({"deleted": True, "id": args.feedback_id})
    else:
        print(f"deleted feedback {args.feedback_id}")
    return 0
