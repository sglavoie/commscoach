"""Session-wide test configuration.

Every test initializes a throwaway SQLite database and most commit several
times. With SQLite's default ``synchronous = FULL`` each commit waits on an
fsync, which made the suite disk-bound (minutes instead of seconds). Crash
durability is irrelevant for throwaway databases, so syncing is disabled for
every connection the tests open.
"""

from __future__ import annotations

import sqlite3

_connect = sqlite3.connect


def _connect_without_sync(*args, **kwargs):
    conn = _connect(*args, **kwargs)
    conn.execute("PRAGMA synchronous = OFF")
    return conn


sqlite3.connect = _connect_without_sync


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch, tmp_path):
    """Keep every test away from the real journal and the terminal-only output.

    A test that forgets ``--db`` would otherwise read (or worse, write) the
    user's default database, so ``$COMMSCOACH_DB`` points at a fresh path that
    does not exist; tests that set it themselves override this. The pretty and
    color switches are cleared so the plain line format is what tests see, and
    ``claude`` is hidden so the coach launcher is unavailable unless a test sets it.
    """
    monkeypatch.setenv("COMMSCOACH_DB", str(tmp_path / "isolated" / "commscoach.sqlite3"))
    for name in (
        "COMMSCOACH_PRETTY",
        "COMMSCOACH_PLAIN",
        "FORCE_COLOR",
        "CLICOLOR_FORCE",
        "COMMSCOACH_COACH_CMD",
        "COMMSCOACH_COACH_PROMPT",
    ):
        monkeypatch.delenv(name, raising=False)
    # Whether Claude Code is installed must not change what the tests see.
    from commscoach.cli import coach

    monkeypatch.setattr(coach, "find_claude", lambda: None)
