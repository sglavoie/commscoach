"""CLI tests for the home screen, ``log``, nudges, the prep briefing, and hints."""

from __future__ import annotations

import io
import json
import os
from contextlib import closing, redirect_stderr
from datetime import datetime, timedelta
from unittest import mock

from commscoach import cli, db
from commscoach.cli import home, log, prep, suggest

from .cli_entry_case import EntryTestBase


def _pretty():
    return mock.patch.dict(os.environ, {"COMMSCOACH_PRETTY": "1", "NO_COLOR": "1"})


def _ago(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


class MentionParsingTests(EntryTestBase):
    def test_mentions_skip_emails_and_strip_punctuation(self) -> None:
        self.assertEqual(
            log.mentions("Told @jdoe's team, cc @Asmith. Mail me at a@b.com; @jdoe again"),
            ["jdoe", "Asmith"],
        )

    def test_hashtags_need_a_letter(self) -> None:
        self.assertEqual(
            log.hashtags("Fixed issue #42 in #Standup and #1on1."), ["standup", "1on1"]
        )


class LogCommandTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("jdoe", name="John Doe")

    def test_links_people_and_tags_from_the_text(self) -> None:
        rc, out, err = self._run(
            "--json",
            "log",
            "Asked",
            "@jdoe",
            "for",
            "budget",
            "#budget",
            "-s",
            "slack",
            "-w",
            "yesterday",
        )
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["body"], "Asked @jdoe for budget #budget")
        self.assertEqual([p["handle"] for p in payload["people"]], ["jdoe"])
        self.assertEqual(payload["tags"], ["budget"])
        self.assertEqual(payload["source"], "slack")
        self.assertIsNotNone(payload["occurred_at"])

    def test_plain_output_matches_entry_add(self) -> None:
        rc, out, _ = self._run("log", "Chat with @jdoe")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("created entry 1\n[1] created "))

    def test_typo_of_existing_handle_is_refused_with_a_suggestion(self) -> None:
        rc, _, err = self._run("log", "Call with @jdo")
        self.assertEqual(rc, 1)
        self.assertIn("did you mean @jdoe?", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_entries(conn), ())

    def test_yes_creates_an_unknown_handle(self) -> None:
        rc, out, err = self._run("--yes", "--json", "log", "Met @newbie")
        self.assertEqual(rc, 0, err)
        self.assertEqual([p["handle"] for p in json.loads(out)["people"]], ["newbie"])

    def test_extra_person_and_tag_flags(self) -> None:
        rc, out, err = self._run("--json", "log", "Standup", "-p", "jdoe", "-t", "daily")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["tags"], ["daily"])
        self.assertEqual([p["handle"] for p in payload["people"]], ["jdoe"])


class NudgeTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("me", is_self=True)
        self._create_person("jdoe")

    def test_entry_add_reacts_in_pretty_mode(self) -> None:
        self._seed_entry("earlier", people=("jdoe",), occurred_at=_ago(1))
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "Lead with the ask", person_handle="jdoe")
        with _pretty():
            rc, out, err = self._run("entry", "add", "Another chat", "--person", "jdoe")
        self.assertEqual(rc, 0, err)
        self.assertIn("✓ Logged entry 2", out)
        self.assertIn("2nd conversation with @jdoe", out)
        self.assertIn("Goal 1 with @jdoe: Lead with the ask", out)
        self.assertIn("/commscoach 2", out)

    def test_first_conversation_and_first_entry(self) -> None:
        with _pretty():
            rc, out, _ = self._run("log", "Hello @jdoe")
        self.assertEqual(rc, 0)
        self.assertIn("First conversation logged with @jdoe", out)

    def test_milestone_on_tenth_entry(self) -> None:
        for index in range(9):
            self._seed_entry(f"entry {index}")
        with _pretty():
            _, out, _ = self._run("log", "the tenth")
        self.assertIn("10th logged conversation", out)

    def test_json_and_plain_never_react(self) -> None:
        _, out, _ = self._run("log", "Hello @jdoe")
        self.assertNotIn("Logged entry", out)
        with _pretty():
            _, out, _ = self._run("--json", "log", "Hello again @jdoe")
        json.loads(out)


