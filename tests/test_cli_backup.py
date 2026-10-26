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
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from commscoach import cli, db, paths
from commscoach.cli import backup as backup_cli

from .db_schema_case import build_v3_database

_NOW = "2026-08-25T12:00:00+00:00"

_V3_REVIEW_COLUMNS = [
    "id",
    "body",
    "scope_kind",
    "scope_limit",
    "created_at",
    "updated_at",
]


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
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), paths.DATA_DIR_MODE)
        with (
            closing(db.connect(self.db_path)) as source,
            closing(db.connect(destination)) as copied,
        ):
            self.assertEqual(
                source.execute("SELECT * FROM entries").fetchall(),
                copied.execute("SELECT * FROM entries").fetchall(),
            )
            db.validate_schema(copied)

    def test_default_path_uses_injected_utc_time(self) -> None:
        args = Namespace(db=str(self.db_path), out=None, keep=None, json=True)
        out = io.StringIO()
        now = datetime(2026, 8, 25, 17, 45, 6, tzinfo=UTC)
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
            now = datetime(2026, 8, 25, 12, 0, second, tzinfo=UTC)
            with redirect_stdout(out):
                backup_cli.cmd_backup(args, now=now)
            created.append(Path(json.loads(out.getvalue())["backup_path"]))
        self.assertFalse(created[0].exists())
        self.assertTrue(created[1].exists())
        self.assertTrue(created[2].exists())

    def test_keep_never_prunes_the_live_database_or_unrelated_files(self) -> None:
        folder = Path(self._tmp.name) / "flat"
        folder.mkdir()
        live = folder / "commscoach-2025.sqlite3"
        db.init_db(live)
        unrelated = folder / "commscoach-notes.sqlite3"
        unrelated.touch()
        older = folder / "commscoach-20250101T000000Z.sqlite3"
        older.touch()
        newest = folder / "commscoach-20260101T000000Z.sqlite3"
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(
                ["--json", "--db", str(live), "backup", "--out", str(newest), "--keep", "1"]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(live.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(newest.exists())
        self.assertEqual(json.loads(out.getvalue())["pruned"], [str(older)])

    def test_keep_zero_is_rejected_so_the_new_backup_survives(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            cli.main(["--db", str(self.db_path), "backup", "--keep", "0"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("must be at least 1", err.getvalue())

    def test_out_refuses_to_overwrite_an_existing_file_without_yes(self) -> None:
        target = Path(self._tmp.name) / "existing.sqlite3"
        target.write_bytes(b"keep me")
        err = io.StringIO()
        with (
            redirect_stderr(err),
            mock.patch.object(backup_cli, "confirm", return_value=False),
        ):
            rc = cli.main(["--db", str(self.db_path), "backup", "--out", str(target)])
        self.assertEqual(rc, 1)
        self.assertIn("pass --yes to overwrite", err.getvalue())
        self.assertEqual(target.read_bytes(), b"keep me")

        with redirect_stdout(io.StringIO()):
            rc = cli.main(["--db", str(self.db_path), "--yes", "backup", "--out", str(target)])
        self.assertEqual(rc, 0)
        self.assertNotEqual(target.read_bytes(), b"keep me")

    def test_same_second_default_backups_do_not_overwrite(self) -> None:
        args = Namespace(db=str(self.db_path), out=None, keep=None, json=True)
        now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
        created: list[Path] = []
        for _ in range(2):
            out = io.StringIO()
            with redirect_stdout(out):
                backup_cli.cmd_backup(args, now=now)
            created.append(Path(json.loads(out.getvalue())["backup_path"]))
        self.assertEqual(
            [path.name for path in created],
            ["commscoach-20260825T120000Z.sqlite3", "commscoach-20260825T120000Z-2.sqlite3"],
        )

    def test_backup_during_open_write_transaction_is_valid(self) -> None:
        destination = Path(self._tmp.name) / "copies" / "live.sqlite3"
        with (
            closing(sqlite3.connect(self.db_path)) as writer,
            closing(db.connect(self.db_path)) as source,
        ):
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


class LegacySchemaBackupTests(unittest.TestCase):
    """``backup`` must run on the pre-v4 database it exists to protect.

    The README's migration recipe tells a v3 user to snapshot *before*
    ``commscoach init`` upgrades them, so this command has to accept the exact
    database that every other command correctly refuses. It used to open
    through the schema-validating path and answer "run 'commscoach init'
    first" — telling the user to perform the very migration the backup was
    supposed to make safe.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        self.destination = Path(self._tmp.name) / "Backups" / "commscoach-pre-v4.sqlite3"
        build_v3_database(self.db_path)

    def _run_backup(self) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "backup",
                    "--out",
                    str(self.destination),
                ]
            )

    @staticmethod
    def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, object]]:
        return [dict(row) for row in conn.execute(sql)]

    def test_backup_accepts_pre_v4_database(self) -> None:
        rc = self._run_backup()

        self.assertEqual(rc, 0)
        self.assertTrue(self.destination.exists())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), paths.DB_FILE_MODE)

    def test_backup_accepts_pre_v4_database_preserving_schema_and_data(self) -> None:
        with closing(db.connect(self.db_path)) as source:
            before_reviews = self._rows(
                source,
                f"SELECT {', '.join(_V3_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
            )
            before_links = self._rows(
                source,
                "SELECT review_id, entry_id, position FROM review_entries"
                " ORDER BY review_id, position",
            )
            before_goals = self._rows(source, "SELECT id, title, review_id FROM goals ORDER BY id")

        self.assertEqual(self._run_backup(), 0)

        with closing(db.connect(self.destination)) as copied:
            # The snapshot is pre-migration: still stamped v3, and the reviews
            # table still carries none of the v4 scope columns.
            self.assertEqual(db.get_schema_version(copied), 3)
            columns = {row[1] for row in copied.execute("PRAGMA table_info(reviews)")}
            self.assertEqual(columns, set(_V3_REVIEW_COLUMNS))
            self.assertEqual(
                self._rows(
                    copied,
                    f"SELECT {', '.join(_V3_REVIEW_COLUMNS)} FROM reviews ORDER BY id",
                ),
                before_reviews,
            )
            self.assertEqual(
                self._rows(
                    copied,
                    "SELECT review_id, entry_id, position FROM review_entries"
                    " ORDER BY review_id, position",
                ),
                before_links,
            )
            self.assertEqual(
                self._rows(copied, "SELECT id, title, review_id FROM goals ORDER BY id"),
                before_goals,
            )
            self.assertEqual([row[0] for row in copied.execute("PRAGMA integrity_check")], ["ok"])
            self.assertEqual(copied.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_backup_accepts_pre_v4_database_then_init_migrates_only_the_source(
        self,
    ) -> None:
        """The README procedure end to end: back up, then migrate."""
        self.assertEqual(self._run_backup(), 0)

        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--db", str(self.db_path), "init"]), 0)

        with closing(db.connect(self.db_path)) as source:
            db.validate_schema(source)
            self.assertEqual(db.get_schema_version(source), db.SCHEMA_VERSION)
        with closing(db.connect(self.destination)) as copied:
            self.assertEqual(db.get_schema_version(copied), 3)

    def test_row_commands_still_refuse_a_pre_v4_database(self) -> None:
        """The relaxation is backup-only; a reader would hit missing columns."""
        err_buf = io.StringIO()
        with redirect_stderr(err_buf), redirect_stdout(io.StringIO()):
            rc = cli.main(["--db", str(self.db_path), "review", "list"])

        self.assertEqual(rc, 1)
        self.assertIn("init' first", err_buf.getvalue())

    def test_backup_refuses_a_sqlite_file_that_is_not_a_commscoach_database(
        self,
    ) -> None:
        foreign = Path(self._tmp.name) / "data" / "notes.sqlite3"
        with closing(sqlite3.connect(foreign)) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            conn.commit()

        err_buf = io.StringIO()
        with redirect_stderr(err_buf), redirect_stdout(io.StringIO()):
            rc = cli.main(["--db", str(foreign), "backup", "--out", str(self.destination)])

        self.assertEqual(rc, 1)
        self.assertIn("init' first", err_buf.getvalue())
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
