"""Shared constants and helpers for schema-level DB tests.

Tests built on these use temporary DB files exclusively so the default user DB
is never mutated.

``build_v3_database`` and the ``SCHEMA_V2_SQL_V3_ERA`` fixture it applies live
here rather than in one test module because two suites need a genuine
pre-migration database: the v4 migration tests, which assert what survives the
rebuild, and the backup tests, which assert that a legacy store can be
snapshotted before that rebuild ever runs.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from commscoach import db, paths
from commscoach.db import schema as db_schema

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


# The pre-v4 shape of SCHEMA_V2_SQL, pinned here as a fixture. It is copied
# rather than imported precisely because the source constant now describes the
# v4 shape: importing it would build a table that is already migrated and
# silently assert nothing.
SCHEMA_V2_SQL_V3_ERA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('latest')),
    scope_limit INTEGER CHECK (scope_limit IS NULL OR scope_limit >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_created_at
    ON reviews (created_at, id);

CREATE TABLE IF NOT EXISTS review_entries (
    review_id INTEGER NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (review_id, entry_id),
    UNIQUE (review_id, position)
);

CREATE INDEX IF NOT EXISTS idx_review_entries_entry_id
    ON review_entries (entry_id);

CREATE INDEX IF NOT EXISTS idx_review_entries_review_position
    ON review_entries (review_id, position);

CREATE INDEX IF NOT EXISTS idx_entries_effective_at
    ON entries (COALESCE(occurred_at, created_at) DESC);
"""


def build_v3_database(db_path: Path) -> None:
    """Create a genuine released-schema v3 database at ``db_path``.

    Seeds two people, three entries, two reviews with their entry links, and
    two goals pointing back at those reviews, so a caller can assert that the
    coaching loop's cross-table references survive whatever it does next.
    """
    paths.ensure_data_dir(db_path)
    with closing(db.connect(db_path)) as conn:
        conn.executescript(db_schema.SCHEMA_V1_SQL)
        conn.executescript(SCHEMA_V2_SQL_V3_ERA)
        conn.executescript(db_schema.SCHEMA_V3_SQL)
        conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '3')")
        for handle, is_self in (("me", 1), ("alex", 0)):
            conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, NULL, ?, ?, ?)",
                (handle, is_self, NOW, NOW),
            )
        for body in ("first", "second", "third"):
            conn.execute(
                "INSERT INTO entries"
                " (body, source, occurred_at, created_at, updated_at)"
                " VALUES (?, NULL, NULL, ?, ?)",
                (body, NOW, NOW),
            )
        conn.execute(
            "INSERT INTO reviews"
            " (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES ('weekly synthesis', 'latest', 3, ?, ?)",
            (NOW, NOW),
        )
        conn.execute(
            "INSERT INTO reviews"
            " (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES ('unlimited synthesis', 'latest', NULL, ?, ?)",
            (NOW, NOW),
        )
        for review_id, entry_id, position in ((1, 3, 0), (1, 2, 1), (1, 1, 2), (2, 1, 0)):
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, ?)",
                (review_id, entry_id, position),
            )
        conn.execute(
            "INSERT INTO goals"
            " (title, detail, status, person_id, review_id, outcome,"
            " closed_at, created_at, updated_at)"
            " VALUES ('Ask more questions', NULL, 'active', 2, 1, NULL,"
            " NULL, ?, ?)",
            (NOW, NOW),
        )
        conn.execute(
            "INSERT INTO goals"
            " (title, detail, status, person_id, review_id, outcome,"
            " closed_at, created_at, updated_at)"
            " VALUES ('Slow down', NULL, 'done', NULL, 2, 'worked',"
            " ?, ?, ?)",
            (NOW, NOW, NOW),
        )
        conn.commit()
