"""Tests for ``commscoach entry add``."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import entries as cli_entries
from commscoach.cli import people as cli_people

from .cli_entry_case import EntryTestBase


class EntryAddTests(EntryTestBase):
    def test_add_minimal_creates_entry(self) -> None:
        rc, out, _ = self._run("entry", "add", "hello world")
        self.assertEqual(rc, 0)
        self.assertIn("created entry 1", out)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.get_entry(conn, 1)
        assert entry is not None
        self.assertEqual(entry.body, "hello world")
        self.assertIsNone(entry.source)
        self.assertEqual(entry.tags, ())
        self.assertEqual(entry.people, ())

    def test_add_with_source_occurred_tags_people(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        rc, _, _ = self._run(
            "entry",
            "add",
            "body",
            "--source",
            "slack",
            "--occurred-at",
            "2026-06-20T14:00:00-04:00",
            "--tag",
            "  Meeting  ",
            "--tag",
            "Q3",
            "--tag",
            "meeting",
            "--person",
            "alice",
            "--person",
            "BOB",
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.get_entry(conn, 1)
        assert entry is not None
        self.assertEqual(entry.source, "slack")
        self.assertEqual(entry.occurred_at, "2026-06-20T18:00:00+00:00")
        self.assertEqual(set(entry.tags), {"meeting", "q3"})
        self.assertEqual({p.handle for p in entry.people}, {"alice", "bob"})

    def test_add_unknown_person_exits_one(self) -> None:
        with mock.patch.object(cli_people, "confirm", return_value=False):
            rc, _, err = self._run(
                "entry", "add", "body", "--person", "ghost"
            )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_entries(conn), ())

    def test_piped_body_with_unknown_person_explains_how_to_proceed(self) -> None:
        # stdin is consumed by the body, so the prompt sees EOF.
        rc, _, err = self._run(
            "entry", "add", "-", "--person", "newbie", stdin=io.StringIO("body\n")
        )
        self.assertEqual(rc, 1)
        self.assertIn("Create person @newbie? [y/N] \n", err)
        self.assertIn("person @newbie does not exist; pass --yes to create it", err)
        self.assertIn("commscoach person add newbie", err)

    def test_add_unknown_person_with_yes_creates_and_links(self) -> None:
        rc, _, _ = self._run(
            "--yes",
            "entry",
            "add",
            "body",
            "--person",
            "ghost",
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            person = db.get_person_by_handle(conn, "ghost")
            entry = db.get_entry(conn, 1)
        self.assertIsNotNone(person)
        assert entry is not None
        self.assertEqual([person.handle for person in entry.people], ["ghost"])

    def test_add_without_body_on_non_tty_exits_one_without_writing(self) -> None:
        with mock.patch.object(cli_entries.sys, "stdin", io.StringIO()):
            rc, _, err = self._run("entry", "add")
        self.assertEqual(rc, 1)
        self.assertIn("body required", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_entries(conn), ())

    def test_add_comma_delimited_tags(self) -> None:
        rc, _, _ = self._run(
            "entry", "add", "body", "--tag", "alpha, Beta,alpha"
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.get_entry(conn, 1)
        assert entry is not None
        self.assertEqual(entry.tags, ("alpha", "beta"))

    def test_add_empty_body_exits_one(self) -> None:
        rc, _, err = self._run("entry", "add", "   ")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_add_bad_occurred_at_exits_one_without_writing(self) -> None:
        rc, _, err = self._run(
            "entry", "add", "body", "--occurred-at", "yesterdya"
        )
        self.assertEqual(rc, 1)
        self.assertIn("accepted forms", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_entries(conn), ())

    def test_add_body_from_stdin(self) -> None:
        rc, _, _ = self._run("entry", "add", "-", stdin=io.StringIO("piped body\n"))
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.get_entry(conn, 1)
        assert entry is not None
        self.assertEqual(entry.body, "piped body\n")

    def test_add_body_from_file(self) -> None:
        body_file = self.tmpdir / "body.txt"
        body_file.write_text("from file\n", encoding="utf-8")
        rc, _, _ = self._run("entry", "add", f"@{body_file}")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.get_entry(conn, 1)
        assert entry is not None
        self.assertEqual(entry.body, "from file\n")

    def test_add_body_from_missing_file_exits_one(self) -> None:
        rc, _, err = self._run("entry", "add", "@/no/such/file.txt")
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)

    def test_add_body_from_non_utf8_file_exits_one(self) -> None:
        body_file = self.tmpdir / "bad.txt"
        body_file.write_bytes(b"bad \xff text")
        rc, _, err = self._run("entry", "add", f"@{body_file}")
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body: not valid UTF-8", err)

    def test_add_out_of_range_occurred_at_exits_one(self) -> None:
        rc, _, err = self._run(
            "entry", "add", "body", "--occurred-at", "999999999 days ago"
        )
        self.assertEqual(rc, 1)
        self.assertIn("out of range", err)

    def test_add_json_output_includes_entry_fields(self) -> None:
        self._create_person("alice")
        rc, out, _ = self._run(
            "--json",
            "entry",
            "add",
            "body",
            "--source",
            "email",
            "--tag",
            "x",
            "--person",
            "alice",
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["body"], "body")
        self.assertEqual(payload["source"], "email")
        self.assertEqual(payload["tags"], ["x"])
        self.assertEqual(len(payload["people"]), 1)
        self.assertEqual(payload["people"][0]["handle"], "alice")


if __name__ == "__main__":
    unittest.main()
