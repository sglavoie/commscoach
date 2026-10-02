"""Tests for feedback CRUD in ``commscoach.db``."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import Feedback

from .db_case import DBTestCase, patch_timestamps


class FeedbackCrudTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry = db.create_entry(self.conn, "msg")

    def test_create_feedback_attaches_to_entry(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "great delivery")
        self.assertIsInstance(fb, Feedback)
        self.assertEqual(fb.entry_id, self.entry.id)
        self.assertEqual(fb.body, "great delivery")
        self.assertEqual(fb.created_at, fb.updated_at)

    def test_create_feedback_rejects_empty_body(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_feedback(self.conn, self.entry.id, "   ")

    def test_create_feedback_missing_entry_raises(self) -> None:
        with self.assertRaises(db.EntryNotFoundError):
            db.create_feedback(self.conn, 9999, "nope")

    def test_list_feedback_oldest_first(self) -> None:
        with patch_timestamps("t0", "t1", "t2"):
            db.create_feedback(self.conn, self.entry.id, "first")
            db.create_feedback(self.conn, self.entry.id, "second")
            db.create_feedback(self.conn, self.entry.id, "third")
        bodies = [f.body for f in db.list_feedback(self.conn, self.entry.id)]
        self.assertEqual(bodies, ["first", "second", "third"])

    def test_list_feedback_unknown_entry_returns_empty(self) -> None:
        self.assertEqual(db.list_feedback(self.conn, 9999), ())

    def test_feedback_status_lookup_handles_large_lists_and_multiple_notes(self):
        self.assertEqual(db.feedback_entry_ids(self.conn, ()), set())
        db.create_feedback(self.conn, self.entry.id, "First")
        db.create_feedback(self.conn, self.entry.id, "Second")
        other = db.create_entry(self.conn, "Other")
        db.create_feedback(self.conn, other.id, "Other note")
        # Keep the real ID beyond the first batch, with absent IDs and duplicates.
        requested = [*range(1000, 1600), self.entry.id, self.entry.id]
        self.assertEqual(db.feedback_entry_ids(self.conn, requested), {self.entry.id})

    def test_get_feedback_returns_row_or_none(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "x")
        found = db.get_feedback(self.conn, fb.id)
        self.assertEqual(found, fb)
        self.assertIsNone(db.get_feedback(self.conn, 9999))

    def test_update_feedback_replaces_body(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "first draft")

        updated = db.update_feedback(self.conn, fb.id, body="corrected note")

        self.assertIsInstance(updated, Feedback)
        self.assertEqual(updated.id, fb.id)
        self.assertEqual(updated.body, "corrected note")
        self.assertEqual(updated.entry_id, self.entry.id)
        self.assertEqual(db.get_feedback(self.conn, fb.id), updated)

    def test_update_feedback_advances_updated_at_only(self) -> None:
        with patch_timestamps("t0", "t1"):
            fb = db.create_feedback(self.conn, self.entry.id, "first draft")
            updated = db.update_feedback(self.conn, fb.id, body="corrected")

        self.assertEqual(updated.created_at, fb.created_at)
        self.assertEqual(updated.created_at, "t0")
        self.assertEqual(updated.updated_at, "t1")
        self.assertNotEqual(updated.updated_at, updated.created_at)

    def test_update_feedback_rejects_empty_body_without_writing(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "keep me")

        with self.assertRaises(db.DBError):
            db.update_feedback(self.conn, fb.id, body="   ")

        self.assertEqual(db.get_feedback(self.conn, fb.id), fb)

    def test_update_feedback_missing_id_raises(self) -> None:
        with self.assertRaises(db.FeedbackNotFoundError):
            db.update_feedback(self.conn, 9999, body="nope")

    def test_delete_feedback_returns_true_and_removes_row(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "delete me")

        self.assertTrue(db.delete_feedback(self.conn, fb.id))
        self.assertIsNone(db.get_feedback(self.conn, fb.id))
        self.assertEqual(db.list_feedback(self.conn, self.entry.id), ())

    def test_delete_feedback_leaves_the_entry_and_siblings_intact(self) -> None:
        first = db.create_feedback(self.conn, self.entry.id, "first")
        second = db.create_feedback(self.conn, self.entry.id, "second")

        self.assertTrue(db.delete_feedback(self.conn, first.id))

        self.assertEqual(db.list_feedback(self.conn, self.entry.id), (second,))
        self.assertIsNotNone(db.get_entry(self.conn, self.entry.id))

    def test_delete_feedback_missing_returns_false(self) -> None:
        self.assertFalse(db.delete_feedback(self.conn, 9999))


if __name__ == "__main__":
    unittest.main()
