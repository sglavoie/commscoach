"""Tests for windowed journal statistics and weekly cadence."""

from __future__ import annotations

import unittest
from datetime import date

from commscoach import db

from .db_case import DBTestCase
from .timezone_case import local_timezone


class StatsTests(DBTestCase):
    def _add_person(self, handle: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO people"
            " (handle, name, is_self, created_at, updated_at)"
            " VALUES (?, NULL, 0, '2025-01-01T00:00:00+00:00',"
            " '2025-01-01T00:00:00+00:00')",
            (handle,),
        )
        return int(cur.lastrowid)

    def _add_entry(
        self,
        occurred_at: str,
        *,
        source: str | None = None,
        tags: tuple[str, ...] = (),
        people: tuple[int, ...] = (),
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO entries"
            " (body, source, occurred_at, created_at, updated_at)"
            " VALUES ('body', ?, ?, ?, ?)",
            (source, occurred_at, occurred_at, occurred_at),
        )
        entry_id = int(cur.lastrowid)
        for tag in tags:
            tag_cur = self.conn.execute("INSERT INTO tags (name) VALUES (?)", (tag,))
            self.conn.execute(
                "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_cur.lastrowid),
            )
        for person_id in people:
            self.conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person_id),
            )
        return entry_id

    def test_empty_database_is_all_zero_without_division(self) -> None:
        stats = db.get_stats(self.conn)

        self.assertEqual(stats.entry_count, 0)
        self.assertEqual(stats.entries_with_feedback, 0)
        self.assertEqual(stats.entries_per_week, 0.0)
        self.assertEqual(stats.current_weekly_streak, 0)
        self.assertEqual(stats.longest_weekly_streak, 0)
        self.assertEqual(stats.weekly_buckets, ())
        self.assertIsNone(stats.first_entry)
        self.assertIsNone(stats.last_entry)
        self.assertIsNone(stats.last_review)

    def test_weekly_cadence_and_streak_cross_year_with_gap(self) -> None:
        timestamps = (
            "2025-12-29T09:00:00+00:00",
            "2026-01-05T09:00:00+00:00",
            "2026-01-19T09:00:00+00:00",
            "2026-01-26T09:00:00+00:00",
        )
        for timestamp in timestamps:
            self._add_entry(timestamp)
        self.conn.commit()

        with local_timezone("UTC"):
            stats = db.get_stats(self.conn, today=date(2026, 1, 28))

        self.assertEqual(stats.entries_per_week, 0.8)
        self.assertEqual(stats.current_weekly_streak, 2)
        self.assertEqual(stats.longest_weekly_streak, 2)
        self.assertEqual(
            stats.weekly_buckets,
            (
                ("2025-12-29", 1),
                ("2026-01-05", 1),
                ("2026-01-12", 0),
                ("2026-01-19", 1),
                ("2026-01-26", 1),
            ),
        )

    def test_stats_read_one_snapshot_and_leave_no_open_transaction(self) -> None:
        self._add_entry("2026-01-05T09:00:00+00:00")
        self.conn.commit()
        stats = db.get_stats(self.conn)
        self.assertEqual(stats.entry_count, 1)
        self.assertFalse(self.conn.in_transaction)

    def test_streak_that_ended_before_last_week_is_not_current(self) -> None:
        for timestamp in ("2026-01-05T09:00:00+00:00", "2026-01-12T09:00:00+00:00"):
            self._add_entry(timestamp)
        self.conn.commit()

        with local_timezone("UTC"):
            ongoing = db.get_stats(self.conn, today=date(2026, 1, 21))
            ended = db.get_stats(self.conn, today=date(2026, 3, 1))
            windowed = db.get_stats(self.conn, until="2026-01-18", today=date(2026, 3, 1))

        self.assertEqual(ongoing.current_weekly_streak, 2)
        self.assertEqual(ended.current_weekly_streak, 0)
        self.assertEqual(ended.longest_weekly_streak, 2)
        self.assertEqual(windowed.current_weekly_streak, 2)

    def test_top_sources_group_case_insensitively(self) -> None:
        for source in ("Slack", "slack", "SLACK", "email"):
            self._add_entry("2026-01-05T09:00:00+00:00", source=source)
        self.conn.commit()
        stats = db.get_stats(self.conn)
        self.assertEqual(stats.top_sources, (("SLACK", 3), ("email", 1)))

    def test_top_sources_group_non_ascii_case_together(self) -> None:
        for source in ("ÉQUIPE", "équipe", "Équipe", "equipe"):
            self._add_entry("2026-01-05T09:00:00+00:00", source=source)
        self.conn.commit()
        stats = db.get_stats(self.conn)
        self.assertEqual(stats.top_sources, (("ÉQUIPE", 3), ("equipe", 1)))

    def test_future_dated_entry_does_not_reset_current_streak(self) -> None:
        for timestamp in (
            "2026-01-05T09:00:00+00:00",
            "2026-01-12T09:00:00+00:00",
            "2026-01-19T09:00:00+00:00",
            "2026-02-09T09:00:00+00:00",  # three weeks ahead of "today"
        ):
            self._add_entry(timestamp)
        self.conn.commit()

        with local_timezone("UTC"):
            stats = db.get_stats(self.conn, today=date(2026, 1, 21))

        self.assertEqual(stats.current_weekly_streak, 3)
        self.assertEqual(stats.longest_weekly_streak, 3)

    def test_weeks_follow_the_local_calendar(self) -> None:
        # Sunday 2026-01-11 21:00 in New York is already Monday in UTC.
        self._add_entry("2026-01-12T02:00:00+00:00")
        self.conn.commit()

        with local_timezone("America/New_York"):
            stats = db.get_stats(self.conn, today=date(2026, 1, 11))

        self.assertEqual(stats.weekly_buckets, (("2026-01-05", 1),))

    def test_top_dimensions_are_ranked_and_truncated(self) -> None:
        alice = self._add_person("alice")
        bob = self._add_person("bob")
        first = self._add_entry(
            "2026-01-05T09:00:00+00:00",
            source="slack",
            tags=("focus",),
            people=(alice,),
        )
        second = self._add_entry(
            "2026-01-06T09:00:00+00:00",
            source="slack",
            people=(alice, bob),
        )
        focus_id = self.conn.execute("SELECT id FROM tags WHERE name = 'focus'").fetchone()[0]
        self.conn.execute(
            "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            (second, focus_id),
        )
        self.conn.execute(
            "INSERT INTO feedback"
            " (entry_id, body, created_at, updated_at) VALUES (?, 'note', ?, ?)",
            (first, "2026-01-07T00:00:00+00:00", "2026-01-07T00:00:00+00:00"),
        )
        self.conn.commit()

        stats = db.get_stats(self.conn, top=1)

        self.assertEqual(stats.people_count, 2)
        self.assertEqual(stats.tag_count, 1)
        self.assertEqual(stats.feedback_count, 1)
        self.assertEqual(stats.entries_with_feedback, 1)
        self.assertEqual(stats.top_tags, (("focus", 2),))
        self.assertEqual(stats.top_sources, (("slack", 2),))
        self.assertEqual(stats.top_people, (("alice", 2),))

    def test_window_filters_entries_links_reviews_and_goal_events(self) -> None:
        alice = self._add_person("alice")
        bob = self._add_person("bob")
        old_entry = self._add_entry("2026-01-01T10:00:00+00:00", tags=("old",), people=(alice,))
        current_entry = self._add_entry(
            "2026-02-10T10:00:00+00:00", tags=("current",), people=(bob,)
        )
        for entry_id, created_at in (
            (old_entry, "2026-01-02T00:00:00+00:00"),
            (current_entry, "2026-02-11T00:00:00+00:00"),
        ):
            self.conn.execute(
                "INSERT INTO feedback"
                " (entry_id, body, created_at, updated_at) VALUES (?, 'note', ?, ?)",
                (entry_id, created_at, created_at),
            )
        self.conn.execute(
            "INSERT INTO reviews"
            " (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES ('old', 'latest', 1, '2026-01-03T00:00:00+00:00',"
            " '2026-01-03T00:00:00+00:00')"
        )
        self.conn.execute(
            "INSERT INTO reviews"
            " (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES ('current', 'latest', 1, '2026-02-12T00:00:00+00:00',"
            " '2026-02-12T00:00:00+00:00')"
        )
        self.conn.execute(
            "INSERT INTO goals"
            " (title, status, created_at, updated_at)"
            " VALUES ('open', 'active', '2026-02-08T00:00:00+00:00',"
            " '2026-02-08T00:00:00+00:00')"
        )
        self.conn.execute(
            "INSERT INTO goals"
            " (title, status, closed_at, created_at, updated_at)"
            " VALUES ('closed', 'done', '2026-02-15T00:00:00+00:00',"
            " '2025-12-01T00:00:00+00:00', '2026-02-15T00:00:00+00:00')"
        )
        self.conn.commit()

        stats = db.get_stats(self.conn, since="2026-02-01", until="2026-02-28")

        self.assertEqual(stats.entry_count, 1)
        self.assertEqual(stats.people_count, 1)
        self.assertEqual(stats.tag_count, 1)
        self.assertEqual(stats.feedback_count, 1)
        self.assertEqual(stats.entries_with_feedback, 1)
        self.assertEqual(stats.review_count, 1)
        self.assertEqual(stats.open_goal_count, 1)
        self.assertEqual(stats.closed_goal_count, 1)
        self.assertEqual(stats.first_entry, "2026-02-10T10:00:00+00:00")
        self.assertEqual(stats.last_review, "2026-02-12T00:00:00+00:00")
        self.assertEqual(stats.top_tags, (("current", 1),))
        self.assertEqual(stats.top_people, (("bob", 1),))

    def test_negative_top_is_semantic_error(self) -> None:
        with self.assertRaisesRegex(db.DBError, "top must be non-negative"):
            db.get_stats(self.conn, top=-1)

    def test_reversed_date_range_is_semantic_error(self) -> None:
        with self.assertRaisesRegex(db.DBError, "since must not be after until"):
            db.get_stats(self.conn, since="2026-06-03", until="2026-06-02")

    def test_single_date_bound_is_valid(self) -> None:
        self.assertEqual(db.get_stats(self.conn, until="2026-06-02").entry_count, 0)


if __name__ == "__main__":
    unittest.main()
