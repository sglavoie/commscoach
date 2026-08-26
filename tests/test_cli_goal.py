"""Tests for the ``commscoach goal`` command group."""

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
from commscoach.cli import goals as cli_goals


class GoalCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "goals.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self, *args: str, stdin: io.StringIO | None = None
    ) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        argv = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is None:
                rc = cli.main(argv)
            else:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(argv)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_person(self, handle: str = "alex") -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle)

    def _seed_review(self) -> db.Review:
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "entry")
            return db.create_review(conn, "review", [entry.id])

    def _seed_goal(self, title: str = "Practice brevity") -> db.Goal:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_goal(conn, title)

    def test_add_human_with_detail_person_and_review(self) -> None:
        person = self._seed_person()
        review = self._seed_review()
        detail_file = self.tmpdir / "detail.md"
        detail_file.write_text("Try this in the next 1:1", encoding="utf-8")

        rc, out, _ = self._run(
            "goal",
            "add",
            "Ask one question",
            "--detail",
            f"@{detail_file}",
            "--person",
            person.handle,
            "--review",
            str(review.id),
        )

        self.assertEqual(rc, 0)
        self.assertIn("created goal 1", out)
        self.assertIn("Ask one question", out)
        self.assertIn("Try this in the next 1:1", out)

    def test_add_json_and_stdin_detail(self) -> None:
        rc, out, _ = self._run(
            "--json",
            "goal",
            "add",
            "Piped goal",
            "--detail",
            "-",
            stdin=io.StringIO("piped detail\n"),
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["title"], "Piped goal")
        self.assertEqual(payload["detail"], "piped detail\n")
        self.assertTrue(payload["is_open"])
        self.assertIsNone(payload["person"])

    def test_add_unknown_references_exit_one_without_write(self) -> None:
        rc, _, err = self._run("goal", "add", "goal", "--person", "missing")
        self.assertEqual(rc, 1)
        self.assertIn("missing", err)
        rc, _, err = self._run("goal", "add", "goal", "--review", "999")
        self.assertEqual(rc, 1)
        self.assertIn("999", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_goals(conn), ())

    def test_list_human_defaults_active_and_filters_person(self) -> None:
        self._seed_person()
        with closing(db.connect(self.db_path)) as conn:
            kept = db.create_goal(conn, "kept", person_handle="alex")
            hidden = db.create_goal(conn, "hidden")
            db.close_goal(conn, hidden.id)
        rc, out, _ = self._run("goal", "list", "--person", "alex")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{kept.id}] active kept @alex", out)
        self.assertNotIn("hidden", out)

    def test_list_json_all_statuses_and_limit(self) -> None:
        first = self._seed_goal("first")
        second = self._seed_goal("second")
        with closing(db.connect(self.db_path)) as conn:
            db.close_goal(conn, second.id, dropped=True)
        rc, out, _ = self._run(
            "--json", "goal", "list", "--status", "all", "--limit", "2"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([row["id"] for row in payload], [first.id, second.id])
        self.assertEqual(payload[1]["status"], "dropped")

    def test_show_human(self) -> None:
        goal = self._seed_goal("Visible goal")
        rc, out, _ = self._run("goal", "show", str(goal.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"[{goal.id}]", out)
        self.assertIn("Visible goal", out)

    def test_show_json(self) -> None:
        goal = self._seed_goal("JSON goal")
        rc, out, _ = self._run("--json", "goal", "show", str(goal.id))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["id"], goal.id)

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("goal", "show", "999")
        self.assertEqual(rc, 1)
        self.assertIn("goal 999 not found", err)

    def test_edit_human_updates_and_clears(self) -> None:
        self._seed_person()
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(
                conn, "old", detail="old detail", person_handle="alex"
            )
        rc, out, _ = self._run(
            "goal",
            "edit",
            str(goal.id),
            "--title",
            "new",
            "--clear-detail",
            "--clear-person",
            "--status",
            "done",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"updated goal {goal.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_goal(conn, goal.id)
        assert updated is not None
        self.assertEqual(updated.title, "new")
        self.assertIsNone(updated.detail)
        self.assertIsNone(updated.person)
        self.assertEqual(updated.status, "done")
        self.assertIsNotNone(updated.closed_at)

    def test_edit_json_reads_detail(self) -> None:
        goal = self._seed_goal()
        rc, out, _ = self._run(
            "--json",
            "goal",
            "edit",
            str(goal.id),
            "--detail",
            "new detail",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["detail"], "new detail")

    def test_edit_mutual_exclusion_and_empty_edit_exit_one(self) -> None:
        goal = self._seed_goal()
        rc, _, err = self._run(
            "goal", "edit", str(goal.id), "--detail", "x", "--clear-detail"
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot be combined", err)
        rc, _, err = self._run("goal", "edit", str(goal.id))
        self.assertEqual(rc, 1)
        self.assertIn("requires at least one", err)

    def test_close_human_reads_outcome_from_stdin(self) -> None:
        goal = self._seed_goal()
        rc, out, _ = self._run(
            "goal",
            "close",
            str(goal.id),
            "--outcome",
            "-",
            stdin=io.StringIO("It worked\n"),
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"closed goal {goal.id}", out)
        self.assertIn("It worked", out)

    def test_close_json_dropped(self) -> None:
        goal = self._seed_goal()
        rc, out, _ = self._run(
            "--json", "goal", "close", str(goal.id), "--dropped"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "dropped")
        self.assertFalse(payload["is_open"])

    def test_rm_human_confirms_and_deletes(self) -> None:
        goal = self._seed_goal()
        with mock.patch.object(cli_goals, "confirm", return_value=True):
            rc, out, _ = self._run("goal", "rm", str(goal.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"deleted goal {goal.id}", out)

    def test_rm_json_with_yes(self) -> None:
        goal = self._seed_goal()
        rc, out, _ = self._run("--yes", "--json", "goal", "rm", str(goal.id))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"deleted": True, "id": goal.id})

    def test_rm_rejected_confirmation_keeps_goal(self) -> None:
        goal = self._seed_goal()
        with mock.patch.object(cli_goals, "confirm", return_value=False):
            rc, _, err = self._run("goal", "rm", str(goal.id))
        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_goal(conn, goal.id))

    def test_goal_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("goal")
        self.assertEqual(rc, 0)
        for verb in ("add", "list", "show", "edit", "close", "rm"):
            self.assertIn(verb, out)


if __name__ == "__main__":
    unittest.main()
