"""The ``commscoach entry`` subcommands."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import db
from .common import (
    confirm,
    non_negative_int,
    open_db,
    parse_date_bound,
    parse_when_or_raise,
    print_json,
    read_body_arg,
)
from .editor import compose_in_editor
from .people import ensure_people
from .serializers import entry_to_dict


ENTRY_PREAMBLE = "Write the entry body below. Lines beginning with #> are ignored."


def register(subparsers: argparse._SubParsersAction) -> None:
    entry_parser = subparsers.add_parser(
        "entry",
        help="Manage communication entries.",
    )
    entry_sub = entry_parser.add_subparsers(
        dest="entry_command", metavar="SUBCOMMAND"
    )

    def _show_entry_help(_args: argparse.Namespace) -> int:
        entry_parser.print_help()
        return 0

    entry_parser.set_defaults(func=_show_entry_help)

    add = entry_sub.add_parser("add", help="Create a new entry.")
    add.add_argument(
        "body",
        nargs="?",
        default=None,
        help=(
            "Entry body (literal text, '-' for stdin, '@PATH' for a UTF-8 file,"
            " or omit to open $EDITOR)."
        ),
    )
    add.add_argument(
        "--source",
        default=None,
        help="Channel or medium (e.g. slack, email, in-person).",
    )
    add.add_argument(
        "--occurred-at",
        dest="occurred_at",
        default=None,
        metavar="TS",
        help="When it happened (ISO-8601 or a supported relative timestamp).",
    )
    add.add_argument(
        "--tag",
        action="append",
        default=[],
        dest="tags",
        metavar="TAG",
        help="Attach a tag (may be repeated; values are stripped + lowercased).",
    )
    add.add_argument(
        "--person",
        action="append",
        default=[],
        dest="people",
        metavar="HANDLE",
        help="Link an existing person by handle (may be repeated).",
    )
    add.set_defaults(func=cmd_entry_add)

    get = entry_sub.add_parser("get", help="Show one entry by id.")
    get.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    get.set_defaults(func=cmd_entry_get)

    edit = entry_sub.add_parser("edit", help="Update an existing entry.")
    edit.add_argument("entry_id", type=int, metavar="ENTRY_ID")
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
    edit.add_argument(
        "--source",
        default=None,
        help="Replace the source. Pass an empty string to clear it.",
    )
    edit.add_argument(
        "--occurred-at",
        dest="occurred_at",
        default=None,
        metavar="TS",
        help="Replace occurred_at (ISO-8601/relative); empty clears it.",
    )
    edit.add_argument(
        "--tag",
        action="append",
        default=None,
        dest="tags",
        metavar="TAG",
        help="Replace tag list with these values (may be repeated).",
    )
    edit.add_argument(
        "--clear-tags",
        action="store_true",
        dest="clear_tags",
        help="Remove every tag from this entry.",
    )
    edit.add_argument(
        "--person",
        action="append",
        default=None,
        dest="people",
        metavar="HANDLE",
        help="Replace linked-people list with these handles (may be repeated).",
    )
    edit.add_argument(
        "--clear-people",
        action="store_true",
        dest="clear_people",
        help="Remove every linked person from this entry.",
    )
    edit.set_defaults(func=cmd_entry_edit)

    lst = entry_sub.add_parser("list", help="List entries newest-first.")
    lst.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Cap the number of entries returned.",
    )
    _add_date_bounds(lst)
    lst.set_defaults(func=cmd_entry_list)

    srch = entry_sub.add_parser(
        "search", help="Search entries by body substring, tag, and/or person."
    )
    srch.add_argument(
        "--query",
        default=None,
        metavar="TEXT",
        help="Body search query (FTS5 when available).",
    )
    srch.add_argument(
        "--exact",
        action="store_true",
        help="Force case-insensitive substring matching instead of token search.",
    )
    srch.add_argument(
        "--tag", default=None, metavar="TAG", help="Filter by a single normalized tag."
    )
    srch.add_argument(
        "--person",
        default=None,
        metavar="HANDLE",
        help="Filter by linked person handle (case-insensitive).",
    )
    _add_date_bounds(srch)
    srch.add_argument(
        "--no-feedback",
        action="store_true",
        help="Show entries without feedback (the coaching backlog).",
    )
    srch.add_argument("--limit", type=non_negative_int, default=None, metavar="N")
    srch.set_defaults(func=cmd_entry_search)

    rm = entry_sub.add_parser(
        "rm", help="Delete an entry and its linked tags, people, and feedback."
    )
    rm.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    rm.set_defaults(func=cmd_entry_rm)


def _add_date_bounds(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--since",
        default=None,
        metavar="DATE",
        help="Include entries on or after DATE (ISO-8601 or relative).",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="DATE",
        help="Include entries through DATE (ISO-8601 or relative).",
    )


def _parse_date_bounds(args: argparse.Namespace) -> tuple[str | None, str | None]:
    since = (
        parse_date_bound(args.since, flag="--since")
        if args.since is not None
        else None
    )
    until = (
        parse_date_bound(args.until, flag="--until")
        if args.until is not None
        else None
    )
    return since, until


def _format_entry_human(entry: db.Entry) -> str:
    header_parts = [f"[{entry.id}]", f"created {entry.created_at}"]
    if entry.occurred_at:
        header_parts.append(f"occurred {entry.occurred_at}")
    if entry.source:
        header_parts.append(f"source: {entry.source}")
    lines = [" ".join(header_parts)]
    if entry.people:
        lines.append("people: " + ", ".join(p.handle for p in entry.people))
    if entry.tags:
        lines.append("tags: " + ", ".join(entry.tags))
    lines.append("---")
    lines.append(entry.body)
    return "\n".join(lines)


def _format_entry_short(entry: db.Entry) -> str:
    first_line = entry.body.strip().split("\n", 1)[0]
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    parts = [f"[{entry.id}]", first_line]
    if entry.tags:
        parts.append("#" + ",".join(entry.tags))
    if entry.people:
        parts.append("@" + ",".join(p.handle for p in entry.people))
    return " ".join(parts)


def _emit_entry(
    args: argparse.Namespace,
    entry: db.Entry,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(entry_to_dict(entry))
        return
    if human_prefix:
        print(f"{human_prefix} entry {entry.id}")
    print(_format_entry_human(entry))


def _emit_entries(args: argparse.Namespace, entries: tuple[db.Entry, ...]) -> None:
    if args.json:
        print_json([entry_to_dict(e) for e in entries])
        return
    if not entries:
        print("(no entries)")
        return
    for entry in entries:
        print(_format_entry_short(entry))


def cmd_entry_add(args: argparse.Namespace) -> int:
    body = read_body_arg(
        args.body,
        what="body",
        template="",
        preamble=ENTRY_PREAMBLE,
        isatty=sys.stdin.isatty,
        compose=compose_in_editor,
    )
    occurred_at = (
        parse_when_or_raise(args.occurred_at)
        if args.occurred_at is not None
        else None
    )
    with open_db(args) as conn:
        ensure_people(conn, args.people, assume_yes=args.yes)
        entry = db.create_entry(
            conn,
            body,
            source=args.source,
            occurred_at=occurred_at,
            tags=args.tags,
            people=args.people,
        )
    _emit_entry(args, entry, human_prefix="created")
    return 0


def cmd_entry_get(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        entry = db.get_entry(conn, args.entry_id)
    if entry is None:
        raise db.EntryNotFoundError(args.entry_id)
    _emit_entry(args, entry)
    return 0


def cmd_entry_edit(args: argparse.Namespace) -> int:
    if args.clear_tags and args.tags is not None:
        raise db.DBError("--clear-tags cannot be combined with --tag")
    if args.clear_people and args.people is not None:
        raise db.DBError("--clear-people cannot be combined with --person")

    update_kwargs: dict[str, Any] = {}
    if args.body not in (None, ""):
        update_kwargs["body"] = read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=ENTRY_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
    if args.source is not None:
        update_kwargs["source"] = args.source if args.source else None
    if args.occurred_at is not None:
        update_kwargs["occurred_at"] = (
            parse_when_or_raise(args.occurred_at) if args.occurred_at else None
        )
    if args.clear_tags:
        update_kwargs["tags"] = ()
    elif args.tags is not None:
        update_kwargs["tags"] = tuple(args.tags)
    if args.clear_people:
        update_kwargs["people"] = ()
    elif args.people is not None:
        update_kwargs["people"] = tuple(args.people)

    if not update_kwargs and args.body != "":
        raise db.DBError(
            "entry edit requires at least one of "
            "--body/--source/--occurred-at/--tag/--clear-tags/--person/--clear-people"
        )

    with open_db(args) as conn:
        if args.body == "":
            current = db.get_entry(conn, args.entry_id)
            if current is None:
                raise db.EntryNotFoundError(args.entry_id)
            update_kwargs["body"] = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=ENTRY_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        if args.people is not None:
            if db.get_entry(conn, args.entry_id) is None:
                raise db.EntryNotFoundError(args.entry_id)
            ensure_people(conn, args.people, assume_yes=args.yes)
        entry = db.update_entry(conn, args.entry_id, **update_kwargs)
    _emit_entry(args, entry, human_prefix="updated")
    return 0


def cmd_entry_list(args: argparse.Namespace) -> int:
    since, until = _parse_date_bounds(args)
    with open_db(args) as conn:
        entries = db.list_entries(
            conn,
            since=since,
            until=until,
            limit=args.limit,
        )
    _emit_entries(args, entries)
    return 0


def cmd_entry_search(args: argparse.Namespace) -> int:
    since, until = _parse_date_bounds(args)
    with open_db(args) as conn:
        entries = db.search_entries(
            conn,
            query=args.query,
            tag=args.tag,
            person_handle=args.person,
            since=since,
            until=until,
            without_feedback=args.no_feedback,
            limit=args.limit,
            mode="like" if args.exact else "auto",
        )
    _emit_entries(args, entries)
    return 0


def cmd_entry_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        if not confirm(f"Delete entry {args.entry_id}?", assume_yes=args.yes):
            raise db.DBError("aborted")
        deleted = db.delete_entry(conn, args.entry_id)
    if not deleted:
        raise db.EntryNotFoundError(args.entry_id)
    if args.json:
        print_json({"deleted": True, "id": args.entry_id})
    else:
        print(f"deleted entry {args.entry_id}")
    return 0
