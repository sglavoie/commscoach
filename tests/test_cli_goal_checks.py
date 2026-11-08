"""Tests for recording goal checks on feedback and showing a goal's trail."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from commscoach import cli, db
from commscoach.cli import feedback as cli_feedback
from commscoach.cli import goals as cli_goals
from commscoach.cli.common import format_stamp


class GoalCheckCLITestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "checks.sqlite3"
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            db.create_person(conn, "me", is_self=True)
            self.goal = db.create_goal(conn, "Lead with the ask")
            self.other = db.create_goal(conn, "Slow down")
            self.entry = db.create_entry(conn, "talked", occurred_at="2026-11-01T12:00:00+00:00")

    def _run(self, *args: str, db_path: Path | None = None) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        argv = ["--db", str(db_path or self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(argv)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _check(self, occurred_at: str | None, *checks: tuple[int, str, str | None]) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "msg", occurred_at=occurred_at)
            return db.create_feedback(conn, entry.id, "note", goal_checks=list(checks))

    def _stored_checks(self) -> int:
        with closing(db.connect(self.db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM goal_checks").fetchone()[0]


class FeedbackAddGoalCheckTests(GoalCheckCLITestBase):
    def test_add_stores_checks_and_reports_one_line_each(self) -> None:
        rc, out, err = self._run(
            "feedback",
            "add",
            str(self.entry.id),
            "Good",
            "--goal-check",
            f"{self.goal.id}=advanced:Asked first",
            "--goal-check",
            f"{self.other.id}=HURT",
        )

        self.assertEqual(rc, 0, err)
        with closing(db.connect(self.db_path)) as conn:
            note = db.list_feedback(conn, self.entry.id)[0]
        self.assertEqual(
            out,
            f"created feedback {note.id}\n"
            f"[{note.id}] entry {self.entry.id} created {format_stamp(note.created_at)}\n"
            "---\n"
            "Good\n"
            f"goal {self.goal.id} advanced: Lead with the ask — Asked first\n"
            f"goal {self.other.id} hurt: Slow down\n",
        )

    def test_add_without_checks_keeps_its_output_and_gains_an_empty_json_list(self) -> None:
        rc, out, err = self._run("feedback", "add", str(self.entry.id), "Good")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("goal", out)
        self.assertEqual(out.count("\n"), 4)

        rc, out, err = self._run("feedback", "add", str(self.entry.id), "Again", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["goal_checks"], [])

    def test_add_json_lists_the_stored_checks(self) -> None:
        rc, out, err = self._run(
            "--json",
            "feedback",
            "add",
            str(self.entry.id),
            "Good",
            "--goal-check",
            f"{self.goal.id}=neutral : no chance to ask ",
        )

        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        [check] = payload["goal_checks"]
        self.assertEqual(
            {key: check[key] for key in ("feedback_id", "goal_id", "entry_id", "verdict", "note")},
            {
                "feedback_id": payload["id"],
                "goal_id": self.goal.id,
                "entry_id": self.entry.id,
                "verdict": "neutral",
                "note": "no chance to ask",
            },
        )
        self.assertEqual(check["at"], "2026-11-01T12:00:00+00:00")
        self.assertEqual(check["created_at"], payload["created_at"])

    def test_malformed_checks_are_usage_errors(self) -> None:
        for value, message in (
            ("advanced", "expected GOAL_ID=VERDICT"),
            ("x=advanced", "goal id must be a positive integer"),
            ("0=advanced", "goal id must be a positive integer"),
            (f"{self.goal.id}=won", "verdict must be advanced, hurt, or neutral"),
            (f"{self.goal.id}=advanced:  ", "note after ':' must not be empty"),
        ):
            err_buf = io.StringIO()
            with (
                self.subTest(value=value),
                redirect_stderr(err_buf),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "feedback",
                        "add",
                        str(self.entry.id),
                        "Good",
                        "--goal-check",
                        value,
                    ]
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(message, err_buf.getvalue())
        self.assertEqual(self._stored_checks(), 0)

    def test_duplicate_goal_is_a_usage_error(self) -> None:
        rc, out, err = self._run(
            "feedback",
            "add",
            str(self.entry.id),
            "Good",
            "--goal-check",
            f"{self.goal.id}=advanced",
            "--goal-check",
            f"{self.goal.id}=hurt",
        )
        self.assertEqual(rc, 2)
        self.assertIn(f"--goal-check names goal {self.goal.id} more than once", err)

    def test_unknown_goal_stores_nothing_and_never_opens_the_editor(self) -> None:
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch.object(cli_feedback, "compose_in_editor") as compose,
        ):
            rc, out, err = self._run(
                "feedback",
                "add",
                str(self.entry.id),
                "--goal-check",
                f"{self.goal.id}=advanced",
                "--goal-check",
                "999=hurt",
            )
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("999", err)
        compose.assert_not_called()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_feedback(conn, self.entry.id), ())
        self.assertEqual(self._stored_checks(), 0)

    def test_multi_line_note_from_stdin_body_is_rejected_atomically(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("Body from stdin")):
            rc, _, err = self._run(
                "feedback",
                "add",
                str(self.entry.id),
                "-",
                "--goal-check",
                f"{self.goal.id}=advanced:one\ntwo",
            )
        self.assertEqual(rc, 1)
        self.assertIn("single line", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_feedback(conn, self.entry.id), ())


class FeedbackReadAndEditGoalCheckTests(GoalCheckCLITestBase):
    def test_list_show_and_search_json_include_each_notes_checks(self) -> None:
        checked = self._check(None, (self.goal.id, "advanced", None))
        with closing(db.connect(self.db_path)) as conn:
            plain = db.create_feedback(conn, checked.entry_id, "plain note")

        payload = json.loads(self._run("feedback", "list", str(checked.entry_id), "--json")[1])
        self.assertEqual([len(item["goal_checks"]) for item in payload], [1, 0])
        for args in (
            ("feedback", "show", str(checked.id), "--json"),
            ("feedback", "show", str(checked.id), "--with-entry", "--json"),
        ):
            with self.subTest(args=args):
                shown = json.loads(self._run(*args)[1])
                self.assertEqual(shown["goal_checks"][0]["verdict"], "advanced")
        found = json.loads(self._run("feedback", "search", "--json")[1])
        self.assertEqual(
            {item["id"]: len(item["goal_checks"]) for item in found}, {checked.id: 1, plain.id: 0}
        )
        rc, out, _ = self._run("feedback", "show", str(checked.id))
        self.assertEqual(rc, 0)
        self.assertNotIn("advanced", out)

    def test_edit_replaces_checks_without_touching_the_body(self) -> None:
        note = self._check(None, (self.goal.id, "advanced", None), (self.other.id, "hurt", None))

        rc, out, err = self._run(
            "feedback", "edit", str(note.id), "--goal-check", f"{self.other.id}=neutral:fine"
        )

        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith(f"updated feedback {note.id}\n"))
        self.assertTrue(out.endswith(f"goal {self.other.id} neutral: Slow down — fine\n"))
        with closing(db.connect(self.db_path)) as conn:
            checks = db.list_feedback_goal_checks(conn, [note.id])[note.id]
            stored = db.get_feedback(conn, note.id)
        assert stored is not None
        self.assertEqual([(c.goal_id, c.verdict) for c in checks], [(self.other.id, "neutral")])
        self.assertEqual(stored.body, "note")
        self.assertNotEqual(stored.updated_at, note.updated_at)

    def test_edit_with_the_same_checks_is_unchanged(self) -> None:
        note = self._check(None, (self.goal.id, "advanced", "kept"))

        rc, out, err = self._run(
            "feedback", "edit", str(note.id), "--goal-check", f"{self.goal.id}=advanced:kept"
        )

        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith(f"unchanged feedback {note.id}\n"))

    def test_edit_clears_checks_and_rejects_conflicting_flags(self) -> None:
        note = self._check(None, (self.goal.id, "advanced", None))

        rc, out, err = self._run("--json", "feedback", "edit", str(note.id), "--clear-goal-checks")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["goal_checks"], [])

        rc, _, err = self._run(
            "feedback",
            "edit",
            str(note.id),
            "--clear-goal-checks",
            "--goal-check",
            f"{self.goal.id}=hurt",
        )
        self.assertEqual(rc, 2)
        self.assertIn("--clear-goal-checks cannot be combined with --goal-check", err)

        rc, _, err = self._run("feedback", "edit", str(note.id))
        self.assertEqual(rc, 1)
        self.assertIn("requires --body, --goal-check, or --clear-goal-checks", err)

    def test_edit_body_and_checks_commit_together(self) -> None:
        note = self._check(None, (self.goal.id, "advanced", None))

        rc, _, err = self._run(
            "feedback", "edit", str(note.id), "--body", "Rewritten", "--goal-check", "999=hurt"
        )

        self.assertEqual(rc, 1)
        self.assertIn("999", err)
        with closing(db.connect(self.db_path)) as conn:
            stored = db.get_feedback(conn, note.id)
            checks = db.list_feedback_goal_checks(conn, [note.id])[note.id]
        assert stored is not None
        self.assertEqual(stored.body, "note")
        self.assertEqual([c.verdict for c in checks], ["advanced"])

        rc, out, err = self._run(
            "--json",
            "feedback",
            "edit",
            str(note.id),
            "--body",
            "Rewritten",
            "--goal-check",
            f"{self.goal.id}=hurt",
        )
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["body"], "Rewritten")
        self.assertEqual([c["verdict"] for c in payload["goal_checks"]], ["hurt"])


class GoalTrailTests(GoalCheckCLITestBase):
    def _seed_trail(self) -> None:
        # Logged out of order on purpose: the trail follows when it happened.
        self._check("2026-11-05T12:00:00+00:00", (self.goal.id, "hurt", None))
        self._check("2026-11-02T12:00:00+00:00", (self.goal.id, "advanced", "Asked first"))
        self._check("2026-11-07T12:00:00+00:00", (self.goal.id, "advanced", None))
        self._check("2026-11-06T12:00:00+00:00", (self.goal.id, "neutral", None))

    def test_show_appends_glyph_strip_counts_and_one_line_per_check(self) -> None:
        self._seed_trail()
        _, before, _ = self._run("goal", "show", str(self.other.id))

        rc, out, err = self._run("goal", "show", str(self.goal.id))

        self.assertEqual(rc, 0, err)
        header, trail = out.split("\n---\n")
        self.assertTrue(header.endswith("Lead with the ask"))
        lines = trail.splitlines()
        self.assertEqual(lines[0], "progress: ✓✗·✓")
        self.assertEqual(lines[1], "advanced 2 · hurt 1 · neutral 1")
        self.assertEqual(len(lines), 6)
        self.assertRegex(lines[2], r"^\d{4}-\d{2}-\d{2} · entry 3 · advanced — Asked first$")
        self.assertRegex(lines[3], r"^\d{4}-\d{2}-\d{2} · entry 2 · hurt$")
        self.assertNotIn("progress", before)
        self.assertNotIn("---", before)

    def test_show_json_has_checks_and_summary(self) -> None:
        self._seed_trail()

        payload = json.loads(self._run("goal", "show", str(self.goal.id), "--json")[1])

        self.assertEqual(
            [check["verdict"] for check in payload["checks"]],
            ["advanced", "hurt", "neutral", "advanced"],
        )
        self.assertEqual(
            payload["check_summary"],
            {
                "advanced": 2,
                "hurt": 1,
                "neutral": 1,
                "total": 4,
                "last_verdict": "advanced",
                "last_at": "2026-11-07T12:00:00+00:00",
                "first_advanced_at": "2026-11-02T12:00:00+00:00",
            },
        )
        quiet = json.loads(self._run("goal", "show", str(self.other.id), "--json")[1])
        self.assertEqual(quiet["checks"], [])
        self.assertEqual(quiet["check_summary"]["total"], 0)
        self.assertIsNone(quiet["check_summary"]["last_verdict"])

    def test_list_rows_name_only_the_verdicts_that_occurred(self) -> None:
        _, before, _ = self._run("goal", "list")
        self._check(None, (self.goal.id, "advanced", None))
        self._check(None, (self.goal.id, "advanced", None))
        self._check(None, (self.goal.id, "hurt", None))

        rc, out, err = self._run("goal", "list")

        self.assertEqual(rc, 0, err)
        rows = out.splitlines()
        self.assertTrue(rows[1].endswith("· advanced 2×, hurt 1×"), rows[1])
        self.assertEqual(rows[0], before.splitlines()[0])
        search = self._run("goal", "search", "--query", "lead")[1]
        self.assertIn("· advanced 2×, hurt 1×", search)

    def test_list_json_items_carry_check_summary(self) -> None:
        self._check(None, (self.other.id, "neutral", None))

        payload = json.loads(self._run("goal", "list", "--json")[1])

        by_id = {item["id"]: item["check_summary"] for item in payload}
        self.assertEqual(by_id[self.other.id]["neutral"], 1)
        self.assertEqual(by_id[self.goal.id]["total"], 0)

    def test_close_prints_the_journey_only_when_checks_exist(self) -> None:
        self._seed_trail()

        rc, out, err = self._run("goal", "close", str(self.goal.id), "--outcome", "Done")

        self.assertEqual(rc, 0, err)
        last = out.splitlines()[-1]
        self.assertRegex(
            last,
            r"^Closed after 0 days · 4 checks \(2 advanced, 1 hurt, 1 neutral\)"
            r" · first win \d{4}-\d{2}-\d{2}$",
        )
        rc, out, err = self._run("goal", "close", str(self.other.id), "--dropped")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines()[-1], "Slow down")

    def test_close_json_has_summary_but_no_journey_line(self) -> None:
        self._check(None, (self.goal.id, "hurt", None))

        rc, out, err = self._run("--json", "goal", "close", str(self.goal.id))

        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["check_summary"]["hurt"], 1)

    def test_journey_counts_days_and_names_dropped_goals(self) -> None:
        goal = replace(
            self.goal,
            status="dropped",
            created_at="2026-10-10T12:00:00+00:00",
            closed_at="2026-11-02T13:00:00+00:00",
        )
        summary = db.GoalCheckSummary(hurt=1, last_verdict="hurt", last_at="2026-11-01")

        self.assertEqual(
            cli_goals._format_journey(goal, summary), "Dropped after 23 days · 1 check (1 hurt)"
        )


class GoalCheckContextExportTests(GoalCheckCLITestBase):
    def test_context_goals_carry_summary_and_prior_feedback_its_checks(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(
                conn, self.entry.id, "earlier", goal_checks=[(self.goal.id, "advanced", None)]
            )

        payload = json.loads(self._run("context", str(self.entry.id))[1])

        by_id = {goal["id"]: goal["check_summary"] for goal in payload["goals"]}
        self.assertEqual(by_id[self.goal.id]["advanced"], 1)
        self.assertEqual(by_id[self.other.id]["total"], 0)
        [prior] = payload["prior_feedback"]
        self.assertEqual(prior["goal_checks"][0]["goal_id"], self.goal.id)

    def test_markdown_export_lists_checks_under_their_note_only(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, self.entry.id, "plain")
        _, before, _ = self._run("export")
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(
                conn,
                self.entry.id,
                "checked",
                goal_checks=[
                    (self.goal.id, "advanced", "Asked first"),
                    (self.other.id, "hurt", None),
                ],
            )

        rc, out, err = self._run("export")

        self.assertEqual(rc, 0, err)
        self.assertNotIn("Goal checks", before)
        self.assertIn(
            "checked\n\n**Goal checks**\n\n"
            f"- Goal {self.goal.id} (Lead with the ask): advanced — Asked first\n"
            f"- Goal {self.other.id} (Slow down): hurt\n",
            out,
        )
        self.assertEqual(out.count("Goal checks"), 1)

    def test_import_summary_names_goal_checks_only_when_there_are_some(self) -> None:
        archive = self.tmpdir / "archive.json"
        target = self.tmpdir / "target.sqlite3"
        self._run("export", "--format", "json", "--out", str(archive))
        db.init_db(target)
        rc, out, err = self._run("import", str(archive), "--dry-run", db_path=target)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("goal check", out)

        self._check(None, (self.goal.id, "advanced", None))
        self._run("export", "--format", "json", "--out", str(archive), "--yes")
        rc, out, err = self._run("import", str(archive), db_path=target)
        self.assertEqual(rc, 0, err)
        self.assertIn("2 goals, 0 person notes, 1 goal check from", out)
        with closing(db.connect(target)) as conn:
            self.assertEqual(db.goal_check_summaries(conn, [self.goal.id])[self.goal.id].total, 1)


if __name__ == "__main__":
    unittest.main()
