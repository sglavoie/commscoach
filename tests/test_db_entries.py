"""Tests for entry CRUD, tag normalization, cascades, and search."""

from __future__ import annotations

import unittest
from unittest import mock

from commscoach import db
from commscoach.db import entries as entries_mod
from commscoach.models import Entry

from .db_case import DBTestCase, patch_timestamps


class EntryCrudTests(DBTestCase):
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

    def test_create_entry_rejects_bad_occurred_at_without_writing(self) -> None:
        with self.assertRaises(db.DBError):
            db.create_entry(self.conn, "msg", occurred_at="yesterdya")
        self.assertEqual(db.list_entries(self.conn), ())

    def test_create_entry_normalizes_occurred_at(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", occurred_at="2026-01-01T01:30:00+01:30"
        )
        self.assertEqual(entry.occurred_at, "2026-01-01T00:00:00+00:00")

    def test_create_entry_normalizes_and_dedups_tags(self) -> None:
        entry = db.create_entry(
            self.conn,
            "msg",
            tags=["Work", " work ", "Focus", "FOCUS"],
        )
        self.assertEqual(set(entry.tags), {"work", "focus"})
        self.assertEqual(len(entry.tags), 2)

    def test_create_entry_splits_comma_delimited_tags(self) -> None:
        entry = db.create_entry(
            self.conn,
            "msg",
            tags=["work, Focus", "focus,planning"],
        )
        self.assertEqual(set(entry.tags), {"work", "focus", "planning"})

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
        entry = db.create_entry(self.conn, "msg", tags=["a", "b"], people=["alice"])
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
        entry = db.create_entry(self.conn, "msg", tags=["a"], people=["alice"])
        cleared = db.update_entry(self.conn, entry.id, tags=(), people=())
        self.assertEqual(cleared.tags, ())
        self.assertEqual(cleared.people, ())

    def test_update_entry_bumps_updated_at_on_relation_only_change(self) -> None:
        with patch_timestamps("t0", "t1"):
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

    def test_update_entry_rejects_bad_occurred_at_without_changing_entry(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", occurred_at="2026-01-01T00:00:00+00:00"
        )
        with self.assertRaises(db.DBError):
            db.update_entry(self.conn, entry.id, occurred_at="not-a-time")
        unchanged = db.get_entry(self.conn, entry.id)
        assert unchanged is not None
        self.assertEqual(unchanged.occurred_at, "2026-01-01T00:00:00+00:00")

    def test_update_entry_does_not_touch_unspecified_fields(self) -> None:
        entry = db.create_entry(
            self.conn, "msg", source="cli", occurred_at="2026-01-01T00:00:00Z"
        )
        updated = db.update_entry(self.conn, entry.id, body="new")
        self.assertEqual(updated.body, "new")
        self.assertEqual(updated.source, "cli")
        self.assertEqual(updated.occurred_at, "2026-01-01T00:00:00+00:00")

    def test_list_entries_newest_first(self) -> None:
        with patch_timestamps("t0", "t1", "t2"):
            db.create_entry(self.conn, "first")
            db.create_entry(self.conn, "second")
            db.create_entry(self.conn, "third")
        bodies = [e.body for e in db.list_entries(self.conn)]
        self.assertEqual(bodies, ["third", "second", "first"])

    def test_list_and_search_order_by_occurrence_then_creation(self) -> None:
        with patch_timestamps(
            "2026-06-03T00:00:00+00:00",
            "2026-06-04T00:00:00+00:00",
        ):
            db.create_entry(self.conn, "created earlier")
            db.create_entry(
                self.conn,
                "created later but occurred first",
                occurred_at="2026-06-01T00:00:00+00:00",
            )
        expected = ["created earlier", "created later but occurred first"]
        self.assertEqual([entry.body for entry in db.list_entries(self.conn)], expected)
        self.assertEqual(
            [entry.body for entry in db.search_entries(self.conn, query="created")],
            expected,
        )

    def test_list_entries_honors_limit(self) -> None:
        for body in ("a", "b", "c"):
            db.create_entry(self.conn, body)
        limited = db.list_entries(self.conn, limit=2)
        self.assertEqual(len(limited), 2)

    def test_list_entries_rejects_negative_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.list_entries(self.conn, limit=-1)

    def test_list_entries_delegates_filters_to_search(self) -> None:
        sentinel: tuple[Entry, ...] = ()
        with mock.patch.object(
            entries_mod, "search_entries", return_value=sentinel
        ) as search:
            result = entries_mod.list_entries(
                self.conn,
                since="2026-06-01",
                until="2026-06-02",
                limit=4,
            )
        self.assertIs(result, sentinel)
        search.assert_called_once_with(
            self.conn,
            since="2026-06-01",
            until="2026-06-02",
            limit=4,
        )

    def test_delete_entry_cascades_links_and_feedback(self) -> None:
        entry = db.create_entry(self.conn, "msg", tags=["work"], people=["alice"])
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

    def test_search_entries_like_and_fts_agree_for_simple_term(self) -> None:
        match = db.create_entry(self.conn, "Clear quarterly feedback")
        db.create_entry(self.conn, "Daily standup")

        like_results = db.search_entries(self.conn, query="quarterly", mode="like")
        fts_results = db.search_entries(self.conn, query="quarterly", mode="fts")

        self.assertEqual([entry.id for entry in like_results], [match.id])
        self.assertEqual(fts_results, like_results)

    def test_search_entries_like_finds_midword_substring_fts_misses(self) -> None:
        match = db.create_entry(self.conn, "Quarterly planning")

        self.assertEqual(
            [
                entry.id
                for entry in db.search_entries(
                    self.conn, query="arter", mode="like"
                )
            ],
            [match.id],
        )
        self.assertEqual(
            db.search_entries(self.conn, query="arter", mode="fts"),
            (),
        )
        self.assertEqual(db.search_entries(self.conn, query="arter"), ())

    def test_search_entries_auto_falls_back_for_malformed_match(self) -> None:
        match = db.create_entry(self.conn, "A literal opening ( parenthesis")

        results = db.search_entries(self.conn, query="(")

        self.assertEqual([entry.id for entry in results], [match.id])

    def test_search_entries_fts_triggers_follow_edit_and_delete(self) -> None:
        entry = db.create_entry(self.conn, "Original searchable token")
        self.assertEqual(
            [
                item.id
                for item in db.search_entries(
                    self.conn, query="original", mode="fts"
                )
            ],
            [entry.id],
        )

        db.update_entry(self.conn, entry.id, body="Replacement searchable token")
        self.assertEqual(
            db.search_entries(self.conn, query="original", mode="fts"),
            (),
        )
        self.assertEqual(
            [
                item.id
                for item in db.search_entries(
                    self.conn, query="replacement", mode="fts"
                )
            ],
            [entry.id],
        )

        db.delete_entry(self.conn, entry.id)
        self.assertEqual(
            db.search_entries(self.conn, query="replacement", mode="fts"),
            (),
        )

    def test_search_entries_probe_negative_uses_like(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('fts5', '0')"
        )
        match = db.create_entry(self.conn, "Quarterly planning")

        results = db.search_entries(self.conn, query="arter")

        self.assertEqual([entry.id for entry in results], [match.id])

    def test_search_entries_rejects_unknown_mode(self) -> None:
        with self.assertRaises(db.DBError):
            db.search_entries(self.conn, mode="unknown")  # type: ignore[arg-type]

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

    def test_search_entries_date_boundaries_use_effective_time(self) -> None:
        created_at_values = (
            "2026-06-01T00:00:00+00:00",
            "2026-06-02T23:59:59+00:00",
            "2026-06-03T00:00:00+00:00",
            "2026-06-01T12:00:00+00:00",
            "2026-05-01T00:00:00+00:00",
        )
        with patch_timestamps(*created_at_values):
            since_edge = db.create_entry(self.conn, "since edge")
            until_edge = db.create_entry(self.conn, "until edge")
            db.create_entry(self.conn, "after until")
            db.create_entry(
                self.conn,
                "created inside but occurred outside",
                occurred_at="2026-05-31T23:59:59+00:00",
            )
            occurred_inside = db.create_entry(
                self.conn,
                "created outside but occurred inside",
                occurred_at="2026-06-02T12:00:00+00:00",
            )

        results = db.search_entries(
            self.conn,
            since="2026-06-01",
            until="2026-06-02",
        )
        self.assertEqual(
            {entry.id for entry in results},
            {since_edge.id, until_edge.id, occurred_inside.id},
        )

    def test_search_entries_without_feedback_handles_zero_one_and_two_notes(self) -> None:
        no_notes = db.create_entry(self.conn, "no notes")
        one_note = db.create_entry(self.conn, "one note")
        two_notes = db.create_entry(self.conn, "two notes")
        db.create_feedback(self.conn, one_note.id, "first")
        db.create_feedback(self.conn, two_notes.id, "first")
        db.create_feedback(self.conn, two_notes.id, "second")

        results = db.search_entries(self.conn, without_feedback=True)

        self.assertEqual([entry.id for entry in results], [no_notes.id])

    def test_search_entries_date_filters_and_with_tag_and_person(self) -> None:
        with patch_timestamps(
            "2026-06-10T12:00:00+00:00",
            "2026-06-10T12:00:01+00:00",
            "2026-06-10T12:00:02+00:00",
            "2026-06-09T23:59:59+00:00",
        ):
            match = db.create_entry(
                self.conn, "match", tags=["work"], people=["alice"]
            )
            db.create_entry(
                self.conn, "wrong tag", tags=["other"], people=["alice"]
            )
            db.create_entry(
                self.conn, "wrong person", tags=["work"], people=["bob"]
            )
            db.create_entry(
                self.conn, "wrong date", tags=["work"], people=["alice"]
            )

        results = db.search_entries(
            self.conn,
            tag="work",
            person_handle="alice",
            since="2026-06-10",
            until="2026-06-10",
        )
        self.assertEqual([entry.id for entry in results], [match.id])

    def test_search_entries_rejects_negative_limit(self) -> None:
        with self.assertRaises(db.DBError):
            db.search_entries(self.conn, limit=-1)


if __name__ == "__main__":
    unittest.main()
