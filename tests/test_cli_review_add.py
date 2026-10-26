"""Tests for ``commscoach review add``, including the scoped variants."""

from __future__ import annotations

import io
import json
from contextlib import closing, redirect_stderr
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
            with (
                self.subTest(flags=flags),
                mock.patch(
                    "sys.stdin.isatty",
                    return_value=True,
                ),
                mock.patch.object(cli_reviews, "compose_in_editor") as compose,
            ):
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
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch.object(
                cli_reviews,
                "compose_in_editor",
                return_value="Written review",
            ) as compose,
        ):
            rc, out, err = self._run(
                "review",
                "add",
                "--entry",
                str(entry.id),
                str(entry.id),
                "--person",
                person.handle.upper(),
                "--json",
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
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch.object(
                cli_reviews,
                "compose_in_editor",
            ) as compose,
        ):
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
        self.assertEqual([entry["id"] for entry in payload["entries"]], [second.id, first.id])

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

        rc, _, _ = self._run("review", "add", f"@{body_file}", "--entry", str(entry.id))

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            review = db.get_review(conn, 1)
        assert review is not None
        self.assertEqual(review.body, "file review")

    def test_add_missing_file_exits_one(self) -> None:
        entry = self._seed_entry("entry")
        rc, _, err = self._run("review", "add", "@/no/such/review.txt", "--entry", str(entry.id))
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
        rc, _, err = self._run("review", "add", "body", "--entry", str(self.entry.id), *args)
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

    def test_entries_outside_the_scope_warn_but_still_store(self) -> None:
        self._seed_person("bob")
        self._seed_entry("Unrelated", people=("bob",))
        for args, warned in (
            (("--person", "alice"), False),
            (("--tag", "conflict"), False),
            (("--since", "2000-01-01"), False),
            (("--person", "bob"), True),
            (("--tag", "other"), True),
            (("--until", "2000-01-01"), True),
        ):
            with self.subTest(args=args):
                if args == ("--tag", "other"):
                    self._seed_entry("tagged", tags=("other",))
                rc, _, err = self._run(
                    "review", "add", "body", "--entry", str(self.entry.id), *args
                )
                self.assertEqual(rc, 0, err)
                if warned:
                    self.assertIn(f"1 covered entry ({self.entry.id}) falls outside", err)
                else:
                    self.assertNotIn("outside", err)

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

    def test_conflicting_axis_flags_exit_two_without_writing(self) -> None:
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
        self.assertEqual(rc, 2)
        self.assertIn("--person", err)
        self.assertIn("--tag", err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.list_reviews(conn), ())

    def test_conflict_is_rejected_before_the_body_is_read(self) -> None:
        composed = mock.Mock()
        tty = mock.Mock()
        tty.isatty.return_value = True
        with (
            mock.patch.object(cli_reviews.sys, "stdin", tty),
            mock.patch.object(cli_reviews, "compose_in_editor", composed),
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
        self.assertEqual(rc, 2)
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
        with (
            mock.patch.object(cli_reviews.sys, "stdin", tty),
            mock.patch.object(cli_reviews, "compose_in_editor", composed),
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
