"""Tests for Unicode normalization of handles and tag names."""

from __future__ import annotations

import unicodedata
from contextlib import closing

from commscoach import db
from commscoach.db.tags import normalize_tag

from .db_case import DBTestCase

NFC = unicodedata.normalize("NFC", "Émile")
NFD = unicodedata.normalize("NFD", "Émile")
CAFE_NFD = unicodedata.normalize("NFD", "café")
_TS = "2026-01-01T00:00:00+00:00"


class NormalizationTests(DBTestCase):
    def test_tag_names_are_nfc_without_a_leading_hash(self) -> None:
        self.assertEqual(normalize_tag(" #Urgent "), "urgent")
        self.assertEqual(normalize_tag(CAFE_NFD), "café")
        self.assertEqual(normalize_tag("#"), "")

    def test_decomposed_tag_names_the_same_tag(self) -> None:
        db.create_entry(self.conn, "a", tags=("café",))
        db.create_entry(self.conn, "b", tags=(CAFE_NFD, "#café"))
        self.assertEqual([(t.name, t.entry_count) for t in db.list_tags(self.conn)], [("café", 2)])

    def test_decomposed_handle_is_stored_composed_and_names_the_same_person(self) -> None:
        person = db.create_person(self.conn, NFD)
        self.assertEqual(person.handle, NFC)
        self.assertEqual(db.get_person_by_handle(self.conn, NFD), person)
        with self.assertRaises(db.DBError):
            db.create_person(self.conn, NFC.lower())


class BackfillTests(DBTestCase):
    def test_init_renormalizes_names_stored_under_older_rules(self) -> None:
        entry = db.create_entry(self.conn, "a", tags=("urgent",))
        self.conn.execute("INSERT INTO tags (name) VALUES ('#urgent'), (?)", (CAFE_NFD,))
        hash_id, cafe_id = (
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM tags WHERE name IN ('#urgent', ?) ORDER BY name", (CAFE_NFD,)
            )
        )
        other = db.create_entry(self.conn, "b")
        self.conn.executemany(
            "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            [(entry.id, hash_id), (other.id, hash_id), (other.id, cafe_id)],
        )
        self.conn.execute(
            "INSERT INTO people (handle, is_self, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (NFD, _TS, _TS),
        )
        self.conn.execute("DELETE FROM _meta WHERE key = 'unicode_names_backfill_v1'")
        self.conn.commit()

        db.init_db(self.db_path)

        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(
                [(t.name, t.entry_count) for t in db.list_tags(conn)],
                [("urgent", 2), ("café", 1)],
            )
            self.assertEqual(db.get_entry(conn, other.id).tags, ("café", "urgent"))
            self.assertEqual([p.handle for p in db.list_people(conn)], [NFC])


class ImportTests(DBTestCase):
    def test_an_export_with_older_tag_names_still_imports(self) -> None:
        entry = db.create_entry(self.conn, "a", tags=("urgent",))
        data = db.dump_to_dict(self.conn)
        data["tags"][0]["name"] = "#urgent"

        target = self.db_path.with_name("target.sqlite3")
        db.init_db(target)
        with closing(db.connect(target)) as conn:
            db.load_from_dict(conn, data)
            self.assertEqual(db.get_entry(conn, entry.id).tags, ("urgent",))

    def test_colliding_handles_explain_how_to_fix_the_export(self) -> None:
        data = db.dump_to_dict(self.conn)
        data["people"] = [
            {
                "id": index,
                "handle": handle,
                "name": None,
                "notes": None,
                "is_self": 0,
                "created_at": _TS,
                "updated_at": _TS,
            }
            for index, handle in ((1, "Émile"), (2, "émile"))
        ]
        with self.assertRaisesRegex(db.DBError, "person edit HANDLE --handle NEW"):
            db.validate_dump(data)
