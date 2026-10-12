"""The ``commscoach entry`` subcommands."""

from __future__ import annotations

import argparse
import shlex
import sys
from typing import Any

from .. import db
from ..db.tags import normalize_tag_list
from .common import (
    add_date_bounds,
    confirm,
    non_negative_int,
    open_db,
    parse_date_bounds,
    parse_when_or_raise,
    print_json,
    read_body_arg,
    search_excerpt,
)
from .editor import compose_in_editor
from .entries_format import emit_entries, emit_entry
from .feedback import _format_feedback_human
from .people import ensure_people
from .serializers import entry_to_dict, feedback_to_dict


ENTRY_PREAMBLE = (
    "Write the entry body below. Lines beginning with #> are ignored.\n"
    "Optional prompts (answer any that help):\n"
    "What did you want?\n"
    "What did you say?\n"
    "How did they respond?\n"
    "What would you like feedback on?"
)


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

    show = entry_sub.add_parser("show", help="Show one entry by id.")
    show.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    show.add_argument(
        "--with-feedback", action="store_true",
        help="Include full feedback notes, oldest first.",
    )
    show.set_defaults(func=cmd_entry_show)

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
    for noun in ("tag", "person"):
        for action in ("add", "remove"):
            edit.add_argument(
                f"--{action}-{noun}", action="append", default=[],
                metavar="TAG" if noun == "tag" else "HANDLE",
                help=(f"{action.capitalize()} a linked {noun} without replacing "
                      "the others (repeatable)."),
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
    add_date_bounds(lst)
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
        "--source", default=None, metavar="SOURCE",
        help="Filter by channel or medium (case-insensitive exact match).",
    )
    srch.add_argument(
        "--person",
        default=None,
        metavar="HANDLE",
        help="Filter by linked person handle (case-insensitive).",
    )
    add_date_bounds(srch)
    srch.add_argument(
        "--no-feedback",
        action="store_true",
        help="Show entries without feedback (the coaching backlog).",
    )
    srch.add_argument("--limit", type=non_negative_int, default=None, metavar="N")
    srch.add_argument(
        "--oldest-first", action="store_true",
        help="Return the oldest matching entries first, before applying --limit.",
    )
    srch.set_defaults(func=cmd_entry_search)

    rm = entry_sub.add_parser(
        "rm", help="Delete an entry and its linked tags, people, and feedback."
    )
    rm.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    rm.set_defaults(func=cmd_entry_rm)



def cmd_entry_add(args: argparse.Namespace) -> int:
    occurred_at = (
        parse_when_or_raise(args.occurred_at)
        if args.occurred_at is not None
        else None
    )
    with open_db(args) as conn:
        body = read_body_arg(
            args.body,
            what="body",
            template="",
            preamble=ENTRY_PREAMBLE,
            isatty=sys.stdin.isatty,
            compose=compose_in_editor,
        )
        ensure_people(conn, args.people, assume_yes=args.yes)
        entry = db.create_entry(
            conn,
            body,
            source=args.source,
            occurred_at=occurred_at,
            tags=args.tags,
            people=args.people,
        )
    emit_entry(args, entry, human_prefix="created")
    return 0


def cmd_entry_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        entry = db.get_entry(conn, args.entry_id)
        if entry is None:
            raise db.EntryNotFoundError(args.entry_id)
        feedback = db.list_feedback(conn, entry.id) if args.with_feedback else ()
    if args.with_feedback and args.json:
        print_json(
            {
                **entry_to_dict(entry),
                "feedback": [feedback_to_dict(item) for item in feedback],
            }
        )
    else:
        emit_entry(args, entry)
        if args.with_feedback:
            print("\nFeedback:")
            print(
                "\n\n".join(_format_feedback_human(item) for item in feedback)
                or "(no feedback)"
            )
    return 0


