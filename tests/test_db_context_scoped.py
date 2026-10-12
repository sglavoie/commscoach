"""Tests for scoped review context in the DB layer."""

from __future__ import annotations

from commscoach import db
from commscoach.models import (
    ReviewContext,
)

from .db_case import DBTestCase, patch_timestamps


class ScopedReviewContextTests(DBTestCase):
    """`get_review_context` narrowed to one person, tag, or date range."""

    def setUp(self) -> None:
        super().setUp()
        self.me = db.create_person(self.conn, "me", is_self=True)
        self.alice = db.create_person(self.conn, "alice")
        self.bob = db.create_person(self.conn, "bob")

    def test_person_scope_gathers_only_that_persons_entries_oldest_first(self) -> None:
        alice_old = db.create_entry(
            self.conn,
            "alice old",
            occurred_at="2026-01-01T09:00:00Z",
            people=["alice"],
        )
        db.create_entry(
            self.conn,
            "bob only",
            occurred_at="2026-01-02T09:00:00Z",
            people=["bob"],
        )
        alice_new = db.create_entry(
            self.conn,
            "alice new",
            occurred_at="2026-01-03T09:00:00Z",
            people=["alice"],
        )
        feedback = db.create_feedback(self.conn, alice_new.id, "alice note")

        context = db.get_review_context(
            self.conn, scope_kind="person", person_handle="ALICE"
        )

        self.assertIsInstance(context, ReviewContext)
        self.assertEqual(context.scope_kind, "person")
        self.assertEqual(context.scope_person.id, self.alice.id)
        self.assertIsNone(context.scope_tag)
        self.assertEqual(
            [entry_ctx.entry.id for entry_ctx in context.entries],
            [alice_old.id, alice_new.id],
        )
        self.assertEqual(
            [f.id for f in context.entries[1].feedback], [feedback.id]
        )

    def test_person_scope_honours_entry_limit(self) -> None:
        db.create_entry(
            self.conn,
            "alice older",
            occurred_at="2026-01-01T09:00:00Z",
            people=["alice"],
        )
        newest = db.create_entry(
            self.conn,
            "alice newest",
            occurred_at="2026-01-02T09:00:00Z",
            people=["alice"],
        )

        context = db.get_review_context(
            self.conn, limit=1, scope_kind="person", person_handle="alice"
        )

        self.assertEqual(context.scope_limit, 1)
        self.assertEqual(
            [entry_ctx.entry.id for entry_ctx in context.entries], [newest.id]
        )

    def test_person_scope_prior_reviews_match_the_person(self) -> None:
        entry = db.create_entry(self.conn, "with alice", people=["alice"])
        other = db.create_entry(self.conn, "with bob", people=["bob"])
        with patch_timestamps("r0", "r0", "r1", "r1", "r2", "r2"):
            db.create_review(self.conn, "general", [entry.id])
            about_alice = db.create_review(
                self.conn,
                "about alice",
                [entry.id],
                scope_kind="person",
                scope_person_handle="alice",
            )
            db.create_review(
                self.conn,
                "about bob",
                [other.id],
                scope_kind="person",
                scope_person_handle="bob",
            )

        context = db.get_review_context(
            self.conn, scope_kind="person", person_handle="alice"
        )

        self.assertEqual(
            [review.id for review in context.prior_reviews], [about_alice.id]
        )

    def test_person_scope_unknown_handle_raises(self) -> None:
        db.create_entry(self.conn, "with alice", people=["alice"])

        with self.assertRaises(db.PersonNotFoundError):
            db.get_review_context(
                self.conn, scope_kind="person", person_handle="nobody"
            )

    def test_tag_scope_normalizes_the_tag_and_filters_entries(self) -> None:
        tagged = db.create_entry(
            self.conn,
            "hard conversation",
            occurred_at="2026-01-01T09:00:00Z",
            tags=["conflict"],
        )
        db.create_entry(
            self.conn,
            "easy conversation",
            occurred_at="2026-01-02T09:00:00Z",
            tags=["praise"],
        )
        also_tagged = db.create_entry(
            self.conn,
            "another hard one",
            occurred_at="2026-01-03T09:00:00Z",
            tags=["conflict"],
        )

        context = db.get_review_context(self.conn, scope_kind="tag", tag="Conflict")

        self.assertEqual(context.scope_kind, "tag")
        self.assertEqual(context.scope_tag, "conflict")
        self.assertIsNone(context.scope_person)
        self.assertEqual(
            [entry_ctx.entry.id for entry_ctx in context.entries],
            [tagged.id, also_tagged.id],
        )

    def test_tag_scope_prior_reviews_match_the_normalized_tag(self) -> None:
        entry = db.create_entry(self.conn, "hard one", tags=["conflict"])
        other = db.create_entry(self.conn, "nice one", tags=["praise"])
        with patch_timestamps("r0", "r0", "r1", "r1", "r2", "r2"):
            db.create_review(self.conn, "general", [entry.id])
            about_conflict = db.create_review(
                self.conn,
                "about conflict",
                [entry.id],
                scope_kind="tag",
                scope_tag="Conflict",
            )
            db.create_review(
                self.conn,
                "about praise",
                [other.id],
                scope_kind="tag",
                scope_tag="praise",
            )

        context = db.get_review_context(self.conn, scope_kind="tag", tag="CONFLICT")

        self.assertEqual(
            [review.id for review in context.prior_reviews], [about_conflict.id]
        )

    def test_range_scope_bounds_are_inclusive_on_the_effective_date(self) -> None:
        before = db.create_entry(
            self.conn, "before", occurred_at="2026-01-31T23:00:00Z"
        )
        on_since = db.create_entry(
            self.conn, "on since", occurred_at="2026-02-01T00:00:00Z"
        )
        middle = db.create_entry(
            self.conn, "middle", occurred_at="2026-02-10T12:00:00Z"
        )
        on_until = db.create_entry(
            self.conn, "on until", occurred_at="2026-02-28T23:59:00Z"
        )
        after = db.create_entry(
            self.conn, "after", occurred_at="2026-03-01T00:00:00Z"
        )

        context = db.get_review_context(
            self.conn,
            scope_kind="range",
            since="2026-02-01",
            until="2026-02-28",
        )

        self.assertEqual(context.scope_kind, "range")
        self.assertEqual(context.scope_since, "2026-02-01")
        self.assertEqual(context.scope_until, "2026-02-28")
        gathered = [entry_ctx.entry.id for entry_ctx in context.entries]
        self.assertEqual(gathered, [on_since.id, middle.id, on_until.id])
        self.assertNotIn(before.id, gathered)
        self.assertNotIn(after.id, gathered)

    def test_range_scope_prior_reviews_are_unfiltered(self) -> None:
        entry = db.create_entry(
            self.conn, "in range", occurred_at="2026-02-10T12:00:00Z"
        )
        with patch_timestamps("r0", "r0", "r1", "r1"):
            general = db.create_review(self.conn, "general", [entry.id])
            about_alice = db.create_review(
                self.conn,
                "about alice",
                [entry.id],
                scope_kind="person",
                scope_person_handle="alice",
            )

        context = db.get_review_context(
            self.conn, scope_kind="range", since="2026-02-01"
        )

        self.assertEqual(
            {review.id for review in context.prior_reviews},
            {general.id, about_alice.id},
        )

    def test_empty_scope_returns_a_valid_context_rather_than_raising(self) -> None:
        db.create_entry(self.conn, "with bob", people=["bob"])

        context = db.get_review_context(
            self.conn, scope_kind="person", person_handle="alice"
        )

        self.assertIsInstance(context, ReviewContext)
        self.assertEqual(context.entries, ())
        self.assertEqual(context.scope_person.id, self.alice.id)
        self.assertEqual(context.self_person.id, self.me.id)

    def test_goals_stay_unfiltered_and_capped_under_every_scope(self) -> None:
        db.create_entry(
            self.conn,
            "with alice",
            occurred_at="2026-02-10T12:00:00Z",
            people=["alice"],
            tags=["conflict"],
        )
        db.create_goal(self.conn, "oldest")
        newest = db.create_goal(self.conn, "newest")
        scopes: list[dict[str, object]] = [
            {"scope_kind": "latest"},
            {"scope_kind": "person", "person_handle": "alice"},
            {"scope_kind": "tag", "tag": "conflict"},
            {"scope_kind": "range", "since": "2026-02-01", "until": "2026-02-28"},
        ]

        for scope in scopes:
            with self.subTest(scope=scope["scope_kind"]):
                context = db.get_review_context(self.conn, goal_limit=1, **scope)
                self.assertEqual(
                    [goal.id for goal in context.goals], [newest.id]
                )

    def test_person_handle_rename_keeps_prior_reviews_in_the_scoped_context(
        self,
    ) -> None:
        entry = db.create_entry(self.conn, "with alice", people=["alice"])
        about_alice = db.create_review(
            self.conn,
            "about alice",
            [entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        db.update_person(self.conn, self.alice.id, handle="ally")

        context = db.get_review_context(
            self.conn, scope_kind="person", person_handle="ally"
        )

        self.assertEqual(context.scope_person.id, self.alice.id)
        self.assertEqual(context.scope_person.handle, "ally")
        self.assertEqual([ctx.entry.id for ctx in context.entries], [entry.id])
        self.assertEqual(
            [review.id for review in context.prior_reviews], [about_alice.id]
        )

    def test_person_handle_rename_leaves_the_old_handle_unresolvable(self) -> None:
        db.create_entry(self.conn, "with alice", people=["alice"])
        db.update_person(self.conn, self.alice.id, handle="ally")

        with self.assertRaises(db.PersonNotFoundError):
            db.get_review_context(
                self.conn, scope_kind="person", person_handle="alice"
            )

    def test_reversed_range_is_rejected_before_any_context_is_built(self) -> None:
        db.create_entry(self.conn, "in range", occurred_at="2026-02-10T12:00:00Z")

        with self.assertRaises(db.DBError) as ctx:
            db.get_review_context(
                self.conn,
                scope_kind="range",
                since="2026-09-01",
                until="2026-08-01",
            )

        self.assertEqual(str(ctx.exception), "since must not be after until")

    def test_reversed_range_matches_the_error_create_review_raises(self) -> None:
        """Both surfaces must reject the same scope for the same stated reason."""
        entry = db.create_entry(self.conn, "any", occurred_at="2026-02-10T12:00:00Z")
        with self.assertRaises(db.DBError) as context_error:
            db.get_review_context(
                self.conn,
                scope_kind="range",
                since="2026-09-01",
                until="2026-08-01",
            )
        with self.assertRaises(db.DBError) as create_error:
            db.create_review(
                self.conn,
                "body",
                [entry.id],
                scope_kind="range",
                scope_since="2026-09-01",
                scope_until="2026-08-01",
            )

        # Each surface names its own axes, but the claim is one template, so
        # neither can drift into rejecting a range the other accepts.
        self.assertEqual(str(context_error.exception), "since must not be after until")
        self.assertEqual(
            str(create_error.exception),
            "review scope_since must not be after review scope_until",
        )

    def test_reversed_range_is_rejected_by_date_not_by_text(self) -> None:
        """``20260201`` is a valid ISO date and must order as one."""
        with self.assertRaises(db.DBError) as ctx:
            db.get_review_context(
                self.conn, scope_kind="range", since="20260201", until="2026-01-05"
            )

        self.assertEqual(str(ctx.exception), "since must not be after until")

    def test_equal_range_bounds_are_a_valid_single_day_scope(self) -> None:
        same_day = db.create_entry(
            self.conn, "on the day", occurred_at="2026-02-10T12:00:00Z"
        )
        db.create_entry(self.conn, "next day", occurred_at="2026-02-11T12:00:00Z")

        context = db.get_review_context(
            self.conn, scope_kind="range", since="2026-02-10", until="2026-02-10"
        )

        self.assertEqual(
            [entry_ctx.entry.id for entry_ctx in context.entries], [same_day.id]
        )

    def test_rejects_unknown_kind_and_axes_from_another_kind(self) -> None:
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, scope_kind="everything")
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, scope_kind="latest", tag="conflict")
        with self.assertRaises(db.DBError):
            db.get_review_context(
                self.conn, scope_kind="person", person_handle="alice", tag="conflict"
            )
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, scope_kind="person")
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, scope_kind="tag", tag="   ")
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, scope_kind="range")
