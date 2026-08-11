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

    def test_get_feedback_returns_row_or_none(self) -> None:
        fb = db.create_feedback(self.conn, self.entry.id, "x")
        found = db.get_feedback(self.conn, fb.id)
        self.assertEqual(found, fb)
        self.assertIsNone(db.get_feedback(self.conn, 9999))


if __name__ == "__main__":
    unittest.main()
