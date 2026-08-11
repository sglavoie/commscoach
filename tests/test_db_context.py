"""Tests for the aggregated entry and review contexts used by the skill."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import EntryContext, ReviewContext, ReviewEntryContext

from .db_case import DBTestCase, patch_timestamps


class ReviewContextTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.me = db.create_person(self.conn, "me", is_self=True)

    def test_review_context_returns_latest_entries_oldest_first_with_feedback(self) -> None:
        with patch_timestamps(
            "e0", "e1", "e2", "e2", "f0", "f1", "f2", "r0", "r1", "r2", "r3"
        ):
            first = db.create_entry(self.conn, "first")
            second = db.create_entry(self.conn, "second")
            third = db.create_entry(self.conn, "third")
            fourth = db.create_entry(self.conn, "fourth")
            db.create_feedback(self.conn, second.id, "second old")
            db.create_feedback(self.conn, second.id, "second new")
            db.create_feedback(self.conn, fourth.id, "fourth note")
            db.create_review(self.conn, "oldest review", [first.id], scope_limit=1)
            db.create_review(self.conn, "second review", [second.id], scope_limit=1)
            db.create_review(self.conn, "third review", [third.id], scope_limit=1)
            db.create_review(self.conn, "fourth review", [fourth.id], scope_limit=1)

        context = db.get_review_context(self.conn, limit=3, prior_review_limit=3)

        self.assertIsInstance(context, ReviewContext)
        self.assertEqual(context.self_person.id, self.me.id)
        self.assertEqual(context.scope_kind, "latest")
        self.assertEqual(context.scope_limit, 3)
        self.assertEqual(
            [entry_ctx.entry.id for entry_ctx in context.entries],
            [second.id, third.id, fourth.id],
        )
        self.assertTrue(
            all(
                isinstance(entry_ctx, ReviewEntryContext)
                for entry_ctx in context.entries
            )
        )
        feedback_by_entry = {
            entry_ctx.entry.id: [feedback.body for feedback in entry_ctx.feedback]
            for entry_ctx in context.entries
        }
        self.assertEqual(feedback_by_entry[second.id], ["second old", "second new"])
        self.assertEqual(feedback_by_entry[third.id], [])
        self.assertEqual(feedback_by_entry[fourth.id], ["fourth note"])
        self.assertEqual(
            [review.body for review in context.prior_reviews],
            ["second review", "third review", "fourth review"],
        )

    def test_review_context_allows_zero_entry_limit(self) -> None:
        entry = db.create_entry(self.conn, "reviewed")
        review = db.create_review(self.conn, "prior review", [entry.id], scope_limit=1)

        context = db.get_review_context(self.conn, limit=0, prior_review_limit=1)

        self.assertEqual(context.scope_limit, 0)
        self.assertEqual(context.entries, ())
        self.assertEqual([r.id for r in context.prior_reviews], [review.id])

    def test_review_context_missing_self_raises(self) -> None:
        db.delete_person(self.conn, self.me.id)
        db.create_entry(self.conn, "orphan")

        with self.assertRaises(db.MissingSelfError):
            db.get_review_context(self.conn)

    def test_review_context_rejects_negative_limits(self) -> None:
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, limit=-1)
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, prior_review_limit=-1)


class EntryContextTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.me = db.create_person(self.conn, "me", is_self=True)
        self.alice = db.create_person(self.conn, "alice")
        self.bob = db.create_person(self.conn, "bob")

    def test_context_excludes_self_from_people(self) -> None:
        entry = db.create_entry(
            self.conn,
            "team standup",
            people=["me", "alice", "bob"],
        )
        ctx = db.get_entry_context(self.conn, entry.id)
        self.assertIsInstance(ctx, EntryContext)
        self.assertEqual(ctx.self_person.id, self.me.id)
        handles = {p.handle for p in ctx.people}
        self.assertEqual(handles, {"alice", "bob"})
        # Entry itself still reflects what was actually linked, including self
        entry_handles = {p.handle for p in ctx.entry.people}
        self.assertEqual(entry_handles, {"me", "alice", "bob"})

    def test_context_includes_prior_feedback_oldest_first(self) -> None:
        entry = db.create_entry(self.conn, "msg")
        with patch_timestamps("f0", "f1"):
            db.create_feedback(self.conn, entry.id, "first")
            db.create_feedback(self.conn, entry.id, "second")
        ctx = db.get_entry_context(self.conn, entry.id)
        bodies = [f.body for f in ctx.prior_feedback]
        self.assertEqual(bodies, ["first", "second"])

    def test_context_missing_entry_raises(self) -> None:
        with self.assertRaises(db.EntryNotFoundError):
            db.get_entry_context(self.conn, 9999)

    def test_context_missing_self_raises(self) -> None:
        db.delete_person(self.conn, self.me.id)
        entry = db.create_entry(self.conn, "orphan", people=["alice"])
        with self.assertRaises(db.MissingSelfError):
            db.get_entry_context(self.conn, entry.id)


if __name__ == "__main__":
    unittest.main()
