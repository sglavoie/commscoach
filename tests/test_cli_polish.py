"""Regression tests for CLI edge cases: rollback, empty flags, no-op edits, listings."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from commscoach import db
from commscoach.cli import editor as cli_editor
from commscoach.cli import entries as cli_entries
from commscoach.cli import people as cli_people

from .cli_entry_case import EntryTestBase


class _TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class EntryWriteTests(EntryTestBase):
    def _handles(self) -> list[str]:
        with closing(db.connect(self.db_path)) as conn:
            return [person.handle for person in db.list_people(conn)]

    def test_failed_add_does_not_keep_new_people(self) -> None:
        rc, _, err = self._run("entry", "add", "", "--person", "newguy", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)
        self.assertEqual(self._handles(), [])

    def test_failed_edit_does_not_keep_new_people(self) -> None:
        entry = self._seed_entry("body")
        rc, _, _ = self._run(
            "entry", "edit", str(entry.id), "--person", "carol", "--body", "", "--yes"
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self._handles(), [])

    def test_new_person_is_confirmed_before_the_editor_opens(self) -> None:
        with (
            mock.patch.object(cli_people, "confirm", return_value=False),
            mock.patch.object(cli_entries, "compose_in_editor") as compose,
        ):
            rc, _, err = self._run("entry", "add", "--person", "brandnew", stdin=_TtyInput())
        self.assertEqual(rc, 1)
        self.assertIn("person @brandnew does not exist", err)
        compose.assert_not_called()

    def test_empty_person_or_tag_on_edit_is_rejected(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body", tags=("work",), people=("alice",))
        for flag, hint in (("--person", "--clear-people"), ("--tag", "--clear-tags")):
            with self.subTest(flag=flag):
                rc, _, err = self._run("entry", "edit", str(entry.id), flag, "")
                self.assertEqual(rc, 1)
                self.assertIn(hint, err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, entry.id), entry)

    def test_blank_source_is_stored_as_none(self) -> None:
        rc, out, _ = self._run("entry", "add", "body", "--source", "   ", "--json")
        self.assertEqual(rc, 0)
        self.assertIsNone(json.loads(out)["source"])
        rc, out, _ = self._run("entry", "add", "body", "--source", " slack ", "--json")
        self.assertEqual(json.loads(out)["source"], "slack")

    def test_noop_edit_keeps_updated_at_and_says_unchanged(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("same", source="email", tags=("work",), people=("alice",))
        for flags in (
            ("--body", "same"),
            ("--source", "email"),
            ("--tag", "work"),
            ("--person", "alice"),
            ("--remove-person", "nobody"),
        ):
            with self.subTest(flags=flags):
                rc, out, _ = self._run("entry", "edit", str(entry.id), *flags)
                self.assertEqual(rc, 0)
                self.assertTrue(out.startswith(f"unchanged entry {entry.id}"), out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, entry.id).updated_at, entry.updated_at)
            feedback = db.create_feedback(conn, entry.id, "Advice")
        rc, out, _ = self._run("feedback", "edit", str(feedback.id), "--body", "Advice")
        self.assertTrue(out.startswith("unchanged"), out)
        rc, out, _ = self._run("entry", "edit", str(entry.id), "--body", "new")
        self.assertTrue(out.startswith(f"updated entry {entry.id}"), out)

    def test_entry_show_prefixes_handles(self) -> None:
        self._create_person("bob")
        entry = self._seed_entry("body", people=("bob",))
        _, out, _ = self._run("entry", "show", str(entry.id))
        self.assertIn("people: @bob", out)

    def test_rm_warns_when_a_review_would_cover_no_entries(self) -> None:
        only = self._seed_entry("only")
        shared = self._seed_entry("shared")
        with closing(db.connect(self.db_path)) as conn:
            emptied = db.create_review(conn, "Review", [only.id])
            db.create_review(conn, "Review", [only.id, shared.id])
        rc, out, err = self._run("entry", "rm", str(only.id), "--yes")
        self.assertEqual(rc, 0)
        self.assertEqual(out, f"deleted entry {only.id}\n")
        self.assertEqual(err, f"Review {emptied.id} now covers no entries.\n")


class RepeatedFilterTests(EntryTestBase):
    def test_repeated_single_value_option_is_a_usage_error(self) -> None:
        for argv in (
            ("entry", "search", "--person", "a", "--person", "b"),
            ("entry", "search", "--tag", "a", "--tag", "b"),
            ("review", "list", "--limit", "1", "--limit", "2"),
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                self._run(*argv)
            self.assertEqual(raised.exception.code, 2)

    def test_global_db_flag_still_takes_the_last_value(self) -> None:
        rc, _, _ = self._run("entry", "list", "--db", str(self.db_path))
        self.assertEqual(rc, 0)


class ListingTests(EntryTestBase):
    def test_entry_and_review_lists_can_start_oldest_first(self) -> None:
        first = self._seed_entry("first", occurred_at="2026-01-01T10:00:00+00:00")
        second = self._seed_entry("second", occurred_at="2026-02-01T10:00:00+00:00")
        _, out, _ = self._run("entry", "list", "--oldest-first", "--limit", "1", "--json")
        self.assertEqual([item["id"] for item in json.loads(out)], [first.id])
        with closing(db.connect(self.db_path)) as conn:
            older = db.create_review(conn, "Older", [first.id])
            db.create_review(conn, "Newer", [second.id])
        _, out, _ = self._run("review", "list", "--oldest-first", "--limit", "1", "--json")
        self.assertEqual([item["id"] for item in json.loads(out)], [older.id])

    def test_feedback_search_filters_by_written_date(self) -> None:
        entry = self._seed_entry("body")
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, entry.id, "lead with the ask")
        _, out, _ = self._run(
            "feedback", "search", "--query", "ask", "--until", "2000-01-01", "--json"
        )
        self.assertEqual(json.loads(out), [])
        _, out, _ = self._run(
            "feedback", "search", "--query", "ask", "--since", "2000-01-01", "--oldest-first"
        )
        self.assertIn("lead with the ask", out)

    def test_person_and_tag_lists_report_truncation(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        self._seed_entry("body", tags=("one", "two"))
        _, out, err = self._run("person", "list", "--limit", "1")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(err, "Showing 1 of 2 people.\n")
        _, out, err = self._run("tag", "list", "--limit", "1")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(err, "Showing 1 of 2 tags.\n")

    def test_tag_rm_checks_the_tag_before_prompting(self) -> None:
        self._seed_entry("body", tags=("work",))
        with mock.patch("commscoach.cli.tags.confirm") as confirm:
            rc, _, err = self._run("tag", "rm", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("tag 'nope' not found", err)
        confirm.assert_not_called()
        with mock.patch("commscoach.cli.tags.confirm", return_value=False) as confirm:
            self._run("tag", "rm", "work")
        self.assertIn("removed from 1 entry", confirm.call_args.args[0])


class DraftRecoveryTests(EntryTestBase):
    def test_recovered_draft_drops_editor_instructions(self) -> None:
        draft = self.tmpdir / "draft.md"
        draft.write_text("#> Write the entry body below.\n\nMy text\n", encoding="utf-8")
        cli_editor._finish_draft(draft, recover=True)
        self.assertEqual(draft.read_text(encoding="utf-8"), "My text\n")


class ForeignInitTests(EntryTestBase):
    def test_refused_init_leaves_the_journal_mode_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("CREATE TABLE messages (x)")
            with self.assertRaises(db.DBError):
                db.init_db(path)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
