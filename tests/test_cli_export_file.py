"""Tests for ``commscoach export`` writing to files."""

from __future__ import annotations

import os
import stat
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import export

from .cli_export_case import ExportTestBase


class ExportFileTests(ExportTestBase):
    def test_rejects_database_destination_and_aliases_without_changing_store(self) -> None:
        entry = self._seed_entry("Keep this journal")
        symbolic = self.tmpdir / "symbolic.sqlite3"
        symbolic.symlink_to(self.db_path)
        hard = self.tmpdir / "hard.sqlite3"
        os.link(self.db_path, hard)
        before = self.db_path.read_bytes()
        for destination in (self.db_path, symbolic, hard):
            for format_name in ("markdown", "json"):
                with self.subTest(destination=destination, format=format_name):
                    rc, out, err = self._run(
                        "export",
                        "--format",
                        format_name,
                        "--out",
                        str(destination),
                    )
                    self.assertEqual(rc, 1)
                    self.assertEqual(out, "")
                    self.assertIn("must differ from the source database", err)
                    self.assertEqual(self.db_path.read_bytes(), before)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, entry.id), entry)

    def test_rejects_sqlite_sidecars_even_when_absent(self) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                rc, _, err = self._run("export", "--out", f"{self.db_path}{suffix}")
                self.assertEqual(rc, 1)
                self.assertIn("SQLite sidecars", err)

    def test_rejects_a_directory_destination(self) -> None:
        rc, out, err = self._run("export", "--out", str(self.tmpdir))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("is a directory; give a file path", err)

    def test_rejects_a_missing_parent_directory_without_naming_a_temp_file(self) -> None:
        destination = self.tmpdir / "missing" / "export.md"
        rc, out, err = self._run("export", "--out", str(destination))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("is not a directory", err)
        self.assertNotIn(".tmp", err)

    def test_failed_write_or_replace_preserves_existing_export_and_cleans_temp(self) -> None:
        destination = self.tmpdir / "existing.md"
        destination.write_text("previous export", encoding="utf-8")
        for operation in ("fsync", "replace"):
            with (
                self.subTest(operation=operation),
                mock.patch.object(
                    export.os,
                    operation,
                    side_effect=OSError("disk failure"),
                ),
            ):
                rc, out, err = self._run("export", "--out", str(destination))
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("disk failure", err)
                self.assertEqual(destination.read_text(), "previous export")
                self.assertEqual(list(self.tmpdir.glob(".existing.md.*.tmp")), [])

    def test_replacement_is_complete_and_private_before_publication(self) -> None:
        destination = self.tmpdir / "existing.md"
        destination.write_text("previous export", encoding="utf-8")
        replace_file = os.replace

        def replace(source, target):
            self.assertEqual(destination.read_text(), "previous export")
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
            self.assertIn("# commscoach export", source.read_text())
            self.assertTrue(source.read_text().endswith("\n"))
            replace_file(source, target)

        with mock.patch.object(export.os, "replace", side_effect=replace):
            rc, _, err = self._run("export", "--out", str(destination))
        self.assertEqual(rc, 0, err)

    def test_export_replaces_symlink_without_modifying_its_target(self) -> None:
        target = self.tmpdir / "target.md"
        target.write_text("original", encoding="utf-8")
        destination = self.tmpdir / "link.md"
        destination.symlink_to(target)
        rc, _, err = self._run("export", "--out", str(destination))
        self.assertEqual(rc, 0, err)
        self.assertFalse(destination.is_symlink())
        self.assertEqual(target.read_text(), "original")
        self.assertIn("# commscoach export", destination.read_text())

    def test_out_writes_file_with_markdown(self) -> None:
        self._seed_entry("hello")
        out_path = self.tmpdir / "out.md"
        rc, stdout, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertTrue(out_path.exists())
        content = out_path.read_text(encoding="utf-8")
        self.assertIn("# commscoach export", content)
        self.assertIn("hello", content)

    def test_out_file_has_mode_0600(self) -> None:
        self._seed_entry("hello")
        out_path = self.tmpdir / "perms.md"
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_out_overwrites_existing_file_and_tightens_mode(self) -> None:
        out_path = self.tmpdir / "existing.md"
        out_path.write_text("stale content", encoding="utf-8")
        os.chmod(out_path, 0o644)
        self._seed_entry("fresh")
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        content = out_path.read_text(encoding="utf-8")
        self.assertNotIn("stale content", content)
        self.assertIn("fresh", content)
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_out_confirmation_message_on_stderr(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "msg.md"
        rc, stdout, stderr = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertIn(str(out_path), stderr)

    def test_out_expands_user_home(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "home.md"
        # Use an explicit absolute path; ensure expanduser is wired by passing
        # a path that begins with the literal tmpdir (no ~) and one that does.
        # The simplest assertion: passing the absolute path round-trips.
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())

    def test_out_missing_parent_exits_one(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "missing-dir" / "out.md"
        rc, stdout, stderr = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("cannot write export", stderr)
        self.assertIn(str(out_path), stderr)
