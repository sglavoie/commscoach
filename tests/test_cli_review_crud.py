"""Tests for the ``commscoach review`` add/list/show/edit/rm subcommands.

Split out of ``test_cli_review.py`` when the scope surface landed;
``review context`` and the shared scope flags live in
``test_cli_review_context.py``.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime
from unittest import mock

from commscoach import cli, db
from commscoach.cli import reviews as cli_reviews

from .cli_review_case import ReviewTestBase


class ReviewAddTests(ReviewTestBase):
    def test_show_with_entries_and_goals_preserves_json(self) -> None:
        entry = self._seed_entry("Complete evidence text", tags=("work",))
        review = self._seed_review("Synthesis", [entry.id])
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "Linked experiment", review_id=review.id)
        args = ("review", "show", str(review.id))
        self.assertNotIn("Complete evidence text", self._run(*args)[1])
        rc, out, err = self._run(*args, "--with-entries", "--with-goals")
        self.assertEqual(rc, 0, err)
        self.assertIn("Linked entries (current text):", out)
        self.assertIn("Complete evidence text", out)
        self.assertIn("Linked experiment", out)
        self.assertEqual(self._run(*args, "--json"), self._run(*args, "--with-entries", "--json"))
        self.assertEqual(
            self._run(*args, "--with-goals", "--json"),
            self._run(*args, "--with-entries", "--with-goals", "--json"),
        )
        with closing(db.connect(self.db_path)) as conn:
            db.delete_entry(conn, entry.id)
        rc, out, err = self._run(*args, "--with-entries")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no linked entries remain)", out)
        self.assertEqual(self._run("review", "show", "9999", "--with-entries")[0], 1)

    def test_invalid_prerequisites_do_not_open_editor(self) -> None:
        entry = self._seed_entry()
        cases = (
            (("--entry", str(entry.id), "9999"), "9999"),
            (("--entry", str(entry.id), "--person", "missing"), "missing"),
            (("--entry", str(entry.id), "--tag", "   "), "non-empty tag"),
            (("--entry", str(entry.id), "--db", str(self.tmpdir / "missing.sqlite3")), "init"),
        )
        for flags, message in cases:
            with self.subTest(flags=flags), mock.patch(
                "sys.stdin.isatty", return_value=True,
            ), mock.patch.object(cli_reviews, "compose_in_editor") as compose:
                rc, out, err = self._run("review", "add", *flags)
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)
                compose.assert_not_called()
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_valid_review_opens_editor_without_requiring_self(self) -> None:
        person = self._seed_person("alice")
        entry = self._seed_entry()
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(
            cli_reviews, "compose_in_editor", return_value="Written review",
        ) as compose:
            rc, out, err = self._run(
                "review", "add", "--entry", str(entry.id), str(entry.id),
                "--person", person.handle.upper(), "--json",
            )
        self.assertEqual(rc, 0, err)
        compose.assert_called_once()
        payload = json.loads(out)
        self.assertEqual(payload["body"], "Written review")
        self.assertEqual(payload["entry_ids"], [entry.id])
        self.assertEqual(payload["scope_person_handle"], person.handle)

    def test_outdated_database_does_not_open_editor(self) -> None:
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE _meta SET value = '3' WHERE key = 'schema_version'")
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(
            cli_reviews, "compose_in_editor",
        ) as compose:
            rc, out, err = self._run("review", "add", "--entry", "1")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("init", err)
        compose.assert_not_called()

    def test_add_creates_review_with_default_scope(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")

        rc, out, _ = self._run(
            "review",
            "add",
            "weekly synthesis",
            "--entry",
            str(second.id),
            str(first.id),
        )

        self.assertEqual(rc, 0)
        self.assertIn("created review 1", out)
        self.assertIn("weekly synthesis", out)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.scope_kind, "latest")
        self.assertEqual(review.scope_limit, 20)
        self.assertEqual([entry.id for entry in review.entries], [second.id, first.id])

    def test_add_without_body_on_non_tty_exits_one_without_writing(self) -> None:
        entry = self._seed_entry()
        with mock.patch.object(cli_reviews.sys, "stdin", io.StringIO()):
            rc, _, err = self._run(
                "review",
                "add",
                "--entry",
                str(entry.id),
            )
        self.assertEqual(rc, 1)
        self.assertIn("body required", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_add_json_output_uses_review_shape(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")

        rc, out, _ = self._run(
            "--json",
            "review",
            "add",
            "json synthesis",
            "--entry",
            str(second.id),
            "--entry",
            str(first.id),
            "--limit",
            "5",
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            set(payload.keys()),
            {
                "id",
                "body",
                "scope_kind",
                "scope_limit",
                "scope_person",
                "scope_person_handle",
                "scope_tag",
                "scope_since",
                "scope_until",
                "created_at",
                "updated_at",
                "entry_ids",
                "entries",
            },
        )
        self.assertEqual(payload["body"], "json synthesis")
        self.assertEqual(payload["scope_kind"], "latest")
        self.assertEqual(payload["scope_limit"], 5)
        self.assertIsNone(payload["scope_person"])
        self.assertIsNone(payload["scope_person_handle"])
        self.assertIsNone(payload["scope_tag"])
        self.assertIsNone(payload["scope_since"])
        self.assertIsNone(payload["scope_until"])
        self.assertEqual(payload["entry_ids"], [second.id, first.id])
        self.assertEqual(
            [entry["id"] for entry in payload["entries"]], [second.id, first.id]
        )

    def test_add_body_from_stdin(self) -> None:
        entry = self._seed_entry("entry")
        rc, _, _ = self._run(
            "review",
            "add",
            "-",
            "--entry",
            str(entry.id),
            stdin=io.StringIO("piped review\n"),
        )
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.body, "piped review\n")

    def test_add_body_from_file(self) -> None:
        entry = self._seed_entry("entry")
        body_file = self.tmpdir / "review.txt"
        body_file.write_text("file review", encoding="utf-8")

        rc, _, _ = self._run(
            "review", "add", f"@{body_file}", "--entry", str(entry.id)
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.body, "file review")

    def test_add_missing_file_exits_one(self) -> None:
        entry = self._seed_entry("entry")
        rc, _, err = self._run(
            "review", "add", "@/no/such/review.txt", "--entry", str(entry.id)
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read body", err)

    def test_add_unknown_entry_exits_one_without_writing(self) -> None:
        rc, _, err = self._run("review", "add", "body", "--entry", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_add_without_entry_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "add",
                    "body",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)


class ScopedAddTests(ReviewTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = self._seed_person("alice", name="Alice")
        self.entry = self._seed_entry("Pushed back", people=("alice",), tags=("conflict",))

    def _add(self, *args: str) -> db.Review:
        rc, _, err = self._run(
            "review", "add", "body", "--entry", str(self.entry.id), *args
        )
        self.assertEqual(rc, 0, err)
        with closing(db.connect(self.db_path)) as conn:
            reviews = db.list_reviews(conn, limit=1)
        return reviews[0]

    def test_person_flag_stores_person_scope(self) -> None:
        review = self._add("--person", "alice")
        self.assertEqual(review.scope_kind, "person")
        self.assertEqual(review.scope_person_handle, "alice")
        assert review.scope_person is not None
        self.assertEqual(review.scope_person.id, self.alice.id)
        self.assertIsNone(review.scope_tag)

    def test_tag_flag_stores_normalized_tag_scope(self) -> None:
        review = self._add("--tag", "Conflict")
        self.assertEqual(review.scope_kind, "tag")
        self.assertEqual(review.scope_tag, "conflict")
        self.assertIsNone(review.scope_person_handle)

    def test_date_bounds_store_range_scope(self) -> None:
        review = self._add("--since", "2026-07-01", "--until", "2026-09-01")
        self.assertEqual(review.scope_kind, "range")
        self.assertEqual(
            datetime.fromisoformat(review.scope_since).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-07-01T00:00:00"),
        )
        self.assertEqual(
            datetime.fromisoformat(review.scope_until).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-09-01T23:59:59.999999"),
        )

    def test_since_alone_stores_an_open_ended_range(self) -> None:
        review = self._add("--since", "2026-07-01")
        self.assertEqual(review.scope_kind, "range")
        self.assertEqual(
            datetime.fromisoformat(review.scope_since).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-07-01T00:00:00"),
        )
        self.assertIsNone(review.scope_until)

    def test_until_alone_stores_an_open_started_range(self) -> None:
        review = self._add("--until", "2026-09-01")
        self.assertEqual(review.scope_kind, "range")
        self.assertIsNone(review.scope_since)
        self.assertEqual(
            datetime.fromisoformat(review.scope_until).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-09-01T23:59:59.999999"),
        )

    def test_relative_date_grammar_is_accepted(self) -> None:
        review = self._add("--since", "8 weeks ago")
        self.assertEqual(review.scope_kind, "range")
        self.assertRegex(review.scope_since or "", r"^\d{4}-\d{2}-\d{2}T.+\+00:00$")

    def test_explicit_scope_matching_the_axis_flag_is_accepted(self) -> None:
        review = self._add("--scope", "person", "--person", "alice")
        self.assertEqual(review.scope_kind, "person")

    def test_legacy_scope_latest_with_limit_still_works(self) -> None:
        review = self._add("--scope", "latest", "--limit", "30")
        self.assertEqual(review.scope_kind, "latest")
        self.assertEqual(review.scope_limit, 30)

    def test_scope_contradicting_the_axis_flag_exits_one_without_writing(self) -> None:
        rc, _, err = self._run(
            "review",
            "add",
            "body",
            "--entry",
            str(self.entry.id),
            "--person",
            "alice",
            "--scope",
            "tag",
        )
        self.assertEqual(rc, 1)
        self.assertIn("--scope tag", err)
        self.assertIn("--person", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_conflicting_axis_flags_exit_one_without_writing(self) -> None:
        rc, _, err = self._run(
            "review",
            "add",
            "body",
            "--entry",
            str(self.entry.id),
            "--person",
            "alice",
            "--tag",
            "conflict",
        )
        self.assertEqual(rc, 1)
        self.assertIn("--person", err)
        self.assertIn("--tag", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_conflict_is_rejected_before_the_body_is_read(self) -> None:
        composed = mock.Mock()
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_reviews.sys, "stdin", tty), mock.patch.object(
            cli_reviews, "compose_in_editor", composed
        ):
            rc, _, err = self._run(
                "review",
                "add",
                "--entry",
                str(self.entry.id),
                "--person",
                "alice",
                "--tag",
                "conflict",
            )
        self.assertEqual(rc, 1)
        self.assertIn("--person", err)
        composed.assert_not_called()

    def test_reversed_range_exits_one_without_writing(self) -> None:
        rc, _, err = self._run(
            "review",
            "add",
            "body",
            "--entry",
            str(self.entry.id),
            "--since",
            "2026-09-01",
            "--until",
            "2026-08-01",
        )
        self.assertEqual(rc, 1)
        self.assertIn("--since must not be after --until", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_reversed_range_is_rejected_before_the_body_is_composed(self) -> None:
        """The whole point of the finding: no $EDITOR session for a bad range."""
        composed = mock.Mock()
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_reviews.sys, "stdin", tty), mock.patch.object(
            cli_reviews, "compose_in_editor", composed
        ):
            rc, _, err = self._run(
                "review",
                "add",
                "--entry",
                str(self.entry.id),
                "--since",
                "2026-09-01",
                "--until",
                "2026-08-01",
            )
        self.assertEqual(rc, 1)
        self.assertIn("must not be after", err)
        composed.assert_not_called()

    def test_unknown_person_exits_one_without_writing(self) -> None:
        rc, _, err = self._run(
            "review",
            "add",
            "body",
            "--entry",
            str(self.entry.id),
            "--person",
            "nobody",
        )
        self.assertEqual(rc, 1)
        self.assertIn("nobody", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_json_output_carries_the_scope_axis(self) -> None:
        rc, out, err = self._run(
            "--json",
            "review",
            "add",
            "Alice sync",
            "--person",
            "alice",
            "--limit",
            "10",
            "--entry",
            str(self.entry.id),
        )
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["scope_kind"], "person")
        self.assertEqual(payload["scope_limit"], 10)
        self.assertEqual(payload["scope_person"]["handle"], "alice")
        self.assertEqual(payload["scope_person"]["name"], "Alice")
        self.assertEqual(payload["scope_person_handle"], "alice")
        self.assertIsNone(payload["scope_tag"])


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


class ReviewShowTests(ReviewTestBase):
    def test_with_goals_includes_linked_experiments_and_outcomes_only(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("Review to revisit", [entry.id])
        other = self._seed_review("Other review", [entry.id])
        with closing(db.connect(self.db_path)) as conn:
            active = db.create_goal(conn, "Keep practicing", review_id=review.id, detail="Ask first.")
            done = db.create_goal(conn, "Completed experiment", review_id=review.id)
            db.close_goal(conn, done.id, outcome="Worked in three meetings.")
            dropped = db.create_goal(conn, "Dropped experiment", review_id=review.id)
            db.close_goal(conn, dropped.id, dropped=True, outcome="Role changed.")
            db.create_goal(conn, "Unrelated experiment", review_id=other.id)
            db.create_goal(conn, "Unlinked experiment")
        rc, out, err = self._run("review", "show", str(review.id), "--with-goals")
        self.assertEqual(rc, 0, err)
        for text in ("Review to revisit", "Linked goals:", "Keep practicing", "Ask first.",
                     "Completed experiment", "Worked in three meetings.", "Dropped experiment", "Role changed."):
            self.assertIn(text, out)
        self.assertNotIn("Unrelated experiment", out)
        self.assertNotIn("Unlinked experiment", out)
        rc, out, err = self._run("review", "show", str(review.id), "--with-goals", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual([g["id"] for g in payload["goals"]], [active.id, dropped.id, done.id])
        self.assertEqual([g["status"] for g in payload["goals"]], ["active", "dropped", "done"])
        self.assertEqual(payload["goals"][2]["outcome"], "Worked in three meetings.")
        rc, out, err = self._run("review", "show", str(review.id), "--json")
        self.assertEqual(rc, 0, err)
        goals = payload.pop("goals")
        self.assertEqual(json.loads(out), payload)
        self.assertEqual(len(goals), 3)
        rc, out, err = self._run("review", "show", str(review.id))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Linked goals:", out)

    def test_with_goals_handles_empty_and_missing_reviews(self) -> None:
        review = self._seed_review("No experiments yet", [self._seed_entry().id])
        rc, out, err = self._run("review", "show", str(review.id), "--with-goals")
        self.assertEqual(rc, 0, err)
        self.assertIn("Linked goals:\n(no goals)", out)
        rc, out, err = self._run("review", "show", str(review.id), "--with-goals", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["goals"], [])
        rc, out, err = self._run("review", "show", "9999", "--with-goals", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("9999", err)

    def test_show_existing_prints_human(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("strong summary", [entry.id])

        rc, out, _ = self._run("review", "show", str(review.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"[{review.id}]", out)
        self.assertIn("context limit 20", out)
        self.assertIn("strong summary", out)
        self.assertIn(f"entries: {entry.id}", out)

    def test_show_json_round_trip(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("stored summary", [entry.id])

        rc, out, _ = self._run("--json", "review", "show", str(review.id))

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], review.id)
        self.assertEqual(payload["body"], "stored summary")
        self.assertEqual(payload["entry_ids"], [entry.id])

    def test_show_json_carries_the_range_axis(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review(
            "stored summary",
            [entry.id],
            scope_kind="range",
            scope_since="2026-07-01",
            scope_until="2026-09-01",
        )

        rc, out, _ = self._run("--json", "review", "show", str(review.id))

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["scope_kind"], "range")
        self.assertEqual(payload["scope_since"], "2026-07-01")
        self.assertEqual(payload["scope_until"], "2026-09-01")
        self.assertIsNone(payload["scope_person"])
        self.assertIsNone(payload["scope_tag"])

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("review", "show", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_show_non_integer_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(["--db", str(self.db_path), "review", "show", "abc"])
        self.assertEqual(ctx.exception.code, 2)


class ReviewEditTests(ReviewTestBase):
    def test_edit_body_literal(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])

        rc, out, _ = self._run(
            "review", "edit", str(review.id), "--body", "tightened synthesis"
        )

        self.assertEqual(rc, 0)
        self.assertIn(f"updated review {review.id}", out)
        self.assertIn("tightened synthesis", out)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.body, "tightened synthesis")
        self.assertNotEqual(updated.updated_at, updated.created_at)

    def test_edit_leaves_scope_and_covered_entries_untouched(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")
        review = self._seed_review("first draft", [second.id, first.id])

        rc, _, _ = self._run("review", "edit", str(review.id), "--body", "revised")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.scope_kind, "latest")
        self.assertEqual(updated.scope_limit, 20)
        self.assertEqual(
            [entry.id for entry in updated.entries], [second.id, first.id]
        )

    def test_edit_leaves_a_scoped_axis_untouched(self) -> None:
        self._seed_person("alice")
        entry = self._seed_entry("entry")
        review = self._seed_review(
            "first draft",
            [entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        rc, _, _ = self._run("review", "edit", str(review.id), "--body", "revised")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.scope_kind, "person")
        self.assertEqual(updated.scope_person_handle, "alice")

    def test_edit_body_from_stdin(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])

        rc, _, _ = self._run(
            "review",
            "edit",
            str(review.id),
            "--body",
            "-",
            stdin=io.StringIO("piped revision\n"),
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.body, "piped revision\n")

    def test_edit_body_from_file(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])
        body_file = self.tmpdir / "revision.txt"
        body_file.write_text("file revision", encoding="utf-8")

        rc, _, _ = self._run(
            "review", "edit", str(review.id), "--body", f"@{body_file}"
        )

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.body, "file revision")

    def test_edit_bare_body_opens_editor_seeded_with_current_body(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])

        def compose(*, template: str, preamble: str) -> str:
            self.assertEqual(template, "first draft")
            self.assertIn("#>", preamble)
            return "edited in place"

        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_reviews.sys, "stdin", tty), mock.patch.object(
            cli_reviews, "compose_in_editor", side_effect=compose
        ):
            rc, _, _ = self._run("review", "edit", str(review.id), "--body")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.body, "edited in place")

    def test_edit_bare_body_missing_id_exits_one_before_the_editor(self) -> None:
        composed = mock.Mock()
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(cli_reviews.sys, "stdin", tty), mock.patch.object(
            cli_reviews, "compose_in_editor", composed
        ):
            rc, _, err = self._run("review", "edit", "9999", "--body")

        self.assertEqual(rc, 1)
        self.assertIn("review 9999 not found", err)
        composed.assert_not_called()

    def test_edit_without_body_flag_exits_one(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("unchanged", [entry.id])

        rc, _, err = self._run("review", "edit", str(review.id))

        self.assertEqual(rc, 1)
        self.assertIn("--body", err)
        with closing(db.connect(self.db_path)) as conn:
            unchanged = db.get_review(conn, review.id)
        assert unchanged is not None
        self.assertEqual(unchanged.body, "unchanged")

    def test_edit_empty_body_exits_one(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("keep me", [entry.id])

        rc, _, err = self._run("review", "edit", str(review.id), "--body", "   ")

        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_edit_missing_id_exits_one(self) -> None:
        rc, _, err = self._run("review", "edit", "9999", "--body", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_edit_json_output(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])

        rc, out, _ = self._run(
            "--json", "review", "edit", str(review.id), "--body", "json edit"
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], review.id)
        self.assertEqual(payload["body"], "json edit")
        self.assertEqual(payload["entry_ids"], [entry.id])
        self.assertNotEqual(payload["updated_at"], payload["created_at"])


class ReviewRmTests(ReviewTestBase):
    def test_rm_with_yes_deletes(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("delete me", [entry.id])

        rc, out, _ = self._run("--yes", "review", "rm", str(review.id))

        self.assertEqual(rc, 0)
        self.assertIn(f"deleted review {review.id}", out)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_review(conn, review.id))

    def test_rm_confirm_no_aborts(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("keep me", [entry.id])

        with mock.patch.object(cli_reviews, "confirm", return_value=False):
            rc, _, err = self._run("review", "rm", str(review.id))

        self.assertEqual(rc, 1)
        self.assertIn("aborted", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNotNone(db.get_review(conn, review.id))

    def test_rm_json_output(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("delete me", [entry.id])

        rc, out, _ = self._run("--yes", "--json", "review", "rm", str(review.id))

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"deleted": True, "id": review.id})

    def test_rm_missing_exits_one(self) -> None:
        rc, _, err = self._run("--yes", "review", "rm", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)


class ReviewHelpTests(ReviewTestBase):
    def test_review_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("review")
        self.assertEqual(rc, 0)
        self.assertIn("context", out)
        self.assertIn("add", out)
        self.assertIn("list", out)
        self.assertIn("show", out)
        self.assertIn("edit", out)
        self.assertIn("rm", out)

    def _help(self, subcommand: str) -> str:
        # argparse's --help action exits the process rather than returning
        # through cli.main, so it cannot go through ``_run``.
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), self.assertRaises(SystemExit) as ctx:
            cli.main(["--db", str(self.db_path), "review", subcommand, "--help"])
        self.assertEqual(ctx.exception.code, 0)
        return out_buf.getvalue()

    def test_add_help_documents_the_scope_flags(self) -> None:
        out = self._help("add")
        for flag in ("--scope", "--person", "--tag", "--since", "--until"):
            self.assertIn(flag, out)

    def test_context_help_documents_the_scope_flags(self) -> None:
        out = self._help("context")
        for flag in ("--scope", "--person", "--tag", "--since", "--until"):
            self.assertIn(flag, out)


if __name__ == "__main__":
    unittest.main()
