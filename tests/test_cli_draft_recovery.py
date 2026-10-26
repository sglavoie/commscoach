"""Editor text survives failures through the final database save."""

from __future__ import annotations

import os
import signal
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
        self.enterContext(
            mock.patch.object(
                editor.tempfile,
                "mkstemp",
                side_effect=lambda **kw: mkstemp(dir=self.tmpdir, **kw),
            )
        )

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

                def run(command, failure=failure):
                    self._write_draft(command)
                    self.draft_path.chmod(0o644)
                    if isinstance(failure, BaseException):
                        raise failure
                    return failure

                with mock.patch.object(editor.subprocess, "call", side_effect=run):
                    rc, out, err = self._run("entry", "add", "--json")
                self.assertEqual(rc, 130 if isinstance(failure, KeyboardInterrupt) else 1)
                self.assertEqual(out, "")
                self._assert_recovered(err)
                self.assertNotIn("My unsaved text", err)

    def test_all_editor_commands_keep_draft_after_save_failure(self) -> None:
        entry = self._seed_entry()
        self._create_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            feedback = db.create_feedback(conn, entry.id, "original feedback")
            review = db.create_review(conn, "original review", [entry.id])
            goal = db.create_goal(conn, "Practice brevity")
        cases = (
            (("entry", "add"), "create_entry"),
            (("entry", "edit", str(entry.id), "--body"), "update_entry"),
            (("feedback", "add", str(entry.id)), "create_feedback"),
            (("feedback", "edit", str(feedback.id), "--body"), "update_feedback"),
            (("review", "add", "--entry", str(entry.id)), "create_review"),
            (("review", "edit", str(review.id), "--body"), "update_review"),
            (("person", "edit", "alice", "--notes"), "set_person_note"),
            (("goal", "add", "Practice", "--detail"), "create_goal"),
            (("goal", "edit", str(goal.id), "--detail"), "update_goal"),
            (("goal", "close", str(goal.id), "--outcome"), "close_goal"),
        )
        for args, mutator in cases:
            with (
                self.subTest(command=args),
                mock.patch.object(
                    editor.subprocess,
                    "call",
                    side_effect=self._write_draft,
                ),
                mock.patch.object(db, mutator, side_effect=db.DBError("save failed")),
            ):
                rc, out, err = self._run(*args)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("save failed", err)
                self._assert_recovered(err)

    def test_goal_editors_clean_successful_and_empty_drafts(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Practice brevity")
        for command, field in (("edit", "detail"), ("close", "outcome")):
            with self.subTest(command=command):
                with mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft):
                    rc, _, err = self._run("goal", command, str(goal.id), f"--{field}")
                self.assertEqual(rc, 0, err)
                self.assertFalse(self.draft_path.exists())
                with closing(db.connect(self.db_path)) as conn:
                    before = db.get_goal(conn, goal.id)
                self.assertEqual(getattr(before, field), "My unsaved text")

                def empty_draft(command):
                    self.draft_path = Path(command[-1])
                    self.draft_path.write_text("#> instructions only\n", encoding="utf-8")
                    return 0

                with mock.patch.object(editor.subprocess, "call", side_effect=empty_draft):
                    rc, _, err = self._run("goal", command, str(goal.id), f"--{field}")
                self.assertEqual(rc, 1)
                self.assertIn("empty body", err)
                self.assertFalse(self.draft_path.exists())
                with closing(db.connect(self.db_path)) as conn:
                    self.assertEqual(db.get_goal(conn, goal.id), before)

    def test_goal_add_cleans_successful_and_empty_drafts(self) -> None:
        with mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft):
            rc, _, err = self._run("goal", "add", "Practice", "--detail")
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.draft_path.exists())
        with closing(db.connect(self.db_path)) as conn:
            before = db.list_goals(conn)
        self.assertEqual(before[0].detail, "My unsaved text")

        def empty_draft(command):
            self.draft_path = Path(command[-1])
            self.draft_path.write_text("#> instructions only\n", encoding="utf-8")
            return 0

        with mock.patch.object(editor.subprocess, "call", side_effect=empty_draft):
            rc, _, err = self._run("goal", "add", "Empty", "--detail")
        self.assertEqual(rc, 1)
        self.assertIn("empty body", err)
        self.assertFalse(self.draft_path.exists())
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_goals(conn), before)

    def test_notes_editor_cleans_successful_draft_and_rejects_empty_edits(self) -> None:
        person = self._create_person("alice")
        with mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft):
            rc, _, err = self._run("person", "edit", "alice", "--notes", "--json")
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.draft_path.exists())

        def empty_draft(command):
            self.draft_path = Path(command[-1])
            self.draft_path.write_text("#> instructions only\n", encoding="utf-8")
            return 0

        with mock.patch.object(editor.subprocess, "call", side_effect=empty_draft):
            rc, _, err = self._run("person", "edit", "alice", "--notes", "--name", "Changed")
        self.assertEqual(rc, 1)
        self.assertIn("empty body", err)
        self.assertFalse(self.draft_path.exists())
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_person_note(conn, person.id).body, "My unsaved text")
            self.assertIsNone(db.get_person(conn, person.id).name)

    def test_draft_exists_during_save_and_is_removed_after_success(self) -> None:
        create_entry = db.create_entry

        def save(*args, **kwargs):
            assert self.draft_path is not None
            self.assertTrue(self.draft_path.exists())
            return create_entry(*args, **kwargs)

        with (
            mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft),
            mock.patch.object(db, "create_entry", side_effect=save),
        ):
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

    def test_cancelled_edit_of_unchanged_text_leaves_no_draft(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "Private original text")

        def run(command):
            self.draft_path = Path(command[-1])
            return 1  # e.g. vim's :cq without touching the buffer

        with mock.patch.object(editor.subprocess, "call", side_effect=run):
            rc, _, err = self._run("entry", "edit", str(entry.id), "--body")
        self.assertEqual(rc, 1)
        self.assertNotIn("Draft saved", err)
        assert self.draft_path is not None
        self.assertFalse(self.draft_path.exists())

    def test_sigterm_during_editor_keeps_new_text_and_removes_untouched_drafts(
        self,
    ) -> None:
        before = signal.getsignal(signal.SIGTERM)
        for wrote_text in (True, False):
            with self.subTest(wrote_text=wrote_text):

                def run(command, wrote_text=wrote_text):
                    if wrote_text:
                        self._write_draft(command)
                    else:
                        self.draft_path = Path(command[-1])
                    os.kill(os.getpid(), signal.SIGTERM)
                    return 0

                with mock.patch.object(editor.subprocess, "call", side_effect=run):
                    rc, _, err = self._run("entry", "add")
                self.assertEqual(rc, 128 + signal.SIGTERM)
                self.assertIn("terminated", err)
                assert self.draft_path is not None
                if wrote_text:
                    self._assert_recovered(err)
                else:
                    self.assertFalse(self.draft_path.exists())
        # The previous handler is restored once the command finishes.
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

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
        with (
            mock.patch.object(editor.subprocess, "call", side_effect=self._write_draft),
            mock.patch.object(db, "create_entry", side_effect=sqlite3.OperationalError("locked")),
        ):
            rc, _, err = self._run("entry", "add")
        self.assertEqual(rc, 1)
        self.assertIn("database error: locked", err)
        assert self.draft_path is not None
        self.assertIn("My unsaved text", self.draft_path.read_text(encoding="utf-8"))
