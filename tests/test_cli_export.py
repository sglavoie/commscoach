"""Tests for ``commscoach export`` to stdout, review scope and help."""

from __future__ import annotations

import io
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout

from commscoach import cli, db

from .cli_export_case import ExportTestBase


class ExportStdoutTests(ExportTestBase):
    def test_empty_db_prints_header_and_no_entries_marker(self) -> None:
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertIn("# commscoach export", out)
        self.assertIn("_No entries._", out)

    def test_single_entry_renders_metadata_and_body(self) -> None:
        entry = self._seed_entry(
            "hello body",
            source="slack",
            occurred_at="2026-06-20T12:00:00+00:00",
            tags=("foo", "bar"),
        )
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertIn(f"## Entry {entry.id}", out)
        self.assertIn(f"- **Created**: {entry.created_at}", out)
        self.assertIn("- **Occurred**: 2026-06-20T12:00:00+00:00", out)
        self.assertIn("- **Source**: slack", out)
        # tags are normalized lowercase, sorted in DB hydration
        self.assertIn("- **Tags**: ", out)
        self.assertIn("bar", out)
        self.assertIn("foo", out)
        self.assertIn("hello body", out)

    def test_entry_with_people_renders_handles_and_names(self) -> None:
        self._seed_person("me", is_self=True)
        self._seed_person("alice", name="Alice A.")
        self._seed_person("bob")
        entry = self._seed_entry(
            "team sync",
            people=("alice", "bob", "me"),
        )
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertIn(f"## Entry {entry.id}", out)
        self.assertIn("- **People**: ", out)
        self.assertIn("@alice (Alice A.)", out)
        self.assertIn("@bob", out)
        self.assertIn("@me (self)", out)

    def test_entry_omits_optional_metadata_when_unset(self) -> None:
        entry = self._seed_entry("just body")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        entry_section = out.split(f"## Entry {entry.id}", 1)[1]
        self.assertNotIn("**Occurred**", entry_section)
        self.assertNotIn("**Source**", entry_section)
        self.assertNotIn("**Tags**", entry_section)
        self.assertNotIn("**People**", entry_section)

    def test_feedback_grouped_under_entry_oldest_first(self) -> None:
        entry = self._seed_entry("body")
        first = self._seed_feedback(entry.id, "first note")
        second = self._seed_feedback(entry.id, "second note")
        third = self._seed_feedback(entry.id, "third note")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertIn("### Feedback", out)
        idx_first = out.find(f"#### Feedback {first.id} ")
        idx_second = out.find(f"#### Feedback {second.id} ")
        idx_third = out.find(f"#### Feedback {third.id} ")
        self.assertNotEqual(idx_first, -1)
        self.assertGreater(idx_second, idx_first)
        self.assertGreater(idx_third, idx_second)
        self.assertIn("first note", out)
        self.assertIn("third note", out)

    def test_review_section_renders_before_entries(self) -> None:
        first = self._seed_entry("old entry")
        second = self._seed_entry("new entry")
        review = self._seed_review(
            "weekly synthesis\nnext step",
            [second.id, first.id],
            scope_limit=2,
        )
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertIn("## Reviews", out)
        self.assertIn(f"### Review {review.id}", out)
        self.assertIn(f"- **Created**: {review.created_at}", out)
        self.assertIn("- **Scope**: latest (context limit 2)", out)
        self.assertIn(f"- **Covered entry IDs**: {second.id}, {first.id}", out)
        self.assertIn("weekly synthesis\nnext step", out)
        self.assertLess(out.find("## Reviews"), out.find("## Entry "))

    def test_goal_section_follows_reviews_with_active_goals_first(self) -> None:
        person = self._seed_person("alex")
        entry = self._seed_entry("source entry")
        review = self._seed_review("source review", [entry.id])
        active = self._seed_goal(
            "Active goal",
            detail="Ask one question",
            person_handle=person.handle,
            review_id=review.id,
        )
        closed = self._seed_goal("Closed goal")
        with closing(db.connect(self.db_path)) as conn:
            db.close_goal(conn, closed.id, outcome="It worked")

        rc, out, _ = self._run("export")

        self.assertEqual(rc, 0)
        self.assertLess(out.find("## Reviews"), out.find("## Goals"))
        self.assertLess(out.find("## Goals"), out.find("## Entry "))
        self.assertLess(out.find(f"### Goal {active.id}"), out.find(f"### Goal {closed.id}"))
        self.assertIn("- **Person**: @alex", out)
        self.assertIn(f"- **Review**: {review.id}", out)
        self.assertIn("#### Outcome", out)
        self.assertIn("It worked", out)

    def test_entries_emitted_newest_first(self) -> None:
        first = self._seed_entry("oldest entry")
        second = self._seed_entry("middle entry")
        third = self._seed_entry("newest entry")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        idx_first = out.find(f"## Entry {first.id}")
        idx_second = out.find(f"## Entry {second.id}")
        idx_third = out.find(f"## Entry {third.id}")
        self.assertGreater(idx_first, idx_second)
        self.assertGreater(idx_second, idx_third)

    def test_entries_separated_by_horizontal_rule(self) -> None:
        self._seed_entry("first")
        self._seed_entry("second")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        # exactly one separator between two entries
        self.assertEqual(out.count("\n---\n"), 1)

    def test_entry_without_feedback_skips_feedback_section(self) -> None:
        entry = self._seed_entry("solo entry")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        entry_section = out.split(f"## Entry {entry.id}", 1)[1]
        self.assertNotIn("### Feedback", entry_section)

    def test_output_ends_with_newline(self) -> None:
        self._seed_entry("body")
        rc, out, _ = self._run("export")
        self.assertEqual(rc, 0)
        self.assertTrue(out.endswith("\n"))

    def test_stdout_export_does_not_create_files(self) -> None:
        self._seed_entry("body")
        before = set(self.tmpdir.iterdir())
        rc, _, _ = self._run("export")
        self.assertEqual(rc, 0)
        after = set(self.tmpdir.iterdir())
        self.assertEqual(before, after)


