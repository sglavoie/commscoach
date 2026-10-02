"""Tests for the top-level ``commscoach stats`` command."""

from __future__ import annotations

import io
import json
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
        rc, _, err = self._run(
            "stats", "--since", "2026-06-03", "--until", "2026-06-02"
        )
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

    def test_negative_weeks_is_semantic_error(self) -> None:
        rc, _, err = self._run("stats", "--weeks", "-1")

        self.assertEqual(rc, 1)
        self.assertIn("weeks must be non-negative", err)


if __name__ == "__main__":
    unittest.main()
