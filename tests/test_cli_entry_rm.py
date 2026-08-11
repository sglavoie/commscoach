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
