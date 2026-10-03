"""Editor text survives failures through the final database save."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from unittest import mock

from commscoach import db
from commscoach.cli import editor

from .cli_entry_case import EntryTestBase


class DraftRecoveryTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.draft_path: Path | None = None
        self.enterContext(mock.patch.dict(os.environ, {"VISUAL": "fake-editor"}))
        self.enterContext(mock.patch("sys.stdin.isatty", return_value=True))
        # Keep even deliberately retained drafts inside the test directory.
        mkstemp = editor.tempfile.mkstemp
        self.enterContext(mock.patch.object(
            editor.tempfile, "mkstemp",
            side_effect=lambda **kw: mkstemp(dir=self.tmpdir, **kw),
        ))

    def _write_draft(self, command: list[str]) -> int:
        self.draft_path = Path(command[-1])
        self.draft_path.write_text("#> instructions\nMy unsaved text\n", encoding="utf-8")
        return 0

    def _assert_recovered(self, err: str) -> None:
        assert self.draft_path is not None
        self.assertIn(f"Draft saved for recovery: {self.draft_path}", err)
        self.assertIn("My unsaved text", self.draft_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(self.draft_path.stat().st_mode), 0o600)

    def test_editor_error_and_interrupt_preserve_nonempty_private_draft(self) -> None:
        for failure in (7, KeyboardInterrupt()):
            with self.subTest(failure=failure):
                def run(command):
                    self._write_draft(command)
                    self.draft_path.chmod(0o644)
                    if isinstance(failure, BaseException):
                        raise failure
                    return failure

                with mock.patch.object(editor.subprocess, "call", side_effect=run):
                    rc, out, err = self._run("entry", "add", "--json")
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self._assert_recovered(err)
                self.assertNotIn("My unsaved text", err)

    def test_all_editor_commands_keep_draft_after_save_failure(self) -> None:
        entry = self._seed_entry()
        with closing(db.connect(self.db_path)) as conn:
            feedback = db.create_feedback(conn, entry.id, "original feedback")
            review = db.create_review(conn, "original review", [entry.id])
        cases = (
            (("entry", "add"), "create_entry"),
            (("entry", "edit", str(entry.id), "--body"), "update_entry"),
            (("feedback", "add", str(entry.id)), "create_feedback"),
            (("feedback", "edit", str(feedback.id), "--body"), "update_feedback"),
            (("review", "add", "--entry", str(entry.id)), "create_review"),
            (("review", "edit", str(review.id), "--body"), "update_review"),
        )
        for args, mutator in cases:
            with self.subTest(command=args), mock.patch.object(
                editor.subprocess, "call", side_effect=self._write_draft,
            ), mock.patch.object(db, mutator, side_effect=db.DBError("save failed")):
                rc, out, err = self._run(*args)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("save failed", err)
                self._assert_recovered(err)

    def test_draft_exists_during_save_and_is_removed_after_success(self) -> None:
        create_entry = db.create_entry

        def save(*args, **kwargs):
            assert self.draft_path is not None
            self.assertTrue(self.draft_path.exists())
            return create_entry(*args, **kwargs)

        with mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft), \
                mock.patch.object(db, "create_entry", side_effect=save):
            rc, _, err = self._run("entry", "add")
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        assert self.draft_path is not None
        self.assertFalse(self.draft_path.exists())
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, 1).body, "My unsaved text")

    def test_empty_failed_draft_is_removed(self) -> None:
        def run(command):
            self.draft_path = Path(command[-1])
            return 7

        with mock.patch.object(editor.subprocess, "call", side_effect=run):
            rc, _, err = self._run("entry", "add")
        self.assertEqual(rc, 1)
        self.assertNotIn("Draft saved", err)
        assert self.draft_path is not None
        self.assertFalse(self.draft_path.exists())

    def test_invalid_utf8_draft_is_retained(self) -> None:
        def run(command):
            self.draft_path = Path(command[-1])
            self.draft_path.write_bytes(b"unfinished \xff text")
            return 0

        with mock.patch.object(editor.subprocess, "call", side_effect=run):
            rc, _, err = self._run("entry", "add")
        self.assertEqual(rc, 1)
        self.assertIn("Draft saved for recovery:", err)
        self.assertEqual(self.draft_path.read_bytes(), b"unfinished \xff text")

    def test_unexpected_database_error_also_keeps_draft(self) -> None:
        with mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft), \
                mock.patch.object(db, "create_entry", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                self._run("entry", "add")
        assert self.draft_path is not None
        self.assertIn("My unsaved text", self.draft_path.read_text(encoding="utf-8"))
