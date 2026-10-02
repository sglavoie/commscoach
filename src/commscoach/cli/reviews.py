"""The ``commscoach review`` subcommands."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from .. import db
from .common import (
    add_date_bounds,
    confirm,
    first_prose_line,
    format_scope,
    non_negative_int,
    open_db,
    parse_date_bounds,
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

#: Each axis flag as ``(flag, argparse dest, scope kind it derives)``, in the
#: order the flags are declared so a conflict message names them predictably.
#: ``latest`` has no flag: it is what "no axis given" derives to.
_SCOPE_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("--person", "scope_person", "person"),
    ("--tag", "scope_tag", "tag"),
    ("--since", "since", "range"),
    ("--until", "until", "range"),
)

#: What an explicit ``--scope KIND`` needs when no axis flag was supplied.
_SCOPE_REQUIRES = {
    "person": "--person",
    "tag": "--tag",
    "range": "--since or --until",
}


@dataclass(frozen=True, slots=True)
class _Scope:
    """One resolved review scope: the kind plus only the axis it owns.

    The two DB entry points spell the axes differently (``get_review_context``
    takes ``person_handle``/``tag``/``since``/``until`` while ``create_review``
    takes ``scope_*``), so the resolved scope is kept in one neutral shape that
    each caller adapts, rather than as a kwargs dict aimed at one of them.
    """

    kind: str
    person_handle: str | None = None
    tag: str | None = None
    since: str | None = None
    until: str | None = None


def _add_scope_flags(parser: argparse.ArgumentParser) -> None:
    """Declare the shared review scope surface on ``parser``.

    ``--person`` / ``--tag`` / ``--since`` / ``--until`` each select one scope
    axis and ``--scope`` names the kind explicitly. ``--scope`` defaults to
    ``None`` rather than ``"latest"`` so an omitted flag stays distinguishable
    from an explicit ``--scope latest``; without that, every axis flag would
    look like it contradicted a ``--scope`` the user never typed.
    """
    parser.add_argument(
        "--scope",
        choices=db.SCOPE_KINDS,
        default=None,
        dest="scope_kind",
        help=(
            "Review scope kind. Derived from the axis flags when omitted;"
            " when given it must agree with them."
        ),
    )
    parser.add_argument(
        "--person",
        default=None,
        dest="scope_person",
        metavar="HANDLE",
        help="Scope to entries involving HANDLE (scope kind 'person').",
    )
    parser.add_argument(
        "--tag",
        default=None,
        dest="scope_tag",
        metavar="NAME",
        help="Scope to entries carrying NAME (scope kind 'tag').",
    )
    add_date_bounds(parser, noun="scoped entries")


def _resolve_scope(args: argparse.Namespace) -> _Scope:
    """Derive one scope from the axis flags, cross-checking an explicit ``--scope``.

    Argparse's mutually-exclusive groups cannot express "``--since`` and
    ``--until`` together, but neither alongside ``--person``", so the conflict
    is rejected here as a ``db.DBError`` (exit 1) naming every flag involved --
    the same way ``cmd_entry_edit`` rejects ``--clear-tags`` with ``--tag``.

    A reversed closed range is rejected here rather than left to the DB layer
    because ``cmd_review_add`` resolves the scope *before* ``read_body_arg``:
    catching it later would mean opening ``$EDITOR``, composing a whole review,
    and only then being told the flags were incoherent.
    """
    supplied = [
        (flag, kind)
        for flag, dest, kind in _SCOPE_FLAGS
        if getattr(args, dest) is not None
    ]
    flags = [flag for flag, _ in supplied]
    kinds = {kind for _, kind in supplied}
    if len(kinds) > 1:
        raise db.DBError(
            f"{flags[0]} cannot be combined with {', '.join(flags[1:])}"
        )
    derived = next(iter(kinds), "latest")

    requested = getattr(args, "scope_kind", None)
    if requested is not None and requested != derived:
        if not flags:
            raise db.DBError(
                f"--scope {requested} requires {_SCOPE_REQUIRES[requested]}"
            )
        raise db.DBError(
            f"--scope {requested} does not match "
            f"{', '.join(flags)} (scope '{derived}')"
        )

    since, until = parse_date_bounds(args) if derived == "range" else (None, None)
    return _Scope(
        kind=derived,
        person_handle=args.scope_person,
        tag=args.scope_tag,
        since=since,
        until=until,
    )


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
    _add_scope_flags(context_cmd)
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
    _add_scope_flags(add)
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
    # Filters over stored rows, not a scope to derive, so these are declared
    # separately from ``_add_scope_flags`` and combine with AND.
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

    show = review_sub.add_parser("show", help="Show one review by id.")
    show.add_argument("review_id", type=int, metavar="REVIEW_ID")
    show.set_defaults(func=cmd_review_show)

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


def _review_context_to_dict(context: db.ReviewContext) -> dict[str, Any]:
    return {
        "self": person_to_dict(context.self_person),
        # The scope block grew additively in session 6: ``kind``/``limit``/
        # ``entry_count`` keep their original meaning so no existing skill
        # parse breaks, and the axis keys are ``null`` for the kinds that do
        # not own them.
        "scope": {
            "kind": context.scope_kind,
            "limit": context.scope_limit,
            "entry_count": len(context.entries),
            "person": (
                person_to_dict(context.scope_person)
                if context.scope_person is not None
                else None
            ),
            "tag": context.scope_tag,
            "since": context.scope_since,
            "until": context.scope_until,
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
        f"scope {format_scope(review)}",
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
        f"scope:{format_scope(review)}",
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
    scope = _resolve_scope(args)
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
    print_json(_review_context_to_dict(context))
    return 0


def cmd_review_add(args: argparse.Namespace) -> int:
    scope = _resolve_scope(args)
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
            scope_kind=scope.kind,
            scope_limit=args.limit,
            scope_person_handle=scope.person_handle,
            scope_tag=scope.tag,
            scope_since=scope.since,
            scope_until=scope.until,
        )
    _emit_review(args, review, human_prefix="created")
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        reviews = db.list_reviews(
            conn,
            limit=args.limit,
            scope_kind=args.scope_kind,
            person_handle=args.person_handle,
            tag=args.tag,
        )
    _emit_reviews(args, reviews)
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        review = db.get_review(conn, args.review_id)
    if review is None:
        raise db.ReviewNotFoundError(args.review_id)
    _emit_review(args, review)
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
    _emit_review(args, review, human_prefix="updated")
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
