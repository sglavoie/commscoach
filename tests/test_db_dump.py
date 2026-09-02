"""Tests for the lossless database dump and restore format."""

from __future__ import annotations

import copy
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from commscoach import db

from .db_case import DBTestCase
from .db_schema_case import NOW


def seed_complete_store(conn: object) -> None:
    me = db.create_person(conn, "me", name="Me", is_self=True)
    alex = db.create_person(conn, "alex", name="Alex")
    first = db.create_entry(
        conn,
        "first",
        source="slack",
        occurred_at="2026-08-20T09:30:00+00:00",
        tags=("work", "coaching"),
        people=(me.handle, alex.handle),
    )
    second = db.create_entry(
        conn,
        "second",
        tags=("work",),
        people=(alex.handle,),
    )
    third = db.create_entry(
        conn,
        "third",
        tags=("personal", "coaching"),
        people=(me.handle,),
    )
    db.create_feedback(conn, first.id, "first feedback")
    db.create_feedback(conn, first.id, "second feedback")
    db.create_feedback(conn, third.id, "third feedback")
    review = db.create_review(
        conn,
        "review",
        [third.id, first.id, second.id],
        scope_limit=3,
    )
    db.set_person_note(conn, alex.id, "Remember this context")
    db.create_goal(
        conn,
        "Active experiment",
        detail="Ask a follow-up",
        person_handle=alex.handle,
        review_id=review.id,
    )
    closed = db.create_goal(conn, "Completed experiment")
    db.close_goal(conn, closed.id, outcome="It helped")


class DumpRoundTripTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        seed_complete_store(self.conn)

    def _fresh_connection(self) -> object:
        restored_path = Path(self._tmp.name) / "restored" / "commscoach.sqlite3"
        db.init_db(restored_path)
        restored = db.connect(restored_path)
        self.addCleanup(restored.close)
        return restored

    def test_round_trip_preserves_every_table_and_link(self) -> None:
        original = db.dump_to_dict(self.conn)
        restored = self._fresh_connection()

        db.load_from_dict(restored, original)

        self.assertEqual(db.dump_to_dict(restored), original)

    def test_review_entry_positions_survive(self) -> None:
        original = db.dump_to_dict(self.conn)
        restored = self._fresh_connection()
        db.load_from_dict(restored, original)

        positions = restored.execute(
            "SELECT entry_id, position FROM review_entries ORDER BY position"
        ).fetchall()

        expected = original["reviews"][0]["entries"]
        self.assertEqual(
            [(row["entry_id"], row["position"]) for row in positions],
            [(row["entry_id"], row["position"]) for row in expected],
        )

    def test_post_restore_entry_id_continues_from_max_plus_one(self) -> None:
        original = db.dump_to_dict(self.conn)
        restored = self._fresh_connection()
        db.load_from_dict(restored, original)

        created = db.create_entry(restored, "after restore")

        self.assertEqual(
            created.id,
            max(row["id"] for row in original["entries"]) + 1,
        )

    def test_non_empty_database_is_refused_and_untouched(self) -> None:
        original = db.dump_to_dict(self.conn)
        before = copy.deepcopy(original)

        with self.assertRaises(db.DatabaseNotEmptyError):
            db.load_from_dict(self.conn, original)

        self.assertEqual(db.dump_to_dict(self.conn), before)

    def test_newer_format_version_is_a_hard_error(self) -> None:
        payload = db.dump_to_dict(self.conn)
        payload["format_version"] = db.FORMAT_VERSION + 1

        with self.assertRaisesRegex(db.DumpFormatError, "unsupported dump format"):
            db.validate_dump(payload)

    def test_current_format_version_is_two(self) -> None:
        self.assertEqual(db.FORMAT_VERSION, 2)
        self.assertEqual(db.dump_to_dict(self.conn)["format_version"], 2)

    def test_round_trip_preserves_every_review_scope_column(self) -> None:
        # The CRUD layer cannot write these yet, so drive the columns directly.
        person_id = self.conn.execute(
            "SELECT id FROM people WHERE handle = 'alex'"
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_limit,"
            " scope_person_id, scope_person_handle, created_at, updated_at)"
            " VALUES ('about alex', 'person', NULL, ?, 'alex', ?, ?)",
            (person_id, NOW, NOW),
        )
        self.conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_tag,"
            " created_at, updated_at)"
            " VALUES ('conflict theme', 'tag', 'conflict', ?, ?)",
            (NOW, NOW),
        )
        self.conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_since, scope_until,"
            " created_at, updated_at)"
            " VALUES ('june', 'range', '2026-06-01', '2026-06-30', ?, ?)",
            (NOW, NOW),
        )
        self.conn.commit()
        original = db.dump_to_dict(self.conn)
        restored = self._fresh_connection()

        db.load_from_dict(restored, original)

        self.assertEqual(db.dump_to_dict(restored), original)
        scoped = {
            review["body"]: review
            for review in db.dump_to_dict(restored)["reviews"]
        }
        self.assertEqual(scoped["about alex"]["scope_person_handle"], "alex")
        self.assertEqual(scoped["conflict theme"]["scope_tag"], "conflict")
        self.assertEqual(scoped["june"]["scope_since"], "2026-06-01")
        self.assertEqual(scoped["june"]["scope_until"], "2026-06-30")

    def test_format_version_one_archive_restores_with_null_scope_columns(self) -> None:
        # Hand-built: exactly what a pre-v4 export produced, with no scope
        # columns beyond scope_kind/scope_limit on the reviews rows.
        payload = db.dump_to_dict(self.conn)
        payload["format_version"] = 1
        payload["schema_version"] = 3
        for review in payload["reviews"]:
            for column in (
                "scope_person_id",
                "scope_person_handle",
                "scope_tag",
                "scope_since",
                "scope_until",
            ):
                del review[column]
        restored = self._fresh_connection()

        with self.assertWarns(RuntimeWarning):
            db.load_from_dict(restored, payload)

        rows = restored.execute(
            "SELECT scope_kind, scope_limit, scope_person_id,"
            " scope_person_handle, scope_tag, scope_since, scope_until"
            " FROM reviews ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            dict(rows[0]),
            {
                "scope_kind": "latest",
                "scope_limit": 3,
                "scope_person_id": None,
                "scope_person_handle": None,
                "scope_tag": None,
                "scope_since": None,
                "scope_until": None,
            },
        )

    def test_missing_required_column_is_still_rejected(self) -> None:
        payload = db.dump_to_dict(self.conn)
        del payload["reviews"][0]["scope_kind"]

        with self.assertRaisesRegex(db.DumpFormatError, "missing column 'scope_kind'"):
            db.validate_dump(payload)

    def test_unknown_keys_and_columns_are_ignored(self) -> None:
        payload = db.dump_to_dict(self.conn)
        payload["future_table"] = [{"future": True}]
        payload["people"][0]["future_column"] = "ignored"
        restored = self._fresh_connection()

        db.load_from_dict(restored, payload)

        expected = copy.deepcopy(payload)
        expected.pop("future_table")
        expected["people"][0].pop("future_column")
        self.assertEqual(db.dump_to_dict(restored), expected)

    def test_absent_known_table_is_treated_as_empty(self) -> None:
        payload = db.dump_to_dict(self.conn)
        for table in (
            "people",
            "entries",
            "tags",
            "feedback",
            "reviews",
            "goals",
            "person_notes",
        ):
            payload.pop(table)
        restored = self._fresh_connection()

        db.load_from_dict(restored, payload)

        self.assertEqual(db.dump_to_dict(restored)["people"], [])

    def test_schema_version_mismatch_warns_but_loads(self) -> None:
        payload = db.dump_to_dict(self.conn)
        payload["schema_version"] = db.SCHEMA_VERSION - 1
        restored = self._fresh_connection()

        with self.assertWarns(RuntimeWarning):
            db.load_from_dict(restored, payload)

        self.assertEqual(len(db.dump_to_dict(restored)["entries"]), 3)

    def test_bad_foreign_key_rolls_back_to_empty(self) -> None:
        payload = db.dump_to_dict(self.conn)
        payload["entries"][0]["person_ids"].append(999)
        restored = self._fresh_connection()

        with self.assertRaises(db.DumpFormatError):
            db.load_from_dict(restored, payload)

        restored_dump = db.dump_to_dict(restored)
        for table in (
            "people",
            "entries",
            "tags",
            "feedback",
            "reviews",
            "goals",
            "person_notes",
        ):
            self.assertEqual(restored_dump[table], [])

    def test_dry_run_performs_restore_checks_without_writes(self) -> None:
        payload = db.dump_to_dict(self.conn)
        restored = self._fresh_connection()

        db.load_from_dict(restored, payload, dry_run=True)

        self.assertEqual(db.dump_to_dict(restored)["entries"], [])


class SequenceResetTests(unittest.TestCase):
    def test_empty_but_previously_used_target_still_continues_at_dump_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.sqlite3"
            target_path = Path(tmp) / "target.sqlite3"
            db.init_db(source_path)
            db.init_db(target_path)
            with closing(db.connect(source_path)) as source, closing(
                db.connect(target_path)
            ) as target:
                first = db.create_entry(source, "source")
                for index in range(5):
                    db.create_entry(target, f"old {index}")
                target.execute("DELETE FROM entries")
                target.commit()

                db.load_from_dict(target, db.dump_to_dict(source))
                created = db.create_entry(target, "new")

                self.assertEqual(created.id, first.id + 1)


if __name__ == "__main__":
    unittest.main()
