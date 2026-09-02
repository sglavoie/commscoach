"""Tests for the frozen-dataclass in-process data contracts."""

from __future__ import annotations

import dataclasses
import unittest

from commscoach.models import (
    Entry,
    EntryContext,
    Feedback,
    Goal,
    Person,
    PersonContext,
    PersonHistoryItem,
    PersonNote,
    Review,
    ReviewContext,
    ReviewEntryContext,
    Stats,
    Tag,
)


def _make_person(**overrides):
    base = dict(
        id=1,
        handle="alex",
        name="Alex Example",
        is_self=False,
        created_at="2026-06-20T00:00:00Z",
        updated_at="2026-06-20T00:00:00Z",
    )
    base.update(overrides)
    return Person(**base)


def _make_entry(**overrides):
    base = dict(
        id=10,
        body="Standup recap",
        source="standup",
        occurred_at="2026-06-20T09:00:00Z",
        created_at="2026-06-20T09:30:00Z",
        updated_at="2026-06-20T09:30:00Z",
    )
    base.update(overrides)
    return Entry(**base)


def _make_feedback(**overrides):
    base = dict(
        id=5,
        entry_id=10,
        body="Nice framing.",
        created_at="2026-06-20T10:00:00Z",
        updated_at="2026-06-20T10:00:00Z",
    )
    base.update(overrides)
    return Feedback(**base)


def _make_review(**overrides):
    base = dict(
        id=20,
        body="Weekly synthesis",
        scope_kind="latest",
        scope_limit=20,
        created_at="2026-06-20T12:00:00Z",
        updated_at="2026-06-20T12:00:00Z",
    )
    base.update(overrides)
    return Review(**base)


def _make_goal(**overrides):
    base = dict(
        id=30,
        title="Ask a clarifying question",
        detail=None,
        status="active",
        person=None,
        review=None,
        outcome=None,
        closed_at=None,
        created_at="2026-06-20T12:00:00Z",
        updated_at="2026-06-20T12:00:00Z",
    )
    base.update(overrides)
    return Goal(**base)


class PersonTests(unittest.TestCase):
    def test_is_frozen(self):
        person = _make_person()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            person.handle = "other"  # type: ignore[misc]

    def test_optional_name_allows_none(self):
        person = _make_person(name=None)
        self.assertIsNone(person.name)


