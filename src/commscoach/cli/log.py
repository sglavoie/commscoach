"""``commscoach log``: capture a conversation in one line.

``commscoach log "Pushed back on @jdoe's deadline in #standup"`` links @jdoe
and tags the entry #standup straight from the text, which is kept verbatim.
It is ``entry add`` without the flags: the same storage, the same new-person
confirmation, and the same JSON output with ``--json``.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys

from .. import db
from ..db.common import handle_key, write_transaction
from ..db.tags import normalize_tag_list
from . import nudges, suggest
from .common import (
    command_prefix,
    handle_arg,
    open_db,
    parse_when_or_raise,
    read_body_arg,
)
from .editor import compose_in_editor
from .entries import ENTRY_PREAMBLE, _clean_source
from .entries_format import emit_entry
from .people import confirm_new_people, create_people

# A mention starts a word: "a@b.com" is an address, not a handle. Trailing
# punctuation ("@jdoe's", "@jdoe,", "@jdoe.") is not part of it.
_MENTION_RE = re.compile(r"(?<![\w@/:.])@([\w][\w.\-]*)")
# A tag needs a letter, so "issue #42" stays prose while "#1on1" is a tag.
_TAG_RE = re.compile(r"(?<![\w#&/])#([\w][\w\-]*)")


def _strip_trailing(token: str) -> str:
    return token.rstrip(".-")


def mentions(text: str) -> list[str]:
    """Handles mentioned as ``@handle``, in order, without duplicates."""
    seen: dict[str, str] = {}
    for match in _MENTION_RE.finditer(text):
        handle = _strip_trailing(match.group(1))
        if handle.endswith("'s"):
            handle = handle[:-2]
        if handle:
            seen.setdefault(handle_key(handle), handle)
    return list(seen.values())


def hashtags(text: str) -> list[str]:
    """Tags written as ``#tag`` that contain at least one letter."""
    found = []
    for match in _TAG_RE.finditer(text):
        name = _strip_trailing(match.group(1))
        if any(char.isalpha() for char in name):
            found.append(name)
    return normalize_tag_list(found)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "log",
        help="Quickly log a conversation; @handles and #tags in the text are linked.",
        description=(
            "Log a conversation in one line. @handle mentions link people and #tags tag "
            "the entry; the text is stored as written. Quote the text in shells where # "
            "starts a comment (bash)."
        ),
    )
    parser.add_argument(
        "words",
        nargs="*",
        metavar="TEXT",
        help=(
            "What happened, as literal text (joined with spaces; '-' reads stdin;"
            " omit to open $EDITOR)."
        ),
    )
    parser.add_argument("--source", "-s", default=None, help="Channel (slack, email, in-person…).")
    parser.add_argument(
        "--when",
        "-w",
        "--occurred-at",
        dest="occurred_at",
        default=None,
        metavar="TS",
        help="When it happened (e.g. yesterday, '2h ago', 2026-11-02).",
    )
    parser.add_argument(
        "--person",
        "-p",
        action="extend",
        default=None,
        dest="people",
        type=lambda value: [handle_arg(v) for v in value.split(",") if v.strip()],
        metavar="HANDLE",
        help="Also link a person not mentioned in the text (repeatable).",
    )
    parser.add_argument(
        "--tag",
        "-t",
        action="append",
        default=None,
        dest="tags",
        metavar="TAG",
        help="Also add a tag not written in the text (repeatable).",
    )
    parser.set_defaults(func=cmd_log)


def _refuse_likely_typos(conn, handles: list[str], args: argparse.Namespace) -> None:  # type: ignore[no-untyped-def]
    """Stop before creating ``@jdo`` when ``@jdoe`` exists, unless --yes insists."""
    if args.yes:
        return
    for handle in handles:
        if db.get_person_by_handle(conn, handle) is not None:
            continue
        hint = suggest.not_found_hint(db.PersonNotFoundError(handle), conn)
        if hint:
            raise db.DBError(
                f"person @{handle} not found; {hint} "
                f"(pass --yes to create @{handle}, or {shlex.join([*command_prefix(args), 'person', 'add', handle])})"
            )


def cmd_log(args: argparse.Namespace) -> int:
    words = args.words
    # The words are literal text: "@jdoe ..." is a mention, never an @PATH file.
    literal = " ".join(words) if words and words != ["-"] else None
    value = None if literal is not None or not words else "-"
    occurred_at = parse_when_or_raise(args.occurred_at) if args.occurred_at is not None else None
    with open_db(args) as conn:
        body = (
            literal
            if literal is not None
            else read_body_arg(
                value,
                what="text",
                template="",
                preamble=ENTRY_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        )
        people = mentions(body) + list(args.people or [])
        tags = hashtags(body) + list(args.tags or [])
        _refuse_likely_typos(conn, people, args)
        new_people = confirm_new_people(
            conn, people, assume_yes=args.yes, command=command_prefix(args)
        )
        with write_transaction(conn):
            create_people(conn, new_people)
            entry = db.create_entry(
                conn,
                body,
                source=_clean_source(args.source),
                occurred_at=occurred_at,
                tags=tags,
                people=people,
            )
        reaction = nudges.entry_logged(conn, entry) if nudges.enabled(args) else None
    if reaction is not None:
        print("\n".join(reaction))
    else:
        emit_entry(args, entry, human_prefix="created")
    return 0
