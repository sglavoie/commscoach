"""Tests for ``commscoach goal`` list/show/edit/close/rm."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from unittest import mock

from commscoach import db
from commscoach.cli import goals as cli_goals

from .cli_goal_case import GoalTestBase


class GoalCrudTests(GoalTestBase):
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

    def test_list_oldest_first_json_keeps_existing_shape(self) -> None:
        first = self._seed_goal("first")
        self._seed_goal("second")
        rc, out, err = self._run("goal", "list", "--oldest-first", "--limit", "1", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual([item["id"] for item in payload], [first.id])
        _, shown, _ = self._run("goal", "show", str(first.id), "--json")
        self.assertEqual(payload[0], json.loads(shown))

    def test_active_goal_age_uses_elapsed_days_since_creation(self) -> None:
        goal = self._seed_goal()
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        for stamp, expected in (
            ("2026-08-25T12:00:00+00:00", "38 days"),
            ("2026-10-01T12:00:00+00:00", "1 day"),
            ("2026-10-01T13:00:00+00:00", "0 days"),
            ("2026-10-01T06:00:00-06:00", "1 day"),
            ("2026-10-03T12:00:00+00:00", "0 days"),
        ):
            with self.subTest(stamp=stamp):
                rendered = cli_goals._format_goal_short(replace(goal, created_at=stamp), now=now)
                self.assertTrue(rendered.endswith(f"age: {expected}"), rendered)
        for variant in (replace(goal, status="done"), replace(goal, created_at="invalid")):
            self.assertNotIn("age:", cli_goals._format_goal_short(variant, now=now))

    def test_human_listing_includes_age(self) -> None:
        self._seed_goal()
        rc, out, err = self._run("goal", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("age: 0 days", out)

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
