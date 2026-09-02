"""The ``commscoach backup`` command.

Unlike every other command, this one opens the store with
``allow_legacy_schema=True``: a backup is a page-level copy that works on any
schema version, and the README's migration recipe depends on being able to
take one *before* ``init`` upgrades the database.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .. import db
from ..db.backup import BACKUP_PREFIX, BACKUP_SUFFIX
from .common import non_negative_int, open_db, print_json, resolve_db_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "backup",
        help="Create a consistent SQLite backup.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write to PATH (default: a timestamped file under backups/).",
    )
    parser.add_argument(
        "--keep",
        type=non_negative_int,
        default=None,
        metavar="N",
        help="Keep only the newest N timestamped backups in the output directory.",
    )
    parser.set_defaults(func=cmd_backup)


def default_backup_path(db_path: Path, *, now: datetime | None = None) -> Path:
    """Return the timestamped default backup path for ``db_path``."""
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    stamp = current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return db_path.parent / "backups" / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"


def cmd_backup(args: argparse.Namespace, *, now: datetime | None = None) -> int:
    source_path = resolve_db_path(args)
    destination = (
        Path(args.out).expanduser()
        if args.out is not None
        else default_backup_path(source_path, now=now)
    )
    if destination.resolve() == source_path.resolve():
        raise db.DBError("backup destination must differ from the source database")
    # Legacy schemas are accepted on purpose: the documented way to survive a
    # migration is to back up first, so refusing a pre-migration database
    # would make this command useless at the one moment it matters most.
    with open_db(args, allow_legacy_schema=True) as conn:
        created = db.backup_database(conn, destination)
    removed = db.prune_backups(created.parent, args.keep) if args.keep is not None else ()
    payload = {
        "backup_path": str(created),
        "pruned": [str(path) for path in removed],
    }
    if args.json:
        print_json(payload)
    else:
        print(f"backed up to {created}")
        if removed:
            print(f"pruned {len(removed)} old backup(s)")
    return 0
