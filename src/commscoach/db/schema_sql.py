"""DDL and FTS5 SQL statements for the SQLite schema."""

from __future__ import annotations

SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    name TEXT,
    is_self INTEGER NOT NULL DEFAULT 0 CHECK (is_self IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_handle_lower
    ON people (LOWER(handle));

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_one_self
    ON people (is_self) WHERE is_self = 1;

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    source TEXT,
    occurred_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries (created_at);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_tags_tag_id ON entry_tags (tag_id);

CREATE TABLE IF NOT EXISTS entry_people (
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_people_person_id
    ON entry_people (person_id);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_entry_id ON feedback (entry_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback (created_at);
"""

# Templated on the table name so the v4 rebuild builds ``reviews_new`` from the
# exact DDL a fresh database gets. Duplicating it would let the two drift.
_REVIEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    scope_kind TEXT NOT NULL
        CHECK (scope_kind IN ('latest', 'person', 'tag', 'range')),
    scope_limit INTEGER CHECK (scope_limit IS NULL OR scope_limit >= 0),
    scope_person_id INTEGER REFERENCES people (id) ON DELETE SET NULL,
    scope_person_handle TEXT,
    scope_tag TEXT,
    scope_since TEXT,
    scope_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((scope_kind = 'person') = (scope_person_handle IS NOT NULL)),
    CHECK ((scope_kind = 'tag') = (scope_tag IS NOT NULL)),
    CHECK (scope_person_id IS NULL OR scope_kind = 'person'),
    CHECK (scope_kind = 'range'
           OR (scope_since IS NULL AND scope_until IS NULL)),
    CHECK (scope_kind <> 'range'
           OR scope_since IS NOT NULL OR scope_until IS NOT NULL)
);
"""

# A table rebuild drops the table's indexes with it, so the rebuild replays
# these one by one; the cumulative DDL below runs the same list as a script.
_REVIEWS_INDEX_STATEMENTS = (
    ("CREATE INDEX IF NOT EXISTS idx_reviews_created_at ON reviews (created_at, id)"),
    ("CREATE INDEX IF NOT EXISTS idx_reviews_scope ON reviews (scope_kind, created_at)"),
    ("CREATE INDEX IF NOT EXISTS idx_reviews_scope_person_id ON reviews (scope_person_id)"),
)

SCHEMA_V2_SQL = (
    _REVIEWS_TABLE_SQL.format(table="reviews")
    + "".join(f"\n{statement};\n" for statement in _REVIEWS_INDEX_STATEMENTS)
    + """
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
)

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'done', 'dropped')),
    person_id INTEGER REFERENCES people (id) ON DELETE SET NULL,
    review_id INTEGER REFERENCES reviews (id) ON DELETE SET NULL,
    outcome TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((status = 'active') = (closed_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_goals_status
    ON goals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_goals_person_id ON goals (person_id);
CREATE INDEX IF NOT EXISTS idx_goals_review_id ON goals (review_id);

CREATE TABLE IF NOT EXISTS person_notes (
    person_id INTEGER PRIMARY KEY REFERENCES people (id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_FTS5_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    body,
    content='entries',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_FTS5_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS entries_fts_after_insert
AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_after_delete
AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, body)
    VALUES ('delete', old.id, old.body);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_after_update
AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, body)
    VALUES ('delete', old.id, old.body);
    INSERT INTO entries_fts(rowid, body) VALUES (new.id, new.body);
END;
"""

_FTS5_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS entries_fts_after_insert;
DROP TRIGGER IF EXISTS entries_fts_after_delete;
DROP TRIGGER IF EXISTS entries_fts_after_update;
"""
