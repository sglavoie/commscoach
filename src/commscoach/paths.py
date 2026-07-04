"""Path resolution and secure permission helpers for commscoach.

Resolution precedence for the DB path:

1. Explicit ``cli_db`` argument (from the ``--db`` global flag).
2. ``COMMSCOACH_DB`` environment variable.
3. The current user's iCloud Drive ``commscoach`` directory.

The default DB is derived from ``Path.home()`` so it follows the current macOS
user instead of hardcoding an absolute username.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "COMMSCOACH_DB"
DATA_DIR_MODE = 0o700
DB_FILE_MODE = 0o600

DEFAULT_DATA_DIR = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "commscoach"
)
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
