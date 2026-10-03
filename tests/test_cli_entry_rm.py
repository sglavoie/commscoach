"""Tests for ``commscoach entry rm``."""

from __future__ import annotations

import json
import unittest
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import entries as cli_entries

from .cli_entry_case import EntryTestBase


class EntryRmTests(EntryTestBase):
    def test_confirmation_counts_feedback_and_review_links_and_decline_preserves_them(self):
        entry = self._seed_entry("body")
        other = self._seed_entry("other")
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, entry.id, "first")
            db.create_feedback(conn, entry.id, "second")
            db.create_feedback(conn, other.id, "unrelated")
            review = db.create_review(conn, "review", [entry.id, other.id])
            db.create_review(conn, "unrelated review", [other.id])
        with mock.patch.object(cli_entries, "confirm", return_value=False) as confirm:
            rc, _, err = self._run("entry", "rm", str(entry.id))
        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        prompt = confirm.call_args.args[0]
        self.assertIn("delete 2 feedback notes", prompt)
        self.assertIn("remove 1 review link;", prompt)
        self.assertIn("reviews themselves remain", prompt)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(len(db.list_feedback(conn, entry.id)), 2)
            self.assertEqual(len(db.get_review(conn, review.id).entries), 2)
        with mock.patch.object(cli_entries, "confirm", return_value=True):
            rc, _, err = self._run("entry", "rm", str(entry.id))
        self.assertEqual(rc, 0, err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_feedback(conn, entry.id), ())
            self.assertEqual([e.id for e in db.get_review(conn, review.id).entries], [other.id])

    def test_confirmation_with_no_dependents(self):
        entry = self._seed_entry("body")
        with mock.patch.object(cli_entries, "confirm", return_value=False) as confirm:
            self._run("entry", "rm", str(entry.id))
        self.assertIn("0 feedback notes", confirm.call_args.args[0])
        self.assertIn("0 review links", confirm.call_args.args[0])

    def test_rm_with_yes_skips_prompt(self) -> None:
        entry = self._seed_entry("body")
        rc, out, _ = self._run("--yes", "entry", "rm", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"deleted entry {entry.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_entry(conn, entry.id))

    def test_rm_confirm_yes_deletes(self) -> None:
        entry = self._seed_entry("body")
        with mock.patch.object(cli_entries, "confirm", return_value=True):
            rc, out, _ = self._run("entry", "rm", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"deleted entry {entry.id}", out)

    def test_rm_confirm_no_aborts(self) -> None:
        entry = self._seed_entry("body")
        with mock.patch.object(cli_entries, "confirm", return_value=False):
            rc, _, err = self._run("entry", "rm", str(entry.id))
        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_entry(conn, entry.id))

    def test_rm_missing_entry_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "entry", "rm", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_rm_json_output(self) -> None:
        entry = self._seed_entry("body")
        rc, out, _ = self._run("--yes", "--json", "entry", "rm", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload, {"deleted": True, "id": entry.id})

    def test_rm_cascades_feedback_and_links(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body", tags=("x",), people=("alice",))
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, entry.id, "nice work")
        rc, _, _ = self._run("--yes", "entry", "rm", str(entry.id))
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_feedback(conn, entry.id), ())
            # orphaned tag is GC'd by db layer
            rows = conn.execute("SELECT COUNT(*) FROM tags").fetchone()
            self.assertEqual(rows[0], 0)
            # person remains
            self.assertIsNotNone(db.get_person_by_handle(conn, "alice"))


if __name__ == "__main__":
    unittest.main()