class HomeTests(EntryTestBase):
    def _home(self) -> str:
        with _pretty():
            rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        return out

    def test_empty_journal_shows_onboarding_progress(self) -> None:
        self._create_person("me", is_self=True)
        out = self._home()
        self.assertIn("Welcome to commscoach", out)
        self.assertIn("✓ Create your journal", out)
        self.assertIn("✓ Tell the coach who you are", out)
        self.assertIn("○ Add someone you talk to", out)

    def test_populated_journal(self) -> None:
        self._create_person("me", is_self=True, name="Sam Rivera")
        self._create_person("asmith")
        for days in (60, 50, 40):
            self._seed_entry("sync", people=("asmith",), occurred_at=_ago(days))
        self._seed_entry("recent", occurred_at=_ago(0))
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, 4, "fine")
            db.create_goal(conn, "Lead with the ask")
        out = self._home()
        self.assertIn(", Sam\n", out)
        self.assertIn("Waiting for coaching", out)
        self.assertIn("3 entries", out)
        self.assertIn("/commscoach 1", out)
        self.assertIn("Lead with the ask", out)
        self.assertIn("Gone quiet", out)
        self.assertIn("@asmith", out)

    def test_on_this_day(self) -> None:
        self._create_person("me", is_self=True)
        now = datetime.now().astimezone()
        month_ago = now.replace(month=now.month - 1 or 12, year=now.year - (now.month == 1))
        self._seed_entry("a memory", occurred_at=month_ago.isoformat())
        self.assertIn("A month ago today", self._home())

    def test_render_home_never_creates_a_database(self) -> None:
        self.db_path.unlink()
        with _pretty():
            rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("○ Create your journal", out)
        self.assertFalse(self.db_path.exists())
        self.assertTrue(callable(home.render_home))


class PrepBriefingTests(EntryTestBase):
    def test_section_items(self) -> None:
        body = (
            "Intro.\n\n## Try next time\n- Lead with the ask\n  in one line\n"
            "- Then context\n\n## Follow-ups\nConfirm by Friday.\n"
        )
        self.assertEqual(
            prep.section_items(body, prep._TRY), ["Lead with the ask in one line", "Then context"]
        )
        self.assertEqual(prep.section_items(body, prep._FOLLOW), ["Confirm by Friday."])
        self.assertEqual(prep.section_items("no headings", prep._TRY), [])

    def test_prep_opens_with_the_briefing(self) -> None:
        self._create_person("me", is_self=True)
        self._create_person("jdoe", name="John Doe")
        entry = self._seed_entry("budget talk", people=("jdoe",), occurred_at=_ago(2))
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(
                conn, entry.id, "## Try next time\n- Ask first\n\n## Follow-ups\n- Send numbers"
            )
            db.create_goal(conn, "General habit")
            db.create_goal(conn, "Lead with the ask", person_handle="jdoe")
            db.set_person_note(conn, 2, "Prefers numbers up front.")
        with _pretty():
            rc, out, err = self._run("person", "prep", "jdoe", "--brief")
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "Before you talk to John Doe (@jdoe)")
        self.assertIn("1 conversation", out)
        remember = [line for line in lines if "◎" in line]
        self.assertIn("Lead with the ask", remember[0])
        self.assertIn("General habit", remember[1])
        self.assertIn("→ Ask first", out)
        self.assertIn("○ Send numbers", out)
        self.assertIn("Prefers numbers up front.", out)
        self.assertIn("Everything: commscoach --db", out)

    def test_plain_prep_is_unchanged(self) -> None:
        self._create_person("jdoe")
        _, out, _ = self._run("person", "prep", "jdoe")
        self.assertNotIn("Before you talk", out)


