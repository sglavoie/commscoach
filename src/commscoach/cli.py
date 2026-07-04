"""Argparse CLI framework, shared helpers, and the ``init`` subcommand.

This module owns the ``commscoach`` console script entry point. Later sessions
extend ``build_parser`` with command-specific subparsers for entries, people,
feedback, context, and export; this session sets the shared scaffolding all
subcommands rely on:

- ``--db PATH`` / ``$COMMSCOACH_DB`` precedence is resolved by ``resolve_db_path``.
- ``--json`` switches per-command output to a stable JSON document.
- ``--yes`` skips confirmation prompts for destructive commands.
- ``read_body`` accepts a literal string, ``-`` (stdin), or ``@PATH`` (file).
- ``confirm`` prompts on stderr and treats EOF as "no".
- ``open_db`` is a context manager that closes the connection cleanly.

Exit codes follow the project contract:

- ``0`` success.
- ``1`` semantic user error (any ``db.DBError`` raised by a handler, a rejected
  confirmation, or ``KeyboardInterrupt``).
- ``2`` argparse syntax error (raised by ``argparse`` itself).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from . import __version__, db, paths


PROG = "commscoach"


def print_err(message: str, *, stream: TextIO | None = None) -> None:
    """Write a single line to stderr (or an injected stream for tests)."""
    out = stream if stream is not None else sys.stderr
    print(message, file=out)


def print_json(obj: Any, *, stream: TextIO | None = None) -> None:
    """Serialize ``obj`` as a pretty, sorted JSON document with a trailing newline."""
    out = stream if stream is not None else sys.stdout
    json.dump(obj, out, indent=2, sort_keys=True, default=str)
    out.write("\n")


def read_body(value: str, *, stdin: TextIO | None = None) -> str:
    """Resolve a CLI body argument.

    The literal value ``-`` reads the full contents of stdin. A value starting
    with ``@`` is treated as a path: the trailing portion is read as UTF-8 from
    disk. Anything else is returned verbatim. ``stdin`` is injectable for tests.
    """
    if value == "-":
        source = stdin if stdin is not None else sys.stdin
        return source.read()
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        return path.read_text(encoding="utf-8")
    return value


def confirm(
    prompt: str,
    *,
    assume_yes: bool = False,
    stream: TextIO | None = None,
    input_fn: Callable[[str], str] = input,
) -> bool:
    """Prompt the user for a y/N confirmation.

    Returns ``True`` only when the user types ``y`` or ``yes`` (case-insensitive).
    EOF and any other input return ``False``. ``assume_yes`` skips the prompt
    entirely so ``--yes`` callers do not block on stdin.
    """
    if assume_yes:
        return True
    out = stream if stream is not None else sys.stderr
    out.write(f"{prompt} [y/N] ")
    out.flush()
    try:
        answer = input_fn("")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def resolve_db_path(args: argparse.Namespace) -> Path:
    """Apply ``--db`` then ``$COMMSCOACH_DB`` then the built-in default."""
    return paths.resolve_db_path(getattr(args, "db", None))


def _non_negative_int(value: str) -> int:
    """Parse an argparse integer that cannot be negative."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


@contextmanager
def open_db(args: argparse.Namespace) -> Iterator[Any]:
    """Yield a ``sqlite3.Connection`` resolved from CLI/env/default, closing it on exit."""
    db_path = resolve_db_path(args)
    if not db_path.exists():
        raise db.DBError("run 'commscoach init' first")
    try:
        conn = db.connect(db_path)
    except sqlite3.Error as exc:
        raise db.DBError(f"cannot open database {db_path}: {exc}") from exc
    with closing(conn) as conn:
        db.validate_schema(conn)
        yield conn


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with global flags and subcommands.

    Later sessions register additional subparsers on the returned object via
    ``parser._subparsers`` or by calling the same ``add_subparsers`` action.
    This function keeps the framework small and side-effect free.
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Personal communications coach: track interactions, generate feedback.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Override the SQLite DB path (also reads $COMMSCOACH_DB).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output for the command.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    _register_init(subparsers)
    _register_entry(subparsers)
    _register_person(subparsers)
    _register_feedback(subparsers)
    _register_context(subparsers)
    _register_export(subparsers)

    return parser