class ExportReviewScopeTests(ExportTestBase):
    """The Markdown scope line names its axis, one case per ``scope_kind``."""

    def _scope_line(self, out: str) -> str:
        for line in out.splitlines():
            if line.startswith("- **Scope**: "):
                return line
        self.fail(f"no scope line in export:\n{out}")

    def test_latest_scope_keeps_the_bare_kind_and_limit_suffix(self) -> None:
        entry = self._seed_entry("note")
        self._seed_review("body", [entry.id], scope_limit=20)
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: latest (context limit 20)")

    def test_latest_scope_without_a_limit_omits_the_suffix(self) -> None:
        entry = self._seed_entry("note")
        self._seed_review("body", [entry.id], scope_limit=None)
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: latest")

    def test_person_scope_names_the_handle_and_keeps_the_limit_suffix(self) -> None:
        self._seed_person("alice")
        entry = self._seed_entry("note", people=("alice",))
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="person",
            scope_limit=20,
            scope_person_handle="alice",
        )
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: person @alice (context limit 20)")

    def test_person_scope_renders_the_stored_handle_casing(self) -> None:
        self._seed_person("Alice")
        entry = self._seed_entry("note", people=("Alice",))
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="person",
            scope_limit=None,
            scope_person_handle="ALICE",
        )
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: person @Alice")

    def test_person_scope_survives_the_person_being_deleted(self) -> None:
        person = self._seed_person("alice")
        entry = self._seed_entry("note", people=("alice",))
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="person",
            scope_limit=None,
            scope_person_handle="alice",
        )
        with closing(db.connect(self.db_path)) as conn:
            self.assertTrue(db.delete_person(conn, person.id))
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: person @alice")

    def test_person_handle_rename_names_the_current_handle(self) -> None:
        person = self._seed_person("alice")
        entry = self._seed_entry("note", people=("alice",))
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="person",
            scope_limit=None,
            scope_person_handle="alice",
        )
        with closing(db.connect(self.db_path)) as conn:
            db.update_person(conn, person.id, handle="ally")
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: person @ally")

    def test_tag_scope_names_the_normalized_tag(self) -> None:
        entry = self._seed_entry("note", tags=("conflict",))
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="tag",
            scope_limit=None,
            scope_tag="Conflict",
        )
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: tag conflict")

    def test_range_scope_names_both_bounds(self) -> None:
        entry = self._seed_entry("note")
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="range",
            scope_limit=None,
            scope_since="2026-07-01",
            scope_until="2026-09-01",
        )
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: range 2026-07-01..2026-09-01")

    def test_range_scope_leaves_an_open_bound_empty(self) -> None:
        entry = self._seed_entry("note")
        self._seed_review(
            "body",
            [entry.id],
            scope_kind="range",
            scope_limit=None,
            scope_since="2026-07-01",
        )
        _, out, _ = self._run("export")
        self.assertEqual(self._scope_line(out), "- **Scope**: range 2026-07-01..")


class ExportHelpTests(ExportTestBase):
    def test_export_help_lists_out_flag(self) -> None:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--db", str(self.db_path), "export", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        rendered = out_buf.getvalue()
        self.assertIn("--out", rendered)
        self.assertIn("--format", rendered)
        self.assertIn("Markdown", rendered)


if __name__ == "__main__":
    unittest.main()
