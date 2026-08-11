"""Argparse wiring, the ``init`` subcommand, and the console-script entry point.

Exit codes follow the project contract:

- ``0`` success.
- ``1`` semantic user error (any ``db.DBError`` raised by a handler, a rejected
  confirmation, or ``KeyboardInterrupt``).
- ``2`` argparse syntax error (raised by ``argparse`` itself).
"""

from __future__ import annotations

import argparse
from typing import Callable

from .. import __version__, db
from . import context, entries, export, feedback, people, reviews
from .common import print_err, print_json, resolve_db_path

PROG = "commscoach"


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with global flags and subcommands."""
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
    entries.register(subparsers)
    people.register(subparsers)
    feedback.register(subparsers)
    context.register(subparsers)
    reviews.register(subparsers)
    export.register(subparsers)

    return parser


def _register_init(subparsers: argparse._SubParsersAction) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help="Create or reaffirm the SQLite DB (idempotent).",
    )
    init_parser.set_defaults(func=cmd_init)


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
