"""Tests for SQLite schema v4, idempotent init, and connection settings.

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
from unittest import mock

from commscoach import db, paths
from commscoach.db import schema as db_schema
from tests.db_schema_case import (
    EXPECTED_TABLES,
    NOW,
    build_v3_database,
    index_names,
    table_names,
)
from tests.timezone_case import local_timezone


class InitDbSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"

    def test_init_creates_all_tables(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertTrue(EXPECTED_TABLES.issubset(table_names(conn)))

    def test_init_creates_expected_indexes(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            indexes = index_names(conn)
            effective_index_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_entries_effective_at'"
            ).fetchone()["sql"]
        for required in (
            "idx_people_handle_lower",
            "idx_people_one_self",
            "idx_entries_effective_at",
            "idx_entries_created_at",
            "idx_entry_tags_tag_id",
            "idx_entry_people_person_id",
            "idx_feedback_entry_id",
            "idx_feedback_created_at",
            "idx_reviews_created_at",
            "idx_reviews_scope",
            "idx_reviews_scope_person_id",
            "idx_reviews_scope_tag",
            "idx_reviews_scope_person_handle_lower",
            "idx_entries_source_nocase",
            "idx_review_entries_entry_id",
            "idx_review_entries_review_position",
            "idx_goals_status",
            "idx_goals_person_id",
            "idx_goals_review_id",
        ):
            self.assertIn(required, indexes)
        self.assertIn("COALESCE(occurred_at, created_at) DESC", effective_index_sql)

    def test_filter_queries_use_their_indexes(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            for sql, index in (
                (
                    "SELECT id FROM entries e WHERE e.source = ? COLLATE NOCASE",
                    "idx_entries_source_nocase",
                ),
                ("SELECT id FROM reviews WHERE scope_tag = ?", "idx_reviews_scope_tag"),
                (
                    "SELECT id FROM reviews WHERE LOWER(scope_person_handle) = LOWER(?)",
                    "idx_reviews_scope_person_handle_lower",
                ),
            ):
                with self.subTest(index=index):
                    plan = " ".join(
                        row["detail"] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", ("x",))
                    )
                    self.assertIn(index, plan)

    def test_init_stamps_schema_version(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.SCHEMA_VERSION, 4)
            self.assertEqual(db.get_schema_version(conn), 4)

    def test_init_adds_no_tables_beyond_the_expected_set(self) -> None:
        db.init_db(self.db_path)
        self.assertEqual(db_schema.EXPECTED_TABLES, frozenset(EXPECTED_TABLES))

    def test_init_feature_detects_fts5_and_creates_sync_triggers(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()["value"],
                "1",
            )
            self.assertIn("entries_fts", table_names(conn))
            self.assertNotIn("entries_fts", db_schema.EXPECTED_TABLES)
            fts_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'entries_fts'"
            ).fetchone()["sql"]
            triggers = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            }
            db.validate_schema(conn)
        self.assertIn("content='entries'", fts_sql)
        self.assertIn("content_rowid='id'", fts_sql)
        self.assertIn("tokenize='unicode61 remove_diacritics 2'", fts_sql)
        self.assertEqual(
            triggers,
            {
                "entries_fts_after_insert",
                "entries_fts_after_delete",
                "entries_fts_after_update",
            },
        )

    def test_init_stamps_fts5_zero_when_probe_fails(self) -> None:
        with mock.patch.object(db_schema, "_probe_fts5", return_value=False):
            db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()["value"],
                "0",
            )
            self.assertNotIn("entries_fts", table_names(conn))
            match = db.create_entry(conn, "Quarterly planning")
            self.assertEqual(
                [entry.id for entry in db.search_entries(conn, query="arter")],
                [match.id],
            )

    def test_init_backfills_fts_once_when_counts_differ(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES ('legacy searchable body', NULL, NULL, ?, ?)",
                (NOW, NOW),
            )
            conn.commit()

        with mock.patch.object(
            db_schema, "_rebuild_fts5", wraps=db_schema._rebuild_fts5
        ) as rebuild:
            db.init_db(self.db_path)
            db.init_db(self.db_path)

        rebuild.assert_called_once()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                [entry.body for entry in db.search_entries(conn, query="searchable")],
                ["legacy searchable body"],
            )

    def test_init_rebuilds_fts_after_edits_made_without_fts5(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "budget discussion")
        with mock.patch.object(db_schema, "_probe_fts5", return_value=False):
            db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            db.update_entry(conn, entry.id, body="hiring discussion")

        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                [found.id for found in db.search_entries(conn, query="hiring")],
                [entry.id],
            )
            self.assertEqual(db.search_entries(conn, query="budget"), ())

    def test_init_rolls_back_every_step_when_one_fails(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '1')")
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES ('legacy', NULL, '2026-06-20T14:00:00-04:00', ?, ?)",
                (NOW, NOW),
            )
            conn.commit()

        with (
            mock.patch.object(db_schema, "_configure_fts5", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), 1)
            self.assertEqual(
                conn.execute("SELECT occurred_at FROM entries").fetchone()[0],
                "2026-06-20T14:00:00-04:00",
            )
            self.assertNotIn("goals", table_names(conn))
            (foreign_keys,) = conn.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(foreign_keys, 1)

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

    def test_init_refuses_a_foreign_sqlite_file_and_leaves_it_untouched(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, total REAL)")
            conn.execute("INSERT INTO invoices (total) VALUES (12.5)")
            conn.commit()
        os.chmod(self.db_path, 0o644)

        with self.assertRaisesRegex(db.DBError, "did not create"):
            db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(table_names(conn), {"invoices"})
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o644)

    def test_init_is_idempotent(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, ?, 0, ?, ?)",
                ("alice", "Alice", NOW, NOW),
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
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.execute("CREATE INDEX idx_entries_occurred_at ON entries (occurred_at)")
            conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '1')")
            conn.execute(
                "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                ("me", "Me", NOW, NOW),
            )
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    "original entry",
                    "email",
                    "2026-06-20T14:00:00-04:00",
                    NOW,
                    NOW,
                ),
            )
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES (?, NULL, ?, ?, ?)",
                ("unparseable entry", "legacy-value", NOW, NOW),
            )
            conn.commit()

        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
            self.assertTrue(EXPECTED_TABLES.issubset(table_names(conn)))
            person = conn.execute("SELECT handle, name FROM people").fetchone()
            entries = conn.execute(
                "SELECT body, source, occurred_at FROM entries ORDER BY id"
            ).fetchall()
            self.assertEqual(dict(person), {"handle": "me", "name": "Me"})
            self.assertEqual(
                dict(entries[0]),
                {
                    "body": "original entry",
                    "source": "email",
                    "occurred_at": "2026-06-20T18:00:00+00:00",
                },
            )
            # Unparsable text moves into the body so stats, filters, and
            # export/import keep working, and nothing is lost.
            self.assertIsNone(entries[1]["occurred_at"])
            self.assertEqual(
                entries[1]["body"],
                "unparseable entry\n\n(Originally recorded occurrence time: legacy-value)",
            )
            indexes = index_names(conn)
            self.assertIn("idx_entries_effective_at", indexes)
            self.assertNotIn("idx_entries_occurred_at", indexes)

    def test_init_reads_legacy_relative_occurrence_against_creation_time(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '1')")
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES ('legacy', NULL, 'yesterday 9am', ?, ?)",
                ("2026-06-20T16:00:00+00:00", "2026-06-20T16:00:00+00:00"),
            )
            conn.commit()

        with local_timezone("UTC"):
            db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT occurred_at FROM entries").fetchone()[0],
                "2026-06-19T09:00:00+00:00",
            )

    def test_init_migrates_v2_database_without_data_loss(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.executescript(db_schema.SCHEMA_V1_SQL)
            conn.executescript(db_schema.SCHEMA_V2_SQL)
            conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '2')")
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
                " VALUES ('kept entry', NULL, NULL, ?, ?)",
                (NOW, NOW),
            )
            conn.execute(
                "INSERT INTO reviews"
                " (body, scope_kind, scope_limit, created_at, updated_at)"
                " VALUES ('kept review', 'latest', 1, ?, ?)",
                (NOW, NOW),
            )
            conn.commit()

        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
            self.assertTrue(EXPECTED_TABLES.issubset(table_names(conn)))
            self.assertEqual(
                conn.execute("SELECT body FROM entries").fetchone()["body"],
                "kept entry",
            )
            self.assertEqual(
                conn.execute("SELECT body FROM reviews").fetchone()["body"],
                "kept review",
            )

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

    def test_connection_pins_delete_journal_mode(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
        with closing(db.connect(self.db_path)) as conn:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
            self.assertEqual(mode.lower(), "delete")

    def test_connection_returns_row_objects(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            row = conn.execute("SELECT 1 AS value").fetchone()
            self.assertEqual(row["value"], 1)


class ValidateBackupSourceTests(unittest.TestCase):
    """The relaxed check that lets ``backup`` snapshot a pre-migration store.

    ``validate_schema`` gates every row-touching command on the current
    version. This one gates only on "recognizably a commscoach database", so
    the tests are about where that looser line falls.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"

    def _assert_rejected(self) -> None:
        with (
            closing(db.connect(self.db_path)) as conn,
            self.assertRaisesRegex(db.DBError, "run 'commscoach init' first"),
        ):
            db.validate_backup_source(conn)

    def test_accepts_a_released_v3_database(self) -> None:
        build_v3_database(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.validate_backup_source(conn), 3)

    def test_accepts_the_current_schema(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.validate_backup_source(conn), db.SCHEMA_VERSION)

    def test_accepts_a_version_newer_than_this_build(self) -> None:
        """Refusing to copy a future release's data would be the same bug."""
        db.init_db(self.db_path)
        future = db.SCHEMA_VERSION + 1
        with closing(db.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
                (str(future),),
            )
            conn.commit()
            self.assertEqual(db.validate_backup_source(conn), future)

    def test_rejects_an_empty_file(self) -> None:
        paths.ensure_data_dir(self.db_path)
        self.db_path.touch()
        self._assert_rejected()

    def test_rejects_a_sqlite_file_that_is_not_ours(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            conn.commit()
        self._assert_rejected()

    def test_rejects_a_meta_table_with_no_stamped_version(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM _meta WHERE key = 'schema_version'")
            conn.commit()
        self._assert_rejected()

    def test_rejects_a_stamped_version_that_lost_the_baseline_tables(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP TABLE entries")
            conn.commit()
        self._assert_rejected()


if __name__ == "__main__":
    unittest.main()


class Fts5PortabilityTests(unittest.TestCase):
    def test_validate_schema_asks_for_init_when_fts5_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "commscoach.sqlite3"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('fts5', '1')")
                conn.commit()
                # On this build the FTS table is readable, so validation passes.
                self.assertTrue(db_schema._fts5_usable(conn))
                db.validate_schema(conn)
                with (
                    mock.patch.object(db_schema, "_fts5_usable", return_value=False),
                    self.assertRaises(db.InitRequiredError) as raised,
                ):
                    db.validate_schema(conn)
        self.assertIn("full-text search", str(raised.exception))
        self.assertIn("run 'commscoach init' first", str(raised.exception))
