"""The ``commscoach review`` subcommands."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import db
from .common import (
    confirm,
    first_prose_line,
    non_negative_int,
    open_db,
    print_json,
    read_body_arg,
    truncate,
)
from .editor import compose_in_editor
from .serializers import (
    entry_to_dict,
    feedback_to_dict,
    goal_to_dict,
    person_to_dict,
    review_to_dict,
)


REVIEW_PREAMBLE = "Write the review body below. Lines beginning with #> are ignored."


def register(subparsers: argparse._SubParsersAction) -> None:
    review_parser = subparsers.add_parser(
        "review",
        help="Manage stored review syntheses.",
    )
    review_sub = review_parser.add_subparsers(
        dest="review_command", metavar="SUBCOMMAND"
    )

    def _show_review_help(_args: argparse.Namespace) -> int:
        review_parser.print_help()
        return 0

    review_parser.set_defaults(func=_show_review_help)

    context_cmd = review_sub.add_parser(
        "context", help="Emit JSON context for a stored review synthesis."
    )
    context_cmd.add_argument(
        "--limit",
        type=non_negative_int,
        default=20,
        metavar="N",
        help="Number of latest entries to include.",
    )
    context_cmd.add_argument(
        "--prior-review-limit",
        type=non_negative_int,
        default=3,
        metavar="N",
        help="Number of prior review syntheses to include.",
    )
    context_cmd.add_argument(
        "--goal-limit",
        type=non_negative_int,
        default=10,
        metavar="N",
        help="Number of active goals to include.",
    )
    context_cmd.set_defaults(func=cmd_review_context)

    add = review_sub.add_parser("add", help="Store a review synthesis.")
    add.add_argument(
        "body",
        nargs="?",
        default=None,
        help=(
            "Review body (literal text, '-' for stdin, '@PATH' for a UTF-8 file,"
            " or omit to open $EDITOR)."
        ),
    )
    add.add_argument(
        "--entry",
        action="extend",
        nargs="+",
        type=int,
        required=True,
        default=[],
        dest="entry_ids",
        metavar="ID",
        help="Entry id covered by this review (may list multiple ids).",
    )
    add.add_argument(
        "--scope",
        choices=("latest",),
        default="latest",
        dest="scope_kind",
        help="Review scope kind.",
    )
    add.add_argument(
        "--limit",
        type=non_negative_int,
        default=20,
        metavar="N",
        help="Stored source context limit.",
    )
    add.set_defaults(func=cmd_review_add)

    lst = review_sub.add_parser("list", help="List reviews newest-first.")
    lst.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Cap the number of reviews returned.",
    )
    lst.set_defaults(func=cmd_review_list)

    show = review_sub.add_parser("show", help="Show one review by id.")
    show.add_argument("review_id", type=int, metavar="REVIEW_ID")
    show.set_defaults(func=cmd_review_show)

    rm = review_sub.add_parser("rm", help="Delete one review.")
    rm.add_argument("review_id", type=int, metavar="REVIEW_ID")
    rm.set_defaults(func=cmd_review_rm)


def _review_context_to_dict(context: db.ReviewContext) -> dict[str, Any]:
    return {
        "self": person_to_dict(context.self_person),
        "scope": {
            "kind": context.scope_kind,
            "limit": context.scope_limit,
            "entry_count": len(context.entries),
        },
        "entries": [
            {
                "entry": entry_to_dict(item.entry),
                "feedback": [feedback_to_dict(feedback) for feedback in item.feedback],
            }
            for item in context.entries
        ],
        "prior_reviews": [
            review_to_dict(review) for review in context.prior_reviews
        ],
        "goals": [goal_to_dict(goal) for goal in context.goals],
    }


def _format_review_human(review: db.Review) -> str:
    header_parts = [
        f"[{review.id}]",
        f"created {review.created_at}",
        f"scope {review.scope_kind}",
    ]
    if review.scope_limit is not None:
        header_parts.append(f"context limit {review.scope_limit}")
    if review.updated_at != review.created_at:
        header_parts.append(f"updated {review.updated_at}")
    lines = [" ".join(header_parts)]
    if review.entries:
        entry_ids = ", ".join(str(entry.id) for entry in review.entries)
        lines.append(f"entries: {entry_ids}")
    lines.append("---")
    lines.append(review.body)
    return "\n".join(lines)


def _format_review_short(review: db.Review) -> str:
    entry_ids = ",".join(str(entry.id) for entry in review.entries)
    parts = [
        f"[{review.id}]",
        review.created_at,
        f"scope:{review.scope_kind}",
    ]
    if review.scope_limit is not None:
        parts.append(f"context-limit:{review.scope_limit}")
    if entry_ids:
        parts.append(f"entries:{entry_ids}")
    parts.append(truncate(first_prose_line(review.body)))
    return " ".join(parts)


def _emit_review(
    args: argparse.Namespace,
    review: db.Review,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(review_to_dict(review))
        return
    if human_prefix:
        print(f"{human_prefix} review {review.id}")
    print(_format_review_human(review))


def _emit_reviews(args: argparse.Namespace, reviews: tuple[db.Review, ...]) -> None:
    if args.json:
        print_json([review_to_dict(review) for review in reviews])
        return
    if not reviews:
        print("(no reviews)")
        return
    for review in reviews:
        print(_format_review_short(review))


def cmd_review_context(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        context = db.get_review_context(
            conn,
            limit=args.limit,
            prior_review_limit=args.prior_review_limit,
            goal_limit=args.goal_limit,
        )
    print_json(_review_context_to_dict(context))
    return 0


def cmd_review_add(args: argparse.Namespace) -> int:
    body = read_body_arg(
        args.body,
        what="body",
        template="",
        preamble=REVIEW_PREAMBLE,
        isatty=sys.stdin.isatty,
        compose=compose_in_editor,
    )
    with open_db(args) as conn:
        review = db.create_review(
            conn,
            body,
            args.entry_ids,
            scope_kind=args.scope_kind,
            scope_limit=args.limit,
        )
    _emit_review(args, review, human_prefix="created")
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        reviews = db.list_reviews(conn, limit=args.limit)
    _emit_reviews(args, reviews)
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        review = db.get_review(conn, args.review_id)
    if review is None:
        raise db.ReviewNotFoundError(args.review_id)
    _emit_review(args, review)
    return 0


def cmd_review_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_review(conn, args.review_id) is None:
            raise db.ReviewNotFoundError(args.review_id)
        if not confirm(f"Delete review {args.review_id}?", assume_yes=args.yes):
            raise db.DBError("aborted")
        deleted = db.delete_review(conn, args.review_id)
    if not deleted:
        raise db.ReviewNotFoundError(args.review_id)
    if args.json:
        print_json({"deleted": True, "id": args.review_id})
    else:
        print(f"deleted review {args.review_id}")
    return 0
