"""Path resolution and secure permission helpers for commscoach.

Resolution precedence for the DB path:

1. Explicit ``cli_db`` argument (from the ``--db`` global flag).
2. ``COMMSCOACH_DB`` environment variable.
3. ``$XDG_DATA_HOME/commscoach``, or ``~/.local/share/commscoach`` when XDG is
   unset.

The former iCloud location is retained only for migration detection; it is
never selected implicitly as the live database.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

ENV_VAR = "COMMSCOACH_DB"
DATA_DIR_MODE = 0o700
DB_FILE_MODE = 0o600

LEGACY_ICLOUD_DB_PATH = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "commscoach"
    / "commscoach.sqlite3"
)


class LegacyDatabaseMigrationRequired(RuntimeError):
    """Raised when the old iCloud DB must be moved or selected explicitly."""


def default_data_dir() -> Path:
    """Return the current environment's XDG-compatible data directory."""
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "commscoach"
    return Path.home() / ".local" / "share" / "commscoach"


def default_db_path() -> Path:
    """Return the current environment's default SQLite database path."""
    return default_data_dir() / "commscoach.sqlite3"


def resolve_db_path(cli_db: str | os.PathLike[str] | None = None) -> Path:
    """Return the effective DB path, honoring ``--db`` then ``$COMMSCOACH_DB``.

    Empty strings are treated as "not set" so that ``--db ""`` or ``COMMSCOACH_DB=``
    fall through to the next source instead of resolving to the current directory.
    """
    if cli_db is not None and str(cli_db) != "":
        return Path(cli_db).expanduser()
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    resolved_default = default_db_path()
    if not resolved_default.exists() and LEGACY_ICLOUD_DB_PATH.exists():
        mkdir_command = shlex.join(["mkdir", "-p", str(resolved_default.parent)])
        move_command = shlex.join(
            ["mv", str(LEGACY_ICLOUD_DB_PATH), str(resolved_default)]
        )
        raise LegacyDatabaseMigrationRequired(
            f"legacy iCloud database found at {LEGACY_ICLOUD_DB_PATH}; "
            "commscoach will not open it implicitly. Move it to the new default "
            f"location with:\n{mkdir_command}\n{move_command}\n"
            "Alternatively, pass --db PATH or set $COMMSCOACH_DB to select a "
            "database explicitly."
        )
    return resolved_default


def icloud_placeholder_for(db_path: Path | str) -> Path:
    """Return the iCloud placeholder path corresponding to ``db_path``."""
    resolved = Path(db_path).expanduser()
    return resolved.with_name(f".{resolved.name}.icloud")


def sidecar_paths(db_path: Path | str) -> tuple[Path, ...]:
    """Return the SQLite sidecar paths that may accompany ``db_path``."""
    resolved = Path(db_path).expanduser()
    return tuple(Path(f"{resolved}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def conflict_copies(db_path: Path | str) -> tuple[Path, ...]:
    """Return iCloud-style conflicted copies next to ``db_path``.

    Apple includes ``conflicted copy`` in these filenames, but the surrounding
    host and date text varies. Match case-insensitively while requiring the
    original filename stem and suffix so unrelated conflicts are ignored.
    """
    resolved = Path(db_path).expanduser()
    parent = resolved.parent
    stem = resolved.stem.casefold()
    suffix = resolved.suffix.casefold()
    try:
        matches = [
            path
            for path in parent.iterdir()
            if path != resolved
            and path.is_file()
            and path.stem.casefold().startswith(stem)
            and path.suffix.casefold() == suffix
            and "conflicted copy" in path.name.casefold()
        ]
    except OSError:
        return ()
    return tuple(sorted(matches, key=lambda path: path.name.casefold()))


def ensure_data_dir(db_path: Path) -> Path:
    """Ensure the DB's parent directory exists with ``0o700`` permissions.

    Creates missing parent directories, then tightens the final directory's mode
    to ``0o700``. Returns the resolved parent directory.
    """
    parent = Path(db_path).expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(parent, DATA_DIR_MODE)
    return parent


def secure_file(path: Path) -> None:
    """Chmod an existing DB/export file to ``0o600``."""
    os.chmod(Path(path).expanduser(), DB_FILE_MODE)
