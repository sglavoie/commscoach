"""Tests for the aggregated entry and review contexts used by the skill."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import (
    EntryContext,
    PersonContext,
    PersonHistoryItem,
    ReviewContext,
    ReviewEntryContext,
)

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

    def test_review_context_includes_only_open_goals_up_to_limit(self) -> None:
        entry = db.create_entry(self.conn, "reviewed")
        oldest = db.create_goal(self.conn, "oldest")
        closed = db.create_goal(self.conn, "closed")
        newest = db.create_goal(self.conn, "newest")
        db.close_goal(self.conn, closed.id)

        context = db.get_review_context(self.conn, goal_limit=1)

        self.assertEqual([goal.id for goal in context.goals], [newest.id])
        self.assertNotIn(oldest.id, [goal.id for goal in context.goals])
        self.assertNotIn(closed.id, [goal.id for goal in context.goals])

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
        with self.assertRaises(db.DBError):
            db.get_review_context(self.conn, goal_limit=-1)

    def test_review_context_latest_prior_reviews_are_unfiltered(self) -> None:
        alice = db.create_person(self.conn, "alice")
        entry = db.create_entry(self.conn, "with alice", people=["alice"])
        with patch_timestamps("r0", "r0", "r1", "r1", "r2", "r2"):
            general = db.create_review(self.conn, "general", [entry.id])
            about_alice = db.create_review(
                self.conn,
                "about alice",
                [entry.id],
                scope_kind="person",
                scope_person_handle=alice.handle,
            )

        context = db.get_review_context(self.conn)

        self.assertEqual(context.scope_kind, "latest")
        self.assertIsNone(context.scope_person)
        self.assertIsNone(context.scope_tag)
        self.assertEqual(
            {review.id for review in context.prior_reviews},
            {general.id, about_alice.id},
        )


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
        handles = {p.person.handle for p in ctx.people}
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

    def test_context_enriches_people_with_notes_and_filtered_history(self) -> None:
        alice_old = db.create_entry(
            self.conn,
            "alice old",
            occurred_at="2026-01-01T09:00:00Z",
            people=["alice"],
        )
        bob_only = db.create_entry(
            self.conn,
            "bob only",
            occurred_at="2026-01-02T09:00:00Z",
            people=["bob"],
        )
        alice_recent = db.create_entry(
            self.conn,
            "alice recent",
            occurred_at="2026-01-03T09:00:00Z",
            people=["alice"],
        )
        alice_newest = db.create_entry(
            self.conn,
            "alice newest",
            occurred_at="2026-01-04T09:00:00Z",
            people=["alice"],
        )
        current = db.create_entry(
            self.conn,
            "current",
            occurred_at="2026-01-05T09:00:00Z",
            people=["alice", "bob"],
        )
        feedback = db.create_feedback(self.conn, alice_recent.id, "history note")
        db.set_person_note(self.conn, self.alice.id, "Prefers direct feedback")

        context = db.get_entry_context(self.conn, current.id, history_limit=2)
        by_handle = {item.person.handle: item for item in context.people}
        alice = by_handle["alice"]

        self.assertIsInstance(alice, PersonContext)
        self.assertEqual(alice.notes, "Prefers direct feedback")
        self.assertEqual(
            [item.entry.id for item in alice.history],
            [alice_recent.id, alice_newest.id],
        )
        self.assertTrue(
            all(isinstance(item, PersonHistoryItem) for item in alice.history)
        )
        self.assertEqual(
            [item.id for item in alice.history[0].feedback], [feedback.id]
        )
        history_ids = {item.entry.id for item in alice.history}
        self.assertNotIn(current.id, history_ids)
        self.assertNotIn(alice_old.id, history_ids)
        self.assertNotIn(bob_only.id, history_ids)
        self.assertIsNone(by_handle["bob"].notes)
        self.assertEqual(
            [item.entry.id for item in by_handle["bob"].history],
            [bob_only.id],
        )

    def test_context_allows_zero_history_limit(self) -> None:
        db.create_entry(self.conn, "prior", people=["alice"])
        current = db.create_entry(self.conn, "current", people=["alice"])

        context = db.get_entry_context(self.conn, current.id, history_limit=0)

        self.assertEqual(context.people[0].history, ())

    def test_context_includes_open_goals_and_prior_reviews(self) -> None:
        prior_entry = db.create_entry(self.conn, "prior")
        current = db.create_entry(self.conn, "current")
        first_review = db.create_review(self.conn, "first", [prior_entry.id])
        latest_review = db.create_review(self.conn, "latest", [current.id])
        active_goal = db.create_goal(self.conn, "active")
        closed_goal = db.create_goal(self.conn, "closed")
        db.close_goal(self.conn, closed_goal.id)

        context = db.get_entry_context(
            self.conn,
            current.id,
            prior_review_limit=1,
        )

        self.assertEqual(
            [review.id for review in context.prior_reviews], [latest_review.id]
        )
        self.assertNotIn(
            first_review.id,
            [review.id for review in context.prior_reviews],
        )
        self.assertEqual([goal.id for goal in context.goals], [active_goal.id])

    def test_context_rejects_negative_limits(self) -> None:
        entry = db.create_entry(self.conn, "current")
        with self.assertRaises(db.DBError):
            db.get_entry_context(self.conn, entry.id, history_limit=-1)
        with self.assertRaises(db.DBError):
            db.get_entry_context(self.conn, entry.id, prior_review_limit=-1)

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
