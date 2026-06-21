"""Tests for the Session 4 CRUD layer in ``commscoach.db``.

Covers people/entry/feedback CRUD, search, self switching, cascades, tag
normalization plus orphan GC, and the context aggregation contract used by
the coaching skill.

Tests use temporary DB files exclusively so the tracked project DB at
``data/commscoach.sqlite3`` is never mutated. Where timestamps need to differ
deterministically, ``db._utcnow_iso`` is patched with an iterator so we never
rely on real clock drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest import mock

from commscoach import db
from commscoach.models import Entry, EntryContext, Feedback, Person


def _ts_iter(values: list[str]) -> Iterator[str]:
    return iter(values)


class _DBTestCase(unittest.TestCase):
    """Shared setup that creates an isolated DB and opens a connection."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)


class PeopleCrudTests(_DBTestCase):
    def test_create_person_returns_person_with_timestamps(self) -> None:
        person = db.create_person(self.conn, "alice", name="Alice")
        self.assertIsInstance(person, Person)
        self.assertEqual(person.handle, "alice")
        self.assertEqual(person.name, "Alice")
        self.assertFalse(person.is_self)
        self.assertEqual(person.created_at, person.updated_at)
        self.assertTrue(person.created_at)

    def test_create_person_rejects_empty_handle(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_person(self.conn, "   ")

    def test_create_person_duplicate_handle_case_insensitive_raises(self) -> None:
        db.create_person(self.conn, "Alice")
        with self.assertRaises(db.DuplicateHandleError) as ctx:
            db.create_person(self.conn, "alice")
        self.assertEqual(ctx.exception.handle, "alice")

    def test_create_person_with_self_flag(self) -> None:
        person = db.create_person(self.conn, "me", is_self=True)
        self.assertTrue(person.is_self)

    def test_create_person_second_self_raises(self) -> None:
        db.create_person(self.conn, "me", is_self=True)
        with self.assertRaises(db.DuplicateSelfError):
            db.create_person(self.conn, "alt-me", is_self=True)

    def test_update_person_changes_fields_and_bumps_updated_at(self) -> None:
        with mock.patch.object(
            db, "_utcnow_iso", side_effect=_ts_iter(["t0", "t1"]).__next__
        ):
            person = db.create_person(self.conn, "alice")
            updated = db.update_person(self.conn, person.id, name="Alice A.")
        self.assertEqual(updated.name, "Alice A.")
        self.assertEqual(updated.created_at, "t0")
        self.assertEqual(updated.updated_at, "t1")

    def test_update_person_with_no_args_is_noop(self) -> None:
        person = db.create_person(self.conn, "alice")
        same = db.update_person(self.conn, person.id)
        self.assertEqual(same.updated_at, person.updated_at)

    def test_update_person_handle_collision_raises(self) -> None:
        db.create_person(self.conn, "alice")
        bob = db.create_person(self.conn, "bob")
        with self.assertRaises(db.DuplicateHandleError):
            db.update_person(self.conn, bob.id, handle="Alice")

    def test_update_person_missing_raises(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.update_person(self.conn, 9999, name="x")

    def test_update_person_rejects_empty_handle(self) -> None:
        person = db.create_person(self.conn, "alice")
        with self.assertRaises(db.DBError):
            db.update_person(self.conn, person.id, handle="  ")

    def test_get_person_by_handle_is_case_insensitive(self) -> None:
        person = db.create_person(self.conn, "Alice")
        found = db.get_person_by_handle(self.conn, "ALICE")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, person.id)

    def test_get_person_by_handle_missing_returns_none(self) -> None:
        self.assertIsNone(db.get_person_by_handle(self.conn, "ghost"))

    def test_list_people_orders_self_first_then_handle(self) -> None:
        db.create_person(self.conn, "charlie")
        db.create_person(self.conn, "alice")
        db.create_person(self.conn, "me", is_self=True)
        db.create_person(self.conn, "Bob")
        result = db.list_people(self.conn)
        self.assertEqual([p.handle for p in result], ["me", "alice", "Bob", "charlie"])

    def test_delete_person_returns_true_and_cascades_entry_people(self) -> None:
        alice = db.create_person(self.conn, "alice")
        entry = db.create_entry(self.conn, "msg", people=["alice"])
        self.assertEqual(len(entry.people), 1)

        self.assertTrue(db.delete_person(self.conn, alice.id))
        refreshed = db.get_entry(self.conn, entry.id)
        assert refreshed is not None
        self.assertEqual(refreshed.people, ())

    def test_delete_person_missing_returns_false(self) -> None:
        self.assertFalse(db.delete_person(self.conn, 9999))

    def test_get_self_person_is_none_until_set(self) -> None:
        self.assertIsNone(db.get_self_person(self.conn))
        db.create_person(self.conn, "alice")
        self.assertIsNone(db.get_self_person(self.conn))

    def test_set_self_atomic_swap(self) -> None:
        first = db.create_person(self.conn, "first", is_self=True)
        second = db.create_person(self.conn, "second")
        promoted = db.set_self(self.conn, second.id)

        self.assertTrue(promoted.is_self)
        demoted = db.get_person(self.conn, first.id)
        assert demoted is not None
        self.assertFalse(demoted.is_self)

        people_with_self = [
            p for p in db.list_people(self.conn) if p.is_self
        ]
        self.assertEqual(len(people_with_self), 1)
        self.assertEqual(people_with_self[0].id, second.id)

    def test_set_self_when_no_existing_self(self) -> None:
        person = db.create_person(self.conn, "me")
        promoted = db.set_self(self.conn, person.id)
        self.assertTrue(promoted.is_self)

    def test_set_self_missing_person_raises(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.set_self(self.conn, 9999)


class EntryCrudTests(_DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.me = db.create_person(self.conn, "me", is_self=True)
        self.alice = db.create_person(self.conn, "alice", name="Alice")
        self.bob = db.create_person(self.conn, "bob", name="Bob")

    def test_create_entry_basic(self) -> None:
        entry = db.create_entry(self.conn, "hello world")
        self.assertIsInstance(entry, Entry)
        self.assertEqual(entry.body, "hello world")
        self.assertEqual(entry.tags, ())
        self.assertEqual(entry.people, ())
        self.assertEqual(entry.created_at, entry.updated_at)

    def test_create_entry_rejects_empty_body(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_entry(self.conn, "   ")

    def test_create_entry_normalizes_and_dedups_tags(self) -> None:
        entry = db.create_entry(
            self.conn,
            "msg",
            tags=["Work", " work ", "Focus", "FOCUS"],
        )
        self.assertEqual(set(entry.tags), {"work", "focus"})
        self.assertEqual(len(entry.tags), 2)

    def test_create_entry_links_existing_people_case_insensitive(self) -> None:
        entry = db.create_entry(self.conn, "msg", people=["ALICE", "Bob"])
        handles = {p.handle for p in entry.people}
        self.assertEqual(handles, {"alice", "bob"})

    def test_create_entry_unknown_person_raises_without_writing(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.create_entry(self.conn, "msg", tags=["x"], people=["ghost"])
        # No partial entry/tag rows were written
        self.assertEqual(db.list_entries(self.conn), ())
        rows = self.conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()
        self.assertEqual(rows["c"], 0)

    def test_get_entry_returns_tags_and_people(self) -> None:
        created = db.create_entry(
            self.conn,
            "msg",
            tags=["work", "focus"],
            people=["alice", "bob"],
        )
        loaded = db.get_entry(self.conn, created.id)
        assert loaded is not None
        self.assertEqual(set(loaded.tags), {"work", "focus"})
        self.assertEqual({p.handle for p in loaded.people}, {"alice", "bob"})

    def test_get_entry_missing_returns_none(self) -> None:
        self.assertIsNone(db.get_entry(self.conn, 9999))

    def test_update_entry_replaces_tags_and_people(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", tags=["a", "b"], people=["alice"]
        )
        updated = db.update_entry(
            self.conn,
            entry.id,
            tags=["c"],
            people=["bob"],
        )
        self.assertEqual(updated.tags, ("c",))
        self.assertEqual(len(updated.people), 1)
        self.assertEqual(updated.people[0].handle, "bob")

    def test_update_entry_clears_relations_with_empty_tuple(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", tags=["a"], people=["alice"]
        )
        cleared = db.update_entry(self.conn, entry.id, tags=(), people=())
        self.assertEqual(cleared.tags, ())
        self.assertEqual(cleared.people, ())

    def test_update_entry_bumps_updated_at_on_relation_only_change(self) -> None:
        with mock.patch.object(
            db, "_utcnow_iso", side_effect=_ts_iter(["t0", "t1"]).__next__
        ):
            entry = db.create_entry(self.conn, "msg")
            updated = db.update_entry(self.conn, entry.id, tags=["x"])
        self.assertEqual(updated.created_at, "t0")
        self.assertEqual(updated.updated_at, "t1")

    def test_update_entry_missing_raises(self) -> None:
        with self.assertRaises(db.EntryNotFoundError):
            db.update_entry(self.conn, 9999, body="x")

    def test_update_entry_rejects_empty_body(self) -> None:
        entry = db.create_entry(self.conn, "msg")
        with self.assertRaises(db.DBError):
            db.update_entry(self.conn, entry.id, body="  ")

    def test_update_entry_does_not_touch_unspecified_fields(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", source="cli", occurred_at="2026-01-01T00:00:00Z"
        )
        updated = db.update_entry(self.conn, entry.id, body="new")
        self.assertEqual(updated.body, "new")
        self.assertEqual(updated.source, "cli")
        self.assertEqual(updated.occurred_at, "2026-01-01T00:00:00Z")

    def test_list_entries_newest_first(self) -> None:
        with mock.patch.object(
            db,
            "_utcnow_iso",
            side_effect=_ts_iter(["t0", "t1", "t2"]).__next__,
        ):
            db.create_entry(self.conn, "first")
            db.create_entry(self.conn, "second")
            db.create_entry(self.conn, "third")
        bodies = [e.body for e in db.list_entries(self.conn)]
        self.assertEqual(bodies, ["third", "second", "first"])

    def test_list_entries_honors_limit(self) -> None:
        for body in ("a", "b", "c"):
            db.create_entry(self.conn, body)
        limited = db.list_entries(self.conn, limit=2)
        self.assertEqual(len(limited), 2)

    def test_list_entries_rejects_negative_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.list_entries(self.conn, limit=-1)

    def test_delete_entry_cascades_links_and_feedback(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", tags=["work"], people=["alice"]
        )
        db.create_feedback(self.conn, entry.id, "nice")
        self.assertTrue(db.delete_entry(self.conn, entry.id))
        self.assertIsNone(db.get_entry(self.conn, entry.id))
        self.assertEqual(db.list_feedback(self.conn, entry.id), ())
        (ep_count,) = self.conn.execute(
            "SELECT COUNT(*) FROM entry_people"
        ).fetchone()
        self.assertEqual(ep_count, 0)

    def test_delete_entry_garbage_collects_orphan_tags(self) -> None:
        entry = db.create_entry(self.conn, "msg", tags=["work", "focus"])
        # "shared" is referenced by a second entry, so it must survive GC
        other = db.create_entry(self.conn, "other", tags=["shared"])
        db.update_entry(self.conn, entry.id, tags=["shared"])

        (count_before_delete,) = self.conn.execute(
            "SELECT COUNT(*) FROM tags"
        ).fetchone()
        self.assertEqual(count_before_delete, 1)  # work and focus already GC'd

        db.delete_entry(self.conn, other.id)
        # entry still references "shared", so it stays
        (count_after_other_delete,) = self.conn.execute(
            "SELECT COUNT(*) FROM tags"
        ).fetchone()
        self.assertEqual(count_after_other_delete, 1)

        db.delete_entry(self.conn, entry.id)
        (count_after_all,) = self.conn.execute(
            "SELECT COUNT(*) FROM tags"
        ).fetchone()
        self.assertEqual(count_after_all, 0)

    def test_delete_entry_missing_returns_false(self) -> None:
        self.assertFalse(db.delete_entry(self.conn, 9999))

    def test_search_entries_by_body_substring_case_insensitive(self) -> None:
        db.create_entry(self.conn, "Hello world")
        db.create_entry(self.conn, "goodbye")
        results = db.search_entries(self.conn, query="HELLO")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].body, "Hello world")

    def test_search_entries_by_tag(self) -> None:
        db.create_entry(self.conn, "a", tags=["work"])
        db.create_entry(self.conn, "b", tags=["focus"])
        results = db.search_entries(self.conn, tag="Work")
        self.assertEqual([e.body for e in results], ["a"])

    def test_search_entries_by_person(self) -> None:
        db.create_entry(self.conn, "with alice", people=["alice"])
        db.create_entry(self.conn, "with bob", people=["bob"])
        results = db.search_entries(self.conn, person_handle="ALICE")
        self.assertEqual([e.body for e in results], ["with alice"])

    def test_search_entries_combined_filters_and(self) -> None:
        db.create_entry(self.conn, "alpha", tags=["x"], people=["alice"])
        db.create_entry(self.conn, "alpha", tags=["x"], people=["bob"])
        db.create_entry(self.conn, "alpha", tags=["y"], people=["alice"])
        results = db.search_entries(
            self.conn, query="alpha", tag="x", person_handle="alice"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].people[0].handle, "alice")
        self.assertEqual(results[0].tags, ("x",))

    def test_search_entries_rejects_negative_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.search_entries(self.conn, limit=-1)


class FeedbackCrudTests(_DBTestCase):
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
        with mock.patch.object(
            db,
            "_utcnow_iso",
            side_effect=_ts_iter(["t0", "t1", "t2"]).__next__,
        ):
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


class EntryContextTests(_DBTestCase):
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
        with mock.patch.object(
            db,
            "_utcnow_iso",
            side_effect=_ts_iter(["f0", "f1"]).__next__,
        ):
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
