"""Tests for ``commscoach goal`` outcomes, search, and add/editor flows."""

from __future__ import annotations

import io
import json
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import goals as cli_goals

from .cli_goal_case import GoalTestBase


class GoalOutcomeSearchTests(GoalTestBase):
    def test_edit_outcome_preserves_status_and_closure(self) -> None:
        for status in ("active", "done", "dropped"):
            with self.subTest(status=status):
                goal = self._seed_goal()
                with closing(db.connect(self.db_path)) as conn:
                    before = db.update_goal(conn, goal.id, status=status)
                for flags, expected in (
                    (("--outcome", "Corrected result"), "Corrected result"),
                    (("--clear-outcome",), None),
                ):
                    rc, out, err = self._run("goal", "edit", str(goal.id), *flags, "--json")
                    self.assertEqual(rc, 0, err)
                    payload = json.loads(out)
                    self.assertEqual(payload["outcome"], expected)
                    self.assertEqual(payload["status"], before.status)
                    self.assertEqual(payload["closed_at"], before.closed_at)

    def test_edit_outcome_editor_and_input_sources(self) -> None:
        goal = self._seed_goal()
        with closing(db.connect(self.db_path)) as conn:
            db.close_goal(conn, goal.id, outcome="Original", dropped=True)
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch.object(
                cli_goals,
                "compose_in_editor",
                return_value="Revised",
            ) as compose,
        ):
            rc, out, err = self._run("goal", "edit", str(goal.id), "--outcome", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(compose.call_args.kwargs["template"], "Original")
        self.assertEqual(json.loads(out)["outcome"], "Revised")
        self.assertEqual(json.loads(out)["status"], "dropped")
        source = self.tmpdir / "outcome.txt"
        source.write_text("From file", encoding="utf-8")
        rc, out, err = self._run("goal", "edit", str(goal.id), "--outcome", f"@{source}", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["outcome"], "From file")
        rc, out, err = self._run(
            "goal",
            "edit",
            str(goal.id),
            "--outcome",
            "-",
            "--json",
            stdin=io.StringIO("From stdin"),
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["outcome"], "From stdin")

    def test_edit_validates_before_opening_any_editor(self) -> None:
        goal = self._seed_goal()
        for field in ("--detail", "--outcome"):
            for flags in (
                ("--title", " "),
                ("--person", "missing"),
                ("--outcome", "--clear-outcome"),
            ):
                with (
                    self.subTest(field=field, flags=flags),
                    mock.patch.object(
                        cli_goals,
                        "compose_in_editor",
                    ) as compose,
                ):
                    if field == flags[0]:
                        # A repeated --outcome is an argparse usage error.
                        with self.assertRaises(SystemExit) as raised:
                            self._run("goal", "edit", str(goal.id), field, *flags)
                        self.assertEqual(raised.exception.code, 2)
                    else:
                        rc, _, _ = self._run("goal", "edit", str(goal.id), field, *flags)
                        # Conflicting flags are usage errors, like argparse's own.
                        self.assertEqual(rc, 2 if "--clear-outcome" in flags else 1)
                    compose.assert_not_called()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_goal(conn, goal.id), goal)

    def test_search_and_limited_list_footers_respect_filters(self):
        self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            for _ in range(3):
                goal = db.create_goal(
                    conn, "Try 10%_! less", detail="10%_! detail", person_handle="alice"
                )
                db.close_goal(conn, goal.id, outcome="10%_! outcome")
            db.create_goal(conn, "Try 10%_! less", person_handle="alice")
            goal = db.create_goal(conn, "Try 10%_! less")
            db.close_goal(conn, goal.id)
            goal = db.create_goal(conn, "Try 100xyz! less", person_handle="alice")
            db.close_goal(conn, goal.id)
        for command, total in ((("search", "--query", "10%_!"), 3), (("list",), 4)):
            flags = ("goal", *command, "--status", "done", "--person", "ALICE")
            for limit in (0, 1, total, total + 1):
                with self.subTest(command=command, limit=limit):
                    rc, out, err = self._run(*flags, "--limit", str(limit), "--oldest-first")
                    self.assertEqual(rc, 0, err)
                    if limit < total:
                        self.assertIn(f"Showing {limit} of {total} matching goals.", err)
                    else:
                        self.assertNotIn("Showing", out + err)
                    rc, out, err = self._run(*flags, "--limit", str(limit), "--json")
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(len(json.loads(out)), min(limit, total))
        self.assertNotIn("Showing", "".join(self._run("goal", "list", "--status", "all")[1:]))
        self.assertEqual(self._run("goal", "search", "--query", "missing")[1], "(no goals)\n")

    def test_outcome_list_and_search_show_full_text_without_duplicate_excerpt(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Practice", detail="Try this")
            db.close_goal(conn, goal.id, outcome="Worked well\nTry again next week")
            empty = db.create_goal(conn, "Practice without outcome")
            db.close_goal(conn, empty.id)
            dropped = db.create_goal(conn, "Practice dropped")
            db.close_goal(conn, dropped.id, dropped=True, outcome="No longer relevant")
        for command in (
            ("list", "--status", "all"),
            ("search", "--query", "Practice"),
            ("search", "--query", "Worked"),
        ):
            with self.subTest(command=command):
                rc, out, err = self._run("goal", *command, "--with-outcome", "--with-detail")
                self.assertEqual(rc, 0, err)
                self.assertIn("  outcome:\n    Worked well\n    Try again next week", out)
                self.assertEqual(out.count("Worked well"), 1)
                self.assertIn("  Try this", out)
                self.assertEqual(
                    self._run("goal", *command, "--json"),
                    self._run("goal", *command, "--with-outcome", "--json"),
                )
        out = self._run("goal", "list", "--status", "all", "--with-outcome")[1]
        self.assertIn("(no outcome recorded)", out)
        self.assertIn("No longer relevant", out)
        self.assertNotIn("Worked well", self._run("goal", "list", "--status", "done")[1])

    def test_person_filter_distinguishes_unknown_from_empty(self) -> None:
        self._seed_person("alex")
        for command in (("list",), ("search", "--query", "Practice")):
            for output in ((), ("--json",)):
                with self.subTest(command=command, output=output):
                    rc, out, err = self._run(
                        "goal", *command, "--person", "typo", "--limit", "0", *output
                    )
                    self.assertEqual(rc, 1)
                    self.assertIn("typo", err)
                    self.assertEqual(out, "")
                    rc, out, err = self._run("goal", *command, "--person", "ALEX", *output)
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(out, "[]\n" if output else "(no goals)\n")

    def test_list_with_detail_keeps_json_and_default_output(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "Practice", detail="First step\nSecond step")
        rc, out, err = self._run("goal", "list", "--with-detail")
        self.assertEqual(rc, 0, err)
        self.assertIn("  First step\n  Second step", out)
        self.assertNotIn("First step", self._run("goal", "list")[1])
        self.assertEqual(
            self._run("goal", "list", "--json"),
            self._run("goal", "list", "--with-detail", "--json"),
        )

    def test_search_finds_outcomes_and_shows_matching_passages(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Practice", detail="Details")
            db.close_goal(
                conn, goal.id, outcome="Unrelated introduction. " * 30 + "Saved 10% effort!"
            )
            db.create_goal(conn, "Saved 100% effort")
        rc, out, err = self._run("goal", "search", "--query", "10%")
        self.assertEqual(rc, 0, err)
        self.assertIn("outcome:", out)
        self.assertIn("Saved 10% effort!", out)
        self.assertNotIn("Saved 100%", out)
        rc, out, err = self._run("goal", "search", "--query", "10%", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([row["id"] for row in json.loads(out)], [goal.id])
        self.assertTrue(json.loads(out)[0]["outcome"].startswith("Unrelated"))
        self.assertEqual(
            self._run("goal", "search", "--query", "10%", "--status", "active")[1], "(no goals)\n"
        )
        self.assertEqual(self._run("goal", "search", "--query", "   ")[0], 1)
        self.assertEqual(
            self._run("goal", "search", "--query", "10%", "--limit", "0", "--json")[1], "[]\n"
        )

    def test_editor_prefills_goal_detail_and_outcome(self) -> None:
        goal = self._seed_goal()
        for field, command in (("detail", "edit"), ("outcome", "close")):
            for original in (None, "Existing text"):
                with self.subTest(field=field, original=original):
                    with closing(db.connect(self.db_path)) as conn:
                        if field == "detail":
                            db.update_goal(conn, goal.id, detail=original)
                        elif original is not None:
                            db.close_goal(conn, goal.id, outcome=original)
                        before = db.get_goal(conn, goal.id)
                    with (
                        mock.patch("sys.stdin.isatty", return_value=True),
                        mock.patch.object(
                            cli_goals,
                            "compose_in_editor",
                            return_value="Revised text",
                        ) as compose,
                    ):
                        rc, out, err = self._run(
                            "goal",
                            command,
                            str(goal.id),
                            f"--{field}",
                            "--json",
                        )
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(compose.call_args.kwargs["template"], original or "")
                    payload = json.loads(out)
                    self.assertEqual(payload[field], "Revised text")
                    if field == "outcome":
                        self.assertEqual(payload["status"], "done")
                        if before.closed_at is not None:
                            self.assertEqual(payload["closed_at"], before.closed_at)

    def test_add_detail_editor_saves_goal_with_references(self) -> None:
        person = self._seed_person()
        review = self._seed_review()
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch.object(
                cli_goals,
                "compose_in_editor",
                return_value="Practice in three meetings",
            ) as compose,
        ):
            rc, out, err = self._run(
                "goal",
                "add",
                "Lead with the ask",
                "--detail",
                "--person",
                "ALEX",
                "--review",
                str(review.id),
                "--json",
            )
        self.assertEqual(rc, 0, err)
        self.assertEqual(compose.call_args.kwargs["template"], "")
        goal = json.loads(out)
        self.assertEqual(goal["detail"], "Practice in three meetings")
        self.assertEqual(goal["person"]["id"], person.id)
        self.assertEqual(goal["review_id"], review.id)

    def test_add_validates_before_opening_editor(self) -> None:
        cases = (
            (("Practice", "--person", "unknown"), "unknown"),
            (("Practice", "--review", "9999"), "9999"),
            (("   ",), "title"),
            (("Practice", "--db", str(self.tmpdir / "missing.sqlite3")), "init"),
        )
        for flags, message in cases:
            with (
                self.subTest(flags=flags),
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch.object(
                    cli_goals,
                    "compose_in_editor",
                ) as compose,
            ):
                rc, out, err = self._run("goal", "add", *flags, "--detail")
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)
                compose.assert_not_called()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_goals(conn), ())

    def test_add_detail_editor_requires_tty_but_omitted_detail_stays_optional(self) -> None:
        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch.object(
                cli_goals,
                "compose_in_editor",
            ) as compose,
        ):
            rc, _, err = self._run("goal", "add", "Practice", "--detail")
            self.assertEqual(rc, 1)
            self.assertIn("detail required", err)
            rc, out, err = self._run("goal", "add", "Practice", "--json")
            self.assertEqual(rc, 0, err)
            self.assertIsNone(json.loads(out)["detail"])
            compose.assert_not_called()

    def test_goal_editor_requires_tty_and_existing_goal(self) -> None:
        goal = self._seed_goal()
        for command, field in (("edit", "detail"), ("close", "outcome")):
            for goal_id, tty, message in (
                (goal.id, False, f"{field} required"),
                (9999, True, "9999"),
            ):
                with (
                    self.subTest(command=command, goal_id=goal_id),
                    mock.patch(
                        "sys.stdin.isatty",
                        return_value=tty,
                    ),
                    mock.patch.object(cli_goals, "compose_in_editor") as compose,
                ):
                    rc, out, err = self._run("goal", command, str(goal_id), f"--{field}")
                    self.assertEqual(rc, 1)
                    self.assertEqual(out, "")
                    self.assertIn(message, err)
                    compose.assert_not_called()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_goal(conn, goal.id), goal)

    def test_clear_detail_conflicts_with_editor_before_opening(self) -> None:
        goal = self._seed_goal()
        with mock.patch.object(cli_goals, "compose_in_editor") as compose:
            rc, _, err = self._run(
                "goal",
                "edit",
                str(goal.id),
                "--detail",
                "--clear-detail",
            )
        self.assertEqual(rc, 2)
        self.assertIn("cannot be combined", err)
        compose.assert_not_called()

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
