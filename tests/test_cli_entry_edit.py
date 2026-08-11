"""Tests for ``commscoach entry edit``."""

from __future__ import annotations

import json
import unittest
from contextlib import closing

from commscoach import db

from .cli_entry_case import EntryTestBase


class EntryEditTests(EntryTestBase):
    def test_edit_body(self) -> None:
        entry = self._seed_entry("old body")
        rc, out, _ = self._run("entry", "edit", str(entry.id), "--body", "new body")
        self.assertEqual(rc, 0)
        self.assertIn(f"updated entry {entry.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(updated.body, "new body")

    def test_edit_body_from_file(self) -> None:
        entry = self._seed_entry("old body")
        body_file = self.tmpdir / "new.txt"
        body_file.write_text("from file edit", encoding="utf-8")
        rc, _, _ = self._run(
            "entry", "edit", str(entry.id), "--body", f"@{body_file}"
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(updated.body, "from file edit")

    def test_edit_source_and_occurred_at(self) -> None:
        entry = self._seed_entry("body", source="slack", occurred_at="2026-01-01")
        rc, _, _ = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--source",
            "email",
            "--occurred-at",
            "2026-02-02",
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(updated.source, "email")
        self.assertEqual(updated.occurred_at, "2026-02-02")

    def test_edit_source_empty_string_clears(self) -> None:
        entry = self._seed_entry("body", source="slack")
        rc, _, _ = self._run("entry", "edit", str(entry.id), "--source", "")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertIsNone(updated.source)

    def test_edit_replaces_tags(self) -> None:
        entry = self._seed_entry("body", tags=("old1", "old2"))
        rc, _, _ = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--tag",
            "new1",
            "--tag",
            "new2",
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(set(updated.tags), {"new1", "new2"})

    def test_edit_clear_tags(self) -> None:
        entry = self._seed_entry("body", tags=("keep", "drop"))
        rc, _, _ = self._run("entry", "edit", str(entry.id), "--clear-tags")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(updated.tags, ())

    def test_edit_replace_people(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        self._create_person("carol")
        entry = self._seed_entry("body", people=("alice",))
        rc, _, _ = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--person",
            "bob",
            "--person",
            "carol",
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual({p.handle for p in updated.people}, {"bob", "carol"})

    def test_edit_clear_people(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body", people=("alice",))
        rc, _, _ = self._run("entry", "edit", str(entry.id), "--clear-people")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_entry(conn, entry.id)
        assert updated is not None
        self.assertEqual(updated.people, ())

    def test_edit_tag_and_clear_tags_conflict(self) -> None:
        entry = self._seed_entry("body")
        rc, _, err = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--tag",
            "x",
            "--clear-tags",
        )
        self.assertEqual(rc, 1)
        self.assertIn("--clear-tags", err)

    def test_edit_person_and_clear_people_conflict(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body")
        rc, _, err = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--person",
            "alice",
            "--clear-people",
        )
        self.assertEqual(rc, 1)
        self.assertIn("--clear-people", err)

    def test_edit_without_any_field_exits_one(self) -> None:
        entry = self._seed_entry("body")
        rc, _, err = self._run("entry", "edit", str(entry.id))
        self.assertEqual(rc, 1)
        self.assertIn("requires at least one", err)

    def test_edit_missing_entry_exits_one(self) -> None:
        rc, _, err = self._run("entry", "edit", "9999", "--body", "x")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_edit_unknown_person_exits_one_and_no_change(self) -> None:
        entry = self._seed_entry("body", tags=("keep",))
        rc, _, err = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--person",
            "ghost",
        )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)
        with closing(db.connect(self.db_path)) as conn:
            after = db.get_entry(conn, entry.id)
        assert after is not None
        self.assertEqual(set(after.tags), {"keep"})

    def test_edit_json_output(self) -> None:
        entry = self._seed_entry("body")
        rc, out, _ = self._run(
            "--json",
            "entry",
            "edit",
            str(entry.id),
            "--body",
            "new",
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["body"], "new")
        self.assertEqual(payload["id"], entry.id)


if __name__ == "__main__":
    unittest.main()