class PersonNoteTests(unittest.TestCase):
    def test_is_frozen_and_holds_side_table_fields(self):
        note = PersonNote(
            person_id=1,
            body="context",
            created_at="2026-06-20T00:00:00Z",
            updated_at="2026-06-20T00:00:00Z",
        )
        self.assertEqual(note.person_id, 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            note.body = "changed"  # type: ignore[misc]


class EntryTests(unittest.TestCase):
    def test_defaults_for_tags_and_people(self):
        entry = _make_entry()
        self.assertEqual(entry.tags, ())
        self.assertEqual(entry.people, ())

    def test_is_frozen(self):
        entry = _make_entry()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.body = "changed"  # type: ignore[misc]

    def test_tags_and_people_are_tuples(self):
        person = _make_person()
        entry = _make_entry(tags=("ops", "1-on-1"), people=(person,))
        self.assertIsInstance(entry.tags, tuple)
        self.assertIsInstance(entry.people, tuple)
        self.assertEqual(entry.tags, ("ops", "1-on-1"))
        self.assertEqual(entry.people[0].handle, "alex")


class TagTests(unittest.TestCase):
    def test_is_frozen(self):
        tag = Tag(id=1, name="conflict", entry_count=2)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            tag.name = "pushback"  # type: ignore[misc]

    def test_rejects_undeclared_attributes(self):
        tag = Tag(id=1, name="conflict", entry_count=2)
        with self.assertRaises(AttributeError):
            object.__setattr__(tag, "colour", "red")

    def test_holds_the_count_alongside_the_name(self):
        tag = Tag(id=3, name="ops", entry_count=0)
        self.assertEqual((tag.id, tag.name, tag.entry_count), (3, "ops", 0))


class FeedbackTests(unittest.TestCase):
    def test_is_frozen(self):
        feedback = _make_feedback()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            feedback.body = "edit"  # type: ignore[misc]


class ReviewTests(unittest.TestCase):
    def test_defaults_for_entries(self):
        review = _make_review()
        self.assertEqual(review.entries, ())

    def test_entries_are_tuple(self):
        entry = _make_entry()
        review = _make_review(entries=(entry,))
        self.assertIsInstance(review.entries, tuple)
        self.assertEqual(review.entries[0].id, entry.id)

    def test_scope_limit_allows_none(self):
        review = _make_review(scope_limit=None)
        self.assertIsNone(review.scope_limit)

    def test_is_frozen(self):
        review = _make_review()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            review.body = "changed"  # type: ignore[misc]


class GoalTests(unittest.TestCase):
    def test_is_open_is_derived_from_status(self):
        self.assertTrue(_make_goal().is_open)
        self.assertFalse(
            _make_goal(status="done", closed_at="2026-06-21T00:00:00Z").is_open
        )

    def test_relationships_and_optional_fields(self):
        person = _make_person()
        review = _make_review()
        goal = _make_goal(person=person, review=review, detail="detail")
        self.assertEqual(goal.person, person)
        self.assertEqual(goal.review, review)
        self.assertEqual(goal.detail, "detail")

    def test_is_frozen(self):
        goal = _make_goal()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            goal.title = "changed"  # type: ignore[misc]


class StatsTests(unittest.TestCase):
    def test_collection_defaults_and_frozen_contract(self):
        stats = Stats(
            entry_count=0,
            people_count=0,
            tag_count=0,
            feedback_count=0,
            review_count=0,
            entries_with_feedback=0,
            open_goal_count=0,
            closed_goal_count=0,
            first_entry=None,
            last_entry=None,
            last_review=None,
            entries_per_week=0.0,
            current_weekly_streak=0,
            longest_weekly_streak=0,
        )
        self.assertEqual(stats.weekly_buckets, ())
        self.assertEqual(stats.top_tags, ())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            stats.entry_count = 1  # type: ignore[misc]


class EntryContextTests(unittest.TestCase):
    def test_holds_aggregated_payload(self):
        self_person = _make_person(id=1, handle="me", is_self=True)
        other = _make_person(id=2, handle="alex")
        entry = _make_entry(people=(other,))
        feedback = Feedback(
            id=7,
            entry_id=entry.id,
            body="Ask one clarifying question next time.",
            created_at="2026-06-20T11:00:00Z",
            updated_at="2026-06-20T11:00:00Z",
        )
        context = EntryContext(
            entry=entry,
            self_person=self_person,
            people=(PersonContext(person=other, notes=None),),
            prior_feedback=(feedback,),
        )
        self.assertTrue(context.self_person.is_self)
        self.assertEqual(context.people[0].person, other)
        self.assertEqual(context.prior_feedback[0].entry_id, entry.id)

    def test_is_frozen(self):
        self_person = _make_person(id=1, handle="me", is_self=True)
        entry = _make_entry()
        context = EntryContext(
            entry=entry, self_person=self_person, people=(), prior_feedback=()
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.people = ()  # type: ignore[misc]


class PersonContextTests(unittest.TestCase):
    def test_holds_notes_and_oldest_first_history_items(self):
        person = _make_person()
        older = PersonHistoryItem(
            entry=_make_entry(id=8),
            feedback=(_make_feedback(entry_id=8),),
        )
        newer = PersonHistoryItem(entry=_make_entry(id=9))
        context = PersonContext(
            person=person,
            notes="Prefers direct feedback",
            history=(older, newer),
        )
        self.assertEqual(context.person, person)
        self.assertEqual(context.notes, "Prefers direct feedback")
        self.assertEqual([item.entry.id for item in context.history], [8, 9])
        self.assertEqual(context.history[0].feedback[0].entry_id, 8)


class ReviewEntryContextTests(unittest.TestCase):
    def test_defaults_for_feedback(self):
        context = ReviewEntryContext(entry=_make_entry())
        self.assertEqual(context.feedback, ())

    def test_feedback_is_tuple(self):
        feedback = _make_feedback()
        context = ReviewEntryContext(entry=_make_entry(), feedback=(feedback,))
        self.assertIsInstance(context.feedback, tuple)
        self.assertEqual(context.feedback[0].body, "Nice framing.")

    def test_is_frozen(self):
        context = ReviewEntryContext(entry=_make_entry())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.feedback = ()  # type: ignore[misc]


class ReviewContextTests(unittest.TestCase):
    def test_defaults_for_entries_and_prior_reviews(self):
        self_person = _make_person(handle="me", is_self=True)
        context = ReviewContext(
            self_person=self_person,
            scope_kind="latest",
            scope_limit=20,
        )
        self.assertEqual(context.entries, ())
        self.assertEqual(context.prior_reviews, ())
        self.assertEqual(context.goals, ())

    def test_holds_review_payload(self):
        self_person = _make_person(handle="me", is_self=True)
        entry_context = ReviewEntryContext(
            entry=_make_entry(),
            feedback=(_make_feedback(),),
        )
        review = _make_review()
        context = ReviewContext(
            self_person=self_person,
            scope_kind="latest",
            scope_limit=20,
            entries=(entry_context,),
            prior_reviews=(review,),
        )
        self.assertTrue(context.self_person.is_self)
        self.assertEqual(context.entries[0].entry.id, entry_context.entry.id)
        self.assertEqual(context.prior_reviews[0].body, "Weekly synthesis")

    def test_is_frozen(self):
        self_person = _make_person(handle="me", is_self=True)
        context = ReviewContext(
            self_person=self_person,
            scope_kind="latest",
            scope_limit=20,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.scope_kind = "all"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
