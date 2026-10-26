"""Consistent, permission-safe SQLite backups."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .. import paths
from .errors import DBError

BACKUP_PREFIX = "commscoach-"
BACKUP_SUFFIX = ".sqlite3"
# Only names produced by the default backup path are ever pruned, e.g.
# ``commscoach-20260101T000000Z.sqlite3`` or a same-second ``...Z-2.sqlite3``.
_BACKUP_NAME = re.compile(
    rf"{re.escape(BACKUP_PREFIX)}(\d{{8}}T\d{{6}}Z)(?:-(\d+))?{re.escape(BACKUP_SUFFIX)}"
)


def backup_database(source: sqlite3.Connection, destination: Path | str) -> Path:
    """Atomically back up ``source`` to a mode-0600 ``destination``.

    The temporary file is created exclusively in the destination directory so
    it is never world-readable and ``os.replace`` remains atomic.
    """
    dest = Path(destination).expanduser()
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    fd: int | None = None
    try:
        paths.ensure_data_dir(dest)
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


def prune_backups(
    directory: Path | str, keep: int, *, protect: Path | str | None = None
) -> tuple[Path, ...]:
    """Delete oldest timestamped backup files until at most ``keep`` remain.

    Only files named exactly like a default backup are considered, so other
    ``commscoach-*.sqlite3`` files in the directory are never touched, and
    ``protect`` (the live database) is excluded even if its name matches.
    """
    root = Path(directory).expanduser()
    try:
        candidates = []
        for path in root.iterdir():
            match = _BACKUP_NAME.fullmatch(path.name)
            if match is None or not path.is_file():
                continue
            if protect is not None and _same_file(path, Path(protect)):
                continue
            candidates.append((match.group(1), int(match.group(2) or 0), path))
    except OSError as exc:
        raise DBError(f"cannot inspect backups in {root}: {exc}") from exc
    candidates.sort()
    remove_count = max(0, len(candidates) - keep)
    removed: list[Path] = []
    for _, _, path in candidates[:remove_count]:
        try:
            path.unlink()
        except OSError as exc:
            raise DBError(f"cannot prune backup {path}: {exc}") from exc
        removed.append(path)
    return tuple(removed)


def _same_file(path: Path, other: Path) -> bool:
    try:
        return path.samefile(other)
    except OSError:
        return False
