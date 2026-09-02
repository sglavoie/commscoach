"""Tests for tag normalization, orphan collection, and vocabulary management."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.db.tags import (
    gc_orphan_tags,
    normalize_tag,
    normalize_tag_list,
    upsert_tag,
)

from .db_case import DBTestCase


class NormalizeTagTests(unittest.TestCase):
    def test_strips_and_lowercases(self) -> None:
        self.assertEqual(normalize_tag("  Conflict  "), "conflict")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(normalize_tag("   "), "")


class NormalizeTagListTests(unittest.TestCase):
    def test_splits_on_commas_and_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            normalize_tag_list(["Ops, conflict", "ops"]), ["ops", "conflict"]
        )

    def test_drops_empties(self) -> None:
        self.assertEqual(normalize_tag_list(["ops,,  ,"]), ["ops"])

    def test_empty_iterable_yields_empty_list(self) -> None:
        self.assertEqual(normalize_tag_list([]), [])


class UpsertTagTests(DBTestCase):
    def test_is_idempotent_and_returns_the_same_id(self) -> None:
        first = upsert_tag(self.conn, "conflict")
        second = upsert_tag(self.conn, "conflict")
        self.assertEqual(first, second)
        self.assertEqual(len(db.list_tags(self.conn)), 1)


class GcOrphanTagsTests(DBTestCase):
    def test_drops_tags_no_entry_references(self) -> None:
        entry = db.create_entry(self.conn, "a", tags=("kept",))
        upsert_tag(self.conn, "orphan")
        self.conn.commit()

        gc_orphan_tags(self.conn)
        self.conn.commit()

        names = [tag.name for tag in db.list_tags(self.conn)]
        self.assertEqual(names, ["kept"])
        self.assertEqual(db.get_entry(self.conn, entry.id).tags, ("kept",))


class ListTagsTests(DBTestCase):
    def test_orders_by_count_desc_then_name_asc(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict", "zeta"))
        db.create_entry(self.conn, "b", tags=("conflict", "alpha"))

        tags = db.list_tags(self.conn)

        self.assertEqual(
            [(tag.name, tag.entry_count) for tag in tags],
            [("conflict", 2), ("alpha", 1), ("zeta", 1)],
        )

    def test_includes_a_tag_no_entry_references(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        # Only reachable by direct insert: entry CRUD runs gc_orphan_tags.
        upsert_tag(self.conn, "unused")
        self.conn.commit()

        tags = db.list_tags(self.conn)

        self.assertEqual(
            [(tag.name, tag.entry_count) for tag in tags],
            [("conflict", 1), ("unused", 0)],
        )

    def test_limit_caps_the_result(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict", "alpha"))
        db.create_entry(self.conn, "b", tags=("conflict",))

        tags = db.list_tags(self.conn, limit=1)

        self.assertEqual([tag.name for tag in tags], ["conflict"])

    def test_zero_limit_returns_nothing(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        self.assertEqual(db.list_tags(self.conn, limit=0), ())

    def test_negative_limit_is_rejected(self) -> None:
        with self.assertRaises(db.DBError):
            db.list_tags(self.conn, limit=-1)

    def test_empty_db_returns_empty_tuple(self) -> None:
        self.assertEqual(db.list_tags(self.conn), ())


class RenameTagTests(DBTestCase):
    def test_renames_in_place_keeping_entry_links(self) -> None:
        entry = db.create_entry(self.conn, "a", tags=("conflict",))
        before = db.list_tags(self.conn)[0]

        renamed = db.rename_tag(self.conn, "conflict", "Pushback")

        self.assertEqual(renamed.id, before.id)
        self.assertEqual(renamed.name, "pushback")
        self.assertEqual(renamed.entry_count, 1)
        self.assertEqual(db.get_entry(self.conn, entry.id).tags, ("pushback",))

    def test_missing_source_raises_tag_not_found(self) -> None:
        with self.assertRaises(db.TagNotFoundError) as ctx:
            db.rename_tag(self.conn, "absent", "other")
        self.assertIn("absent", str(ctx.exception))

    def test_rename_onto_existing_name_points_at_merge(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        db.create_entry(self.conn, "b", tags=("conflcit",))

        with self.assertRaises(db.DuplicateTagError) as ctx:
            db.rename_tag(self.conn, "conflcit", "conflict")

        self.assertIn("tag merge", str(ctx.exception))
        self.assertEqual(len(db.list_tags(self.conn)), 2)

    def test_case_only_rename_is_a_no_op(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))

        renamed = db.rename_tag(self.conn, "conflict", "Conflict")

        self.assertEqual(renamed.name, "conflict")
        self.assertEqual(renamed.entry_count, 1)
        self.assertEqual([tag.name for tag in db.list_tags(self.conn)], ["conflict"])

    def test_source_lookup_is_case_insensitive(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        self.assertEqual(db.rename_tag(self.conn, "  CONFLICT ", "calm").name, "calm")

    def test_empty_names_are_rejected(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        with self.assertRaises(db.DBError):
            db.rename_tag(self.conn, "conflict", "   ")
        with self.assertRaises(db.DBError):
            db.rename_tag(self.conn, "", "conflict")


class MergeTagsTests(DBTestCase):
    def test_repoints_links_and_drops_the_source(self) -> None:
        first = db.create_entry(self.conn, "a", tags=("conflcit",))
        second = db.create_entry(self.conn, "b", tags=("conflict",))

        merged = db.merge_tags(self.conn, "conflcit", "conflict")

        self.assertEqual(merged.name, "conflict")
        self.assertEqual(merged.entry_count, 2)
        self.assertEqual([tag.name for tag in db.list_tags(self.conn)], ["conflict"])
        self.assertEqual(db.get_entry(self.conn, first.id).tags, ("conflict",))
        self.assertEqual(db.get_entry(self.conn, second.id).tags, ("conflict",))

    def test_entry_carrying_both_tags_does_not_violate_the_primary_key(self) -> None:
        both = db.create_entry(self.conn, "a", tags=("conflcit", "conflict"))
        only_source = db.create_entry(self.conn, "b", tags=("conflcit",))

        merged = db.merge_tags(self.conn, "conflcit", "conflict")

        self.assertEqual(merged.entry_count, 2)
        self.assertEqual(db.get_entry(self.conn, both.id).tags, ("conflict",))
        self.assertEqual(db.get_entry(self.conn, only_source.id).tags, ("conflict",))
        rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM entry_tags WHERE entry_id = ?", (both.id,)
        ).fetchone()
        self.assertEqual(rows["n"], 1)

    def test_missing_destination_points_at_rename(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflcit",))

        with self.assertRaises(db.TagNotFoundError) as ctx:
            db.merge_tags(self.conn, "conflcit", "conflict")

        self.assertIn("tag rename", str(ctx.exception))
        self.assertEqual([tag.name for tag in db.list_tags(self.conn)], ["conflcit"])

    def test_missing_source_raises_tag_not_found(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        with self.assertRaises(db.TagNotFoundError):
            db.merge_tags(self.conn, "absent", "conflict")

    def test_merging_a_tag_into_itself_is_rejected(self) -> None:
        db.create_entry(self.conn, "a", tags=("conflict",))
        with self.assertRaises(db.DBError):
            db.merge_tags(self.conn, "conflict", "Conflict")
        self.assertEqual([tag.name for tag in db.list_tags(self.conn)], ["conflict"])


class DeleteTagTests(DBTestCase):
    def test_unlinks_from_every_entry_and_reports_the_count(self) -> None:
        first = db.create_entry(self.conn, "a", tags=("conflict", "ops"))
        second = db.create_entry(self.conn, "b", tags=("conflict",))

        unlinked = db.delete_tag(self.conn, "conflict")

        self.assertEqual(unlinked, 2)
        self.assertEqual([tag.name for tag in db.list_tags(self.conn)], ["ops"])
        self.assertEqual(db.get_entry(self.conn, first.id).tags, ("ops",))
        self.assertEqual(db.get_entry(self.conn, second.id).tags, ())

    def test_deleting_a_tag_on_no_entries_reports_zero(self) -> None:
        upsert_tag(self.conn, "unused")
        self.conn.commit()

        self.assertEqual(db.delete_tag(self.conn, "unused"), 0)
        self.assertEqual(db.list_tags(self.conn), ())

    def test_missing_tag_raises_tag_not_found(self) -> None:
        with self.assertRaises(db.TagNotFoundError):
            db.delete_tag(self.conn, "absent")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(db.DBError):
            db.delete_tag(self.conn, "  ")


if __name__ == "__main__":
    unittest.main()
