"""Tests for ``commscoach entry show`` and the bare ``entry`` help output."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr

from commscoach import cli

from .cli_entry_case import EntryTestBase


class EntryShowTests(EntryTestBase):
    def test_show_existing_prints_human(self) -> None:
        entry = self._seed_entry("hello body", source="slack")
        rc, out, _ = self._run("entry", "show", str(entry.id))
        self.assertEqual(rc, 0)
        self.assertIn(f"[{entry.id}]", out)
        self.assertIn("slack", out)
        self.assertIn("hello body", out)

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("entry", "show", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_show_json_round_trip(self) -> None:
        self._create_person("alice")
        entry = self._seed_entry("body", tags=("a", "b"), people=("alice",))
        rc, out, _ = self._run("--json", "entry", "show", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], entry.id)
        self.assertEqual(payload["tags"], ["a", "b"])
        self.assertEqual(payload["people"][0]["handle"], "alice")

    def test_show_non_integer_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--db", str(self.db_path), "entry", "show", "abc"])
        self.assertEqual(ctx.exception.code, 2)


class EntryHelpTests(EntryTestBase):
    def test_entry_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("entry")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("show", out)
        self.assertIn("edit", out)
        self.assertIn("rm", out)


if __name__ == "__main__":
    unittest.main()
