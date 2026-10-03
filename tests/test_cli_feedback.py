"""Tests for the ``commscoach feedback`` subcommands."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db
from commscoach.cli import feedback as cli_feedback


class FeedbackTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "feedback.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self,
        *args: str,
        stdin: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(full_args)
            else:
                rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_entry(self, body: str = "seed body") -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(conn, body)

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)


class FeedbackAddTests(FeedbackTestBase):
    def test_add_creates_feedback(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run(
            "feedback", "add", str(entry.id), "good listening"
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"created feedback 1", out)
        self.assertIn("good listening", out)
        with closing(db.connect(self.db_path)) as conn:
            items = db.list_feedback(conn, entry.id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].body, "good listening")

    def test_add_unknown_entry_exits_one(self) -> None:
        rc, _, err = self._run(
            "feedback", "add", "9999", "body"
        )
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_add_without_body_on_non_tty_exits_one_without_writing(self) -> None:
        entry = self._seed_entry()
        with mock.patch.object(cli_feedback.sys, "stdin", io.StringIO()):
            rc, _, err = self._run("feedback", "add", str(entry.id))
        self.assertEqual(rc, 1)
        self.assertIn("body required", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_feedback(conn, entry.id), ())

    def test_add_empty_body_exits_one(self) -> None:
        entry = self._seed_entry()
        rc, _, err = self._run(
            "feedback", "add", str(entry.id), "   "
        )
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_add_body_from_stdin(self) -> None:
        entry = self._seed_entry()
        rc, _, _ = self._run(
            "feedback",
            "add",
            str(entry.id),
            "-",
            stdin=io.StringIO("piped feedback\n"),
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            items = db.list_feedback(conn, entry.id)
        self.assertEqual(items[0].body, "piped feedback\n")

    def test_add_body_from_file(self) -> None:
        entry = self._seed_entry()
        body_file = self.tmpdir / "feedback.txt"
        body_file.write_text("from file feedback", encoding="utf-8")
        rc, _, _ = self._run(
            "feedback", "add", str(entry.id), f"@{body_file}"
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            items = db.list_feedback(conn, entry.id)
        self.assertEqual(items[0].body, "from file feedback")

    def test_add_body_from_missing_file_exits_one(self) -> None:
        entry = self._seed_entry()
        rc, _, err = self._run(
            "feedback", "add", str(entry.id), "@/no/such/file.txt"
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)

    def test_add_json_output(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run(
            "--json", "feedback", "add", str(entry.id), "json body"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["entry_id"], entry.id)
        self.assertEqual(payload["body"], "json body")
        self.assertIn("created_at", payload)
        self.assertIn("updated_at", payload)


class FeedbackListTests(FeedbackTestBase):
    def test_list_empty_prints_marker(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn("(no feedback)", out)

    def test_list_oldest_first(self) -> None:
        entry = self._seed_entry()
        first = self._seed_feedback(entry.id, "first")
        second = self._seed_feedback(entry.id, "second")
        third = self._seed_feedback(entry.id, "third")
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        idx_first = out.find(f"[{first.id}]")
        idx_second = out.find(f"[{second.id}]")
        idx_third = out.find(f"[{third.id}]")
        self.assertGreater(idx_second, idx_first)
        self.assertGreater(idx_third, idx_second)

    def test_list_unknown_entry_exits_one(self) -> None:
        rc, _, err = self._run("feedback", "list", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_list_json_empty(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run("--json", "feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_json_payload(self) -> None:
        entry = self._seed_entry()
        first = self._seed_feedback(entry.id, "first")
        second = self._seed_feedback(entry.id, "second")
        rc, out, _ = self._run("--json", "feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([row["id"] for row in payload], [first.id, second.id])
        self.assertEqual(payload[0]["body"], "first")

    def test_list_skips_markdown_headings(self) -> None:
        entry = self._seed_entry()
        feedback = self._seed_feedback(
            entry.id,
            "## Situation\nStandup update ran long.\n\n## Impact\nThe lead lost the thread.",
        )
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertNotIn("## Situation", out)
        self.assertNotIn("## Impact", out)
        self.assertIn("Standup update ran long.", out)
        self.assertIn(f"[{feedback.id}]", out)
        self.assertIn(f"entry {entry.id}", out)

    def test_list_caps_preview_at_three_lines_with_ellipsis(self) -> None:
        entry = self._seed_entry()
        self._seed_feedback(entry.id, "## Situation\n" + "word " * 200)
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        preview = [line for line in out.splitlines() if line.startswith("    ")]
        self.assertEqual(len(preview), 3)
        self.assertTrue(preview[-1].endswith("…"))

    def test_list_heading_only_body_falls_back(self) -> None:
        entry = self._seed_entry()
        self._seed_feedback(entry.id, "## Situation\n### Impact\n")
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn("    ## Situation", out)

    def test_list_separates_blocks_with_a_blank_line(self) -> None:
        entry = self._seed_entry()
        self._seed_feedback(entry.id, "## Situation\nfirst note")
        self._seed_feedback(entry.id, "## Situation\nsecond note")
        rc, out, _ = self._run("feedback", "list", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn("first note\n\n[", out)
        self.assertIn("second note", out)


class FeedbackSearchTests(FeedbackTestBase):
    def test_search_filters_feedback_text_and_person_before_limit(self):
        with closing(db.connect(self.db_path)) as conn:
            db.create_person(conn, "alice")
            db.create_person(conn, "bob")
            entry = db.create_entry(conn, "meeting", people=("alice", "bob"))
            other = db.create_entry(conn, "Lead with the ask", people=("bob",))
            first = db.create_feedback(conn, entry.id, "Lead with the ask")
            second = db.create_feedback(conn, entry.id, "LEAD WITH THE ASK next time")
            db.create_feedback(conn, other.id, "Lead with the ask")
            db.create_feedback(conn, entry.id, "Listen more")
            with conn:
                conn.execute("UPDATE feedback SET created_at = '2026-01-01T00:00:00+00:00'")
        for limit, expected in ((0, []), (1, [second.id]), (20, [second.id, first.id])):
            rc, out, err = self._run(
                "feedback", "search", "--query", "lead with the ask", "--person", "ALICE",
                "--limit", str(limit), "--json",
            )
            self.assertEqual(rc, 0, err)
            self.assertEqual([f["id"] for f in json.loads(out)], expected)
        rc, out, err = self._run("feedback", "search", "--query", "Listen more")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"entry {entry.id}", out)
        self.assertIn("Listen more", out)

    def test_search_treats_wildcards_and_escape_characters_literally(self):
        entry = self._seed_entry()
        exact = self._seed_feedback(entry.id, "Improve 10%_! next time")
        self._seed_feedback(entry.id, "Improve 100xx next time")
        for query in ("%", "_", "!", "%_!"):
            rc, out, err = self._run("feedback", "search", "--query", query, "--json")
            self.assertEqual(rc, 0, err)
            self.assertEqual([f["id"] for f in json.loads(out)], [exact.id])

    def test_search_empty_results_and_invalid_input(self):
        rc, out, err = self._run("feedback", "search", "--query", "missing")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "(no feedback)")
        for flags, code, message in (
            (("--query", "  "), 1, "non-empty"),
            (("--query", "ask", "--person", "ghost"), 1, "ghost"),
        ):
            rc, _, err = self._run("feedback", "search", *flags)
            self.assertEqual(rc, code, err)
            self.assertIn(message, err)
        with self.assertRaises(SystemExit) as raised:
            self._run("feedback", "search", "--query", "ask", "--limit", "-1")
        self.assertEqual(raised.exception.code, 2)


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
