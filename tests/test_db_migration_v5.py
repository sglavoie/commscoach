"""Tests for the schema v5 ``goal_checks`` migration.

v5 is additive: it only creates ``goal_checks`` and its index, so the coverage
here is that a v4 store gains the table in place, nothing that existed before
changes, and the result is indistinguishable from a fresh database.

These tests use temporary DB files exclusively so the default user DB is never
mutated.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from commscoach import db

from .db_schema_case import EXPECTED_TABLES, build_v3_database, index_names, table_names


def build_v4_database(db_path: Path) -> None:
    """Create a released-schema v4 database at ``db_path``, seeded like v3.

    The v3 fixture migrated by init is the current shape; because v5 only adds
    a table, dropping that table and restamping the version is exactly what a
    v4 release left on disk.
    """
    build_v3_database(db_path)
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        conn.execute("DROP TABLE goal_checks")
        conn.execute("UPDATE _meta SET value = '4' WHERE key = 'schema_version'")
        conn.commit()


def _all_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    """Snapshot every v4 table verbatim; ``dump_to_dict`` needs the v5 shape."""
    return {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in sorted(table_names(conn) - {"goal_checks"})
        if not table.startswith("entries_fts")
    }


def _schema(path: Path, table: str) -> list[tuple[str, str]]:
    with closing(db.connect(path)) as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE tbl_name = ? AND sql IS NOT NULL ORDER BY name",
            (table,),
        ).fetchall()
    return [(row["name"], row["sql"]) for row in rows]


class GoalChecksV5MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        build_v4_database(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.before = _all_rows(conn)

    def test_v4_database_requires_init_before_use(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self.assertNotIn("goal_checks", table_names(conn))
            with self.assertRaises(db.InitRequiredError):
                db.validate_schema(conn)

    def test_migration_stamps_v5_and_adds_the_table(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), 5)
            self.assertTrue(EXPECTED_TABLES.issubset(table_names(conn)))
            self.assertIn("idx_goal_checks_goal_id", index_names(conn))
            db.validate_schema(conn)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual([row[0] for row in conn.execute("PRAGMA integrity_check")], ["ok"])

    def test_every_existing_row_survives_unchanged(self) -> None:
        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            after = _all_rows(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goal_checks").fetchone()[0], 0)

        stamp = {"key": "schema_version", "value": "5"}
        self.assertIn(stamp, after["_meta"])
        after["_meta"].remove(stamp)
        self.before["_meta"].remove({"key": "schema_version", "value": "4"})
        self.assertEqual(after, self.before)
        self.assertEqual(len(after["entries"]), 3)

    def test_migrated_goal_checks_schema_matches_a_fresh_database(self) -> None:
        fresh_path = Path(self._tmp.name) / "fresh" / "commscoach.sqlite3"
        db.init_db(fresh_path)
        db.init_db(self.db_path)

        self.assertEqual(_schema(self.db_path, "goal_checks"), _schema(fresh_path, "goal_checks"))

    def test_re_init_is_a_no_op(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            feedback = db.create_feedback(conn, 1, "note", goal_checks=[(1, "advanced", None)])
            first = db.dump_to_dict(conn)

        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.dump_to_dict(conn), first)
            self.assertEqual(len(db.list_goal_checks(conn, 1)), 1)
        self.assertEqual(feedback.entry_id, 1)

    def test_migrated_table_enforces_its_constraints(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            feedback = db.create_feedback(conn, 1, "note", goal_checks=[(1, "advanced", None)])
            stamp = "2026-06-20T00:00:00+00:00"
            for values in (
                (feedback.id, 1, "advanced"),  # duplicate (feedback, goal)
                (feedback.id, 2, "won"),  # unknown verdict
                (999, 2, "hurt"),  # missing feedback
            ):
                with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO goal_checks (feedback_id, goal_id, verdict, created_at)"
                        " VALUES (?, ?, ?, ?)",
                        (*values, stamp),
                    )
            conn.rollback()
            db.delete_entry(conn, 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goal_checks").fetchone()[0], 0)

    def test_v3_database_migrates_straight_to_v5(self) -> None:
        v3_path = Path(self._tmp.name) / "v3" / "commscoach.sqlite3"
        build_v3_database(v3_path)

        db.init_db(v3_path)

        with closing(db.connect(v3_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), 5)
            self.assertIn("goal_checks", table_names(conn))
            db.validate_schema(conn)


if __name__ == "__main__":
    unittest.main()
