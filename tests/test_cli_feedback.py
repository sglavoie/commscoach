"""Tests for ``commscoach feedback`` navigation, add, list and search."""

from __future__ import annotations

import io
import json
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import feedback as cli_feedback

from .cli_feedback_case import FeedbackTestBase


class FeedbackNavigationTests(FeedbackTestBase):
    def test_show_with_entry_uses_current_full_entry_and_selected_feedback(self):
        with closing(db.connect(self.db_path)) as conn:
            db.create_person(conn, "alice")
            entry = db.create_entry(
                conn,
                "Original conversation",
                people=["alice"],
                tags=["meeting"],
                source="email",
                occurred_at="2026-01-02T15:00:00+00:00",
            )
            note = db.create_feedback(conn, entry.id, "Selected advice")
            db.create_feedback(conn, entry.id, "Other advice")
            db.update_entry(conn, entry.id, body="Corrected conversation\nFull second line")
        rc, out, err = self._run("feedback", "show", str(note.id), "--with-entry")
        self.assertEqual(rc, 0, err)
        for text in (
            "Corrected conversation\nFull second line",
            "alice",
            "meeting",
            "email",
            "2026-01-02",
            "Selected advice",
        ):
            self.assertIn(text, out)
        self.assertLess(out.index("Corrected conversation"), out.index("Selected advice"))
        self.assertNotIn("Other advice", out)
        plain = self._run("feedback", "show", str(note.id))[1]
        self.assertNotIn("Corrected conversation", plain)
        base = json.loads(self._run("feedback", "show", str(note.id), "--json")[1])
        rc, expanded, err = self._run("feedback", "show", str(note.id), "--with-entry", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(expanded)
        expected_entry = json.loads(self._run("entry", "show", str(entry.id), "--json")[1])
        self.assertEqual(payload.pop("entry"), expected_entry)
        self.assertEqual(payload, base)
        for flags in ((), ("--json",)):
            rc, out, err = self._run("feedback", "show", "9999", "--with-entry", *flags)
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")
            self.assertIn("9999", err)

    def test_search_footer_counts_filtered_notes_without_duplicate_people(self):
        with closing(db.connect(self.db_path)) as conn:
            db.create_person(conn, "alice")
            db.create_person(conn, "bob")
            entry = db.create_entry(conn, "Conversation", people=["alice", "bob"])
            for _ in range(3):
                db.create_feedback(conn, entry.id, "Try 10%_! less")
            db.create_feedback(conn, entry.id, "Try 100xyz! less")
            other = db.create_entry(conn, "Other conversation", people=["bob"])
            db.create_feedback(conn, other.id, "Try 10%_! less")
        flags = ("feedback", "search", "--query", "10%_!", "--person", "ALICE")
        for limit in (0, 1, 3, 4):
            with self.subTest(limit=limit):
                rc, out, err = self._run(*flags, "--limit", str(limit))
                self.assertEqual(rc, 0, err)
                if limit < 3:
                    self.assertIn(f"Showing {limit} of 3 matching feedback notes.", err)
                else:
                    self.assertNotIn("Showing", out + err)
                rc, out, err = self._run(*flags, "--limit", str(limit), "--json")
                self.assertEqual(rc, 0, err)
                self.assertEqual(len(json.loads(out)), min(limit, 3))
        self.assertEqual(
            self._run("feedback", "search", "--query", "missing")[1], "(no feedback)\n"
        )


class FeedbackAddTests(FeedbackTestBase):
    def test_add_creates_feedback(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run("feedback", "add", str(entry.id), "good listening")
        self.assertEqual(rc, 0)
        self.assertIn("created feedback 1", out)
        self.assertIn("good listening", out)
        with closing(db.connect(self.db_path)) as conn:
            items = db.list_feedback(conn, entry.id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].body, "good listening")

    def test_add_unknown_entry_exits_one(self) -> None:
        rc, _, err = self._run("feedback", "add", "9999", "body")
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
        rc, _, err = self._run("feedback", "add", str(entry.id), "   ")
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
        rc, _, _ = self._run("feedback", "add", str(entry.id), f"@{body_file}")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            items = db.list_feedback(conn, entry.id)
        self.assertEqual(items[0].body, "from file feedback")

    def test_add_body_from_missing_file_exits_one(self) -> None:
        entry = self._seed_entry()
        rc, _, err = self._run("feedback", "add", str(entry.id), "@/no/such/file.txt")
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)

    def test_add_json_output(self) -> None:
        entry = self._seed_entry()
        rc, out, _ = self._run("--json", "feedback", "add", str(entry.id), "json body")
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
                "feedback",
                "search",
                "--query",
                "lead with the ask",
                "--person",
                "ALICE",
                "--limit",
                str(limit),
                "--json",
            )
            self.assertEqual(rc, 0, err)
            self.assertEqual([f["id"] for f in json.loads(out)], expected)
        rc, out, err = self._run("feedback", "search", "--query", "Listen more")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"entry {entry.id}", out)
        self.assertIn("Listen more", out)

    def test_search_without_query_browses_feedback_across_entries(self):
        with closing(db.connect(self.db_path)) as conn:
            db.create_person(conn, "alice")
            mine = db.create_entry(conn, "meeting", people=("alice",))
            other = db.create_entry(conn, "standup")
            first = db.create_feedback(conn, mine.id, "Lead with the ask")
            db.create_feedback(conn, other.id, "Listen more")
            last = db.create_feedback(conn, mine.id, "Summarize at the end")
        rc, out, err = self._run("feedback", "search", "--person", "alice", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([f["id"] for f in json.loads(out)], [last.id, first.id])
        rc, out, err = self._run("feedback", "search", "--limit", "1")
        self.assertEqual(rc, 0, err)
        self.assertIn("Summarize at the end", out)
        self.assertIn("Showing 1 of 3 feedback notes.", err)

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
