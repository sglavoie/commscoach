"""CLI tests for coaching that comes back: plans, rhythm, recap, coach, and polish."""

from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import datetime, timedelta
from unittest import mock

from commscoach import db
from commscoach.cli import coach, insights, style

from .cli_entry_case import EntryTestBase

PLAN = "## Situation\nA 1:1.\n\n## Try next time\n- Open with the **ask**.\n- Then context.\n"


def _pretty():
    return mock.patch.dict(os.environ, {"COMMSCOACH_PRETTY": "1", "NO_COLOR": "1"})


def _ago(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


class InsightHelperTests(EntryTestBase):
    def test_plain_inline_and_excerpt(self) -> None:
        self.assertEqual(insights.plain_inline("**Lead** with `the` *ask*"), "Lead with the ask")
        self.assertEqual(
            insights.excerpt("## Situation\n\n- You **asked** late."), "You asked late."
        )
        self.assertEqual(
            insights.flatten("## What worked\n- Clear agenda.\n\nProse."),
            "What worked: • Clear agenda. Prose.",
        )

    def test_key_advice_prefers_try_next_time(self) -> None:
        self.assertEqual(insights.key_advice(PLAN), "Open with the ask.")
        self.assertEqual(insights.key_advice("## Situation\nJust prose."), "Just prose.")

    def test_rhythm_is_relative_to_the_relationship(self) -> None:
        now = datetime.now().astimezone()
        weekly = tuple(_ago(d) for d in (35, 28, 21))
        beat = insights.rhythm(weekly, now=now)
        assert beat is not None
        self.assertEqual(beat.usual_gap_days, 7)
        self.assertTrue(beat.quiet)  # 21 days >= 3 x 7
        self.assertEqual(beat.usual(), "every ~7 days")
        daily = tuple(_ago(d) for d in (12, 11, 10))
        beat = insights.rhythm(daily, now=now)
        assert beat is not None
        self.assertFalse(beat.quiet)  # under the two-week floor
        self.assertIsNone(insights.rhythm(weekly[:2], now=now))

    def test_advanced_run_skips_neutral_and_stops_at_hurt(self) -> None:
        def checks(*verdicts):
            return [mock.Mock(verdict=v) for v in verdicts]

        self.assertEqual(
            insights.advanced_run(checks("hurt", "advanced", "neutral", "advanced")), 2
        )
        self.assertEqual(insights.advanced_run(checks("advanced", "hurt")), 0)
        self.assertTrue(insights.ready_to_close(checks(*["advanced"] * insights.GRADUATE_RUN)))

    def test_short_when(self) -> None:
        now = datetime(2026, 11, 8, 15, 0).astimezone()
        self.assertEqual(style.short_when(now.isoformat(), now=now), "just now")
        three = (now - timedelta(days=3)).replace(hour=10).isoformat()
        self.assertEqual(style.short_when(three, now=now), "3d ago")
        self.assertEqual(style.short_when("2026-10-01T00:00:00", now=now), "Oct 1")


class PlanComesBackTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("me", is_self=True)
        self._create_person("jdoe")
        entry = self._seed_entry("budget talk", people=("jdoe",), occurred_at=_ago(3))
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, entry.id, PLAN)

    def test_log_recalls_the_plan_for_that_person(self) -> None:
        with _pretty():
            rc, out, err = self._run("log", "Chat", "with", "@jdoe")
        self.assertEqual(rc, 0, err)
        self.assertIn("Last time you planned: Open with the ask.", out)
        self.assertIn("/commscoach 2", out)  # no claude on PATH in tests

    def test_home_shows_the_current_experiment(self) -> None:
        with _pretty():
            rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("Your current experiment", out)
        self.assertIn("→ Open with the ask.", out)
        self.assertIn("from coaching on entry 1 with @jdoe", out)

    def test_prep_details_are_one_line_per_thing(self) -> None:
        with _pretty():
            rc, out, err = self._run("person", "prep", "jdoe")
        self.assertEqual(rc, 0, err)
        self.assertIn("Recent conversations", out)
        self.assertIn("↳ Open with the ask.", out)
        self.assertNotIn("(no feedback)", out)
        self.assertNotIn("Feedback:", out)
        self.assertIn("entry show 1 --with-feedback", out)


class GraduationTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("me", is_self=True)
        with closing(db.connect(self.db_path)) as conn:
            self.goal = db.create_goal(conn, "Lead with the ask")
            first = self._seed_entry("rambled", occurred_at=_ago(30))
            db.create_feedback(
                conn, first.id, "x", goal_checks=[(self.goal.id, "hurt", "Asked late")]
            )
            for days in range(20, 20 - insights.GRADUATE_RUN, -1):
                entry = self._seed_entry("better", occurred_at=_ago(days))
                db.create_feedback(
                    conn, entry.id, "x", goal_checks=[(self.goal.id, "advanced", "Asked first")]
                )

    def test_home_suggests_closing_a_goal_that_stuck(self) -> None:
        with _pretty():
            _, out, _ = self._run()
        self.assertIn(f"{insights.GRADUATE_RUN} wins in a row", out)
        self.assertIn(f"goal close {self.goal.id} --outcome", out)

    def test_feedback_add_counts_the_run(self) -> None:
        entry = self._seed_entry("again")
        with _pretty():
            rc, out, err = self._run(
                "feedback", "add", str(entry.id), "ok", "--goal-check", f"{self.goal.id}=advanced"
            )
        self.assertEqual(rc, 0, err)
        self.assertIn(f"{insights.GRADUATE_RUN + 1} in a row", out)
        self.assertIn("a habit now?", out)

    def test_close_tells_then_and_now(self) -> None:
        with _pretty():
            rc, out, err = self._run("goal", "close", str(self.goal.id), "--outcome", "Done.")
        self.assertEqual(rc, 0, err)
        self.assertIn("Then ✗ Asked late", out)
        self.assertIn("Now  ✓ Asked first", out)


class RecapTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("me", is_self=True)
        self._create_person("jdoe")
        self._create_person("newbie")
        self._seed_entry("old", people=("jdoe",), occurred_at=_ago(10))
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Lead with the ask")
            entry = self._seed_entry("this week", people=("jdoe",), occurred_at=_ago(2))
            db.create_feedback(
                conn, entry.id, PLAN, goal_checks=[(goal.id, "advanced", "Opened with the ask")]
            )
        self._seed_entry("hello", people=("newbie",), occurred_at=_ago(1))

    def test_json(self) -> None:
        rc, out, err = self._run("--json", "recap")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["entry_count"], 2)
        self.assertEqual(payload["previous_entry_count"], 1)
        self.assertEqual(payload["coached_count"], 1)
        self.assertEqual(payload["new_people"], ["newbie"])
        self.assertEqual(payload["goals"][0]["verdicts"], ["advanced"])
        self.assertEqual(payload["best_moment"]["note"], "Opened with the ask")
        self.assertEqual(payload["advice"]["text"], "Open with the ask.")

    def test_pretty(self) -> None:
        with _pretty():
            rc, out, err = self._run("recap")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("Your week"))
        self.assertIn("★ new", out)
        self.assertIn("✨ Opened with the ask", out)
        self.assertIn("→ Open with the ask.", out)

    def test_plain_and_month(self) -> None:
        rc, out, err = self._run("recap", "month")
        self.assertEqual(rc, 0, err)
        self.assertIn("conversations: 3", out)
        self.assertIn("carry forward: Open with the ask.", out)


class CoachCommandTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._seed_entry("first")
        self._seed_entry("second")

    def _coach(self, *args: str, env: str = "my-claude --flag"):
        with (
            mock.patch.dict(os.environ, {coach.ENV_COMMAND: env}),
            mock.patch.object(coach.subprocess, "run") as run,
        ):
            run.return_value.returncode = 0
            rc, out, err = self._run("coach", *args)
        return rc, run, err

    def test_defaults_to_the_oldest_uncoached_entry(self) -> None:
        rc, run, err = self._coach()
        self.assertEqual(rc, 0, err)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["my-claude", "--flag"])
        self.assertTrue(command[-1].startswith("/commscoach 1 (use --db "))

    def test_headless_allows_only_commscoach(self) -> None:
        _, run, _ = self._coach("2", "--print")
        self.assertEqual(run.call_args.args[0][2:5], ["-p", "--allowedTools", coach.ALLOWED_TOOLS])

    def test_prompt_placeholder(self) -> None:
        _, run, _ = self._coach("2", env="tool --ask {prompt} --end")
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["tool", "--ask"])
        self.assertTrue(command[2].startswith("/commscoach 2"))
        self.assertEqual(command[-1], "--end")

    def test_missing_launcher_and_entry(self) -> None:
        rc, _, err = self._run("coach", "1")
        self.assertEqual(rc, 1)
        self.assertIn("not on PATH", err)
        rc, _, err = self._coach("99")
        self.assertEqual(rc, 1)
        self.assertIn("entry 99 not found", err)

    def test_log_never_prompts_without_a_terminal(self) -> None:
        with _pretty(), mock.patch.dict(os.environ, {coach.ENV_COMMAND: "x"}):
            with mock.patch("builtins.input") as ask:
                rc, _, err = self._run("log", "hello")
        self.assertEqual(rc, 0, err)
        ask.assert_not_called()


class PolishTests(EntryTestBase):
    def test_feedback_search_reads_as_prose(self) -> None:
        entry = self._seed_entry("talk")
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, entry.id, PLAN)
        with _pretty():
            _, out, _ = self._run("feedback", "search", "--query", "ask")
        self.assertIn("Try next time: • Open with the ask.", out)
        self.assertNotIn("**", out)
        self.assertNotIn("##", out)

    def test_entry_rows_drop_markdown(self) -> None:
        self._seed_entry("Asked for **budget** first")
        with _pretty():
            _, out, _ = self._run("entry", "list")
        self.assertIn("Asked for budget first", out)

    def test_doctor_is_one_line_when_healthy(self) -> None:
        with _pretty():
            rc, out, _ = self._run("doctor")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("✓ Healthy"))
        self.assertNotIn("[OK]", out)
        with _pretty():
            _, verbose, _ = self._run("doctor", "--verbose")
        self.assertIn("integrity_check", verbose)

    def test_people_and_tags_tables(self) -> None:
        self._create_person("me", is_self=True)
        self._create_person("jdoe", name="John Doe")
        for days in (40, 33, 26):
            self._seed_entry("sync", people=("jdoe",), tags=("1on1",), occurred_at=_ago(days))
        with _pretty():
            _, people, _ = self._run("person", "list", "--with-activity")
            _, tags, _ = self._run("tag", "list")
        self.assertIn("(you)", people)
        self.assertIn("gone quiet", people)
        self.assertIn("3 talks", people)
        self.assertIn("#1on1", tags)
        self.assertIn("█", tags)


class CoachAfterLogTests(EntryTestBase):
    def _log(self, answer: str, *, env: dict[str, str] | None = None):
        with (
            _pretty(),
            mock.patch.dict(os.environ, {coach.ENV_COMMAND: "fake", **(env or {})}),
            mock.patch.object(coach, "_interactive", return_value=True),
            mock.patch("builtins.input", return_value=answer) as ask,
            mock.patch.object(coach.subprocess, "run") as run,
        ):
            run.return_value.returncode = 3
            rc, _, err = self._run("log", "hello")
        return rc, ask, run

    def test_enter_coaches_and_log_still_succeeds(self) -> None:
        rc, ask, run = self._log("")
        self.assertEqual(rc, 0)
        ask.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], "fake")
        self.assertTrue(run.call_args.args[0][-1].startswith("/commscoach 1"))

    def test_no_skips_and_env_disables(self) -> None:
        _, _, run = self._log("n")
        run.assert_not_called()
        _, ask, run = self._log("", env={coach.ENV_PROMPT: "0"})
        ask.assert_not_called()
        run.assert_not_called()
