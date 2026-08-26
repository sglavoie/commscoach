"""Tests for person-note side-table CRUD."""

from __future__ import annotations

from commscoach import db

from tests.db_case import DBTestCase

T0 = "2026-08-25T10:00:00+00:00"
T1 = "2026-08-26T10:00:00+00:00"


class PersonNoteTests(DBTestCase):
    def test_upsert_preserves_created_at(self) -> None:
        person = db.create_person(self.conn, "alice")
        original = db.set_person_note(self.conn, person.id, "First", now=T0)
        updated = db.set_person_note(self.conn, person.id, "Second", now=T1)

        self.assertEqual(original.created_at, T0)
        self.assertEqual(updated.body, "Second")
        self.assertEqual(updated.created_at, T0)
        self.assertEqual(updated.updated_at, T1)

    def test_set_requires_existing_person_and_non_blank_body(self) -> None:
        with self.assertRaises(db.PersonNotFoundError):
            db.set_person_note(self.conn, 999, "note", now=T0)
        person = db.create_person(self.conn, "alice")
        with self.assertRaisesRegex(db.DBError, "non-empty"):
            db.set_person_note(self.conn, person.id, "  ", now=T0)

    def test_clear_returns_whether_note_existed(self) -> None:
        person = db.create_person(self.conn, "alice")
        db.set_person_note(self.conn, person.id, "note", now=T0)

        self.assertTrue(db.clear_person_note(self.conn, person.id))
        self.assertFalse(db.clear_person_note(self.conn, person.id))
        self.assertIsNone(db.get_person_note(self.conn, person.id))

    def test_person_delete_cascades_note(self) -> None:
        person = db.create_person(self.conn, "alice")
        db.set_person_note(self.conn, person.id, "note", now=T0)

        self.assertTrue(db.delete_person(self.conn, person.id))
        self.assertIsNone(db.get_person_note(self.conn, person.id))

    def test_bulk_fetch_returns_only_requested_existing_notes(self) -> None:
        alice = db.create_person(self.conn, "alice")
        bob = db.create_person(self.conn, "bob")
        carol = db.create_person(self.conn, "carol")
        db.set_person_note(self.conn, alice.id, "Alice note", now=T0)
        db.set_person_note(self.conn, bob.id, "Bob note", now=T1)

        notes = db.get_person_notes(
            self.conn, (person_id for person_id in (carol.id, alice.id, alice.id))
        )

        self.assertEqual(set(notes), {alice.id})
        self.assertEqual(notes[alice.id].body, "Alice note")
        self.assertEqual(db.get_person_notes(self.conn, ()), {})
