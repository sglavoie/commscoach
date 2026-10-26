"""The ``commscoach person`` subcommands."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from .. import db
from .common import (
    command_prefix,
    confirm,
    edit_verb,
    first_prose_line,
    format_stamp,
    handle_arg,
    non_negative_int,
    open_db,
    print_json,
    read_body_arg,
    read_body_or_raise,
    truncate,
)
from .editor import compose_in_editor
from .feedback import _format_feedback_human
from .reviews_format import format_review_human
from .serializers import (
    entry_to_dict,
    feedback_to_dict,
    goal_to_dict,
    person_to_dict,
    review_to_dict,
)

_EDIT_NOTES = object()
NOTES_PREAMBLE = (
    "Edit private notes below. Lines beginning with #> are ignored.\n"
    "Use --clear-notes to remove notes; an empty draft will not be saved."
)


def confirm_new_people(
    conn: Any,
    handles: Iterable[str],
    *,
    assume_yes: bool,
    command: list[str] | None = None,
) -> list[str]:
    """Confirm creating any people handles that do not already exist; return them.

    Nothing is written here, so a command can ask before opening the editor
    and create the people in the same transaction as the entry linking them:
    a later failure then leaves no stray people behind. ``command`` is the
    ``command_prefix`` used in the follow-up hint, so it names the same
    ``--db`` the user is working with.
    """
    missing: dict[str, str] = {}
    for handle in handles:
        if handle.casefold() in missing or db.get_person_by_handle(conn, handle) is not None:
            continue
        db.validate_handle(handle)
        if not confirm(f"Create person @{handle}?", assume_yes=assume_yes):
            prefix = command if command is not None else ["commscoach"]
            raise db.DBError(
                f"person @{handle} does not exist; pass --yes to create it, or run "
                f"{shlex.join([*prefix, 'person', 'add', handle])} first"
            )
        missing[handle.casefold()] = handle
    return list(missing.values())


def create_people(conn: Any, handles: Iterable[str]) -> None:
    """Create the people :func:`confirm_new_people` returned."""
    for handle in handles:
        db.create_person(conn, handle, name=None)


def register(subparsers: argparse._SubParsersAction) -> None:
    person_parser = subparsers.add_parser(
        "person",
        help="Manage people (counterparties and self).",
    )
    person_sub = person_parser.add_subparsers(dest="person_command", metavar="SUBCOMMAND")

    def _show_person_help(_args: argparse.Namespace) -> int:
        person_parser.print_help()
        return 0

    person_parser.set_defaults(func=_show_person_help)

    add = person_sub.add_parser("add", help="Create a new person.")
    add.add_argument(
        "handle",
        type=handle_arg,
        help="Unique handle (case-insensitive; a leading @ is dropped; no spaces or commas).",
    )
    add.add_argument("--name", default=None, help="Optional human-readable display name.")
    add.add_argument(
        "--self",
        dest="is_self",
        action="store_true",
        help="Mark this person as the global self.",
    )
    add.set_defaults(func=cmd_person_add)

    show = person_sub.add_parser("show", help="Show one person by handle.")
    show.add_argument("handle", type=handle_arg, metavar="HANDLE")
    show.add_argument(
        "--recent",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Also show the latest N interactions and all active goals linked to this person.",
    )
    show.add_argument(
        "--with-feedback",
        action="store_true",
        help="Include full feedback for each recent interaction; requires --recent N.",
    )
    show.add_argument(
        "--reviews",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Also show the latest N person-scoped reviews in full.",
    )
    show.set_defaults(func=cmd_person_show)

    prep = person_sub.add_parser(
        "prep",
        help="Prepare for a conversation with notes, goals, feedback, and reviews.",
    )
    prep.add_argument("handle", type=handle_arg, metavar="HANDLE")
    prep.add_argument(
        "--brief",
        action="store_true",
        help="Preview notes, up to five goals, latest feedback, and reviews; JSON stays complete.",
    )
    prep.add_argument(
        "--recent",
        type=non_negative_int,
        default=5,
        metavar="N",
        help="Number of recent interactions, with full feedback (default: 5).",
    )
    prep.add_argument(
        "--reviews",
        type=non_negative_int,
        default=2,
        metavar="N",
        help="Number of person-scoped reviews (default: 2).",
    )
    prep.add_argument(
        "--include-general-goals",
        action="store_true",
        help="Also include active goals that are not linked to a person.",
    )
    prep.set_defaults(func=cmd_person_show, with_feedback=True)

    edit = person_sub.add_parser("edit", help="Update handle, name, or private notes for a person.")
    edit.add_argument(
        "handle", type=handle_arg, metavar="HANDLE", help="Existing handle to update."
    )
    edit.add_argument(
        "--handle",
        dest="new_handle",
        type=handle_arg,
        default=None,
        metavar="NEW_HANDLE",
        help="Rename the person's handle.",
    )
    edit.add_argument(
        "--name",
        default=None,
        help="Replace the name. Pass an empty string to clear it.",
    )
    notes = edit.add_mutually_exclusive_group()
    notes.add_argument(
        "--notes",
        nargs="?",
        const=_EDIT_NOTES,
        default=None,
        metavar="TEXT|-|@PATH",
        help="Set private notes from text, stdin, or a UTF-8 file; omit the value to open $EDITOR.",
    )
    notes.add_argument(
        "--clear-notes",
        action="store_true",
        help="Remove existing private notes.",
    )
    edit.set_defaults(func=cmd_person_edit)

    lst = person_sub.add_parser("list", help="List people (self first, then handle-alphabetical).")
    lst.add_argument(
        "--query",
        metavar="TEXT",
        help="Match a literal name or handle substring (a leading @ is ignored).",
    )
    lst.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Cap the number of people returned.",
    )
    lst.add_argument(
        "--with-activity",
        action="store_true",
        help="Include last logged interaction, interaction count, and active goal count.",
    )
    lst.set_defaults(func=cmd_person_list)

    rm = person_sub.add_parser(
        "rm",
        help="Delete a person and their entry links (entries themselves remain).",
    )
    rm.add_argument("handle", type=handle_arg, metavar="HANDLE")
    rm.set_defaults(func=cmd_person_rm)

    self_cmd = person_sub.add_parser(
        "self", help="Promote a person to global self (atomically demotes any prior self)."
    )
    self_cmd.add_argument("handle", type=handle_arg, metavar="HANDLE")
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
    note: db.PersonNote | None = None,
    include_notes: bool = False,
) -> None:
    if args.json:
        payload = person_to_dict(person)
        if include_notes:
            payload["notes"] = note.body if note is not None else None
        print_json(payload)
        return
    line = _format_person_human(person)
    if human_prefix:
        print(f"{human_prefix} {line}")
    else:
        print(line)
    if include_notes and note is not None:
        print(f"\nNotes:\n{note.body}")


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
    if args.with_feedback and args.recent is None:
        raise db.DBError("--with-feedback requires --recent N")
    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        note = db.get_person_note(conn, person.id)
        reviews = (
            db.list_reviews(
                conn,
                limit=args.reviews,
                scope_kind="person",
                person_handle=person.handle,
            )
            if args.reviews is not None
            else ()
        )
        if args.recent is not None:
            entries = db.search_entries(
                conn,
                person_handle=person.handle,
                limit=args.recent,
            )
            goals = db.list_goals(
                conn,
                person_handle=person.handle,
                include_general=getattr(args, "include_general_goals", False),
            )
            feedback = (
                {entry.id: db.list_feedback(conn, entry.id) for entry in entries}
                if args.with_feedback
                else {}
            )
            coached = (
                {entry_id for entry_id, notes in feedback.items() if notes}
                if args.with_feedback
                else db.feedback_entry_ids(conn, (e.id for e in entries))
            )
    if args.json:
        payload = {
            **person_to_dict(person),
            "notes": note.body if note is not None else None,
        }
        if args.recent is not None:
            payload["recent_entries"] = [
                {
                    **entry_to_dict(entry),
                    "has_feedback": entry.id in coached,
                    **(
                        {"feedback": [feedback_to_dict(note) for note in feedback[entry.id]]}
                        if args.with_feedback
                        else {}
                    ),
                }
                for entry in entries
            ]
            payload["active_goals"] = [goal_to_dict(goal) for goal in goals]
        if args.reviews is not None:
            payload["recent_reviews"] = [review_to_dict(review) for review in reviews]
        print_json(payload)
        return 0
    brief = getattr(args, "brief", False)
    command = command_prefix(args)
    _emit_person(args, person, note=note, include_notes=not brief)
    if brief and note is not None:
        print(f"\nNotes:\n{truncate(first_prose_line(note.body), width=180)}")
        print(f"  Full notes: {shlex.join([*command, 'person', 'show', person.handle])}")
    if args.recent is not None:
        # Entry commands import ensure_people, so load their renderer at call time.
        from .entries_format import emit_entries
        from .goals import _emit_goals

        if args.person_command == "prep":
            print("\nActive goals:")
            if brief:
                from .goals import _format_goal_short

                for goal in goals[:5]:
                    label = truncate(" ".join(_format_goal_short(goal).split()), width=180)
                    print(label + (" (general)" if goal.person is None else ""))
                    if goal.detail is not None:
                        print(f"  {truncate(first_prose_line(goal.detail), width=180)}")
                    print(f"  Full goal: {shlex.join([*command, 'goal', 'show', str(goal.id)])}")
                if not goals:
                    print("(no goals)")
                if len(goals) > 5:
                    full = [
                        *command,
                        "person",
                        "prep",
                        person.handle,
                        "--recent",
                        "0",
                        "--reviews",
                        "0",
                    ]
                    if args.include_general_goals:
                        full.append("--include-general-goals")
                    print(f"  Showing 5 of {len(goals)} active goals.")
                    print(f"  All goals: {shlex.join(full)}")
            else:
                _emit_goals(args, goals, with_detail=True)
        print("\nRecent interactions:")
        if args.with_feedback and entries:
            for index, entry in enumerate(entries):
                if index:
                    print()
                emit_entries(args, (entry,), coached)
                print("Feedback:")
                if brief:
                    print(
                        "\n".join(
                            f"  Feedback [{note.id}]: {truncate(first_prose_line(note.body), width=180)}"
                            for note in feedback[entry.id][-1:]
                        )
                        or "(no feedback)"
                    )
                    if len(feedback[entry.id]) > 1:
                        print(f"  Showing latest of {len(feedback[entry.id])} feedback notes.")
                    full = shlex.join([*command, "entry", "show", str(entry.id), "--with-feedback"])
                    print(f"  Full interaction: {full}")
                else:
                    print(
                        "\n\n".join(_format_feedback_human(note) for note in feedback[entry.id])
                        or "(no feedback)"
                    )
        else:
            emit_entries(args, entries, coached)
        if args.person_command != "prep":
            print("\nActive goals:")
            _emit_goals(args, goals)
    if args.reviews is not None:
        print("\nRecent reviews:")
        if brief:
            print(
                "\n".join(
                    f"  Review [{review.id}]: {truncate(first_prose_line(review.body), width=180)}\n"
                    f"  Full review: {shlex.join([*command, 'review', 'show', str(review.id)])}"
                    for review in reviews
                )
                or "(no reviews)"
            )
        else:
            print("\n\n".join(format_review_human(review) for review in reviews) or "(no reviews)")
    return 0


def cmd_person_edit(args: argparse.Namespace) -> int:
    update_kwargs: dict[str, Any] = {}
    if args.new_handle is not None:
        update_kwargs["handle"] = args.new_handle
    if args.name is not None:
        update_kwargs["name"] = args.name if args.name else None
    edit_notes = args.notes is _EDIT_NOTES
    note_body = (
        read_body_or_raise(args.notes) if args.notes is not None and not edit_notes else None
    )

    if not update_kwargs and note_body is None and not edit_notes and not args.clear_notes:
        raise db.DBError(
            "person edit requires at least one of --handle/--name/--notes/--clear-notes"
        )

    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        note = db.get_person_note(conn, person.id)
        if edit_notes:
            note_body = read_body_arg(
                None,
                what="notes",
                template=note.body if note is not None else "",
                preamble=NOTES_PREAMBLE,
                isatty=sys.stdin.isatty,
                compose=compose_in_editor,
            )
        updated = db.update_person(conn, person.id, **update_kwargs) if update_kwargs else person
        notes_changed = False
        if note_body is not None:
            saved = db.set_person_note(conn, person.id, note_body)
            notes_changed = note is None or saved.updated_at != note.updated_at
        elif args.clear_notes:
            notes_changed = db.clear_person_note(conn, person.id)
    verb = "updated" if notes_changed else edit_verb(person.updated_at, updated.updated_at)
    _emit_person(args, updated, human_prefix=verb)
    return 0


def cmd_person_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        # Handles display as @handle, so "@ali" should find @alice like "ali" does.
        query = args.query.strip().removeprefix("@") if args.query is not None else None
        matched = db.list_people(conn, query=query)
        activity = db.get_people_activity(conn) if args.with_activity else {}
    people = matched[: args.limit] if args.limit is not None else matched
    if not args.with_activity:
        _emit_people(args, people)
    elif args.json:
        print_json(
            [
                {**person_to_dict(person), "activity": asdict(activity[person.id])}
                for person in people
            ]
        )
    else:
        if not people:
            print("(no people)")
        for person in people:
            summary = activity[person.id]
            stamp = (
                format_stamp(summary.last_logged_interaction)
                if summary.last_logged_interaction
                else "(none)"
            )
            print(
                f"{_format_person_human(person)} · last logged interaction: {stamp}"
                f" · interactions: {summary.interaction_count}"
                f" · active goals: {summary.active_goal_count}"
            )
    if len(matched) > len(people):
        print(f"Showing {len(people)} of {len(matched)} people.", file=sys.stderr)
    return 0


def cmd_person_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        person = _resolve_existing_person(conn, args.handle)
        note_count = conn.execute(
            "SELECT COUNT(*) FROM person_notes WHERE person_id = ?",
            (person.id,),
        ).fetchone()[0]
        entry_count = conn.execute(
            "SELECT COUNT(*) FROM entry_people WHERE person_id = ?",
            (person.id,),
        ).fetchone()[0]
        goal_count = conn.execute(
            "SELECT COUNT(*) FROM goals WHERE person_id = ?",
            (person.id,),
        ).fetchone()[0]
        review_count = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE scope_person_id = ?",
            (person.id,),
        ).fetchone()[0]
        prompt = (
            f"Delete person @{person.handle}? This will delete {note_count} private "
            f"{'note' if note_count == 1 else 'notes'}, remove {entry_count} "
            f"{'entry link' if entry_count == 1 else 'entry links'}, and detach "
            f"{goal_count} {'goal' if goal_count == 1 else 'goals'} and "
            f"{review_count} {'person-scoped review' if review_count == 1 else 'person-scoped reviews'}. "
            "Entries, feedback, goals, and reviews remain; person-scoped reviews keep the handle."
        )
        if person.is_self:
            prompt += (
                " This removes your self profile; coaching context requires a new self profile."
            )
        if not confirm(prompt, assume_yes=args.yes):
            raise db.DBError("aborted; pass --yes to confirm without a prompt")
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
