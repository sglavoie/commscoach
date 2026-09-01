"""Shared constants and helpers for schema-level DB tests.

Tests built on these use temporary DB files exclusively so the default user DB
is never mutated.
"""

from __future__ import annotations

import sqlite3


EXPECTED_TABLES = {
    "_meta",
    "people",
    "entries",
    "tags",
    "entry_tags",
    "entry_people",
    "feedback",
    "reviews",
    "review_entries",
    "goals",
    "person_notes",
}

NOW = "2026-06-20T00:00:00Z"


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}
