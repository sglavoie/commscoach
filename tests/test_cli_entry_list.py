"""Tests for ``commscoach entry list`` and ``commscoach entry search``."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr

from commscoach import cli

from .cli_entry_case import EntryTestBase


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
        rc, out, _ = self._run("--json", "entry", "search", "--query", "hello")
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


if __name__ == "__main__":
    unittest.main()
