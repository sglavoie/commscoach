"""The ``commscoach review`` subcommands."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .. import db
from ..db.common import date_bound_timestamp, handle_key
from ..db.tags import normalize_tag
from .common import (
    EDIT_IN_EDITOR,
    add_date_bounds,
    add_oldest_first,
    confirm,
    covered_entry_id,
    edit_verb,
    handle_arg,
    non_negative_int,
    open_db,
    parse_date_bounds,
    plural,
    print_err,
    print_json,
    read_body_arg,
    read_replacement_body,
    row_id,
)
from .editor import compose_in_editor
from .reviews_format import emit_review, emit_reviews, review_context_to_dict
from .reviews_scope import Scope, add_scope_flags, resolve_scope
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
    review_sub = review_parser.add_subparsers(dest="review_command", metavar="SUBCOMMAND")

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
        type=covered_entry_id,
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
        type=handle_arg,
        metavar="HANDLE",
        help="Only list reviews scoped to HANDLE.",
    )
    lst.add_argument(
        "--tag",
        default=None,
        metavar="NAME",
        help="Only list reviews scoped to tag NAME.",
    )
    add_date_bounds(lst, noun="reviews written")
    add_oldest_first(lst, noun="reviews")
    lst.set_defaults(func=cmd_review_list)

    search = review_sub.add_parser("search", help="Search stored review bodies.")
    search.add_argument(
        "--query",
        required=True,
        metavar="TEXT",
        help="Literal substring (ignoring case and accents) to find in review bodies.",
    )
    search.add_argument(
        "--limit",
        type=non_negative_int,
        default=20,
        metavar="N",
        help="Maximum results (default: 20); text output reports omitted matches.",
    )
    search.add_argument(
        "--scope",
        choices=db.SCOPE_KINDS,
        default=None,
        dest="scope_kind",
        help="Only search reviews with this scope kind.",
    )
    search.add_argument(
        "--person",
        default=None,
        dest="person_handle",
        type=handle_arg,
        metavar="HANDLE",
        help="Only search reviews scoped to HANDLE, including deleted people.",
    )
    search.add_argument(
        "--tag",
        default=None,
        metavar="NAME",
        help="Only search reviews scoped to tag NAME.",
    )
    add_date_bounds(search, noun="reviews written")
    add_oldest_first(search, noun="matching reviews")
    search.set_defaults(func=cmd_review_list)

    show = review_sub.add_parser("show", help="Show one review by id.")
    show.add_argument("review_id", type=row_id, metavar="REVIEW_ID")
    show.add_argument(
        "--with-goals",
        action="store_true",
        help="Include linked goals of every status, with details and outcomes.",
    )
    show.set_defaults(func=cmd_review_show)
    show.add_argument(
        "--with-entries",
        action="store_true",
        help="Show surviving linked entries in full (already included in JSON).",
    )

    edit = review_sub.add_parser("edit", help="Rewrite a stored review body.")
    edit.add_argument("review_id", type=row_id, metavar="REVIEW_ID")
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
    edit.set_defaults(func=cmd_review_edit)

    rm = review_sub.add_parser("rm", help="Delete one review.")
    rm.add_argument("review_id", type=row_id, metavar="REVIEW_ID")
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


def _entry_in_scope(entry: db.Entry, scope: Scope) -> bool:
    """Report whether ``entry`` is one ``review context`` would select for ``scope``."""
    if scope.kind == "person":
        key = handle_key(scope.person_handle or "")
        return any(handle_key(person.handle) == key for person in entry.people)
    if scope.kind == "tag":
        return normalize_tag(scope.tag or "") in entry.tags
    if scope.kind == "range":
        # Stored times are canonical UTC, so they order as strings, as in SQL.
        when = entry.occurred_at or entry.created_at
        if scope.since is not None and when < date_bound_timestamp(scope.since, name="since"):
            return False
        return scope.until is None or when <= date_bound_timestamp(
            scope.until, name="until", upper=True
        )
    return True


def cmd_review_add(args: argparse.Namespace) -> int:
    scope = resolve_scope(args)
    with open_db(args) as conn:
        if scope.person_handle is not None:
            if db.get_person_by_handle(conn, scope.person_handle) is None:
                raise db.PersonNotFoundError(scope.person_handle)
        if scope.tag is not None:
            # As review context does: a scope no entry can match is a typo.
            db.get_tag(conn, scope.tag, hint="'commscoach tag list' shows existing tags")
        outside: list[int] = []
        for entry_id in dict.fromkeys(args.entry_ids):
            entry = db.get_entry(conn, entry_id)
            if entry is None:
                raise db.EntryNotFoundError(entry_id)
            if not _entry_in_scope(entry, scope):
                outside.append(entry_id)
        if outside:
            # A warning, not an error: an entry may have been edited since the
            # context was generated, and the review is still worth keeping.
            print_err(
                f"warning: {plural(len(outside), 'covered entry', 'covered entries')}"
                f" ({', '.join(map(str, outside))})"
                f" {'falls' if len(outside) == 1 else 'fall'} outside the {scope.kind} scope"
            )
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


def _require_known_review_person(conn: sqlite3.Connection, handle: str) -> None:
    """Reject a handle that names neither a person nor any stored review.

    A deleted person's handle stays valid (their scoped reviews keep it), but a
    typo should not look like "no reviews about this person".
    """
    if db.get_person_by_handle(conn, handle) is not None:
        return
    if db.count_reviews(conn, person_handle=handle):
        return
    raise db.PersonNotFoundError(handle)


def cmd_review_list(args: argparse.Namespace) -> int:
    query = getattr(args, "query", None)
    since, until = parse_date_bounds(args)
    with open_db(args) as conn:
        if args.person_handle is not None:
            _require_known_review_person(conn, args.person_handle)
        reviews = db.list_reviews(
            conn,
            limit=args.limit,
            scope_kind=args.scope_kind,
            person_handle=args.person_handle,
            tag=args.tag,
            query=query,
            since=since,
            until=until,
            oldest_first=args.oldest_first,
        )
        total = (
            db.count_reviews(
                conn,
                scope_kind=args.scope_kind,
                person_handle=args.person_handle,
                tag=args.tag,
                query=query,
                since=since,
                until=until,
            )
            if args.limit is not None
            else len(reviews)
        )
    emit_reviews(args, reviews, query=query)
    if total > len(reviews):
        print(f"Showing {len(reviews)} of {total} matching reviews.", file=sys.stderr)
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        review = db.get_review(conn, args.review_id)
        if review is None:
            raise db.ReviewNotFoundError(args.review_id)
        goals = db.list_goals(conn, status=None, review_id=review.id) if args.with_goals else ()
    if args.with_goals and args.json:
        print_json(
            {
                **review_to_dict(review),
                "goals": [goal_to_dict(goal) for goal in goals],
            }
        )
        return 0
    emit_review(args, review)
    if args.with_entries and not args.json:
        from .entries_format import format_entry_human

        print("\nLinked entries (current text):")
        print(
            "\n\n".join(format_entry_human(entry) for entry in review.entries)
            or "(no linked entries remain)"
        )
    if args.with_goals:
        from .goals import _format_goal_human

        print("\nLinked goals:")
        print("\n\n".join(_format_goal_human(goal) for goal in goals) or "(no goals)")
    return 0


def cmd_review_edit(args: argparse.Namespace) -> int:
    body = read_replacement_body(args.body, command="review edit")
    with open_db(args) as conn:
        current = db.get_review(conn, args.review_id)
        if current is None:
            raise db.ReviewNotFoundError(args.review_id)
        if body is None:
            body = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=REVIEW_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        review = db.update_review(conn, args.review_id, body=body)
    emit_review(args, review, human_prefix=edit_verb(current.updated_at, review.updated_at))
    return 0


def cmd_review_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_review(conn, args.review_id) is None:
            raise db.ReviewNotFoundError(args.review_id)
        if not confirm(f"Delete review {args.review_id}?", assume_yes=args.yes):
            raise db.DBError("aborted; pass --yes to confirm without a prompt")
        deleted = db.delete_review(conn, args.review_id)
    if not deleted:
        raise db.ReviewNotFoundError(args.review_id)
    if args.json:
        print_json({"deleted": True, "id": args.review_id})
    else:
        print(f"deleted review {args.review_id}")
    return 0
