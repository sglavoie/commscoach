"""Tests for the top-level ``commscoach stats`` command."""

from __future__ import annotations

import io
import json
import shlex
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from commscoach import cli, db


class StatsCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "stats.sqlite3"
        db.init_db(self.db_path)

    def _run(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main(["--db", str(self.db_path), *args])
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_empty_json_has_complete_key_set_and_zero_coverage(self) -> None:
        rc, out, err = self._run("--json", "stats")

        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(
            set(payload),
            {
                "entry_count",
                "people_count",
                "tag_count",
                "feedback_count",
                "review_count",
                "entries_with_feedback",
                "entries_awaiting_feedback",
                "feedback_coverage",
                "open_goal_count",
                "closed_goal_count",
                "first_entry",
                "last_entry",
                "last_review",
                "entries_per_week",
                "current_weekly_streak",
                "longest_weekly_streak",
                "weekly_buckets",
                "top_tags",
                "top_sources",
                "top_people",
            },
        )
        self.assertEqual(payload["feedback_coverage"], 0.0)

    def test_weeks_only_truncates_serialized_buckets(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.create_entry(conn, "one", occurred_at="2026-01-05T12:00:00+00:00")
            db.create_entry(conn, "two", occurred_at="2026-01-19T12:00:00+00:00")

        rc, out, _ = self._run("--json", "stats", "--weeks", "2")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["entry_count"], 2)
        self.assertEqual(payload["entries_per_week"], 2 / 3)
        self.assertEqual(
            payload["weekly_buckets"],
            [
                {"week": "2026-01-12", "count": 0},
                {"week": "2026-01-19", "count": 1},
            ],
        )

    def test_date_bounds_reuse_semantic_date_parser(self) -> None:
        rc, _, err = self._run("stats", "--since", "not-a-date")

        self.assertEqual(rc, 1)
        self.assertIn("--since", err)

    def test_reversed_date_range_is_semantic_error(self) -> None:
        rc, _, err = self._run("stats", "--since", "2026-06-03", "--until", "2026-06-02")
        self.assertEqual(rc, 1)
        self.assertIn("--since must not be after --until", err)

    def test_single_date_bound_is_valid(self) -> None:
        rc, _, err = self._run("stats", "--until", "2026-06-02")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_top_truncates_each_ranked_list(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.create_entry(
                conn,
                "one",
                source="slack",
                occurred_at="2026-01-05T12:00:00+00:00",
                tags=("alpha",),
            )
            db.create_entry(
                conn,
                "two",
                source="email",
                occurred_at="2026-01-06T12:00:00+00:00",
                tags=("beta",),
            )

        rc, out, _ = self._run("--json", "stats", "--top", "1")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["top_tags"]), 1)
        self.assertEqual(len(payload["top_sources"]), 1)
        self.assertEqual(payload["top_people"], [])

        rc, out, _ = self._run("stats")
        self.assertIn("  #alpha: 1", out)
        self.assertIn("  slack: 1", out)

    def test_negative_top_or_weeks_is_argparse_error(self) -> None:
        for flag in ("--top", "--weeks"):
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(["--db", str(self.db_path), "stats", flag, "-1"])
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("must be non-negative", err.getvalue())

    def test_backlog_hint_preserves_database_and_resolved_date_window(self) -> None:
        self.db_path = self.db_path.with_name("journal with spaces.sqlite3")
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            older = db.create_entry(conn, "old", occurred_at="2026-01-02T12:00:00Z")
            newer = db.create_entry(conn, "new", occurred_at="2026-01-03T12:00:00Z")
            coached = db.create_entry(conn, "coached", occurred_at="2026-01-04T12:00:00Z")
            db.create_feedback(conn, coached.id, "first note")
            db.create_feedback(conn, coached.id, "second note")
            db.create_entry(conn, "outside window", occurred_at="2025-01-01T12:00:00Z")
        rc, out, err = self._run("stats", "--since", "2026-01-01", "--until", "2026-01-31")
        self.assertEqual(rc, 0)
        self.assertIn("Entries awaiting feedback: 2", out)
        self.assertNotIn("Coach the oldest first", out)
        command = shlex.split(err.split("Coach the oldest first: ")[1].strip())
        self.assertEqual(command[:3], ["commscoach", "--db", str(self.db_path)])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = cli.main([*command[1:], "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual([e["id"] for e in json.loads(stdout.getvalue())], [older.id, newer.id])

    def test_no_hint_when_empty_or_fully_coached(self) -> None:
        for empty in (True, False):
            with self.subTest(empty=empty):
                if not empty:
                    with closing(db.connect(self.db_path)) as conn:
                        entry = db.create_entry(conn, "coached")
                        db.create_feedback(conn, entry.id, "note")
                rc, out, err = self._run("stats")
                self.assertEqual(rc, 0, err)
                self.assertIn("Entries awaiting feedback: 0", out)
                self.assertNotIn("Coach the oldest first:", out + err)


if __name__ == "__main__":
    unittest.main()
