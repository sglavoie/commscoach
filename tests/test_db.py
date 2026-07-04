"""Tests for SQLite schema v2, idempotent init, and DB-level invariants.

These tests use temporary DB files exclusively so the default user DB is never
mutated.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from commscoach import db, paths


_EXPECTED_TABLES = {
    "_meta",
    "people",
    "entries",
    "tags",
    "entry_tags",
    "entry_people",
    "feedback",
    "reviews",
    "review_entries",
}

_NOW = "2026-06-20T00:00:00Z"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


class InitDbSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"

    def test_init_creates_all_tables(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertTrue(_EXPECTED_TABLES.issubset(_table_names(conn)))

    def test_init_creates_expected_indexes(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            indexes = _index_names(conn)
        for required in (
            "idx_people_handle_lower",
            "idx_people_one_self",
            "idx_entries_occurred_at",
            "idx_entries_created_at",
            "idx_entry_tags_tag_id",
            "idx_entry_people_person_id",
            "idx_feedback_entry_id",
            "idx_feedback_created_at",
            "idx_reviews_created_at",
            "idx_review_entries_entry_id",
            "idx_review_entries_review_position",
        ):
            self.assertIn(required, indexes)

    def test_init_stamps_schema_version(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)

    def test_init_returns_resolved_path(self) -> None:
        returned = db.init_db(self.db_path)
        self.assertEqual(returned, self.db_path)
        self.assertTrue(returned.is_file())

    def test_init_applies_data_dir_and_file_permissions(self) -> None:
        db.init_db(self.db_path)
        data_mode = stat.S_IMODE(self.db_path.parent.stat().st_mode)
        file_mode = stat.S_IMODE(self.db_path.stat().st_mode)
        self.assertEqual(data_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

    def test_init_is_idempotent(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, ?, 0, ?, ?)",
                ("alice", "Alice", _NOW, _NOW),
            )
            conn.commit()

        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM people").fetchone()
            self.assertEqual(count, 1)
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
            (meta_count,) = conn.execute(
                "SELECT COUNT(*) FROM _meta WHERE key = 'schema_version'"
            ).fetchone()
            self.assertEqual(meta_count, 1)

    def test_init_migrates_v1_database_without_data_loss(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.executescript(db._SCHEMA_V1_SQL)
            conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('schema_version', '1')"
            )
            conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                ("me", "Me", _NOW, _NOW),
            )
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES (?, ?, NULL, ?, ?)",
                ("original entry", "email", _NOW, _NOW),
            )
            conn.commit()

        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
            self.assertTrue(_EXPECTED_TABLES.issubset(_table_names(conn)))
            person = conn.execute("SELECT handle, name FROM people").fetchone()
            entry = conn.execute("SELECT body, source FROM entries").fetchone()
            self.assertEqual(dict(person), {"handle": "me", "name": "Me"})
            self.assertEqual(dict(entry), {"body": "original entry", "source": "email"})

    def test_init_resolves_default_path_when_none(self) -> None:
        fake_db = Path(self._tmp.name) / "from-env.sqlite3"
        prev = os.environ.get(paths.ENV_VAR)
        os.environ[paths.ENV_VAR] = str(fake_db)
        try:
            returned = db.init_db()
        finally:
            if prev is None:
                del os.environ[paths.ENV_VAR]
            else:
                os.environ[paths.ENV_VAR] = prev
        self.assertEqual(returned, fake_db)
        self.assertTrue(fake_db.is_file())


class ConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)

    def test_connection_has_foreign_keys_enabled(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            (fk_on,) = conn.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(fk_on, 1)

    def test_connection_returns_row_objects(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            row = conn.execute("SELECT 1 AS value").fetchone()
            self.assertEqual(row["value"], 1)


class SchemaInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)

    def _insert_person(self, conn: sqlite3.Connection, handle: str, is_self: int = 0) -> int:
        cur = conn.execute(
            "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
            " VALUES (?, NULL, ?, ?, ?)",
            (handle, is_self, _NOW, _NOW),
        )
        return cur.lastrowid

    def _insert_entry(self, conn: sqlite3.Connection, body: str = "msg") -> int:
        cur = conn.execute(
            "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
            " VALUES (?, NULL, NULL, ?, ?)",
            (body, _NOW, _NOW),
        )
        return cur.lastrowid

    def _insert_review(self, conn: sqlite3.Connection, body: str = "review") -> int:
        cur = conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES (?, 'latest', 20, ?, ?)",
            (body, _NOW, _NOW),
        )
        return cur.lastrowid

    def test_handle_uniqueness_is_case_insensitive(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "Alice")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_person(conn, "alice")

    def test_at_most_one_self_person(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "me", is_self=1)
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_person(conn, "other", is_self=1)

    def test_multiple_non_self_people_allowed(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "a", is_self=0)
            self._insert_person(conn, "b", is_self=0)
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM people WHERE is_self = 0"
            ).fetchone()
            self.assertEqual(count, 2)

    def test_is_self_rejects_non_boolean(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                    " VALUES (?, NULL, 2, ?, ?)",
                    ("bogus", _NOW, _NOW),
                )

    def test_tags_unique_case_insensitive(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO tags (name) VALUES (?)", ("Work",))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tags (name) VALUES (?)", ("work",))

    def test_deleting_entry_cascades_to_links_and_feedback(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            person_id = self._insert_person(conn, "alice")
            tag_cur = conn.execute("INSERT INTO tags (name) VALUES (?)", ("work",))
            tag_id = tag_cur.lastrowid
            conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person_id),
            )
            conn.execute(
                "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_id),
            )
            conn.execute(
                "INSERT INTO feedback (entry_id, body, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (entry_id, "good job", _NOW, _NOW),
            )
            conn.commit()

            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.commit()

            (ep_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_people WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            (et_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_tags WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            (fb_count,) = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(ep_count, 0)
            self.assertEqual(et_count, 0)
            self.assertEqual(fb_count, 0)

    def test_deleting_person_cascades_entry_people_only(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            person_id = self._insert_person(conn, "alice")
            conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person_id),
            )
            conn.commit()

            conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.commit()

            (ep_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_people WHERE person_id = ?", (person_id,)
            ).fetchone()
            (entry_count,) = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(ep_count, 0)
            self.assertEqual(entry_count, 1)

    def test_feedback_requires_existing_entry(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO feedback (entry_id, body, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (9999, "orphan", _NOW, _NOW),
                )

    def test_entry_people_requires_existing_rows(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                    (entry_id, 9999),
                )

    def test_reviews_scope_kind_only_allows_latest(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO reviews (body, scope_kind, scope_limit, created_at, updated_at)"
                " VALUES (?, 'latest', NULL, ?, ?)",
                ("ok", _NOW, _NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO reviews"
                    " (body, scope_kind, scope_limit, created_at, updated_at)"
                    " VALUES (?, 'all', NULL, ?, ?)",
                    ("bad scope", _NOW, _NOW),
                )

    def test_deleting_review_cascades_to_review_entries(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            review_id = self._insert_review(conn)
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, 0)",
                (review_id, entry_id),
            )
            conn.commit()

            conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            conn.commit()

            (count,) = conn.execute(
                "SELECT COUNT(*) FROM review_entries WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            self.assertEqual(count, 0)

    def test_deleting_entry_cascades_to_review_entries(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            review_id = self._insert_review(conn)
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, 0)",
                (review_id, entry_id),
            )
            conn.commit()

            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.commit()

            (count,) = conn.execute(
                "SELECT COUNT(*) FROM review_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
