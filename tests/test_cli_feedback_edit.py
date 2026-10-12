"""Tests for ``commscoach feedback`` excerpt, show, edit, rm and help."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import feedback as cli_feedback

from .cli_feedback_case import FeedbackTestBase


class FeedbackExcerptTests(FeedbackTestBase):
    def test_search_shows_late_match_but_json_keeps_full_body(self):
        entry = self._seed_entry()
        body = "Unrelated introduction. " * 50 + "\n## Lead with the ASK\nTry this tomorrow."
        note = self._seed_feedback(entry.id, body)
        rc, out, err = self._run("feedback", "search", "--query", "lead with the ask")
        self.assertEqual(rc, 0, err)
        self.assertIn("Lead with the ASK", out)
        self.assertIn(f"entry {entry.id}", out)
        self.assertLess(len(out), 400)
        rc, out, err = self._run("feedback", "search", "--query", "lead with the ask", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), [{
            "id": note.id, "entry_id": entry.id, "body": body,
            "created_at": note.created_at, "updated_at": note.updated_at,
        }])
        rc, out, err = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Lead with the ASK", out)


class FeedbackShowTests(FeedbackTestBase):
    def test_show_existing_prints_human(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "great job")
        rc, out, _ = self._run("feedback", "show", str(feedback.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"[{feedback.id}]", out)
        self.assertIn("great job", out)

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("feedback", "show", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_show_json_round_trip(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "round-trip")
        rc, out, _ = self._run("--json", "feedback", "show", str(feedback.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], feedback.id)
        self.assertEqual(payload["entry_id"], entry.id)
        self.assertEqual(payload["body"], "round-trip")


class FeedbackEditTests(FeedbackTestBase):
    def test_edit_body_literal(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "first draft")

        rc, out, _ = self._run(
            "feedback", "edit", str(feedback.id), "--body", "corrected note"
        )

        self.assertEqual(rc, 0)
        self.assertIn(f"updated feedback {feedback.id}", out)
        self.assertIn("corrected note", out)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_feedback(conn, feedback.id)
        assert updated is not None
        self.assertEqual(updated.body, "corrected note")
        self.assertNotEqual(updated.updated_at, updated.created_at)

    def test_edit_body_from_stdin(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "first draft")

        rc, _, _ = self._run(
            "feedback",
            "edit",
            str(feedback.id),
            "--body",
            "-",
            stdin=io.StringIO("piped correction\n"),
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_feedback(conn, feedback.id)
        assert updated is not None
        self.assertEqual(updated.body, "piped correction\n")

    def test_edit_body_from_file(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "first draft")
        body_file = self.tmpdir / "correction.txt"
        body_file.write_text("from file correction", encoding="utf-8")

        rc, _, _ = self._run(
            "feedback", "edit", str(feedback.id), "--body", f"@{body_file}"
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_feedback(conn, feedback.id)
        assert updated is not None
        self.assertEqual(updated.body, "from file correction")

    def test_edit_bare_body_opens_editor_seeded_with_current_body(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "first draft")

        def compose(*, template: str, preamble: str) -> str:
            self.assertEqual(template, "first draft")
            self.assertIn("#>", preamble)
            return "edited in place"

        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_feedback.sys, "stdin", tty), mock.patch.object(
            cli_feedback, "compose_in_editor", side_effect=compose
        ):
            rc, _, _ = self._run("feedback", "edit", str(feedback.id), "--body")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_feedback(conn, feedback.id)
        assert updated is not None
        self.assertEqual(updated.body, "edited in place")

    def test_edit_bare_body_missing_id_exits_one_before_the_editor(self) -> None:
        composed = mock.Mock()
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_feedback.sys, "stdin", tty), mock.patch.object(
            cli_feedback, "compose_in_editor", composed
        ):
            rc, _, err = self._run("feedback", "edit", "9999", "--body")

        self.assertEqual(rc, 1)
        self.assertIn("feedback 9999 not found", err)
        composed.assert_not_called()

    def test_edit_without_body_flag_exits_one(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "unchanged")

        rc, _, err = self._run("feedback", "edit", str(feedback.id))

        self.assertEqual(rc, 1)
        self.assertIn("--body", err)
        with closing(db.connect(self.db_path)) as conn:
            unchanged = db.get_feedback(conn, feedback.id)
        assert unchanged is not None
        self.assertEqual(unchanged.body, "unchanged")

    def test_edit_empty_body_exits_one(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "keep me")

        rc, _, err = self._run("feedback", "edit", str(feedback.id), "--body", "   ")

        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_edit_missing_id_exits_one(self) -> None:
        rc, _, err = self._run("feedback", "edit", "9999", "--body", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_edit_json_output(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "first draft")

        rc, out, _ = self._run(
            "--json", "feedback", "edit", str(feedback.id), "--body", "json edit"
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], feedback.id)
        self.assertEqual(payload["entry_id"], entry.id)
        self.assertEqual(payload["body"], "json edit")
        self.assertNotEqual(payload["updated_at"], payload["created_at"])


class FeedbackRmTests(FeedbackTestBase):
    def test_rm_with_yes_deletes(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "delete me")

        rc, out, _ = self._run("--yes", "feedback", "rm", str(feedback.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"deleted feedback {feedback.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_feedback(conn, feedback.id))
            self.assertIsNotNone(db.get_entry(conn, entry.id))

    def test_rm_confirm_yes_deletes(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "delete me")

        with mock.patch.object(cli_feedback, "confirm", return_value=True):
            rc, out, _ = self._run("feedback", "rm", str(feedback.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"deleted feedback {feedback.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_feedback(conn, feedback.id))

    def test_rm_confirm_no_aborts(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "keep me")

        with mock.patch.object(cli_feedback, "confirm", return_value=False):
            rc, _, err = self._run("feedback", "rm", str(feedback.id))

        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_feedback(conn, feedback.id))

    def test_rm_json_output(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(entry.id, "delete me")

        rc, out, _ = self._run(
            "--yes", "--json", "feedback", "rm", str(feedback.id)
        )

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"deleted": True, "id": feedback.id})

    def test_rm_missing_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "feedback", "rm", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)


class FeedbackHelpTests(FeedbackTestBase):
    def test_feedback_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("feedback")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("list", out)
        self.assertIn("show", out)
        self.assertIn("edit", out)
        self.assertIn("rm", out)


if __name__ == "__main__":
    unittest.main()
