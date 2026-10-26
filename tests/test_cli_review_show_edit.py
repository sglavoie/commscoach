"""Tests for ``commscoach review show/edit/rm`` and help output."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from unittest import mock

from commscoach import cli, db
from commscoach.cli import reviews as cli_reviews

from .cli_review_case import ReviewTestBase


class ReviewShowTests(ReviewTestBase):
    def test_with_goals_includes_linked_experiments_and_outcomes_only(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("Review to revisit", [entry.id])
        other = self._seed_review("Other review", [entry.id])
        with closing(db.connect(self.db_path)) as conn:
            active = db.create_goal(
                conn, "Keep practicing", review_id=review.id, detail="Ask first."
            )
            done = db.create_goal(conn, "Completed experiment", review_id=review.id)
            db.close_goal(conn, done.id, outcome="Worked in three meetings.")
            dropped = db.create_goal(conn, "Dropped experiment", review_id=review.id)
            db.close_goal(conn, dropped.id, dropped=True, outcome="Role changed.")
            db.create_goal(conn, "Unrelated experiment", review_id=other.id)
            db.create_goal(conn, "Unlinked experiment")
        rc, out, err = self._run("review", "show", str(review.id), "--with-goals")
        self.assertEqual(rc, 0, err)
        for text in (
            "Review to revisit",
            "Linked goals:",
            "Keep practicing",
            "Ask first.",
            "Completed experiment",
            "Worked in three meetings.",
            "Dropped experiment",
            "Role changed.",
        ):
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

        rc, out, _ = self._run("review", "edit", str(review.id), "--body", "tightened synthesis")

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
        self.assertEqual([entry.id for entry in updated.entries], [second.id, first.id])

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

        rc, _, _ = self._run("review", "edit", str(review.id), "--body", f"@{body_file}")

        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            updated = db.get_review(conn, review.id)
        assert updated is not None
        self.assertEqual(updated.body, "file revision")

    def test_edit_bare_body_opens_editor_seeded_with_current_body(self) -> None:
        entry = self._seed_entry("entry")
        review = self._seed_review("first draft", [entry.id])

        def compose(*, template: str, preamble: str, what: str) -> str:
            self.assertEqual(template, "first draft")
            self.assertIn("#>", preamble)
            return "edited in place"

        tty = mock.Mock()
        tty.isatty.return_value = True
        with (
            mock.patch.object(cli_reviews.sys, "stdin", tty),
            mock.patch.object(cli_reviews, "compose_in_editor", side_effect=compose),
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
        with (
            mock.patch.object(cli_reviews.sys, "stdin", tty),
            mock.patch.object(cli_reviews, "compose_in_editor", composed),
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

        rc, out, _ = self._run("--json", "review", "edit", str(review.id), "--body", "json edit")

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
