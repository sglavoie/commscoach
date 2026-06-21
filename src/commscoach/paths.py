"""Path resolution and secure permission helpers for commscoach.

Resolution precedence for the DB path:

1. Explicit ``cli_db`` argument (from the ``--db`` global flag).
2. ``COMMSCOACH_DB`` environment variable.
3. The default tracked DB inside the project repository's ``data/`` directory.

The default DB lives next to the package source (``<project>/data/commscoach.sqlite3``)
so that ``uv tool install --editable --force .`` keeps every invocation pointed at
the same tracked SQLite file regardless of the user's current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "COMMSCOACH_DB"
DATA_DIR_MODE = 0o700
DB_FILE_MODE = 0o600

_PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_DIR.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "commscoach.sqlite3"


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
    return DEFAULT_DB_PATH


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
