"""Tests for the schema v4 ``reviews`` table rebuild.

This is the only migration in the project that drops and recreates a table
that other tables point at, so the coverage here is about what must *survive*:
review rows, their entry links, and the ``goals.review_id`` back-references
that the coaching loop depends on.

These tests use temporary DB files exclusively so the default user DB is never
mutated.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from commscoach import db, paths
from commscoach.db import schema as db_schema

from .db_schema_case import (
    EXPECTED_TABLES,
    build_v3_database,
    index_names,
    table_names,
)

_OLD_REVIEW_COLUMNS = (
    "id",
    "body",
    "scope_kind",
    "scope_limit",
    "created_at",
    "updated_at",
)

_NEW_REVIEW_COLUMNS = (
    "scope_person_id",
    "scope_person_handle",
    "scope_tag",
    "scope_since",
    "scope_until",
)


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, object]]:
    return [dict(row) for row in conn.execute(sql)]


class ReviewsScopeV4MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        self._build_v3_database()

    def _build_v3_database(self) -> None:
        """Build the shared v3 fixture and snapshot what must survive the rebuild."""
        build_v3_database(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.before_reviews = _rows(
                conn,
                f"SELECT {', '.join(_OLD_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
            )
            self.before_review_entries = _rows(
                conn,
                "SELECT review_id, entry_id, position FROM review_entries"
                " ORDER BY review_id, position",
            )
            self.before_goals = _rows(conn, "SELECT id, title, review_id FROM goals ORDER BY id")

    def test_migration_stamps_v4_and_keeps_the_table_set(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), 4)
            self.assertTrue(EXPECTED_TABLES.issubset(table_names(conn)))
            self.assertNotIn("reviews_new", table_names(conn))

    def test_every_review_row_survives_byte_identical(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            after = _rows(
                conn,
                f"SELECT {', '.join(_OLD_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
            )

        self.assertEqual(after, self.before_reviews)

    def test_new_scope_columns_are_all_null(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            rows = _rows(
                conn,
                f"SELECT {', '.join(_NEW_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
            )

        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row, dict.fromkeys(_NEW_REVIEW_COLUMNS))

    def test_review_entry_links_and_positions_survive(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            after = _rows(
                conn,
                "SELECT review_id, entry_id, position FROM review_entries"
                " ORDER BY review_id, position",
            )

        self.assertEqual(after, self.before_review_entries)
        self.assertEqual(len(after), 4)

    def test_goal_review_references_survive(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            after = _rows(conn, "SELECT id, title, review_id FROM goals ORDER BY id")

        self.assertEqual(after, self.before_goals)
        self.assertEqual([row["review_id"] for row in after], [1, 2])

    def test_migrated_database_is_structurally_clean(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual([row[0] for row in conn.execute("PRAGMA integrity_check")], ["ok"])
            indexes = index_names(conn)

        for required in (
            "idx_reviews_created_at",
            "idx_reviews_scope",
            "idx_reviews_scope_person_id",
        ):
            self.assertIn(required, indexes)

    def test_migrated_reviews_schema_matches_a_fresh_database(self) -> None:
        fresh_path = Path(self._tmp.name) / "fresh" / "commscoach.sqlite3"
        db.init_db(fresh_path)
        db.init_db(self.db_path)

        def review_schema(path: Path) -> list[tuple[str, str]]:
            with closing(db.connect(path)) as conn:
                rows = conn.execute(
                    "SELECT name, sql FROM sqlite_master"
                    " WHERE tbl_name = 'reviews' AND sql IS NOT NULL"
                    " ORDER BY name"
                ).fetchall()
            # ALTER TABLE ... RENAME TO quotes the new name in the stored DDL;
            # that punctuation is the only permitted difference.
            return [(row["name"], row["sql"].replace('"reviews"', "reviews")) for row in rows]

        self.assertEqual(review_schema(self.db_path), review_schema(fresh_path))

    def test_migration_runs_once_and_re_init_is_a_no_op(self) -> None:
        with mock.patch.object(
            db_schema,
            "_rebuild_reviews_table",
            wraps=db_schema._rebuild_reviews_table,
        ) as rebuild:
            db.init_db(self.db_path)
            first = None
            with closing(db.connect(self.db_path)) as conn:
                first = db.dump_to_dict(conn)
            db.init_db(self.db_path)

        rebuild.assert_called_once()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.dump_to_dict(conn), first)

    def test_fresh_database_never_enters_the_rebuild(self) -> None:
        fresh_path = Path(self._tmp.name) / "fresh" / "commscoach.sqlite3"

        with mock.patch.object(
            db_schema,
            "_rebuild_reviews_table",
            wraps=db_schema._rebuild_reviews_table,
        ) as rebuild:
            db.init_db(fresh_path)

        rebuild.assert_not_called()

    def test_failed_rebuild_rolls_back_and_restores_foreign_keys(self) -> None:
        def sabotage(conn: sqlite3.Connection) -> None:
            # Get far enough to destroy the table, then fail. Without the
            # rollback this leaves the database with no reviews at all.
            conn.execute("DROP TABLE reviews")
            raise RuntimeError("rebuild exploded")

        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            with (
                mock.patch.object(db_schema, "_rebuild_reviews_table", side_effect=sabotage),
                self.assertRaisesRegex(RuntimeError, "rebuild exploded"),
            ):
                db_schema._apply_schema(conn)

            (foreign_keys,) = conn.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(
                _rows(
                    conn,
                    f"SELECT {', '.join(_OLD_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
                ),
                self.before_reviews,
            )
            self.assertEqual(
                _rows(
                    conn,
                    "SELECT review_id, entry_id, position FROM review_entries"
                    " ORDER BY review_id, position",
                ),
                self.before_review_entries,
            )
            self.assertEqual(
                _rows(conn, "SELECT id, title, review_id FROM goals ORDER BY id"),
                self.before_goals,
            )
            self.assertEqual(db.get_schema_version(conn), 3)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual([row[0] for row in conn.execute("PRAGMA integrity_check")], ["ok"])

    def test_failed_verification_leaves_the_database_untouched(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            with (
                mock.patch.object(
                    db_schema,
                    "_verify_reviews_rebuild",
                    side_effect=db.DBError("verification refused"),
                ),
                self.assertRaisesRegex(db.DBError, "verification refused"),
            ):
                db_schema._apply_schema(conn)

            (foreign_keys,) = conn.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(foreign_keys, 1)
            self.assertNotIn("reviews_new", table_names(conn))
            self.assertEqual(
                _rows(
                    conn,
                    f"SELECT {', '.join(_OLD_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
                ),
                self.before_reviews,
            )
            self.assertEqual(db.get_schema_version(conn), 3)

    def test_rebuild_does_not_cascade_delete_review_entries(self) -> None:
        """Guards the FK-off step specifically, not just the net outcome."""
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM review_entries").fetchone()
            (orphan_goals,) = conn.execute(
                "SELECT COUNT(*) FROM goals WHERE review_id IS NULL"
            ).fetchone()

        self.assertEqual(count, 4)
        self.assertEqual(orphan_goals, 0)


if __name__ == "__main__":
    unittest.main()
