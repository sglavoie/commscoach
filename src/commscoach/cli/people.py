"""The ``commscoach person`` subcommands."""

from __future__ import annotations

import argparse
from typing import Any

from .. import db
from .common import confirm, open_db, print_json
from .serializers import person_to_dict


def register(subparsers: argparse._SubParsersAction) -> None:
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
        print_json(person_to_dict(person))
        return
    line = _format_person_human(person)
    if human_prefix:
        print(f"{human_prefix} {line}")
    else:
        print(line)


def _emit_people(args: argparse.Namespace, people: tuple[db.Person, ...]) -> None:
    if args.json:
        print_json([person_to_dict(p) for p in people])
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
        raise db.DBError("person edit requires at least one of --handle/--name")

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
        if not confirm(f"Delete person @{person.handle}?", assume_yes=args.yes):
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
