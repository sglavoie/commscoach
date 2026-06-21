"""Tests for the frozen-dataclass in-process data contracts."""

from __future__ import annotations

import dataclasses
import unittest

from commscoach.models import Entry, EntryContext, Feedback, Person


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


class PersonTests(unittest.TestCase):
    def test_is_frozen(self):
        person = _make_person()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            person.handle = "other"  # type: ignore[misc]

    def test_optional_name_allows_none(self):
        person = _make_person(name=None)
        self.assertIsNone(person.name)


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


class FeedbackTests(unittest.TestCase):
    def test_is_frozen(self):
        feedback = Feedback(
            id=5,
            entry_id=10,
            body="Nice framing.",
            created_at="2026-06-20T10:00:00Z",
            updated_at="2026-06-20T10:00:00Z",
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            feedback.body = "edit"  # type: ignore[misc]


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
            people=(other,),
            prior_feedback=(feedback,),
        )
        self.assertTrue(context.self_person.is_self)
        self.assertEqual(context.people, (other,))
        self.assertEqual(context.prior_feedback[0].entry_id, entry.id)

    def test_is_frozen(self):
        self_person = _make_person(id=1, handle="me", is_self=True)
        entry = _make_entry()
        context = EntryContext(
            entry=entry, self_person=self_person, people=(), prior_feedback=()
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.people = ()  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
