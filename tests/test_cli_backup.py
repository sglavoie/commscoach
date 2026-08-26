"""Tests for consistent, atomically published SQLite backups."""

from __future__ import annotations

import io
import json
import sqlite3
import stat
import tempfile
import unittest
from argparse import Namespace
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from commscoach import cli, db, paths
from commscoach.cli import backup as backup_cli

_NOW = "2026-08-25T12:00:00+00:00"


class BackupCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at) "
                "VALUES ('saved row', 'test', NULL, ?, ?)",
                (_NOW, _NOW),
            )
            conn.commit()

    def test_custom_backup_has_secure_modes_and_equal_rows(self) -> None:
        destination = Path(self._tmp.name) / "copies" / "snapshot.sqlite3"
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(
                ["--json", "--db", str(self.db_path), "backup", "--out", str(destination)]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["backup_path"], str(destination))
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), paths.DB_FILE_MODE)
        self.assertEqual(
            stat.S_IMODE(destination.parent.stat().st_mode), paths.DATA_DIR_MODE
        )
        with closing(db.connect(self.db_path)) as source, closing(
            db.connect(destination)
        ) as copied:
            self.assertEqual(
                source.execute("SELECT * FROM entries").fetchall(),
                copied.execute("SELECT * FROM entries").fetchall(),
            )
            db.validate_schema(copied)

    def test_default_path_uses_injected_utc_time(self) -> None:
        args = Namespace(db=str(self.db_path), out=None, keep=None, json=True)
        out = io.StringIO()
        now = datetime(2026, 8, 25, 17, 45, 6, tzinfo=timezone.utc)
        with redirect_stdout(out):
            rc = backup_cli.cmd_backup(args, now=now)
        self.assertEqual(rc, 0)
        created = Path(json.loads(out.getvalue())["backup_path"])
        self.assertEqual(
            created,
            self.db_path.parent / "backups" / "commscoach-20260825T174506Z.sqlite3",
        )
        self.assertTrue(created.exists())

    def test_keep_prunes_oldest_matching_backups(self) -> None:
        args = Namespace(db=str(self.db_path), out=None, keep=2, json=True)
        created: list[Path] = []
        for second in (1, 2, 3):
            out = io.StringIO()
            now = datetime(2026, 8, 25, 12, 0, second, tzinfo=timezone.utc)
            with redirect_stdout(out):
                backup_cli.cmd_backup(args, now=now)
            created.append(Path(json.loads(out.getvalue())["backup_path"]))
        self.assertFalse(created[0].exists())
        self.assertTrue(created[1].exists())
        self.assertTrue(created[2].exists())

    def test_backup_during_open_write_transaction_is_valid(self) -> None:
        destination = Path(self._tmp.name) / "copies" / "live.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as writer, closing(
            db.connect(self.db_path)
        ) as source:
            writer.execute(
                "INSERT INTO entries (body, source, occurred_at, created_at, updated_at) "
                "VALUES ('uncommitted', NULL, NULL, ?, ?)",
                (_NOW, _NOW),
            )
            db.backup_database(source, destination)
            with closing(db.connect(destination)) as copied:
                db.validate_schema(copied)
                rows = copied.execute("SELECT body FROM entries ORDER BY id").fetchall()
                self.assertEqual([row["body"] for row in rows], ["saved row"])
            writer.rollback()

    def test_negative_keep_is_argparse_error(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(["--db", str(self.db_path), "backup", "--keep", "-1"])
        self.assertEqual(ctx.exception.code, 2)

    def test_refuses_to_replace_source_database(self) -> None:
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            rc = cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "backup",
                    "--out",
                    str(self.db_path),
                ]
            )
        self.assertEqual(rc, 1)
        with closing(db.connect(self.db_path)) as conn:
            db.validate_schema(conn)


if __name__ == "__main__":
    unittest.main()
