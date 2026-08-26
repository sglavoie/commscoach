"""Consistent, permission-safe SQLite backups."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .. import paths
from .errors import DBError

BACKUP_PREFIX = "commscoach-"
BACKUP_SUFFIX = ".sqlite3"


def backup_database(source: sqlite3.Connection, destination: Path | str) -> Path:
    """Atomically back up ``source`` to a mode-0600 ``destination``.

    The temporary file is created exclusively in the destination directory so
    it is never world-readable and ``os.replace`` remains atomic.
    """
    dest = Path(destination).expanduser()
    paths.ensure_data_dir(dest)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    fd: int | None = None
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, paths.DB_FILE_MODE)
        os.close(fd)
        fd = None
        paths.secure_file(tmp)
        with closing(sqlite3.connect(str(tmp))) as target:
            source.backup(target)
        paths.secure_file(tmp)
        os.replace(tmp, dest)
        paths.secure_file(dest)
    except (OSError, sqlite3.Error) as exc:
        raise DBError(f"cannot back up database to {dest}: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        tmp.unlink(missing_ok=True)
    return dest


def prune_backups(directory: Path | str, keep: int) -> tuple[Path, ...]:
    """Delete oldest standard backup files until at most ``keep`` remain."""
    root = Path(directory).expanduser()
    try:
        candidates = sorted(
            (
                path
                for path in root.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
                if path.is_file()
            ),
            key=lambda path: (path.name, path.stat().st_mtime_ns),
        )
    except OSError as exc:
        raise DBError(f"cannot inspect backups in {root}: {exc}") from exc
    remove_count = max(0, len(candidates) - keep)
    removed: list[Path] = []
    for path in candidates[:remove_count]:
        try:
            path.unlink()
        except OSError as exc:
            raise DBError(f"cannot prune backup {path}: {exc}") from exc
        removed.append(path)
    return tuple(removed)
