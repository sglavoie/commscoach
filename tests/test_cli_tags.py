"""Tests for the ``commscoach tag`` subcommands."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr
from unittest import mock

from commscoach import cli, db
from commscoach.cli import tags as cli_tags

from .cli_entry_case import EntryTestBase


class TagGroupTests(EntryTestBase):
    def test_bare_group_prints_help_and_exits_zero(self) -> None:
        rc, out, _ = self._run("tag")
        self.assertEqual(rc, 0)
        self.assertIn("SUBCOMMAND", out)
        for subcommand in ("list", "rename", "merge", "rm"):
            self.assertIn(subcommand, out)


class TagListTests(EntryTestBase):
    def test_list_orders_by_count_then_name(self) -> None:
        self._seed_entry("a", tags=("conflict", "zeta"))
        self._seed_entry("b", tags=("conflict", "alpha"))

        rc, out, _ = self._run("tag", "list")

        self.assertEqual(rc, 0)
        self.assertEqual(
            out.splitlines(), ["conflict  2", "alpha  1", "zeta  1"]
        )

    def test_list_empty_prints_placeholder(self) -> None:
        rc, out, _ = self._run("tag", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "(no tags)")

    def test_list_json_is_a_list_of_tag_shapes(self) -> None:
        self._seed_entry("a", tags=("conflict",))

        rc, out, _ = self._run("--json", "tag", "list")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 1)
        self.assertEqual(
            set(payload[0]), {"id", "name", "entry_count"}
        )
        self.assertEqual(payload[0]["name"], "conflict")
        self.assertEqual(payload[0]["entry_count"], 1)

    def test_list_empty_json_is_an_empty_list(self) -> None:
        rc, out, _ = self._run("--json", "tag", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_limit_caps_output(self) -> None:
        self._seed_entry("a", tags=("conflict", "alpha"))
        self._seed_entry("b", tags=("conflict",))

        rc, out, _ = self._run("tag", "list", "--limit", "1")

        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), ["conflict  2"])

    def test_negative_limit_exits_two(self) -> None:
        err_buf = io.StringIO()
        with redirect_stderr(err_buf), self.assertRaises(SystemExit) as ctx:
            cli.main(["--db", str(self.db_path), "tag", "list", "--limit", "-1"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("non-negative", err_buf.getvalue())


class TagRenameTests(EntryTestBase):
    def test_rename_reports_the_normalized_name(self) -> None:
        entry = self._seed_entry("a", tags=("conflict",))

        rc, out, _ = self._run("tag", "rename", "conflict", "Pushback")

        self.assertEqual(rc, 0)
        self.assertIn("renamed tag 'conflict' to 'pushback'", out)
        self.assertIn("pushback  1", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, entry.id).tags, ("pushback",))

    def test_rename_json_emits_the_tag_shape(self) -> None:
        self._seed_entry("a", tags=("conflict",))

        rc, out, _ = self._run("--json", "tag", "rename", "conflict", "pushback")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["name"], "pushback")
        self.assertEqual(payload["entry_count"], 1)

    def test_rename_onto_existing_name_exits_one_pointing_at_merge(self) -> None:
        self._seed_entry("a", tags=("conflict",))
        self._seed_entry("b", tags=("conflcit",))

        rc, _, err = self._run("tag", "rename", "conflcit", "conflict")

        self.assertEqual(rc, 1)
        self.assertIn("tag merge", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(len(db.list_tags(conn)), 2)

    def test_rename_missing_tag_exits_one(self) -> None:
        rc, _, err = self._run("tag", "rename", "absent", "other")
        self.assertEqual(rc, 1)
        self.assertIn("absent", err)


class TagMergeTests(EntryTestBase):
    def test_merge_with_yes_skips_prompt(self) -> None:
        both = self._seed_entry("a", tags=("conflcit", "conflict"))
        only_source = self._seed_entry("b", tags=("conflcit",))

        rc, out, _ = self._run("--yes", "tag", "merge", "conflcit", "conflict")

        self.assertEqual(rc, 0)
        self.assertIn("merged tag 'conflcit' into 'conflict'", out)
        self.assertIn("conflict  2", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual([t.name for t in db.list_tags(conn)], ["conflict"])
            self.assertEqual(db.get_entry(conn, both.id).tags, ("conflict",))
            self.assertEqual(
                db.get_entry(conn, only_source.id).tags, ("conflict",)
            )

    def test_merge_confirm_yes_merges(self) -> None:
        self._seed_entry("a", tags=("conflcit",))
        self._seed_entry("b", tags=("conflict",))

        with mock.patch.object(cli_tags, "confirm", return_value=True):
            rc, out, _ = self._run("tag", "merge", "conflcit", "conflict")

        self.assertEqual(rc, 0)
        self.assertIn("conflict  2", out)

    def test_merge_confirm_no_aborts_and_changes_nothing(self) -> None:
        self._seed_entry("a", tags=("conflcit",))
        self._seed_entry("b", tags=("conflict",))

        with mock.patch.object(cli_tags, "confirm", return_value=False):
            rc, _, err = self._run("tag", "merge", "conflcit", "conflict")

        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(len(db.list_tags(conn)), 2)

    def test_merge_json_emits_the_destination_tag(self) -> None:
        self._seed_entry("a", tags=("conflcit",))
        self._seed_entry("b", tags=("conflict",))

        rc, out, _ = self._run(
            "--yes", "--json", "tag", "merge", "conflcit", "conflict"
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["name"], "conflict")
        self.assertEqual(payload["entry_count"], 2)

    def test_merge_missing_destination_exits_one_pointing_at_rename(self) -> None:
        self._seed_entry("a", tags=("conflcit",))

        rc, _, err = self._run("--yes", "tag", "merge", "conflcit", "conflict")

        self.assertEqual(rc, 1)
        self.assertIn("tag rename", err)


class TagRmTests(EntryTestBase):
    def test_rm_with_yes_reports_the_unlink_count(self) -> None:
        first = self._seed_entry("a", tags=("conflict", "ops"))
        second = self._seed_entry("b", tags=("conflict",))

        rc, out, _ = self._run("--yes", "tag", "rm", "conflict")

        self.assertEqual(rc, 0)
        self.assertIn("removed tag 'conflict' from 2 entries", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual([t.name for t in db.list_tags(conn)], ["ops"])
            self.assertEqual(db.get_entry(conn, first.id).tags, ("ops",))
            self.assertEqual(db.get_entry(conn, second.id).tags, ())

    def test_rm_confirm_yes_deletes(self) -> None:
        self._seed_entry("a", tags=("conflict",))

        with mock.patch.object(cli_tags, "confirm", return_value=True):
            rc, out, _ = self._run("tag", "rm", "conflict")

        self.assertEqual(rc, 0)
        self.assertIn("removed tag 'conflict' from 1 entry", out)

    def test_rm_confirm_no_aborts_and_keeps_the_tag(self) -> None:
        self._seed_entry("a", tags=("conflict",))

        with mock.patch.object(cli_tags, "confirm", return_value=False):
            rc, _, err = self._run("tag", "rm", "conflict")

        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual([t.name for t in db.list_tags(conn)], ["conflict"])

    def test_rm_json_reports_the_blast_radius(self) -> None:
        self._seed_entry("a", tags=("conflict",))
        self._seed_entry("b", tags=("conflict",))

        rc, out, _ = self._run("--yes", "--json", "tag", "rm", "CONFLICT")

        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(out),
            {"deleted": True, "name": "conflict", "entries_unlinked": 2},
        )

    def test_rm_missing_tag_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "tag", "rm", "absent")
        self.assertEqual(rc, 1)
        self.assertIn("absent", err)


if __name__ == "__main__":
    unittest.main()
