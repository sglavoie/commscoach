"""Tests for ``commscoach review list`` and scope/person rendering."""

from __future__ import annotations

import io
import json
from contextlib import closing, redirect_stderr

from commscoach import cli, db

from .cli_review_case import ReviewTestBase


class ReviewListTests(ReviewTestBase):
    def test_list_empty_prints_marker(self) -> None:
        rc, out, _ = self._run("review", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no reviews)", out)

    def test_list_newest_first_and_limit(self) -> None:
        first_entry = self._seed_entry("first entry")
        second_entry = self._seed_entry("second entry")
        first = self._seed_review("first review", [first_entry.id])
        second = self._seed_review("second review", [second_entry.id])
        third = self._seed_review("third review", [first_entry.id])

        rc, out, _ = self._run("review", "list", "--limit", "2")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{third.id}]", out)
        self.assertIn(f"[{second.id}]", out)
        self.assertIn("context-limit:20", out)
        self.assertNotIn(f"[{first.id}]", out)
        self.assertLess(out.find(f"[{third.id}]"), out.find(f"[{second.id}]"))

    def test_list_json_payload(self) -> None:
        first_entry = self._seed_entry("first entry")
        second_entry = self._seed_entry("second entry")
        first = self._seed_review("first review", [first_entry.id])
        second = self._seed_review("second review", [second_entry.id])

        rc, out, _ = self._run("--json", "review", "list")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([row["id"] for row in payload], [second.id, first.id])
        self.assertEqual(payload[0]["entry_ids"], [second_entry.id])

    def test_list_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "list",
                    "--limit",
                    "-1",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)


