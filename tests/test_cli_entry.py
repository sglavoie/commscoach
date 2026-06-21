"""Tests for the ``commscoach entry`` subcommands."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db


class EntryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "entries.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self,
        *args: str,
        stdin: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(full_args)
            else:
                rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _create_person(
        self, handle: str, *, name: str | None = None, is_self: bool = False
    ) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name, is_self=is_self)

    def _seed_entry(
        self,
        body: str = "seeded body",
        *,
        source: str | None = None,
        occurred_at: str | None = None,
        tags: tuple[str, ...] = (),
        people: tuple[str, ...] = (),
    ) -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(
                conn,
                body,
                source=source,
                occurred_at=occurred_at,
                tags=tags,
                people=people,
            )


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
            "2026-06-20T14:00:00",
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
        self.assertEqual(entry.occurred_at, "2026-06-20T14:00:00")
        self.assertEqual(set(entry.tags), {"meeting", "q3"})
        self.assertEqual({p.handle for p in entry.people}, {"alice", "bob"})

    def test_add_unknown_person_exits_one(self) -> None:
        rc, _, err = self._run(
            "entry", "add", "body", "--person", "ghost"
        )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_entries(conn), ())

    def test_add_empty_body_exits_one(self) -> None:
        rc, _, err = self._run("entry", "add", "   ")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_add_body_from_stdin(self) -> None:
        rc, _, _ = self._run(
            "entry", "add", "-", stdin=io.StringIO("piped body\n")
        )
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


class EntryGetTests(EntryTestBase):
    def test_get_existing_prints_human(self) -> None:
        entry = self._seed_entry("hello body", source="slack")
        rc, out, _ = self._run("entry", "get", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"[{entry.id}]", out)
        self.assertIn("slack", out)
        self.assertIn("hello body", out)

    def test_get_missing_exits_one(self) -> None:
        rc, _, err = self._run("entry", "get", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_get_json_round_trip(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body", tags=("a", "b"), people=("alice",))
        rc, out, _ = self._run("--json", "entry", "get", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], entry.id)
        self.assertEqual(payload["tags"], ["a", "b"])
        self.assertEqual(payload["people"][0]["handle"], "alice")

    def test_get_non_integer_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--db", str(self.db_path), "entry", "get", "abc"])
        self.assertEqual(ctx.exception.code, 2)


class EntryEditTests(EntryTestBase):
    def test_edit_body(self) -> None:
        entry = self._seed_entry("old body")
        rc, out, _ = self._run(
            "entry", "edit", str(entry.id), "--body", "new body"
        )
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
        rc, _, _ = self._run(
            "entry", "edit", str(entry.id), "--source", ""
        )
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
        rc, _, _ = self._run(
            "entry", "edit", str(entry.id), "--clear-tags"
        )
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
        rc, _, _ = self._run(
            "entry", "edit", str(entry.id), "--clear-people"
        )
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
        rc, _, err = self._run(
            "entry", "edit", "9999", "--body", "x"
        )
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


class EntryListTests(EntryTestBase):
    def test_list_empty(self) -> None:
        rc, out, _ = self._run("entry", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no entries)", out)

    def test_list_newest_first(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")
        third = self._seed_entry("third")
        rc, out, _ = self._run("entry", "list")
        self.assertEqual(rc, 0)
        idx_third = out.find(f"[{third.id}]")
        idx_second = out.find(f"[{second.id}]")
        idx_first = out.find(f"[{first.id}]")
        self.assertGreater(idx_second, idx_third)
        self.assertGreater(idx_first, idx_second)

    def test_list_limit(self) -> None:
        for i in range(5):
            self._seed_entry(f"entry {i}")
        rc, out, _ = self._run("entry", "list", "--limit", "2")
        self.assertEqual(rc, 0)
        # Two entries → two non-empty lines.
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 2)

    def test_list_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "entry",
                        "list",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)

    def test_list_json_empty(self) -> None:
        rc, out, _ = self._run("--json", "entry", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_json_payload(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second", tags=("hello",))
        rc, out, _ = self._run("--json", "entry", "list")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([p["id"] for p in payload], [second.id, first.id])
        self.assertEqual(payload[0]["tags"], ["hello"])


class EntrySearchTests(EntryTestBase):
    def test_search_by_query_substring_case_insensitive(self) -> None:
        match = self._seed_entry("Quarterly review meeting")
        self._seed_entry("Daily standup")
        rc, out, _ = self._run("entry", "search", "--query", "quarterly")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("standup", out.lower())

    def test_search_by_tag(self) -> None:
        match = self._seed_entry("body", tags=("review",))
        self._seed_entry("body", tags=("standup",))
        rc, out, _ = self._run("entry", "search", "--tag", "review")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)

    def test_search_by_person_handle_case_insensitive(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        match = self._seed_entry("with alice", people=("alice",))
        self._seed_entry("with bob", people=("bob",))
        rc, out, _ = self._run("entry", "search", "--person", "ALICE")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("with bob", out)

    def test_search_combined_filters(self) -> None:
        self._create_person("alice")
        match = self._seed_entry(
            "kickoff sync", tags=("kickoff",), people=("alice",)
        )
        self._seed_entry("kickoff solo", tags=("kickoff",))
        rc, out, _ = self._run(
            "entry",
            "search",
            "--query",
            "kickoff",
            "--tag",
            "kickoff",
            "--person",
            "alice",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("kickoff solo", out)

    def test_search_no_match_prints_empty_marker(self) -> None:
        self._seed_entry("body")
        rc, out, _ = self._run("entry", "search", "--query", "absent")
        self.assertEqual(rc, 0)
        self.assertIn("(no entries)", out)

    def test_search_json_returns_list(self) -> None:
        match = self._seed_entry("hello world")
        self._seed_entry("nothing")
        rc, out, _ = self._run(
            "--json", "entry", "search", "--query", "hello"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], match.id)

    def test_search_limit_caps_results(self) -> None:
        for _ in range(4):
            self._seed_entry("hit")
        rc, out, _ = self._run(
            "--json", "entry", "search", "--query", "hit", "--limit", "2"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 2)

    def test_search_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "entry",
                        "search",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)


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
        with mock.patch.object(cli, "confirm", return_value=True):
            rc, out, _ = self._run("entry", "rm", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"deleted entry {entry.id}", out)

    def test_rm_confirm_no_aborts(self) -> None:
        entry = self._seed_entry("body")
        with mock.patch.object(cli, "confirm", return_value=False):
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
        rc, out, _ = self._run(
            "--yes", "--json", "entry", "rm", str(entry.id)
        )
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


class EntryHelpTests(EntryTestBase):
    def test_entry_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("entry")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("get", out)
        self.assertIn("edit", out)
        self.assertIn("rm", out)


if __name__ == "__main__":
    unittest.main()
