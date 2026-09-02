"""Tests for stored review CRUD in ``commscoach.db``."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import Review

from .db_case import DBTestCase, patch_timestamps


class ReviewCrudTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.first = db.create_entry(self.conn, "first")
        self.second = db.create_entry(self.conn, "second")
        self.third = db.create_entry(self.conn, "third")

    def test_create_review_returns_review_with_entries_in_requested_order(self) -> None:
        review = db.create_review(
            self.conn,
            "weekly synthesis",
            [self.second.id, self.first.id, self.second.id, self.third.id],
            scope_limit=3,
        )

        self.assertIsInstance(review, Review)
        self.assertEqual(review.body, "weekly synthesis")
        self.assertEqual(review.scope_kind, "latest")
        self.assertEqual(review.scope_limit, 3)
        self.assertEqual(review.created_at, review.updated_at)
        self.assertEqual(
            [entry.id for entry in review.entries],
            [self.second.id, self.first.id, self.third.id],
        )

    def test_create_review_rejects_empty_body(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "   ", [self.first.id])
        self.assertEqual(db.list_reviews(self.conn), ())

    def test_create_review_rejects_empty_entry_list(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", [])
        self.assertEqual(db.list_reviews(self.conn), ())

    def test_create_review_rejects_missing_entry_list(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", None)  # type: ignore[arg-type]
        self.assertEqual(db.list_reviews(self.conn), ())

    def test_create_review_unknown_entry_raises_without_writing(self) -> None:
        with self.assertRaises(db.EntryNotFoundError):
            db.create_review(self.conn, "review", [self.first.id, 9999])
        self.assertEqual(db.list_reviews(self.conn), ())
        (count,) = self.conn.execute(
            "SELECT COUNT(*) FROM review_entries"
        ).fetchone()
        self.assertEqual(count, 0)

    def test_create_review_rejects_negative_scope_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", [self.first.id], scope_limit=-1)

    def test_create_review_rejects_unsupported_scope_kind(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", [self.first.id], scope_kind="all")

    def test_get_review_returns_hydrated_review_or_none(self) -> None:
        created = db.create_review(
            self.conn, "review", [self.first.id, self.second.id]
        )
        loaded = db.get_review(self.conn, created.id)
        self.assertEqual(loaded, created)
        self.assertIsNone(db.get_review(self.conn, 9999))

    def test_list_reviews_newest_first(self) -> None:
        with patch_timestamps("t0", "t1", "t2"):
            db.create_review(self.conn, "first review", [self.first.id])
            db.create_review(self.conn, "second review", [self.second.id])
            db.create_review(self.conn, "third review", [self.third.id])
        bodies = [review.body for review in db.list_reviews(self.conn)]
        self.assertEqual(bodies, ["third review", "second review", "first review"])

    def test_list_reviews_honors_limit(self) -> None:
        for body, entry in (
            ("first review", self.first),
            ("second review", self.second),
            ("third review", self.third),
        ):
            db.create_review(self.conn, body, [entry.id])
        limited = db.list_reviews(self.conn, limit=2)
        self.assertEqual(len(limited), 2)

    def test_list_reviews_rejects_negative_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.list_reviews(self.conn, limit=-1)

    def test_delete_review_returns_true_and_cascades_review_entries(self) -> None:
        review = db.create_review(
            self.conn, "review", [self.first.id, self.second.id]
        )

        self.assertTrue(db.delete_review(self.conn, review.id))
        self.assertIsNone(db.get_review(self.conn, review.id))
        (count,) = self.conn.execute(
            "SELECT COUNT(*) FROM review_entries WHERE review_id = ?",
            (review.id,),
        ).fetchone()
        self.assertEqual(count, 0)

    def test_delete_review_missing_returns_false(self) -> None:
        self.assertFalse(db.delete_review(self.conn, 9999))

    def test_update_review_replaces_body(self) -> None:
        review = db.create_review(self.conn, "first draft", [self.first.id])

        updated = db.update_review(self.conn, review.id, body="tightened synthesis")

        self.assertIsInstance(updated, Review)
        self.assertEqual(updated.id, review.id)
        self.assertEqual(updated.body, "tightened synthesis")
        self.assertEqual(db.get_review(self.conn, review.id), updated)

    def test_update_review_advances_updated_at_only(self) -> None:
        with patch_timestamps("t0", "t1"):
            review = db.create_review(self.conn, "first draft", [self.first.id])
            updated = db.update_review(self.conn, review.id, body="revised")

        self.assertEqual(updated.created_at, review.created_at)
        self.assertEqual(updated.created_at, "t0")
        self.assertEqual(updated.updated_at, "t1")
        self.assertNotEqual(updated.updated_at, updated.created_at)

    def test_update_review_preserves_scope_and_covered_entries(self) -> None:
        review = db.create_review(
            self.conn,
            "first draft",
            [self.second.id, self.first.id],
            scope_limit=7,
        )

        updated = db.update_review(self.conn, review.id, body="revised")

        self.assertEqual(updated.scope_kind, review.scope_kind)
        self.assertEqual(updated.scope_limit, 7)
        self.assertEqual(
            [entry.id for entry in updated.entries],
            [self.second.id, self.first.id],
        )

    def test_update_review_without_body_is_a_no_op(self) -> None:
        review = db.create_review(self.conn, "unchanged", [self.first.id])

        self.assertEqual(db.update_review(self.conn, review.id), review)

    def test_update_review_rejects_empty_body_without_writing(self) -> None:
        review = db.create_review(self.conn, "keep me", [self.first.id])

        with self.assertRaises(db.DBError):
            db.update_review(self.conn, review.id, body="   ")

        self.assertEqual(db.get_review(self.conn, review.id), review)

    def test_update_review_missing_id_raises(self) -> None:
        with self.assertRaises(db.ReviewNotFoundError):
            db.update_review(self.conn, 9999, body="nope")


if __name__ == "__main__":
    unittest.main()
