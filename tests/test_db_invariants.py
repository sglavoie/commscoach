"""Tests for DB-level invariants: constraints, uniqueness, and cascades.

These tests use temporary DB files exclusively so the default user DB is never
mutated.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from commscoach import db
from tests.db_case import DBTestCase
from tests.db_schema_case import NOW


class SchemaInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)

    def _insert_person(self, conn: sqlite3.Connection, handle: str, is_self: int = 0) -> int:
        cur = conn.execute(
            "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
            " VALUES (?, NULL, ?, ?, ?)",
            (handle, is_self, NOW, NOW),
        )
        return cur.lastrowid

    def _insert_entry(self, conn: sqlite3.Connection, body: str = "msg") -> int:
        cur = conn.execute(
            "INSERT INTO entries (body, source, occurred_at, created_at, updated_at)"
            " VALUES (?, NULL, NULL, ?, ?)",
            (body, NOW, NOW),
        )
        return cur.lastrowid

    def _insert_review(self, conn: sqlite3.Connection, body: str = "review") -> int:
        cur = conn.execute(
            "INSERT INTO reviews (body, scope_kind, scope_limit, created_at, updated_at)"
            " VALUES (?, 'latest', 20, ?, ?)",
            (body, NOW, NOW),
        )
        return cur.lastrowid

    def test_handle_uniqueness_is_case_insensitive(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "Alice")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_person(conn, "alice")

    def test_at_most_one_self_person(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "me", is_self=1)
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_person(conn, "other", is_self=1)

    def test_multiple_non_self_people_allowed(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            self._insert_person(conn, "a", is_self=0)
            self._insert_person(conn, "b", is_self=0)
            (count,) = conn.execute("SELECT COUNT(*) FROM people WHERE is_self = 0").fetchone()
            self.assertEqual(count, 2)

    def test_is_self_rejects_non_boolean(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO people (handle, name, is_self, created_at, updated_at)"
                    " VALUES (?, NULL, 2, ?, ?)",
                    ("bogus", NOW, NOW),
                )

    def test_tags_unique_case_insensitive(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO tags (name) VALUES (?)", ("Work",))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tags (name) VALUES (?)", ("work",))

    def test_deleting_entry_cascades_to_links_and_feedback(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            person_id = self._insert_person(conn, "alice")
            tag_cur = conn.execute("INSERT INTO tags (name) VALUES (?)", ("work",))
            tag_id = tag_cur.lastrowid
            conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person_id),
            )
            conn.execute(
                "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_id),
            )
            conn.execute(
                "INSERT INTO feedback (entry_id, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (entry_id, "good job", NOW, NOW),
            )
            conn.commit()

            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.commit()

            (ep_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_people WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            (et_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_tags WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            (fb_count,) = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(ep_count, 0)
            self.assertEqual(et_count, 0)
            self.assertEqual(fb_count, 0)

    def test_deleting_person_cascades_entry_people_only(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            person_id = self._insert_person(conn, "alice")
            conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry_id, person_id),
            )
            conn.commit()

            conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.commit()

            (ep_count,) = conn.execute(
                "SELECT COUNT(*) FROM entry_people WHERE person_id = ?", (person_id,)
            ).fetchone()
            (entry_count,) = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
            self.assertEqual(ep_count, 0)
            self.assertEqual(entry_count, 1)

    def test_feedback_requires_existing_entry(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO feedback (entry_id, body, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (9999, "orphan", NOW, NOW),
                )

    def test_entry_people_requires_existing_rows(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                    (entry_id, 9999),
                )

    def _insert_scoped_review(self, conn: sqlite3.Connection, **scope: object) -> sqlite3.Cursor:
        """Insert a review with every scope column stated explicitly."""
        columns = (
            "scope_kind",
            "scope_limit",
            "scope_person_id",
            "scope_person_handle",
            "scope_tag",
            "scope_since",
            "scope_until",
        )
        unknown = set(scope) - set(columns)
        assert not unknown, f"unknown scope columns {sorted(unknown)}"
        values = tuple(scope.get(column) for column in columns)
        return conn.execute(
            "INSERT INTO reviews"
            f" (body, {', '.join(columns)}, created_at, updated_at)"
            f" VALUES (?, {', '.join('?' for _ in columns)}, ?, ?)",
            ("scoped", *values, NOW, NOW),
        )

    def test_reviews_scope_kind_accepts_the_four_kinds(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            person_id = self._insert_person(conn, "alice")
            accepted = (
                {"scope_kind": "latest", "scope_limit": 20},
                {
                    "scope_kind": "person",
                    "scope_person_id": person_id,
                    "scope_person_handle": "alice",
                },
                {"scope_kind": "tag", "scope_tag": "conflict"},
                {"scope_kind": "range", "scope_since": "2026-06-01"},
                {"scope_kind": "range", "scope_until": "2026-06-30"},
                {
                    "scope_kind": "range",
                    "scope_since": "2026-06-01",
                    "scope_until": "2026-06-30",
                },
            )
            for scope in accepted:
                with self.subTest(scope=scope):
                    self._insert_scoped_review(conn, **scope)
            (count,) = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()
            self.assertEqual(count, len(accepted))

    def test_reviews_scope_kind_rejects_unknown_kind(self) -> None:
        with closing(db.connect(self.db_path)) as conn, self.assertRaises(sqlite3.IntegrityError):
            self._insert_scoped_review(conn, scope_kind="all")

    def test_reviews_scope_check_matrix_rejects_mismatched_axes(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            person_id = self._insert_person(conn, "alice")
            rejected = (
                # 'person' demands the denormalized handle.
                {"scope_kind": "person"},
                {"scope_kind": "person", "scope_person_id": person_id},
                # ... and no other kind may carry one.
                {"scope_kind": "latest", "scope_person_handle": "alice"},
                # 'tag' demands scope_tag, and only 'tag' may set it.
                {"scope_kind": "tag"},
                {"scope_kind": "latest", "scope_tag": "conflict"},
                {
                    "scope_kind": "person",
                    "scope_person_handle": "alice",
                    "scope_tag": "conflict",
                },
                # The live person link only makes sense for a person scope.
                {"scope_kind": "latest", "scope_person_id": person_id},
                {
                    "scope_kind": "tag",
                    "scope_tag": "conflict",
                    "scope_person_id": person_id,
                },
                # Date bounds belong to 'range' alone...
                {"scope_kind": "latest", "scope_since": "2026-06-01"},
                {"scope_kind": "latest", "scope_until": "2026-06-30"},
                {
                    "scope_kind": "tag",
                    "scope_tag": "conflict",
                    "scope_since": "2026-06-01",
                },
                # ... and an unbounded range is indistinguishable from 'latest'.
                {"scope_kind": "range"},
                {"scope_kind": "range", "scope_limit": 20},
            )
            for scope in rejected:
                with self.subTest(scope=scope), self.assertRaises(sqlite3.IntegrityError):
                    self._insert_scoped_review(conn, **scope)

    def test_reviews_scope_limit_rejects_negative(self) -> None:
        with closing(db.connect(self.db_path)) as conn, self.assertRaises(sqlite3.IntegrityError):
            self._insert_scoped_review(conn, scope_kind="latest", scope_limit=-1)

    def test_deleting_person_nulls_scope_link_and_keeps_the_handle(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            person_id = self._insert_person(conn, "alice")
            self._insert_scoped_review(
                conn,
                scope_kind="person",
                scope_person_id=person_id,
                scope_person_handle="alice",
            )
            conn.commit()

            conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.commit()

            row = conn.execute(
                "SELECT scope_kind, scope_person_id, scope_person_handle FROM reviews"
            ).fetchone()
            self.assertEqual(
                dict(row),
                {
                    "scope_kind": "person",
                    "scope_person_id": None,
                    "scope_person_handle": "alice",
                },
            )

    def test_deleting_review_cascades_to_review_entries(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            review_id = self._insert_review(conn)
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position) VALUES (?, ?, 0)",
                (review_id, entry_id),
            )
            conn.commit()

            conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            conn.commit()

            (count,) = conn.execute(
                "SELECT COUNT(*) FROM review_entries WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            self.assertEqual(count, 0)

    def test_deleting_entry_cascades_to_review_entries(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            entry_id = self._insert_entry(conn)
            review_id = self._insert_review(conn)
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position) VALUES (?, ?, 0)",
                (review_id, entry_id),
            )
            conn.commit()

            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.commit()

            (count,) = conn.execute(
                "SELECT COUNT(*) FROM review_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(count, 0)


class PersonScopedReviewSurvivalTests(DBTestCase):
    """What a person-scoped review looks like through the API after ``person rm``.

    The DB-level half of this is
    ``test_deleting_person_nulls_scope_link_and_keeps_the_handle``; this is the
    same invariant seen from hydration and listing, which is what the CLI and
    the Markdown export actually read.
    """

    def test_deleted_person_leaves_the_handle_reachable(self) -> None:
        person = db.create_person(self.conn, "alice")
        entry = db.create_entry(self.conn, "standup", people=["alice"])
        review = db.create_review(
            self.conn,
            "how it went with alice",
            [entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        self.assertIsNotNone(review.scope_person)

        db.delete_person(self.conn, person.id)

        orphaned = db.get_review(self.conn, review.id)
        self.assertIsNotNone(orphaned)
        assert orphaned is not None
        self.assertEqual(orphaned.scope_kind, "person")
        self.assertIsNone(orphaned.scope_person)
        self.assertEqual(orphaned.scope_person_handle, "alice")
        self.assertEqual(
            [found.id for found in db.list_reviews(self.conn, person_handle="alice")],
            [review.id],
        )


if __name__ == "__main__":
    unittest.main()