def cmd_entry_edit(args: argparse.Namespace) -> int:
    if args.clear_tags and args.tags is not None:
        raise db.DBError("--clear-tags cannot be combined with --tag")
    if args.clear_people and args.people is not None:
        raise db.DBError("--clear-people cannot be combined with --person")
    if (args.add_tag or args.remove_tag) and (args.tags is not None or args.clear_tags):
        raise db.DBError(
            "--add-tag/--remove-tag cannot be combined with --tag/--clear-tags"
        )
    if (args.add_person or args.remove_person) and (
        args.people is not None or args.clear_people
    ):
        raise db.DBError(
            "--add-person/--remove-person cannot be combined with --person/--clear-people"
        )
    added_tags = normalize_tag_list(args.add_tag)
    removed_tags = set(normalize_tag_list(args.remove_tag))
    if removed_tags.intersection(added_tags):
        raise db.DBError("cannot add and remove the same tag")
    removed_people = {handle.lower() for handle in args.remove_person}
    if removed_people.intersection(handle.lower() for handle in args.add_person):
        raise db.DBError("cannot add and remove the same person")

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

    has_link_edits = bool(
        args.add_tag or args.remove_tag or args.add_person or args.remove_person
    )
    if not update_kwargs and args.body != "" and not has_link_edits:
        raise db.DBError(
            "entry edit requires at least one of "
            "--body/--source/--occurred-at/--tag/--clear-tags/--person/--clear-people/"
            "--add-tag/--remove-tag/--add-person/--remove-person"
        )

    with open_db(args) as conn:
        current = db.get_entry(conn, args.entry_id)
        if current is None:
            raise db.EntryNotFoundError(args.entry_id)
        if args.add_tag or args.remove_tag:
            update_kwargs["tags"] = [
                tag for tag in (*current.tags, *added_tags) if tag not in removed_tags
            ]
        if args.add_person or args.remove_person:
            handles = [p.handle for p in current.people] + args.add_person
            update_kwargs["people"] = [
                handle for handle in handles if handle.lower() not in removed_people
            ]
        if args.body == "":
            update_kwargs["body"] = read_body_arg(
                None,
                what="body",
                template=current.body,
                preamble=ENTRY_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        if args.people is not None or args.add_person:
            ensure_people(conn, args.people or args.add_person, assume_yes=args.yes)
        entry = db.update_entry(conn, args.entry_id, **update_kwargs)
        body_changed = entry.body != current.body
        has_feedback = body_changed and bool(db.feedback_entry_ids(conn, (entry.id,)))
        review_ids = (
            [row[0] for row in conn.execute(
                "SELECT review_id FROM review_entries WHERE entry_id = ? ORDER BY review_id",
                (entry.id,),
            )] if body_changed else []
        )
    emit_entry(args, entry, human_prefix="updated")
    if has_feedback or review_ids:
        command = ["commscoach"]
        if args.db:
            command.extend(("--db", args.db))
        print("Existing coaching may refer to the previous text.", file=sys.stderr)
        if has_feedback:
            print(shlex.join([*command, "entry", "show", str(entry.id), "--with-feedback"]), file=sys.stderr)
        for review_id in review_ids:
            print(shlex.join([*command, "review", "show", str(review_id), "--with-entries"]), file=sys.stderr)
    return 0


def cmd_entry_list(args: argparse.Namespace) -> int:
    since, until = parse_date_bounds(args)
    with open_db(args) as conn:
        entries = db.list_entries(
            conn,
            since=since,
            until=until,
            limit=args.limit,
        )
        coached = (
            db.feedback_entry_ids(conn, (e.id for e in entries))
            if not args.json else set()
        )
    emit_entries(args, entries, coached)
    return 0


def cmd_entry_search(args: argparse.Namespace) -> int:
    since, until = parse_date_bounds(args)
    excerpts = {}
    with open_db(args) as conn:
        entries = db.search_entries(
            conn,
            query=args.query,
            source=args.source,
            tag=args.tag,
            person_handle=args.person,
            since=since,
            until=until,
            without_feedback=args.no_feedback,
            limit=args.limit,
            mode="like" if args.exact else "auto",
            oldest_first=args.oldest_first,
        )
        total = (
            db.count_search_entries(
                conn, query=args.query, source=args.source, tag=args.tag,
                person_handle=args.person, since=since, until=until,
                without_feedback=args.no_feedback,
                mode="like" if args.exact else "auto",
            )
            if not args.json and args.limit is not None else len(entries)
        )
        if args.query and not args.json:
            snippets = (
                db.search_entry_snippets(conn, args.query, (e.id for e in entries))
                if not args.exact else {}
            )
            excerpts = {
                entry.id: (
                    " ".join(snippets[entry.id].split()) if entry.id in snippets
                    else search_excerpt(entry.body, args.query)
                )
                for entry in entries
            }
        coached = (
            db.feedback_entry_ids(conn, (e.id for e in entries))
            if not args.json else set()
        )
    emit_entries(args, entries, coached, excerpts=excerpts)
    if not args.json and total > len(entries):
        print(f"Showing {len(entries)} of {total} matching entries.")
    return 0


def cmd_entry_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        feedback_count = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE entry_id = ?", (args.entry_id,),
        ).fetchone()[0]
        review_count = conn.execute(
            "SELECT COUNT(*) FROM review_entries WHERE entry_id = ?", (args.entry_id,),
        ).fetchone()[0]
        prompt = (
            f"Delete entry {args.entry_id}? This will delete {feedback_count} feedback "
            f"{'note' if feedback_count == 1 else 'notes'} and remove {review_count} "
            f"{'review link' if review_count == 1 else 'review links'}; reviews themselves remain."
        )
        if not confirm(prompt, assume_yes=args.yes):
            raise db.DBError("aborted")
        deleted = db.delete_entry(conn, args.entry_id)
    if not deleted:
        raise db.EntryNotFoundError(args.entry_id)
    if args.json:
        print_json({"deleted": True, "id": args.entry_id})
    else:
        print(f"deleted entry {args.entry_id}")
    return 0
