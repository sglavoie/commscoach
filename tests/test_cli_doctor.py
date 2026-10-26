"""Tests for tolerant store diagnostics and the iCloud placeholder guard."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from commscoach import cli, db, paths
from commscoach.db import schema

_NOW = "2026-08-25T12:00:00+00:00"

# The order every path must emit, healthy or not. ``run_checks`` splices
# ``sync_artifacts`` in at a hard-coded index on the missing/not-downloaded
# paths, so a check appended anywhere but the end of ``_DB_CHECKS`` silently
# reorders these two lists apart.
_EXPECTED_CHECK_ORDER = [
    "db_present",
    "permissions",
    "schema_version",
    "integrity_check",
    "foreign_key_check",
    "journal_mode",
    "sync_artifacts",
    "unparsable_occurred_at",
    "row_counts",
    "review_scope",
]


class DoctorCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"

    def run_doctor(self, *extra: str) -> tuple[int, dict[str, object]]:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(["--json", "--db", str(self.db_path), "doctor", *extra])
        return rc, json.loads(out.getvalue())

    @staticmethod
    def checks_by_name(payload: dict[str, object]) -> dict[str, dict[str, object]]:
        return {item["name"]: item for item in payload["checks"]}  # type: ignore[index]

    @staticmethod
    def check_names(payload: dict[str, object]) -> list[str]:
        return [item["name"] for item in payload["checks"]]  # type: ignore[index]

    def seed_person_scoped_review(self, handle: str) -> tuple[int, int]:
        """Create ``@handle`` and a review scoped to them; return (person, review)."""
        with closing(db.connect(self.db_path)) as conn:
            person = db.create_person(conn, handle)
            entry = db.create_entry(conn, "seed body", people=(handle,))
            review = db.create_review(
                conn,
                "scoped body",
                [entry.id],
                scope_kind="person",
                scope_person_handle=handle,
            )
        return person.id, review.id

    def test_healthy_database_has_all_ok_checks(self) -> None:
        db.init_db(self.db_path)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "ok")
        checks = self.checks_by_name(payload)
        self.assertEqual(
            set(checks),
            {
                "db_present",
                "permissions",
                "schema_version",
                "integrity_check",
                "foreign_key_check",
                "journal_mode",
                "sync_artifacts",
                "unparsable_occurred_at",
                "row_counts",
                "review_scope",
            },
        )
        self.assertTrue(all(item["status"] == "ok" for item in checks.values()))

    def test_quick_uses_quick_check_but_keeps_check_name(self) -> None:
        db.init_db(self.db_path)
        rc, payload = self.run_doctor("--quick")
        self.assertEqual(rc, 0)
        check = self.checks_by_name(payload)["integrity_check"]
        self.assertIn("quick_check", check["message"])

    def test_broad_permissions_warn_without_failure(self) -> None:
        db.init_db(self.db_path)
        os.chmod(self.db_path, 0o644)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "warn")
        self.assertEqual(self.checks_by_name(payload)["permissions"]["status"], "warn")

    def test_v1_database_fails_schema_version(self) -> None:
        paths.ensure_data_dir(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(schema.SCHEMA_V1_SQL)
            conn.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', '1')")
            conn.commit()
        paths.secure_file(self.db_path)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 1)
        self.assertEqual(self.checks_by_name(payload)["schema_version"]["status"], "fail")

    def test_foreign_key_violation_fails(self) -> None:
        db.init_db(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO feedback (entry_id, body, created_at, updated_at) "
                "VALUES (999, 'orphan', ?, ?)",
                (_NOW, _NOW),
            )
            conn.commit()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 1)
        check = self.checks_by_name(payload)["foreign_key_check"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(len(check["details"]), 1)

    def test_mid_file_corruption_fails_without_crashing(self) -> None:
        db.init_db(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            root_page = conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'entries'"
            ).fetchone()[0]
        with self.db_path.open("r+b") as fh:
            fh.seek((root_page - 1) * page_size)
            fh.write(b"\x00" * 256)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 1)
        self.assertTrue(any(item["status"] == "fail" for item in payload["checks"]))

    def test_unparsable_occurrence_warns_without_failure(self) -> None:
        db.init_db(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at) "
                "VALUES ('legacy', NULL, 'not-a-date', ?, ?)",
                (_NOW, _NOW),
            )
            conn.commit()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        check = self.checks_by_name(payload)["unparsable_occurred_at"]
        self.assertEqual(check["status"], "warn")

    def test_sync_artifacts_warn_without_failure(self) -> None:
        db.init_db(self.db_path)
        conflict = self.db_path.with_name("commscoach (Laptop's conflicted copy).sqlite3")
        conflict.touch()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(self.checks_by_name(payload)["sync_artifacts"]["status"], "warn")

    def test_healthy_database_emits_checks_in_the_expected_order(self) -> None:
        db.init_db(self.db_path)
        _, payload = self.run_doctor()
        self.assertEqual(self.check_names(payload), _EXPECTED_CHECK_ORDER)

    def test_missing_database_emits_every_check_in_order(self) -> None:
        _, payload = self.run_doctor()
        self.assertEqual(self.check_names(payload), _EXPECTED_CHECK_ORDER)
        self.assertEqual(self.checks_by_name(payload)["db_present"]["status"], "fail")
        self.assertIn("not run", self.checks_by_name(payload)["review_scope"]["message"])

    def test_not_downloaded_database_emits_every_check_in_order(self) -> None:
        self.db_path.parent.mkdir(parents=True)
        paths.icloud_placeholder_for(self.db_path).touch()
        _, payload = self.run_doctor()
        self.assertEqual(self.check_names(payload), _EXPECTED_CHECK_ORDER)

    def test_review_scope_ok_without_person_scoped_reviews(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "seed body")
            db.create_review(conn, "latest body", [entry.id], scope_limit=20)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.checks_by_name(payload)["review_scope"]["status"], "ok")

    def test_review_scope_ok_while_the_scoped_person_exists(self) -> None:
        db.init_db(self.db_path)
        self.seed_person_scoped_review("alice")
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        check = self.checks_by_name(payload)["review_scope"]
        self.assertEqual(check["status"], "ok")
        self.assertNotIn("details", check)

    def test_review_scope_warns_after_the_scoped_person_is_deleted(self) -> None:
        db.init_db(self.db_path)
        person_id, review_id = self.seed_person_scoped_review("Alice")
        with closing(db.connect(self.db_path)) as conn:
            self.assertTrue(db.delete_person(conn, person_id))
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "warn")
        check = self.checks_by_name(payload)["review_scope"]
        self.assertEqual(check["status"], "warn")
        self.assertIn("not corruption", check["message"])
        self.assertEqual(
            check["details"],
            [{"review_id": review_id, "scope_person_handle": "Alice"}],
        )

    def test_review_scope_fails_when_the_scope_column_is_missing(self) -> None:
        db.init_db(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DROP TABLE reviews")
            conn.execute(
                "CREATE TABLE reviews ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " body TEXT NOT NULL,"
                " scope_kind TEXT NOT NULL,"
                " scope_limit INTEGER,"
                " created_at TEXT NOT NULL,"
                " updated_at TEXT NOT NULL)"
            )
            conn.commit()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 1)
        check = self.checks_by_name(payload)["review_scope"]
        self.assertEqual(check["status"], "fail")
        self.assertIn("cannot inspect review scopes", check["message"])
        self.assertIn("no such column", check["message"])

    def test_placeholder_warns_and_open_db_blocks_init_guidance(self) -> None:
        self.db_path.parent.mkdir(parents=True)
        paths.icloud_placeholder_for(self.db_path).touch()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "warn")
        self.assertIn("brctl download", self.checks_by_name(payload)["db_present"]["message"])

        err = io.StringIO()
        with redirect_stderr(err):
            list_rc = cli.main(["--db", str(self.db_path), "entry", "list"])
        self.assertEqual(list_rc, 1)
        self.assertIn("Do NOT run 'commscoach init'", err.getvalue())
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
