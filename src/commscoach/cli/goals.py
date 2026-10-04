"""The ``commscoach goal`` subcommands."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any

from .. import db
from .common import (
    confirm,
    non_negative_int,
    open_db,
    print_json,
    read_body_arg,
    read_body_or_raise,
    search_excerpt,
)
from .editor import compose_in_editor
from .serializers import goal_to_dict

_EDIT_TEXT = object()
DETAIL_PREAMBLE = (
    "Edit goal detail below. Lines beginning with #> are ignored.\n"
    "Use --clear-detail to remove detail; an empty draft will not be saved."
)
OUTCOME_PREAMBLE = "Edit goal outcome below. Lines beginning with #> are ignored."


def register(subparsers: argparse._SubParsersAction) -> None:
    goal_parser = subparsers.add_parser("goal", help="Manage coaching goals.")
    goal_sub = goal_parser.add_subparsers(dest="goal_command", metavar="SUBCOMMAND")

    def _show_goal_help(_args: argparse.Namespace) -> int:
        goal_parser.print_help()
        return 0

    goal_parser.set_defaults(func=_show_goal_help)

    add = goal_sub.add_parser("add", help="Create a new goal.")
    add.add_argument("title", help="Short goal title.")
    add.add_argument(
        "--detail",
        default=None,
        help="Goal detail (literal text, '-' for stdin, or '@PATH' for a UTF-8 file).",
    )
    add.add_argument("--person", default=None, metavar="HANDLE")
    add.add_argument("--review", type=int, default=None, metavar="ID")
    add.set_defaults(func=cmd_goal_add)

    lst = goal_sub.add_parser("list", help="List goals.")
    lst.add_argument(
        "--status",
        choices=("active", "done", "dropped", "all"),
        default="active",
    )
    lst.add_argument("--person", default=None, metavar="HANDLE")
    lst.add_argument("--limit", type=non_negative_int, default=None, metavar="N")
    lst.add_argument(
        "--oldest-first", action="store_true",
        help="Show oldest goals first before applying --limit (active first with --status all).",
    )
    lst.set_defaults(func=cmd_goal_list)
    lst.add_argument(
        "--with-detail", action="store_true", help="Show full detail beneath each goal.",
    )

    search = goal_sub.add_parser("search", help="Search goal titles, details, and outcomes.")
    search.add_argument("--query", required=True, metavar="TEXT")
    search.add_argument(
        "--status", choices=("active", "done", "dropped", "all"), default="all",
        help="Goal status (default: all, active goals first).",
    )
    search.add_argument("--person", default=None, metavar="HANDLE")
    search.add_argument("--limit", type=non_negative_int, default=20, metavar="N")
    search.add_argument("--oldest-first", action="store_true")
    search.add_argument("--with-detail", action="store_true")
    search.set_defaults(func=cmd_goal_list)

    show = goal_sub.add_parser("show", help="Show one goal by id.")
    show.add_argument("goal_id", type=int, metavar="GOAL_ID")
    show.set_defaults(func=cmd_goal_show)

    edit = goal_sub.add_parser("edit", help="Update an existing goal.")
    edit.add_argument("goal_id", type=int, metavar="GOAL_ID")
    edit.add_argument("--title", default=None)
    edit.add_argument(
        "--detail",
        nargs="?",
        const=_EDIT_TEXT,
        default=None,
        help="Replace detail (text, '-', or '@PATH'); omit the value to open $EDITOR.",
    )
    edit.add_argument("--clear-detail", action="store_true")
    edit.add_argument("--person", default=None, metavar="HANDLE")
    edit.add_argument("--clear-person", action="store_true")
    edit.add_argument("--status", choices=("active", "done", "dropped"), default=None)
    edit.set_defaults(func=cmd_goal_edit)

    close = goal_sub.add_parser("close", help="Close a goal as done or dropped.")
    close.add_argument("goal_id", type=int, metavar="GOAL_ID")
    close.add_argument(
        "--outcome",
        nargs="?",
        const=_EDIT_TEXT,
        default=None,
        help="Outcome (text, '-', or '@PATH'); omit the value to open $EDITOR.",
    )
    close.add_argument("--dropped", action="store_true")
    close.set_defaults(func=cmd_goal_close)

    rm = goal_sub.add_parser("rm", help="Delete one goal.")
    rm.add_argument("goal_id", type=int, metavar="GOAL_ID")
    rm.set_defaults(func=cmd_goal_rm)


def _format_goal_human(goal: db.Goal) -> str:
    parts = [f"[{goal.id}]", goal.status, f"created {goal.created_at}"]
    if goal.closed_at is not None:
        parts.append(f"closed {goal.closed_at}")
    lines = [" ".join(parts), goal.title]
    if goal.person is not None:
        lines.append(f"person: @{goal.person.handle}")
    if goal.review is not None:
        lines.append(f"review: {goal.review.id}")
    if goal.detail is not None:
        lines.extend(("---", goal.detail))
    if goal.outcome is not None:
        lines.extend(("---", "outcome:", goal.outcome))
    return "\n".join(lines)


def _format_goal_short(goal: db.Goal, *, now: datetime | None = None) -> str:
    parts = [f"[{goal.id}]", goal.status, goal.title]
    if goal.person is not None:
        parts.append(f"@{goal.person.handle}")
    if goal.is_open:
        try:
            created = datetime.fromisoformat(goal.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days = max(0, ((now or datetime.now(timezone.utc)) - created).days)
            parts.append(f"· age: {days} {'day' if days == 1 else 'days'}")
        except ValueError:
            pass
    return " ".join(parts)


def _emit_goal(
    args: argparse.Namespace,
    goal: db.Goal,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(goal_to_dict(goal))
        return
    if human_prefix:
        print(f"{human_prefix} goal {goal.id}")
    print(_format_goal_human(goal))


def _emit_goals(
    args: argparse.Namespace, goals: tuple[db.Goal, ...], *, with_detail: bool = False,
    query: str | None = None,
) -> None:
    if args.json:
        print_json([goal_to_dict(goal) for goal in goals])
        return
    if not goals:
        print("(no goals)")
        return
    now = datetime.now(timezone.utc)
    for goal in goals:
        line = _format_goal_short(goal, now=now)
        if with_detail and goal.person is None:
            line += " (general)"
        print(line)
        if with_detail and goal.detail is not None:
            print("\n".join(f"  {line}" for line in goal.detail.splitlines()))
        if query is not None:
            fold = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
            for label, body in (("title", goal.title), ("detail", goal.detail),
                                ("outcome", goal.outcome)):
                if body is not None and query.translate(fold) in body.translate(fold):
                    print(f"  {label}: {search_excerpt(body, query)}")


def cmd_goal_add(args: argparse.Namespace) -> int:
    detail = read_body_or_raise(args.detail) if args.detail is not None else None
    with open_db(args) as conn:
        goal = db.create_goal(
            conn,
            args.title,
            detail=detail,
            person_handle=args.person,
            review_id=args.review,
        )
    _emit_goal(args, goal, human_prefix="created")
    return 0


def cmd_goal_list(args: argparse.Namespace) -> int:
    status = None if args.status == "all" else args.status
    with open_db(args) as conn:
        goals = db.list_goals(
            conn,
            status=status,
            person_handle=args.person,
            limit=args.limit,
            oldest_first=args.oldest_first,
            query=getattr(args, "query", None),
        )
    _emit_goals(args, goals, with_detail=args.with_detail, query=getattr(args, "query", None))
    return 0


def cmd_goal_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        goal = db.get_goal(conn, args.goal_id)
    if goal is None:
        raise db.GoalNotFoundError(args.goal_id)
    _emit_goal(args, goal)
    return 0


def cmd_goal_edit(args: argparse.Namespace) -> int:
    if args.clear_detail and args.detail is not None:
        raise db.DBError("--clear-detail cannot be combined with --detail")
    if args.clear_person and args.person is not None:
        raise db.DBError("--clear-person cannot be combined with --person")

    edit_detail = args.detail is _EDIT_TEXT
    update_kwargs: dict[str, Any] = {}
    if args.title is not None:
        update_kwargs["title"] = args.title
    if args.clear_detail:
        update_kwargs["detail"] = None
    elif args.detail is not None and not edit_detail:
        update_kwargs["detail"] = read_body_or_raise(args.detail)
    if args.clear_person:
        update_kwargs["person_handle"] = None
    elif args.person is not None:
        update_kwargs["person_handle"] = args.person
    if args.status is not None:
        update_kwargs["status"] = args.status

    if not update_kwargs and not edit_detail:
        raise db.DBError(
            "goal edit requires at least one of "
            "--title/--detail/--clear-detail/--person/--clear-person/--status"
        )
    with open_db(args) as conn:
        if edit_detail:
            current = db.get_goal(conn, args.goal_id)
            if current is None:
                raise db.GoalNotFoundError(args.goal_id)
            update_kwargs["detail"] = read_body_arg(
                None,
                what="detail",
                template=current.detail or "",
                preamble=DETAIL_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        goal = db.update_goal(conn, args.goal_id, **update_kwargs)
    _emit_goal(args, goal, human_prefix="updated")
    return 0


def cmd_goal_close(args: argparse.Namespace) -> int:
    edit_outcome = args.outcome is _EDIT_TEXT
    outcome = (
        read_body_or_raise(args.outcome)
        if args.outcome is not None and not edit_outcome else None
    )
    with open_db(args) as conn:
        if edit_outcome:
            current = db.get_goal(conn, args.goal_id)
            if current is None:
                raise db.GoalNotFoundError(args.goal_id)
            outcome = read_body_arg(
                None,
                what="outcome",
                template=current.outcome or "",
                preamble=OUTCOME_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        goal = db.close_goal(
            conn,
            args.goal_id,
            outcome=outcome,
            dropped=args.dropped,
        )
    _emit_goal(args, goal, human_prefix="closed")
    return 0


def cmd_goal_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_goal(conn, args.goal_id) is None:
            raise db.GoalNotFoundError(args.goal_id)
        if not confirm(f"Delete goal {args.goal_id}?", assume_yes=args.yes):
            raise db.DBError("aborted")
        deleted = db.delete_goal(conn, args.goal_id)
    if not deleted:
        raise db.GoalNotFoundError(args.goal_id)
    if args.json:
        print_json({"deleted": True, "id": args.goal_id})
    else:
        print(f"deleted goal {args.goal_id}")
    return 0
