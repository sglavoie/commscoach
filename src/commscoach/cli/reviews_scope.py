"""Scope flags and resolution shared by the ``review`` subcommands."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .. import db
from ..db.tags import normalize_tag
from .common import (
    add_date_bounds,
    handle_arg,
    parse_date_bounds,
)

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
class Scope:
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


def add_scope_flags(parser: argparse.ArgumentParser) -> None:
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
        type=handle_arg,
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


def resolve_scope(args: argparse.Namespace) -> Scope:
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
        (flag, kind) for flag, dest, kind in _SCOPE_FLAGS if getattr(args, dest) is not None
    ]
    flags = [flag for flag, _ in supplied]
    kinds = {kind for _, kind in supplied}
    if len(kinds) > 1:
        raise db.DBError(f"{flags[0]} cannot be combined with {', '.join(flags[1:])}")
    derived = next(iter(kinds), "latest")

    requested = getattr(args, "scope_kind", None)
    if requested is not None and requested != derived:
        if not flags:
            raise db.DBError(f"--scope {requested} requires {_SCOPE_REQUIRES[requested]}")
        raise db.DBError(
            f"--scope {requested} does not match {', '.join(flags)} (scope '{derived}')"
        )

    since, until = parse_date_bounds(args) if derived == "range" else (None, None)
    if derived == "tag" and not normalize_tag(args.scope_tag):
        raise db.DBError("--tag requires a non-empty tag")
    return Scope(
        kind=derived,
        person_handle=args.scope_person,
        tag=args.scope_tag,
        since=since,
        until=until,
    )
