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
            conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('schema_version', '1')"
            )
            conn.commit()
        paths.secure_file(self.db_path)
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 1)
        self.assertEqual(
            self.checks_by_name(payload)["schema_version"]["status"], "fail"
        )

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
        self.assertTrue(
            any(item["status"] == "fail" for item in payload["checks"])
        )

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
        conflict = self.db_path.with_name(
            "commscoach (Laptop's conflicted copy).sqlite3"
        )
        conflict.touch()
        rc, payload = self.run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.checks_by_name(payload)["sync_artifacts"]["status"], "warn"
        )

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