def _register_init(subparsers: argparse._SubParsersAction) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help="Create or reaffirm the SQLite DB (idempotent).",
    )
    init_parser.set_defaults(func=cmd_init)


def _register_entry(subparsers: argparse._SubParsersAction) -> None:
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
        help="Entry body (literal text, '-' for stdin, or '@PATH' for a UTF-8 file).",
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
        help="Optional ISO-8601 timestamp when the communication happened.",
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
        default=None,
        help="Replace the body (literal text, '-' for stdin, or '@PATH' for a file).",
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
        help="Replace occurred_at. Pass an empty string to clear it.",
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
        type=_non_negative_int,
        default=None,
        metavar="N",
        help="Cap the number of entries returned.",
    )
    lst.set_defaults(func=cmd_entry_list)

    srch = entry_sub.add_parser(
        "search", help="Search entries by body substring, tag, and/or person."
    )
    srch.add_argument(
        "--query",
        default=None,
        metavar="TEXT",
        help="Case-insensitive body substring filter.",
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
    srch.add_argument("--limit", type=_non_negative_int, default=None, metavar="N")
    srch.set_defaults(func=cmd_entry_search)

    rm = entry_sub.add_parser(
        "rm", help="Delete an entry and its linked tags, people, and feedback."
    )
    rm.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    rm.set_defaults(func=cmd_entry_rm)


def _register_person(subparsers: argparse._SubParsersAction) -> None:
    person_parser = subparsers.add_parser(
        "person",
        help="Manage people (counterparties and self).",
    )
    person_sub = person_parser.add_subparsers(
        dest="person_command", metavar="SUBCOMMAND"
    )

    def _show_person_help(_args: argparse.Namespace) -> int:
        person_parser.print_help()
        return 0

    person_parser.set_defaults(func=_show_person_help)

    add = person_sub.add_parser("add", help="Create a new person.")
    add.add_argument("handle", help="Unique handle (case-insensitive).")
    add.add_argument(
        "--name", default=None, help="Optional human-readable display name."
    )
    add.add_argument(
        "--self",
        dest="is_self",
        action="store_true",
        help="Mark this person as the global self.",
    )
    add.set_defaults(func=cmd_person_add)

    show = person_sub.add_parser("show", help="Show one person by handle.")
    show.add_argument("handle", metavar="HANDLE")
    show.set_defaults(func=cmd_person_show)

    edit = person_sub.add_parser("edit", help="Update handle or name for a person.")
    edit.add_argument("handle", metavar="HANDLE", help="Existing handle to update.")
    edit.add_argument(
        "--handle",
        dest="new_handle",
        default=None,
        metavar="NEW_HANDLE",
        help="Rename the person's handle.",
    )
    edit.add_argument(
        "--name",
        default=None,
        help="Replace the name. Pass an empty string to clear it.",
    )
    edit.set_defaults(func=cmd_person_edit)

    lst = person_sub.add_parser(
        "list", help="List people (self first, then handle-alphabetical)."
    )
    lst.set_defaults(func=cmd_person_list)

    rm = person_sub.add_parser(
        "rm",
        help="Delete a person and their entry links (entries themselves remain).",
    )
    rm.add_argument("handle", metavar="HANDLE")
    rm.set_defaults(func=cmd_person_rm)

    self_cmd = person_sub.add_parser(
        "self", help="Promote a person to global self (atomically demotes any prior self)."
    )
    self_cmd.add_argument("handle", metavar="HANDLE")
    self_cmd.set_defaults(func=cmd_person_self)


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize the DB at the resolved path. Idempotent: safe to re-run."""
    resolved = db.init_db(resolve_db_path(args))
    if args.json:
        print_json(
            {
                "db_path": str(resolved),
                "schema_version": db.SCHEMA_VERSION,
            }
        )
    else:
        print(f"initialized {resolved}")
    return 0


def _read_body_or_raise(value: str) -> str:
    """Resolve a body argument, mapping filesystem errors to ``db.DBError``."""
    try:
        return read_body(value)
    except OSError as exc:
        raise db.DBError(f"cannot read body: {exc}") from exc


def _person_to_dict(person: db.Person) -> dict[str, Any]:
    return {
        "id": person.id,
        "handle": person.handle,
        "name": person.name,
        "is_self": person.is_self,
    }


def _entry_to_dict(entry: db.Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "body": entry.body,
        "source": entry.source,
        "occurred_at": entry.occurred_at,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "tags": list(entry.tags),
        "people": [_person_to_dict(p) for p in entry.people],
    }


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
        print_json(_entry_to_dict(entry))
        return
    if human_prefix:
        print(f"{human_prefix} entry {entry.id}")
    print(_format_entry_human(entry))


def _emit_entries(args: argparse.Namespace, entries: tuple[db.Entry, ...]) -> None:
    if args.json:
        print_json([_entry_to_dict(e) for e in entries])
        return
    if not entries:
        print("(no entries)")
        return
    for entry in entries:
        print(_format_entry_short(entry))


def cmd_entry_add(args: argparse.Namespace) -> int:
    body = _read_body_or_raise(args.body)
    with open_db(args) as conn:
        entry = db.create_entry(
            conn,
            body,
            source=args.source,
            occurred_at=args.occurred_at,
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
    if args.body is not None:
        update_kwargs["body"] = _read_body_or_raise(args.body)
    if args.source is not None:
        update_kwargs["source"] = args.source if args.source else None
    if args.occurred_at is not None:
        update_kwargs["occurred_at"] = args.occurred_at if args.occurred_at else None
    if args.clear_tags:
        update_kwargs["tags"] = ()
    elif args.tags is not None:
        update_kwargs["tags"] = tuple(args.tags)
    if args.clear_people:
        update_kwargs["people"] = ()
    elif args.people is not None:
        update_kwargs["people"] = tuple(args.people)

    if not update_kwargs:
        raise db.DBError(
            "entry edit requires at least one of "
            "--body/--source/--occurred-at/--tag/--clear-tags/--person/--clear-people"
        )

    with open_db(args) as conn:
        entry = db.update_entry(conn, args.entry_id, **update_kwargs)
    _emit_entry(args, entry, human_prefix="updated")
    return 0


def cmd_entry_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        entries = db.list_entries(conn, limit=args.limit)
    _emit_entries(args, entries)
    return 0


def cmd_entry_search(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        entries = db.search_entries(
            conn,
            query=args.query,
            tag=args.tag,
            person_handle=args.person,
            limit=args.limit,
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


def _format_person_human(person: db.Person) -> str:
    parts = [f"[{person.id}]", f"@{person.handle}"]
    if person.name:
        parts.append(f"name: {person.name}")
    if person.is_self:
        parts.append("(self)")
    return " ".join(parts)


def _emit_person(
    args: argparse.Namespace,
    person: db.Person,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(_person_to_dict(person))
        return
    line = _format_person_human(person)
    if human_prefix:
        print(f"{human_prefix} {line}")
    else:
        print(line)


def _emit_people(args: argparse.Namespace, people: tuple[db.Person, ...]) -> None:
    if args.json:
        print_json([_person_to_dict(p) for p in people])
        return
    if not people:
        print("(no people)")
        return
    for person in people:
        print(_format_person_human(person))


def _resolve_existing_person(conn: Any, handle: str) -> db.Person:
    person = db.get_person_by_handle(conn, handle)
    if person is None:
        raise db.PersonNotFoundError(handle)
    return person


def cmd_person_add(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        person = db.create_person(
            conn,
            args.handle,
            name=args.name,
            is_self=args.is_self,
        )
    _emit_person(args, person, human_prefix="created")
    return 0


def cmd_person_show(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
    _emit_person(args, person)
    return 0


def cmd_person_edit(args: argparse.Namespace) -> int:
    update_kwargs: dict[str, Any] = {}
    if args.new_handle is not None:
        update_kwargs["handle"] = args.new_handle
    if args.name is not None:
        update_kwargs["name"] = args.name if args.name else None

    if not update_kwargs:
        raise db.DBError(
            "person edit requires at least one of --handle/--name"
        )

    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        updated = db.update_person(conn, person.id, **update_kwargs)
    _emit_person(args, updated, human_prefix="updated")
    return 0


def cmd_person_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        people = db.list_people(conn)
    _emit_people(args, people)
    return 0


def cmd_person_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        if not confirm(
            f"Delete person @{person.handle}?", assume_yes=args.yes
        ):
            raise db.DBError("aborted")
        deleted = db.delete_person(conn, person.id)
    if not deleted:
        raise db.PersonNotFoundError(args.handle)
    if args.json:
        print_json({"deleted": True, "id": person.id, "handle": person.handle})
    else:
        print(f"deleted person @{person.handle}")
    return 0


def cmd_person_self(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        updated = db.set_self(conn, person.id)
    _emit_person(args, updated, human_prefix="self ->")
    return 0


def _register_feedback(subparsers: argparse._SubParsersAction) -> None:
    feedback_parser = subparsers.add_parser(
        "feedback",
        help="Manage coaching feedback notes attached to entries.",
    )
    feedback_sub = feedback_parser.add_subparsers(
        dest="feedback_command", metavar="SUBCOMMAND"
    )

    def _show_feedback_help(_args: argparse.Namespace) -> int:
        feedback_parser.print_help()
        return 0

    feedback_parser.set_defaults(func=_show_feedback_help)

    add = feedback_sub.add_parser(
        "add", help="Attach a feedback note to an existing entry."
    )
    add.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    add.add_argument(
        "body",
        help="Feedback body (literal text, '-' for stdin, or '@PATH' for a UTF-8 file).",
    )
    add.set_defaults(func=cmd_feedback_add)

    lst = feedback_sub.add_parser(
        "list", help="List feedback notes for an entry, oldest first."
    )
    lst.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    lst.set_defaults(func=cmd_feedback_list)

    get = feedback_sub.add_parser(
        "get", help="Show one feedback note by id."
    )
    get.add_argument("feedback_id", type=int, metavar="FEEDBACK_ID")
    get.set_defaults(func=cmd_feedback_get)


def _register_context(subparsers: argparse._SubParsersAction) -> None:
    context_parser = subparsers.add_parser(
        "context",
        help="Emit the skill-facing JSON context for one entry (requires self).",
    )
    context_parser.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    context_parser.set_defaults(func=cmd_context)


def _feedback_to_dict(feedback: db.Feedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "entry_id": feedback.entry_id,
        "body": feedback.body,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
    }


def _format_feedback_human(feedback: db.Feedback) -> str:
    header = (
        f"[{feedback.id}] entry {feedback.entry_id}"
        f" created {feedback.created_at}"
    )
    return "\n".join([header, "---", feedback.body])


def _format_feedback_short(feedback: db.Feedback) -> str:
    first_line = feedback.body.strip().split("\n", 1)[0]
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    return f"[{feedback.id}] {feedback.created_at} {first_line}"


def _emit_feedback(
    args: argparse.Namespace,
    feedback: db.Feedback,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(_feedback_to_dict(feedback))
        return
    if human_prefix:
        print(f"{human_prefix} feedback {feedback.id}")
    print(_format_feedback_human(feedback))


def _emit_feedback_list(
    args: argparse.Namespace, items: tuple[db.Feedback, ...]
) -> None:
    if args.json:
        print_json([_feedback_to_dict(f) for f in items])
        return
    if not items:
        print("(no feedback)")
        return
    for feedback in items:
        print(_format_feedback_short(feedback))


def cmd_feedback_add(args: argparse.Namespace) -> int:
    body = _read_body_or_raise(args.body)
    with open_db(args) as conn:
        feedback = db.create_feedback(conn, args.entry_id, body)
    _emit_feedback(args, feedback, human_prefix="created")
    return 0


def cmd_feedback_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if db.get_entry(conn, args.entry_id) is None:
            raise db.EntryNotFoundError(args.entry_id)
        items = db.list_feedback(conn, args.entry_id)
    _emit_feedback_list(args, items)
    return 0


def cmd_feedback_get(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        feedback = db.get_feedback(conn, args.feedback_id)
    if feedback is None:
        raise db.FeedbackNotFoundError(args.feedback_id)
    _emit_feedback(args, feedback)
    return 0


def _context_to_dict(context: db.EntryContext) -> dict[str, Any]:
    return {
        "entry": _entry_to_dict(context.entry),
        "self": _person_to_dict(context.self_person),
        "people": [_person_to_dict(p) for p in context.people],
        "prior_feedback": [_feedback_to_dict(f) for f in context.prior_feedback],
    }


def cmd_context(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        context = db.get_entry_context(conn, args.entry_id)
    print_json(_context_to_dict(context))
    return 0


def _register_export(subparsers: argparse._SubParsersAction) -> None:
    export_parser = subparsers.add_parser(
        "export",
        help="Export all entries to Markdown (stdout by default).",
    )
    export_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write Markdown to this file with mode 0600. Default: stdout.",
    )
    export_parser.set_defaults(func=cmd_export)


def _format_person_export_label(person: db.Person) -> str:
    label = f"@{person.handle}"
    if person.name:
        label += f" ({person.name})"
    if person.is_self:
        label += " (self)"
    return label


def _format_markdown_entry(
    entry: db.Entry, feedback_items: tuple[db.Feedback, ...]
) -> str:
    lines: list[str] = [f"## Entry {entry.id}", ""]
    lines.append(f"- **Created**: {entry.created_at}")
    if entry.occurred_at:
        lines.append(f"- **Occurred**: {entry.occurred_at}")
    if entry.source:
        lines.append(f"- **Source**: {entry.source}")
    if entry.updated_at != entry.created_at:
        lines.append(f"- **Updated**: {entry.updated_at}")
    if entry.tags:
        lines.append("- **Tags**: " + ", ".join(entry.tags))
    if entry.people:
        people_labels = [_format_person_export_label(p) for p in entry.people]
        lines.append("- **People**: " + ", ".join(people_labels))
    lines.append("")
    lines.append(entry.body.rstrip("\n"))
    lines.append("")
    if feedback_items:
        lines.append("### Feedback")
        lines.append("")
        for feedback in feedback_items:
            lines.append(f"#### Feedback {feedback.id} — {feedback.created_at}")
            lines.append("")
            lines.append(feedback.body.rstrip("\n"))
            lines.append("")
    return "\n".join(lines)


def _render_markdown_export(
    entries: tuple[db.Entry, ...],
    feedback_by_entry: dict[int, tuple[db.Feedback, ...]],
) -> str:
    sections: list[str] = ["# commscoach export", ""]
    if not entries:
        sections.append("_No entries._")
        sections.append("")
        return "\n".join(sections)
    rendered_entries: list[str] = []
    for entry in entries:
        rendered_entries.append(
            _format_markdown_entry(entry, feedback_by_entry[entry.id])
        )
    sections.append("\n---\n\n".join(rendered_entries))
    if not sections[-1].endswith("\n"):
        sections.append("")
    return "\n".join(sections)


def _write_export_file(out_path: Path, content: str) -> None:
    """Write the rendered Markdown to ``out_path`` with restricted permissions.

    Opens with ``O_CREAT | O_WRONLY | O_TRUNC`` and mode ``0o600`` so a fresh
    file is never world-readable, then calls ``paths.secure_file`` to enforce
    ``0o600`` even when overwriting a file that already existed with broader
    permissions.
    """
    fd = os.open(
        out_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, paths.DB_FILE_MODE
    )
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    paths.secure_file(out_path)


def cmd_export(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        entries = db.list_entries(conn)
        feedback_by_entry = {
            entry.id: db.list_feedback(conn, entry.id) for entry in entries
        }
    rendered = _render_markdown_export(entries, feedback_by_entry)
    if not rendered.endswith("\n"):
        rendered += "\n"
    if args.out is None:
        sys.stdout.write(rendered)
        return 0
    out_path = Path(args.out).expanduser()
    try:
        _write_export_file(out_path, rendered)
    except OSError as exc:
        raise db.DBError(f"cannot write export to {out_path}: {exc}") from exc
    print_err(f"exported to {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns an integer exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return int(func(args) or 0)
    except db.DBError as exc:
        print_err(str(exc))
        return 1
    except KeyboardInterrupt:
        print_err("interrupted")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
