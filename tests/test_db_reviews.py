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
        (count,) = self.conn.execute("SELECT COUNT(*) FROM review_entries").fetchone()
        self.assertEqual(count, 0)

    def test_create_review_rejects_negative_scope_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", [self.first.id], scope_limit=-1)

    def test_create_review_rejects_unsupported_scope_kind(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_review(self.conn, "review", [self.first.id], scope_kind="all")

    def test_get_review_returns_hydrated_review_or_none(self) -> None:
        created = db.create_review(self.conn, "review", [self.first.id, self.second.id])
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
        review = db.create_review(self.conn, "review", [self.first.id, self.second.id])

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


class ReviewScopeTests(DBTestCase):
    """The scope matrix: one create per kind plus every rejection it enforces."""

    def setUp(self) -> None:
        super().setUp()
        self.alice = db.create_person(self.conn, "Alice")
        self.entry = db.create_entry(self.conn, "standup", people=["alice"], tags=["conflict"])

    def _create(self, **scope: object) -> Review:
        return db.create_review(self.conn, "synthesis", [self.entry.id], **scope)

    def test_scope_kinds_covers_every_kind_the_schema_check_allows(self) -> None:
        self.assertEqual(db.SCOPE_KINDS, ("latest", "person", "tag", "range"))

    def test_create_latest_scope_leaves_every_other_axis_null(self) -> None:
        review = self._create(scope_kind="latest", scope_limit=20)

        self.assertEqual(review.scope_kind, "latest")
        self.assertEqual(review.scope_limit, 20)
        self.assertIsNone(review.scope_person)
        self.assertIsNone(review.scope_person_handle)
        self.assertIsNone(review.scope_tag)
        self.assertIsNone(review.scope_since)
        self.assertIsNone(review.scope_until)

    def test_create_person_scope_resolves_and_links_the_person(self) -> None:
        review = self._create(scope_kind="person", scope_person_handle="alice")

        self.assertEqual(review.scope_kind, "person")
        self.assertIsNotNone(review.scope_person)
        assert review.scope_person is not None
        self.assertEqual(review.scope_person.id, self.alice.id)
        self.assertEqual(review.scope_person_handle, self.alice.handle)
        self.assertIsNone(review.scope_tag)
        self.assertEqual(db.get_review(self.conn, review.id), review)

    def test_create_person_scope_stores_the_canonical_handle_casing(self) -> None:
        # ``people.handle`` keeps the casing it was created with, so the
        # canonical form of ``ALICE`` is the person's own ``Alice``.
        self.assertEqual(self.alice.handle, "Alice")

        review = self._create(scope_kind="person", scope_person_handle="ALICE")

        self.assertEqual(review.scope_person_handle, "Alice")
        (stored,) = self.conn.execute(
            "SELECT scope_person_handle FROM reviews WHERE id = ?", (review.id,)
        ).fetchone()
        self.assertEqual(stored, "Alice")

    def test_create_tag_scope_normalizes_the_tag(self) -> None:
        review = self._create(scope_kind="tag", scope_tag="  Conflict ")

        self.assertEqual(review.scope_kind, "tag")
        self.assertEqual(review.scope_tag, "conflict")
        self.assertIsNone(review.scope_person_handle)

    def test_create_range_scope_accepts_one_or_both_bounds(self) -> None:
        both = self._create(scope_kind="range", scope_since="2026-01-01", scope_until="2026-01-31")
        self.assertEqual((both.scope_since, both.scope_until), ("2026-01-01", "2026-01-31"))

        since_only = self._create(scope_kind="range", scope_since="2026-02-01")
        self.assertEqual(since_only.scope_since, "2026-02-01")
        self.assertIsNone(since_only.scope_until)

        until_only = self._create(scope_kind="range", scope_until="2026-03-01")
        self.assertIsNone(until_only.scope_since)
        self.assertEqual(until_only.scope_until, "2026-03-01")

    def test_create_rejects_an_unknown_scope_kind(self) -> None:
        with self.assertRaises(db.DBError):
            self._create(scope_kind="everything")
        self._assert_no_reviews()

    def test_create_rejects_an_axis_belonging_to_another_kind(self) -> None:
        mismatches = (
            {"scope_kind": "latest", "scope_tag": "conflict"},
            {"scope_kind": "latest", "scope_person_handle": "alice"},
            {"scope_kind": "latest", "scope_since": "2026-01-01"},
            {"scope_kind": "latest", "scope_until": "2026-01-01"},
            {"scope_kind": "person", "scope_person_handle": "alice", "scope_tag": "conflict"},
            {"scope_kind": "person", "scope_person_handle": "alice", "scope_since": "2026-01-01"},
            {"scope_kind": "tag", "scope_tag": "conflict", "scope_person_handle": "alice"},
            {"scope_kind": "tag", "scope_tag": "conflict", "scope_until": "2026-01-01"},
            {"scope_kind": "range", "scope_since": "2026-01-01", "scope_tag": "conflict"},
            {"scope_kind": "range", "scope_since": "2026-01-01", "scope_person_handle": "alice"},
        )
        for scope in mismatches:
            with self.subTest(scope=scope), self.assertRaises(db.DBError):
                self._create(**scope)
        self._assert_no_reviews()

    def test_create_rejects_a_kind_missing_its_own_axis(self) -> None:
        missing = (
            {"scope_kind": "person"},
            {"scope_kind": "person", "scope_person_handle": "   "},
            {"scope_kind": "tag"},
            {"scope_kind": "tag", "scope_tag": "   "},
            {"scope_kind": "range"},
        )
        for scope in missing:
            with self.subTest(scope=scope), self.assertRaises(db.DBError):
                self._create(**scope)
        self._assert_no_reviews()

    def test_create_rejects_non_iso_range_bounds(self) -> None:
        for scope in (
            {"scope_kind": "range", "scope_since": "yesterday"},
            {"scope_kind": "range", "scope_until": "2026-13-01"},
            {"scope_kind": "range", "scope_since": 20260101},
        ):
            with self.subTest(scope=scope), self.assertRaises(db.DBError):
                self._create(**scope)
        self._assert_no_reviews()

    def test_create_rejects_a_range_that_ends_before_it_starts(self) -> None:
        with self.assertRaises(db.DBError):
            self._create(scope_kind="range", scope_since="2026-02-01", scope_until="2026-01-01")
        self._assert_no_reviews()

    def test_create_unknown_person_handle_raises_without_writing(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            self._create(scope_kind="person", scope_person_handle="nobody")

        self._assert_no_reviews()
        (count,) = self.conn.execute("SELECT COUNT(*) FROM review_entries").fetchone()
        self.assertEqual(count, 0)

    def _assert_no_reviews(self) -> None:
        self.assertEqual(db.list_reviews(self.conn), ())


class ListReviewsScopeFilterTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        db.create_person(self.conn, "Alice")
        db.create_person(self.conn, "Bob")
        entry = db.create_entry(self.conn, "standup", people=["alice", "bob"])
        self.latest = db.create_review(self.conn, "latest", [entry.id], scope_limit=5)
        self.alice_review = db.create_review(
            self.conn,
            "alice",
            [entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        self.bob_review = db.create_review(
            self.conn,
            "bob",
            [entry.id],
            scope_kind="person",
            scope_person_handle="bob",
        )
        self.tag_review = db.create_review(
            self.conn,
            "conflict",
            [entry.id],
            scope_kind="tag",
            scope_tag="conflict",
        )
        self.range_review = db.create_review(
            self.conn,
            "range",
            [entry.id],
            scope_kind="range",
            scope_since="2026-01-01",
        )

    def _bodies(self, **filters: object) -> list[str]:
        return [review.body for review in db.list_reviews(self.conn, **filters)]

    def test_unfiltered_listing_is_unchanged(self) -> None:
        self.assertEqual(len(db.list_reviews(self.conn)), 5)

    def test_filters_by_scope_kind(self) -> None:
        self.assertEqual(sorted(self._bodies(scope_kind="person")), ["alice", "bob"])
        self.assertEqual(self._bodies(scope_kind="latest"), ["latest"])
        self.assertEqual(self._bodies(scope_kind="range"), ["range"])

    def test_filters_by_person_handle_case_insensitively(self) -> None:
        self.assertEqual(self._bodies(person_handle="ALICE"), ["alice"])
        self.assertEqual(self._bodies(person_handle="nobody"), [])

    def test_filters_by_normalized_tag(self) -> None:
        self.assertEqual(self._bodies(tag=" Conflict "), ["conflict"])
        self.assertEqual(self._bodies(tag="ops"), [])

    def test_filters_combine_with_limit(self) -> None:
        self.assertEqual(len(db.list_reviews(self.conn, 1, scope_kind="person")), 1)

    def test_rejects_an_unknown_scope_kind_filter(self) -> None:
        with self.assertRaises(db.DBError):
            db.list_reviews(self.conn, scope_kind="everything")


if __name__ == "__main__":
    unittest.main()
