"""The ``commscoach tag`` subcommands."""

from __future__ import annotations

import argparse

from .. import db
from ..db.tags import normalize_tag
from .common import confirm, non_negative_int, open_db, print_json
from .serializers import tag_to_dict


def register(subparsers: argparse._SubParsersAction) -> None:
    tag_parser = subparsers.add_parser(
        "tag",
        help="Manage the tag vocabulary shared by entries.",
    )
    tag_sub = tag_parser.add_subparsers(dest="tag_command", metavar="SUBCOMMAND")

    def _show_tag_help(_args: argparse.Namespace) -> int:
        tag_parser.print_help()
        return 0

    tag_parser.set_defaults(func=_show_tag_help)

    lst = tag_sub.add_parser("list", help="List tags most-used first.")
    lst.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Cap the number of tags returned.",
    )
    lst.set_defaults(func=cmd_tag_list)

    rename = tag_sub.add_parser(
        "rename", help="Rename a tag, keeping every entry link."
    )
    rename.add_argument("old", metavar="OLD")
    rename.add_argument("new", metavar="NEW")
    rename.set_defaults(func=cmd_tag_rename)

    merge = tag_sub.add_parser(
        "merge", help="Fold one existing tag into another and drop it."
    )
    merge.add_argument("source", metavar="SOURCE")
    merge.add_argument("dest", metavar="DEST")
    merge.set_defaults(func=cmd_tag_merge)

    rm = tag_sub.add_parser("rm", help="Unlink a tag from every entry and drop it.")
    rm.add_argument("name", metavar="NAME")
    rm.set_defaults(func=cmd_tag_rm)


def _format_tag_short(tag: db.Tag) -> str:
    return f"{tag.name}  {tag.entry_count}"


def _emit_tag(
    args: argparse.Namespace,
    tag: db.Tag,
    *,
    human_line: str | None = None,
) -> None:
    if args.json:
        print_json(tag_to_dict(tag))
        return
    if human_line:
        print(human_line)
    print(_format_tag_short(tag))


def cmd_tag_list(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        tags = db.list_tags(conn, limit=args.limit)
    if args.json:
        print_json([tag_to_dict(tag) for tag in tags])
        return 0
    if not tags:
        print("(no tags)")
        return 0
    for tag in tags:
        print(_format_tag_short(tag))
    return 0


def cmd_tag_rename(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        tag = db.rename_tag(conn, args.old, args.new)
    _emit_tag(args, tag, human_line=f"renamed tag '{args.old}' to '{tag.name}'")
    return 0


def cmd_tag_merge(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if not confirm(
            f"Merge tag '{args.source}' into '{args.dest}'?", assume_yes=args.yes
        ):
            raise db.DBError("aborted")
        tag = db.merge_tags(conn, args.source, args.dest)
    _emit_tag(args, tag, human_line=f"merged tag '{args.source}' into '{tag.name}'")
    return 0


def cmd_tag_rm(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if not confirm(f"Delete tag '{args.name}'?", assume_yes=args.yes):
            raise db.DBError("aborted")
        unlinked = db.delete_tag(conn, args.name)
    name = normalize_tag(args.name)
    if args.json:
        print_json(
            {"deleted": True, "name": name, "entries_unlinked": unlinked}
        )
    else:
        print(f"removed tag '{name}' from {unlinked} entries")
    return 0