class SuggestionTests(EntryTestBase):
    def test_choice_hint_uses_synonyms_and_spelling(self) -> None:
        self.assertEqual(
            suggest.choice_hint("invalid choice: 'done' (choose from add, list, close, rm)"),
            "did you mean 'close'?",
        )
        self.assertEqual(
            suggest.choice_hint("invalid choice: 'lsit' (choose from 'add', 'list')"),
            "did you mean 'list'?",
        )
        self.assertIsNone(suggest.choice_hint("invalid choice: 'zzz' (choose from add, list)"))

    def test_unknown_subcommand_suggests(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self._run("goal", "done", "1")
        self.assertEqual(caught.exception.code, 2)

    def test_subcommand_hint_reaches_stderr(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(["goal", "done", "1"])
        self.assertIn("did you mean 'close'?", err.getvalue())

    def test_unknown_handle_suggests_by_handle_or_name(self) -> None:
        self._create_person("jdoe", name="John Doe")
        _, _, err = self._run("person", "show", "jdo")
        self.assertIn("person 'jdo' not found; did you mean @jdoe?", err)
        _, _, err = self._run("person", "show", "john")
        self.assertIn("did you mean @jdoe?", err)

    def test_unknown_tag_suggests(self) -> None:
        self._seed_entry("x", tags=("standup",))
        _, _, err = self._run("entry", "search", "--tag", "stnadup")
        self.assertIn("did you mean #standup?", err)


class PrettyStatsTests(EntryTestBase):
    def test_dashboard(self) -> None:
        self._create_person("jdoe")
        self._seed_entry("a", people=("jdoe",), tags=("1on1",), occurred_at=_ago(8))
        self._seed_entry("b", people=("jdoe",), occurred_at=_ago(0))
        with _pretty():
            rc, out, _ = self._run("stats")
        self.assertEqual(rc, 0)
        self.assertIn("Your communication at a glance", out)
        self.assertIn("0/2 coached (0%)", out)
        self.assertIn("Weekly rhythm", out)
        self.assertIn("@jdoe", out)


class PrettyGoalCheckTests(EntryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._create_person("jdoe")
        for days in (3, 2, 1):
            self._seed_entry(f"chat {days}", people=("jdoe",), occurred_at=_ago(days))
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "Lead with the ask", person_handle="jdoe")

    def test_feedback_add_confirms_and_lists_checks(self) -> None:
        with _pretty():
            rc, out, err = self._run(
                "feedback", "add", "1", "Good.", "--goal-check", "1=advanced:asked first"
            )
        self.assertEqual(rc, 0, err)
        self.assertIn("✓ Stored feedback 1 · on entry 1", out)
        self.assertIn("✓ advanced Lead with the ask — asked first", out)
        self.assertIn("2 entries still waiting for coaching", out)

    def test_all_caught_up_and_first_coached_milestone(self) -> None:
        with _pretty():
            _, out, _ = self._run("feedback", "add", "1", "a")
            self.assertIn("1st coached conversation", out)
            self._run("feedback", "add", "2", "b")
            _, out, _ = self._run("feedback", "add", "3", "c")
        self.assertIn("All caught up", out)

    def test_goal_show_trail_and_close_story(self) -> None:
        self._run("feedback", "add", "1", "a", "--goal-check", "1=advanced")
        self._run("feedback", "add", "2", "b", "--goal-check", "1=hurt")
        with _pretty():
            _, show, _ = self._run("goal", "show", "1")
            _, home_out, _ = self._run()
            _, close, _ = self._run("goal", "close", "1")
        self.assertIn("Progress  ✓✗  advanced 1×, slipped 1×", show)
        self.assertIn("advanced in 1 of 2 checked conversations (50%)", show)
        self.assertNotIn("---", show)
        self.assertIn("✓✗ advanced 1×, slipped 1×", home_out)
        self.assertIn("🎉 Done the same day · 2 checked conversations", close)
