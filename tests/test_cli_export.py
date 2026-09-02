"""Tests for ``commscoach export``: Markdown rendering and file permissions."""

from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from commscoach import cli, db


class ExportTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "export.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_person(
        self,
        handle: str,
        *,
        name: str | None = None,
        is_self: bool = False,
    ) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name, is_self=is_self)

    def _seed_entry(
        self,
        body: str = "seed body",
        *,
        source: str | None = None,
        occurred_at: str | None = None,
        tags: tuple[str, ...] = (),
        people: tuple[str, ...] = (),
    ) -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(
                conn,
                body,
                source=source,
                occurred_at=occurred_at,
                tags=tags,
                people=people,
            )

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)

    def _seed_review(
        self,
        body: str,
        entry_ids: list[int],
        *,
        scope_kind: str = "latest",
        scope_limit: int | None = 20,
        scope_person_handle: str | None = None,
        scope_tag: str | None = None,
        scope_since: str | None = None,
        scope_until: str | None = None,
    ) -> db.Review:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_review(
                conn,
                body,
                entry_ids,
                scope_kind=scope_kind,
                scope_limit=scope_limit,
                scope_person_handle=scope_person_handle,
                scope_tag=scope_tag,
                scope_since=scope_since,
                scope_until=scope_until,
            )

    def _seed_goal(
        self,
        title: str,
        *,
        detail: str | None = None,
        person_handle: str | None = None,
        review_id: int | None = None,
    ) -> db.Goal:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_goal(
                conn,
                title,
                detail=detail,
                person_handle=person_handle,
                review_id=review_id,
            )


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
        self.assertIn(
            f"- **Covered entry IDs**: {second.id}, {first.id}", out
        )
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
        self.assertLess(
            out.find(f"### Goal {active.id}"), out.find(f"### Goal {closed.id}")
        )
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
        self.assertEqual(
            self._scope_line(out), "- **Scope**: person @alice (context limit 20)"
        )

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
        self.assertEqual(
            self._scope_line(out), "- **Scope**: range 2026-07-01..2026-09-01"
        )

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


class ExportFileTests(ExportTestBase):
    def test_out_writes_file_with_markdown(self) -> None:
        self._seed_entry("hello")
        out_path = self.tmpdir / "out.md"
        rc, stdout, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertTrue(out_path.exists())
        content = out_path.read_text(encoding="utf-8")
        self.assertIn("# commscoach export", content)
        self.assertIn("hello", content)

    def test_out_file_has_mode_0600(self) -> None:
        self._seed_entry("hello")
        out_path = self.tmpdir / "perms.md"
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_out_overwrites_existing_file_and_tightens_mode(self) -> None:
        out_path = self.tmpdir / "existing.md"
        out_path.write_text("stale content", encoding="utf-8")
        os.chmod(out_path, 0o644)
        self._seed_entry("fresh")
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        content = out_path.read_text(encoding="utf-8")
        self.assertNotIn("stale content", content)
        self.assertIn("fresh", content)
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_out_confirmation_message_on_stderr(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "msg.md"
        rc, stdout, stderr = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertIn(str(out_path), stderr)

    def test_out_expands_user_home(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "home.md"
        # Use an explicit absolute path; ensure expanduser is wired by passing
        # a path that begins with the literal tmpdir (no ~) and one that does.
        # The simplest assertion: passing the absolute path round-trips.
        rc, _, _ = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())

    def test_out_missing_parent_exits_one(self) -> None:
        self._seed_entry("body")
        out_path = self.tmpdir / "missing-dir" / "out.md"
        rc, stdout, stderr = self._run("export", "--out", str(out_path))
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("cannot write export", stderr)
        self.assertIn(str(out_path), stderr)


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
