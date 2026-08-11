"""Tests for people CRUD and self switching in ``commscoach.db``."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.models import Person

from .db_case import DBTestCase, patch_timestamps


class PeopleCrudTests(DBTestCase):
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
        with patch_timestamps("t0", "t1"):
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

        people_with_self = [p for p in db.list_people(self.conn) if p.is_self]
        self.assertEqual(len(people_with_self), 1)
        self.assertEqual(people_with_self[0].id, second.id)

    def test_set_self_when_no_existing_self(self) -> None:
        person = db.create_person(self.conn, "me")
        promoted = db.set_self(self.conn, person.id)
        self.assertTrue(promoted.is_self)

    def test_set_self_missing_person_raises(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.set_self(self.conn, 9999)


if __name__ == "__main__":
    unittest.main()
