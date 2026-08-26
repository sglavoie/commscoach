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


class FeedbackHelpTests(FeedbackTestBase):
    def test_feedback_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("feedback")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("list", out)
        self.assertIn("show", out)


if __name__ == "__main__":
    unittest.main()
