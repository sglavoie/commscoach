"""The ``commscoach review`` subcommands."""

from __future__ import annotations

import argparse
import sys

from .. import db
from .common import (
    confirm,
    non_negative_int,
    open_db,
    print_json,
    read_body_arg,
)
from .editor import compose_in_editor
from .reviews_format import emit_review, emit_reviews, review_context_to_dict
from .reviews_scope import add_scope_flags, resolve_scope
from .serializers import (
    goal_to_dict,
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
        help="Number of most recent entries in the scope to include (default: 20).",
    )
    context_cmd.add_argument(
        "--prior-review-limit",
        type=non_negative_int,
        default=3,
        metavar="N",
        help="Number of prior review syntheses to include (default: 3).",
    )
    context_cmd.add_argument(
        "--goal-limit",
        type=non_negative_int,
        default=10,
        metavar="N",
        help="Number of active goals to include (default: 10).",
    )
    add_scope_flags(context_cmd)
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
    add_scope_flags(add)
    add.add_argument(
        "--limit",
        type=non_negative_int,
        default=20,
        metavar="N",
        help=(
            "Entry limit of the 'review context' call this review was written"
            " from, stored for provenance (default: 20)."
        ),
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
    # Filters over stored rows, not a scope to derive, so these are declared
    # separately from ``add_scope_flags`` and combine with AND.
    lst.add_argument(
        "--scope",
        choices=db.SCOPE_KINDS,
        default=None,
        dest="scope_kind",
        help="Only list reviews with this scope kind.",
    )
    lst.add_argument(
        "--person",
        default=None,
        dest="person_handle",
        metavar="HANDLE",
        help="Only list reviews scoped to HANDLE.",
    )
    lst.add_argument(
        "--tag",
        default=None,
        metavar="NAME",
        help="Only list reviews scoped to tag NAME.",
    )
    lst.set_defaults(func=cmd_review_list)

    search = review_sub.add_parser("search", help="Search stored review bodies.")
    search.add_argument(
        "--query", required=True, metavar="TEXT",
        help="Literal case-insensitive substring to find in review bodies.",
    )
    search.add_argument(
        "--limit", type=non_negative_int, default=20, metavar="N",
        help="Maximum results (default: 20); text output reports omitted matches.",
    )
    search.add_argument(
        "--scope", choices=db.SCOPE_KINDS, default=None, dest="scope_kind",
        help="Only search reviews with this scope kind.",
    )
    search.add_argument(
        "--person", default=None, dest="person_handle", metavar="HANDLE",
        help="Only search reviews scoped to HANDLE, including deleted people.",
    )
    search.add_argument(
        "--tag", default=None, metavar="NAME",
        help="Only search reviews scoped to tag NAME.",
    )
    search.set_defaults(func=cmd_review_list)

    show = review_sub.add_parser("show", help="Show one review by id.")
    show.add_argument("review_id", type=int, metavar="REVIEW_ID")
    show.add_argument(
        "--with-goals", action="store_true",
        help="Include linked goals of every status, with details and outcomes.",
    )
    show.set_defaults(func=cmd_review_show)
    show.add_argument(
        "--with-entries", action="store_true",
        help="Show surviving linked entries in full (already included in JSON).",
    )

    edit = review_sub.add_parser("edit", help="Rewrite a stored review body.")
    edit.add_argument("review_id", type=int, metavar="REVIEW_ID")
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
    edit.set_defaults(func=cmd_review_edit)

    rm = review_sub.add_parser("rm", help="Delete one review.")
    rm.add_argument("review_id", type=int, metavar="REVIEW_ID")
    rm.set_defaults(func=cmd_review_rm)



def cmd_review_context(args: argparse.Namespace) -> int:
    scope = resolve_scope(args)
    with open_db(args) as conn:
        context = db.get_review_context(
            conn,
            limit=args.limit,
            prior_review_limit=args.prior_review_limit,
            goal_limit=args.goal_limit,
            scope_kind=scope.kind,
            person_handle=scope.person_handle,
            tag=scope.tag,
            since=scope.since,
            until=scope.until,
        )
    print_json(review_context_to_dict(context))
    return 0


def cmd_review_add(args: argparse.Namespace) -> int:
    scope = resolve_scope(args)
    with open_db(args) as conn:
        if scope.person_handle is not None:
            if db.get_person_by_handle(conn, scope.person_handle) is None:
                raise db.PersonNotFoundError(scope.person_handle)
        for entry_id in dict.fromkeys(args.entry_ids):
            if db.get_entry(conn, entry_id) is None:
                raise db.EntryNotFoundError(entry_id)
        body = read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=REVIEW_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        review = db.create_review(
            conn,
            body,
            args.entry_ids,
            scope_kind=scope.kind,
            scope_limit=args.limit,
            scope_person_handle=scope.person_handle,
            scope_tag=scope.tag,
            scope_since=scope.since,
            scope_until=scope.until,
        )
    emit_review(args, review, human_prefix="created")
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    query = getattr(args, "query", None)
    with open_db(args) as conn:
        reviews = db.list_reviews(
            conn,
            limit=args.limit,
            scope_kind=args.scope_kind,
            person_handle=args.person_handle,
            tag=args.tag,
            query=query,
        )
        total = (
            db.count_reviews(
                conn, scope_kind=args.scope_kind, person_handle=args.person_handle,
                tag=args.tag, query=query,
            )
            if not args.json and args.limit is not None
            else len(reviews)
        )
    emit_reviews(args, reviews, query=query)
    if not args.json and total > len(reviews):
        print(f"Showing {len(reviews)} of {total} matching reviews.")
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        review = db.get_review(conn, args.review_id)
        if review is None:
            raise db.ReviewNotFoundError(args.review_id)
        goals = (
            db.list_goals(conn, status=None, review_id=review.id)
            if args.with_goals else ()
        )
    if args.with_goals and args.json:
        print_json({
            **review_to_dict(review),
            "goals": [goal_to_dict(goal) for goal in goals],
        })
        return 0
    emit_review(args, review)
    if args.with_entries and not args.json:
        from .entries_format import format_entry_human

        print("\nLinked entries (current text):")
        print("\n\n".join(format_entry_human(entry) for entry in review.entries)
              or "(no linked entries remain)")
    if args.with_goals:
        from .goals import _format_goal_human

        print("\nLinked goals:")
        print("\n\n".join(_format_goal_human(goal) for goal in goals) or "(no goals)")
    return 0


def cmd_review_edit(args: argparse.Namespace) -> int:
    if args.body is None:
        raise db.DBError("review edit requires --body")
    body = (
        read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=REVIEW_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        if args.body != ""
        else None
    )
    with open_db(args) as conn:
        if body is None:
            current = db.get_review(conn, args.review_id)
            if current is None:
                raise db.ReviewNotFoundError(args.review_id)
            body = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=REVIEW_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        review = db.update_review(conn, args.review_id, body=body)
    emit_review(args, review, human_prefix="updated")
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