class ScopedListTests(ReviewTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._seed_person("alice")
        self._seed_person("bob")
        self.entry = self._seed_entry("seed", people=("alice",), tags=("conflict",))
        self.general = self._seed_review("general", [self.entry.id])
        self.about_alice = self._seed_review(
            "about alice",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        self.about_bob = self._seed_review(
            "about bob",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="bob",
        )
        self.about_conflict = self._seed_review(
            "about conflict",
            [self.entry.id],
            scope_kind="tag",
            scope_tag="conflict",
        )

    def _ids(self, *args: str) -> list[int]:
        rc, out, err = self._run("--json", "review", "list", *args)
        self.assertEqual(rc, 0, err)
        return [row["id"] for row in json.loads(out)]

    def test_scope_filter_selects_one_kind(self) -> None:
        self.assertEqual(
            sorted(self._ids("--scope", "person")),
            sorted([self.about_alice.id, self.about_bob.id]),
        )

    def test_person_filter_selects_one_handle(self) -> None:
        self.assertEqual(self._ids("--person", "alice"), [self.about_alice.id])

    def test_person_filter_is_case_insensitive(self) -> None:
        self.assertEqual(self._ids("--person", "ALICE"), [self.about_alice.id])

    def test_tag_filter_selects_one_tag(self) -> None:
        self.assertEqual(self._ids("--tag", "conflict"), [self.about_conflict.id])

    def test_filters_combine_with_and(self) -> None:
        self.assertEqual(
            self._ids("--scope", "person", "--person", "alice"),
            [self.about_alice.id],
        )

    def test_contradictory_filters_return_nothing(self) -> None:
        self.assertEqual(self._ids("--scope", "tag", "--person", "alice"), [])

    def test_no_filter_returns_every_review(self) -> None:
        self.assertEqual(len(self._ids()), 4)

    def test_filter_matching_nothing_prints_the_empty_marker(self) -> None:
        rc, out, _ = self._run("review", "list", "--person", "nobody")
        self.assertEqual(rc, 0)
        self.assertIn("(no reviews)", out)

    def test_unknown_scope_value_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "list",
                    "--scope",
                    "everything",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)


class ScopeRenderingTests(ReviewTestBase):
    """The one axis rendering both human formatters share."""

    def setUp(self) -> None:
        super().setUp()
        self._seed_person("alice")
        self.entry = self._seed_entry("seed")

    def _lines(self, review: db.Review) -> tuple[str, str]:
        rc, long_out, err = self._run("review", "show", str(review.id))
        self.assertEqual(rc, 0, err)
        rc, short_out, err = self._run("review", "list", "--limit", "1")
        self.assertEqual(rc, 0, err)
        return long_out, short_out

    def test_latest_scope_renders_the_bare_kind(self) -> None:
        review = self._seed_review("body", [self.entry.id])
        long_out, short_out = self._lines(review)
        self.assertIn("scope latest", long_out)
        self.assertIn("scope:latest", short_out)

    def test_person_scope_renders_the_handle(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        long_out, short_out = self._lines(review)
        self.assertIn("scope person:@alice", long_out)
        self.assertIn("scope:person:@alice", short_out)

    def test_person_scope_renders_the_handle_after_the_person_is_deleted(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        with closing(db.connect(self.db_path)) as conn:
            alice = db.get_person_by_handle(conn, "alice")
            assert alice is not None
            db.delete_person(conn, alice.id)

        long_out, _ = self._lines(review)

        self.assertIn("scope person:@alice", long_out)

    def test_person_handle_rename_renders_the_current_handle(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        rc, _, err = self._run("person", "edit", "alice", "--handle", "ally")
        self.assertEqual(rc, 0, err)

        long_out, short_out = self._lines(review)

        self.assertIn("scope person:@ally", long_out)
        self.assertIn("scope:person:@ally", short_out)
        self.assertNotIn("@alice", long_out)
        self.assertNotIn("@alice", short_out)

    def test_person_handle_rename_renders_the_live_person_over_a_stale_handle(
        self,
    ) -> None:
        """A row whose denormalized handle drifted still renders the truth.

        ``update_person`` keeps the two in sync, but an archive written before
        that fix and restored by ``import`` carries the pre-rename handle beside
        a live ``scope_person_id``; rendering follows the live person.
        """
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE people SET handle = 'ally' WHERE handle = 'alice'")

        long_out, short_out = self._lines(review)

        self.assertIn("scope person:@ally", long_out)
        self.assertIn("scope:person:@ally", short_out)

    def test_person_handle_rename_keeps_the_review_listable_by_the_new_handle(
        self,
    ) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        rc, _, err = self._run("person", "edit", "alice", "--handle", "ally")
        self.assertEqual(rc, 0, err)

        rc, found, err = self._run("review", "list", "--person", "ally")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"[{review.id}]", found)

        rc, missing, err = self._run("review", "list", "--person", "alice")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(f"[{review.id}]", missing)

    def test_person_handle_rename_then_delete_renders_the_last_known_handle(
        self,
    ) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        rc, _, err = self._run("person", "edit", "alice", "--handle", "ally")
        self.assertEqual(rc, 0, err)
        rc, _, err = self._run("--yes", "person", "rm", "ally")
        self.assertEqual(rc, 0, err)

        long_out, _ = self._lines(review)

        self.assertIn("scope person:@ally", long_out)

    def test_tag_scope_renders_the_tag(self) -> None:
        review = self._seed_review(
            "body", [self.entry.id], scope_kind="tag", scope_tag="conflict"
        )
        long_out, short_out = self._lines(review)
        self.assertIn("scope tag:conflict", long_out)
        self.assertIn("scope:tag:conflict", short_out)

    def test_closed_range_renders_both_bounds(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="range",
            scope_since="2026-07-01",
            scope_until="2026-09-01",
        )
        long_out, short_out = self._lines(review)
        self.assertIn("scope range:2026-07-01..2026-09-01", long_out)
        self.assertIn("scope:range:2026-07-01..2026-09-01", short_out)

    def test_open_started_range_renders_a_leading_separator(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="range",
            scope_until="2026-09-01",
        )
        long_out, short_out = self._lines(review)
        self.assertIn("scope range:..2026-09-01", long_out)
        self.assertIn("scope:range:..2026-09-01", short_out)

    def test_open_ended_range_renders_a_trailing_separator(self) -> None:
        review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="range",
            scope_since="2026-07-01",
        )
        long_out, short_out = self._lines(review)
        self.assertIn("scope range:2026-07-01..", long_out)
        self.assertIn("scope:range:2026-07-01..", short_out)


class DeletedPersonJsonTests(ReviewTestBase):
    """``scope_person_handle`` is the JSON counterpart of the human fallback.

    ``scope_person`` is a live lookup, so the JSON surfaces lose the person
    entirely once she is deleted; the denormalized handle is what keeps a
    machine consumer able to say *whose* review it is read.
    """

    def setUp(self) -> None:
        super().setUp()
        # Created as ``Alice`` and scoped as ``alice``: ``create_review``
        # canonicalizes to the person's own casing, so the stored handle is the
        # canonical one and not whatever the caller typed.
        self.alice = self._seed_person("Alice")
        self.entry = self._seed_entry("seed")
        self.review = self._seed_review(
            "body",
            [self.entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

    def _delete_alice(self) -> None:
        with closing(db.connect(self.db_path)) as conn:
            db.delete_person(conn, self.alice.id)

    def _show(self) -> dict:
        rc, out, err = self._run("--json", "review", "show", str(self.review.id))
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def _list(self) -> dict:
        rc, out, err = self._run("--json", "review", "list")
        self.assertEqual(rc, 0, err)
        rows = json.loads(out)
        self.assertEqual([row["id"] for row in rows], [self.review.id])
        return rows[0]

    def test_show_keeps_the_deleted_person_handle_json(self) -> None:
        live = self._show()
        self.assertEqual(live["scope_person"]["handle"], "Alice")
        self.assertEqual(live["scope_person_handle"], "Alice")

        self._delete_alice()

        orphaned = self._show()
        self.assertEqual(orphaned["scope_kind"], "person")
        self.assertIsNone(orphaned["scope_person"])
        self.assertEqual(orphaned["scope_person_handle"], "Alice")

    def test_list_keeps_the_deleted_person_handle_json(self) -> None:
        self.assertEqual(self._list()["scope_person_handle"], "Alice")

        self._delete_alice()

        orphaned = self._list()
        self.assertIsNone(orphaned["scope_person"])
        self.assertEqual(orphaned["scope_person_handle"], "Alice")

    def test_show_json_keeps_the_stored_handle_over_a_desynced_live_person(
        self,
    ) -> None:
        """The key reports the column, not the live person's current handle.

        ``update_person`` keeps the two in sync, so this state is only reachable
        through an ``import`` of an archive written before that fix. The human
        formatters resolve it by preferring the live person; the JSON shape
        instead reports both values so a consumer can see them disagree.
        """
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE people SET handle = 'ally' WHERE handle = 'Alice'")

        payload = self._show()

        self.assertEqual(payload["scope_person"]["handle"], "ally")
        self.assertEqual(payload["scope_person_handle"], "Alice")
