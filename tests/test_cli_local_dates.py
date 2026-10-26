"""Date-window regressions through the real CLI and temporary databases."""

import json
from contextlib import closing
from unittest import mock

from commscoach import db

from .cli_entry_case import EntryTestBase
from .timezone_case import local_timezone


class LocalDateTests(EntryTestBase):
    def setUp(self):
        super().setUp()
        self.enterContext(local_timezone("America/Mexico_City"))
        self._create_person("me", is_self=True)

    def _json(self, *args):
        rc, out, err = self._run("--json", *args)
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def test_local_day_boundaries_agree_across_commands(self):
        stamps = [
            "2026-10-02T05:59:59.999999+00:00",  # previous local day
            "2026-10-02T06:00:00+00:00",  # local midnight
            "2026-10-03T04:00:00+00:00",  # local 10 p.m.
            "2026-10-03T05:59:59.999999+00:00",  # final local microsecond
            "2026-10-03T06:00:00+00:00",  # next local day
        ]
        entries = [self._seed_entry(str(i), occurred_at=ts) for i, ts in enumerate(stamps)]
        expected = {entry.id for entry in entries[1:4]}
        bounds = ("--since", "2026-10-02", "--until", "2026-10-02")
        for command in (("entry", "list"), ("entry", "search")):
            with self.subTest(command=command):
                self.assertEqual({row["id"] for row in self._json(*command, *bounds)}, expected)
        self.assertEqual(self._json("stats", *bounds)["entry_count"], 3)
        context = self._json("review", "context", *bounds)
        self.assertEqual({row["entry"]["id"] for row in context["entries"]}, expected)
        self.assertEqual(context["scope"]["since"], "2026-10-02T06:00:00+00:00")
        self.assertEqual(context["scope"]["until"], "2026-10-03T05:59:59.999999+00:00")
        review = self._json("review", "add", "Summary", *bounds, "--entry", str(entries[2].id))
        self.assertEqual(review["scope_since"], context["scope"]["since"])
        self.assertEqual(review["scope_until"], context["scope"]["until"])
        # Export/import must preserve the resolved scope without a migration.
        with closing(db.connect(self.db_path)) as conn:
            archive = db.dump_to_dict(conn)
        restored = self.tmpdir / "restored.sqlite3"
        db.init_db(restored)
        with closing(db.connect(restored)) as conn:
            db.load_from_dict(conn, archive)
            saved = db.get_review(conn, review["id"])
            self.assertEqual(saved.scope_since, review["scope_since"])
            self.assertEqual(saved.scope_until, review["scope_until"])

    def test_exact_timestamp_bounds_include_equal_seconds_and_microseconds(self):
        for fraction in ("", ".000001", ".123456"):
            with self.subTest(fraction=fraction):
                stamp = f"2026-10-02T16:30:00{fraction}+00:00"
                entry = self._seed_entry(stamp, occurred_at=stamp)
                bounds = ("--since", stamp, "--until", stamp)
                result = self._json("entry", "search", *bounds)
                self.assertEqual([row["id"] for row in result], [entry.id])
                self.assertEqual(self._json("stats", *bounds)["entry_count"], 1)
        # An explicit local offset must select the same instant.
        result = self._json(
            "entry",
            "list",
            "--since",
            "2026-10-02T10:30:00.123456-06:00",
            "--until",
            "2026-10-02T10:30:00.123456-06:00",
        )
        self.assertEqual([row["id"] for row in result], [entry.id])

    def test_same_day_reversed_times_are_rejected(self):
        for command in (
            ("entry", "list"),
            ("entry", "search"),
            ("stats",),
            ("review", "context"),
        ):
            with self.subTest(command=command):
                rc, out, err = self._run(
                    *command,
                    "--since",
                    "2026-10-02T18:00Z",
                    "--until",
                    "2026-10-02T17:00Z",
                )
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("--since must not be after --until", err)

    def test_stats_and_created_at_fallback_use_same_local_window(self):
        with closing(db.connect(self.db_path)) as conn:
            with mock.patch(
                "commscoach.db.common.utcnow_iso",
                return_value="2026-10-03T04:00:00+00:00",
            ):
                entry = db.create_entry(conn, "Late evening, no occurrence time")
                db.create_feedback(conn, entry.id, "Feedback")
                db.create_review(conn, "Review", [entry.id])
                db.create_goal(conn, "Active")
                closed = db.create_goal(conn, "Closed")
                db.close_goal(conn, closed.id)
        bounds = ("--since", "2026-10-02", "--until", "2026-10-02")
        self.assertEqual([row["id"] for row in self._json("entry", "list", *bounds)], [entry.id])
        stats = self._json("stats", *bounds)
        for key in (
            "entry_count",
            "feedback_count",
            "review_count",
            "open_goal_count",
            "closed_goal_count",
        ):
            self.assertEqual(stats[key], 1, key)

    def test_system_dst_boundaries_use_target_day_offset(self):
        with local_timezone("America/New_York"):
            for day, start, end in (
                (
                    "2026-03-08",
                    "2026-03-08T05:00:00+00:00",
                    "2026-03-09T03:59:59.999999+00:00",
                ),
                (
                    "2026-11-01",
                    "2026-11-01T04:00:00+00:00",
                    "2026-11-02T04:59:59.999999+00:00",
                ),
            ):
                with self.subTest(day=day):
                    context = self._json("review", "context", "--since", day, "--until", day)
                    self.assertEqual(context["scope"]["since"], start)
                    self.assertEqual(context["scope"]["until"], end)
