"""Tests for goal checks and their summaries in ``commscoach.db``."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import GoalCheck, GoalCheckSummary

from .db_case import DBTestCase


class GoalCheckCrudTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry = db.create_entry(self.conn, "msg", occurred_at="2026-10-01T09:00:00+00:00")
        self.goal = db.create_goal(self.conn, "Lead with the ask")
        self.other = db.create_goal(self.conn, "Slow down")

    def test_create_feedback_stores_checks_in_the_same_call(self) -> None:
        feedback = db.create_feedback(
            self.conn,
            self.entry.id,
            "note",
            goal_checks=[
                (self.other.id, "hurt", None),
                (self.goal.id, "advanced", " Asked first "),
            ],
        )

        checks = db.list_feedback_goal_checks(self.conn, [feedback.id])[feedback.id]
        self.assertEqual([c.goal_id for c in checks], [self.goal.id, self.other.id])
        first = checks[0]
        self.assertIsInstance(first, GoalCheck)
        self.assertEqual(first.feedback_id, feedback.id)
        self.assertEqual(first.entry_id, self.entry.id)
        self.assertEqual(first.verdict, "advanced")
        self.assertEqual(first.note, "Asked first")
        self.assertEqual(first.at, "2026-10-01T09:00:00+00:00")
        self.assertEqual(first.created_at, feedback.created_at)

    def test_invalid_check_stores_neither_the_note_nor_any_check(self) -> None:
        cases = {
            "unknown goal": ([(self.goal.id, "advanced", None), (999, "hurt", None)], "999"),
            "bad verdict": ([(self.goal.id, "won", None)], "verdict"),
            "duplicate goal": (
                [(self.goal.id, "advanced", None), (self.goal.id, "hurt", None)],
                "more than once",
            ),
            "blank note": ([(self.goal.id, "advanced", "  ")], "note"),
            "multi-line note": ([(self.goal.id, "advanced", "a\nb")], "single line"),
        }
        for label, (checks, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(db.DBError, message):
                db.create_feedback(self.conn, self.entry.id, "note", goal_checks=checks)
        self.assertEqual(db.list_feedback(self.conn, self.entry.id), ())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM goal_checks").fetchone()[0], 0)

    def test_unknown_goal_is_a_goal_not_found_error(self) -> None:
        with self.assertRaises(db.GoalNotFoundError):
            db.validate_goal_checks(self.conn, [(999, "advanced", None)])

    def test_add_goal_checks_refuses_a_goal_the_note_already_checks(self) -> None:
        feedback = db.create_feedback(
            self.conn, self.entry.id, "note", goal_checks=[(self.goal.id, "advanced", None)]
        )
        with self.assertRaisesRegex(db.DBError, "already checks goal"):
            db.add_goal_checks(
                self.conn,
                feedback.id,
                [(self.other.id, "hurt", None), (self.goal.id, "hurt", None)],
            )
        self.assertEqual(
            len(db.list_feedback_goal_checks(self.conn, [feedback.id])[feedback.id]), 1
        )
        with self.assertRaises(db.FeedbackNotFoundError):
            db.add_goal_checks(self.conn, 999, [(self.goal.id, "hurt", None)])

    def test_replace_goal_checks_keeps_unchanged_rows_and_can_clear(self) -> None:
        feedback = db.create_feedback(
            self.conn,
            self.entry.id,
            "note",
            goal_checks=[(self.goal.id, "advanced", None), (self.other.id, "hurt", None)],
        )
        before = db.list_feedback_goal_checks(self.conn, [feedback.id])[feedback.id]

        after = db.replace_goal_checks(
            self.conn,
            feedback.id,
            [(self.goal.id, "advanced", None), (self.other.id, "neutral", None)],
        )

        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1].verdict, "neutral")
        self.assertEqual(db.replace_goal_checks(self.conn, feedback.id, []), ())
        with self.assertRaises(db.GoalNotFoundError):
            db.replace_goal_checks(self.conn, feedback.id, [(999, "hurt", None)])

    def test_deleting_feedback_goal_or_entry_cascades_to_checks(self) -> None:
        for remove in (
            lambda feedback: db.delete_feedback(self.conn, feedback.id),
            lambda feedback: db.delete_entry(self.conn, feedback.entry_id),
            lambda feedback: db.delete_goal(self.conn, self.goal.id),
        ):
            entry = db.create_entry(self.conn, "msg")
            feedback = db.create_feedback(
                self.conn, entry.id, "note", goal_checks=[(self.goal.id, "advanced", None)]
            )
            remove(feedback)
            self.assertEqual(db.list_goal_checks(self.conn, self.goal.id), ())


class GoalCheckTrailTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.goal = db.create_goal(self.conn, "Lead with the ask")
        self.quiet = db.create_goal(self.conn, "Never checked")

    def _check(self, occurred_at: str | None, verdict: str) -> db.Feedback:
        entry = db.create_entry(self.conn, "msg", occurred_at=occurred_at)
        return db.create_feedback(
            self.conn, entry.id, "note", goal_checks=[(self.goal.id, verdict, None)]
        )

    def test_trail_orders_by_when_the_interaction_happened(self) -> None:
        # Logged out of order: the backfilled conversation belongs first.
        self._check("2026-10-05T09:00:00+00:00", "hurt")
        self._check("2026-10-01T09:00:00+00:00", "advanced")
        undated = self._check(None, "neutral")

        trail = db.list_goal_checks(self.conn, self.goal.id)

        self.assertEqual([c.verdict for c in trail], ["advanced", "hurt", "neutral"])
        entry = db.get_entry(self.conn, undated.entry_id)
        assert entry is not None
        self.assertEqual(trail[-1].at, entry.created_at)

    def test_summaries_count_verdicts_and_name_milestones(self) -> None:
        self._check("2026-10-02T09:00:00+00:00", "hurt")
        self._check("2026-10-03T09:00:00+00:00", "advanced")
        self._check("2026-10-04T09:00:00+00:00", "advanced")
        self._check("2026-10-05T09:00:00+00:00", "neutral")

        summaries = db.goal_check_summaries(self.conn, [self.goal.id, self.quiet.id, 999])

        self.assertEqual(
            summaries[self.goal.id],
            GoalCheckSummary(
                advanced=2,
                hurt=1,
                neutral=1,
                last_verdict="neutral",
                last_at="2026-10-05T09:00:00+00:00",
                first_advanced_at="2026-10-03T09:00:00+00:00",
            ),
        )
        self.assertEqual(summaries[self.goal.id].total, 4)
        self.assertEqual(summaries[self.quiet.id], GoalCheckSummary())
        self.assertEqual(summaries[self.quiet.id].total, 0)
        self.assertEqual(summaries[999], GoalCheckSummary())
        self.assertEqual(db.goal_check_summaries(self.conn, []), {})

    def test_summaries_batch_more_goals_than_one_chunk(self) -> None:
        self._check("2026-10-03T09:00:00+00:00", "advanced")
        ids = [self.goal.id, *range(10_000, 10_700)]

        summaries = db.goal_check_summaries(self.conn, ids)

        self.assertEqual(len(summaries), len(ids))
        self.assertEqual(summaries[self.goal.id].advanced, 1)

    def test_summary_without_advanced_checks_has_no_first_win(self) -> None:
        self._check("2026-10-03T09:00:00+00:00", "hurt")

        summary = db.goal_check_summaries(self.conn, [self.goal.id])[self.goal.id]

        self.assertIsNone(summary.first_advanced_at)
        self.assertEqual(summary.last_verdict, "hurt")


if __name__ == "__main__":
    unittest.main()
