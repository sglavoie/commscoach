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
