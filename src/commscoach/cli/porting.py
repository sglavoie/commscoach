"""The lossless ``commscoach import`` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import db
from .common import open_db, print_json


def register(subparsers: argparse._SubParsersAction) -> None:
    import_parser = subparsers.add_parser(
        "import",
        help="Restore a complete JSON export into an empty database.",
        description=(
            "Restore a complete JSON export into an empty database. "
            "Imports never merge or replace existing data."
        ),
    )
    import_parser.add_argument(
        "path",
        metavar="PATH|-",
        help="Read the JSON dump from PATH, or from stdin with -.",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the full restore including constraints without writing data.",
    )
    import_parser.set_defaults(func=cmd_import)


def _read_dump(path_arg: str) -> Any:
    try:
        text = (
            sys.stdin.read()
            if path_arg == "-"
            else Path(path_arg).expanduser().read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise db.DumpFormatError(f"cannot read import: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise db.DumpFormatError(f"cannot read import: not valid UTF-8 ({exc.reason})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise db.DumpFormatError(
            f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc


def cmd_import(args: argparse.Namespace) -> int:
    payload = _read_dump(args.path)
    with open_db(args) as conn:
        db.load_from_dict(conn, payload, dry_run=args.dry_run)

    result = {
        "dry_run": bool(args.dry_run),
        "imported": not args.dry_run,
        "source": "stdin" if args.path == "-" else str(Path(args.path).expanduser()),
    }
    if args.json:
        print_json(result)
    elif args.dry_run:
        print("import validated; no data written")
    else:
        print(f"imported from {result['source']}")
    return 0
