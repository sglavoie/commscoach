"""Regression coverage for incremental edits and revisiting coaching."""

import io
import json
from contextlib import closing
from unittest import mock

from commscoach import db
from commscoach.cli import entries, feedback

from .cli_entry_case import EntryTestBase


class EntryConvenienceTests(EntryTestBase):
    def test_source_filter_combines_with_existing_filters_and_order(self):
        self._create_person("alice")
        older = self._seed_entry(
            "Ask", source="Email", people=("alice",), tags=("work",), occurred_at="2026-01-01"
        )
        self._seed_entry(
            "Ask", source="email", people=("alice",), tags=("work",), occurred_at="2026-02-01"
        )
        self._seed_entry(
            "Ask", source="slack", people=("alice",), tags=("work",), occurred_at="2025-12-01"
        )
        self._seed_entry("Ask", source="email", tags=("work",), occurred_at="2026-01-01")
        self._seed_entry("Ask", source="email", people=("alice",), occurred_at="2026-01-01")
        self._seed_entry("Ask", people=("alice",), tags=("work",), occurred_at="2026-01-01")
        self._seed_entry(
            "Other", source="email", people=("alice",), tags=("work",), occurred_at="2026-01-01"
        )
        self._seed_entry(
            "Ask", source="email", people=("alice",), tags=("work",), occurred_at="2025-01-01"
        )
        coached = self._seed_entry(
            "Ask", source="email", people=("alice",), tags=("work",), occurred_at="2025-12-01"
        )
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, coached.id, "Already coached")
        rc, out, err = self._run(
            "entry",
            "search",
            "--source",
            "EMAIL",
            "--person",
            "ALICE",
            "--tag",
            "work",
            "--query",
            "Ask",
            "--no-feedback",
            "--since",
            "2025-12-01",
            "--until",
            "2026-02-02",
            "--oldest-first",
            "--limit",
            "1",
            "--json",
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([row["id"] for row in json.loads(out)], [older.id])
        rc, out, err = self._run(
            "entry",
            "search",
            "--source",
            "EMAIL",
            "--person",
            "ALICE",
            "--tag",
            "work",
            "--query",
            "Ask",
            "--no-feedback",
            "--since",
            "2025-12-01",
            "--until",
            "2026-02-02",
            "--oldest-first",
            "--limit",
            "1",
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(f"[{older.id}]", out)
        self.assertIn("Showing 1 of 2 matching entries.", out)

    def test_search_totals_follow_fts_literal_and_fallback_matching(self):
        for body in ("café discussion 10%", "cafe discussion 10%", "cafeteria", "unrelated"):
            self._seed_entry(body)
        with closing(db.connect(self.db_path)) as conn:
            fts = conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()[0] == "1"
        cases = [("%", (), 2), ("10%", ("--exact",), 2)]
        if fts:
            cases.extend(
                (
                    # Plain words match as prefixes; quoted phrases stay exact.
                    ("cafe", (), 3),
                    ('"cafe discussion"', (), 2),
                    ("caf*", (), 3),
                    ("cafe discussion", (), 2),
                )
            )
        for query, extra, total in cases:
            with self.subTest(query=query, extra=extra):
                rc, out, err = self._run(
                    "entry",
                    "search",
                    "--query",
                    query,
                    "--limit",
                    "1",
                    *extra,
                )
                self.assertEqual(rc, 0, err)
                self.assertIn(f"Showing 1 of {total} matching entries.", out)
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE _meta SET value = '0' WHERE key = 'fts5'")
        rc, out, err = self._run("entry", "search", "--query", "10%", "--limit", "1")
        self.assertEqual(rc, 0, err)
        self.assertIn("Showing 1 of 2 matching entries.", out)

    def test_search_totals_only_appear_when_human_results_are_omitted(self):
        for _ in range(2):
            self._seed_entry("matching")
        for flags in (
            (),
            ("--limit", "2"),
            ("--limit", "3"),
            ("--query", "absent", "--limit", "1"),
        ):
            with self.subTest(flags=flags):
                rc, out, err = self._run("entry", "search", *flags)
                self.assertEqual(rc, 0, err)
                self.assertNotIn("Showing", out)
        rc, out, err = self._run("entry", "search", "--limit", "0")
        self.assertEqual(rc, 0, err)
        self.assertIn("Showing 0 of 2 matching entries.", out)
        with mock.patch.object(db, "count_search_entries") as count:
            rc, out, err = self._run("entry", "search", "--limit", "1", "--json")
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(out)), 1)
            count.assert_not_called()

    def test_source_filter_survives_fts_fallback_and_is_an_exact_match(self):
        match = self._seed_entry("10% better", source="email")
        self._seed_entry("10% better", source="email-thread")
        for flags in ((), ("--exact",)):
            rc, out, err = self._run(
                "entry",
                "search",
                "--source",
                "EMAIL",
                "--query",
                "%",
                "--json",
                *flags,
            )
            self.assertEqual(rc, 0, err)
            self.assertEqual([row["id"] for row in json.loads(out)], [match.id])

    def test_incremental_links_preserve_other_links_and_normalize(self):
        for handle in ("alice", "bob", "carol"):
            self._create_person(handle)
        entry = self._seed_entry(tags=("keep", "drop"), people=("alice", "bob"))
        rc, out, err = self._run(
            "--json",
            "entry",
            "edit",
            str(entry.id),
            "--add-tag",
            "NEW,keep",
            "--add-tag",
            "new",
            "--remove-tag",
            " DROP,absent ",
            "--add-person",
            "CAROL",
            "--add-person",
            "ALICE",
            "--remove-person",
            "BOB",
            "--remove-person",
            "absent",
        )
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["tags"], ["keep", "new"])
        self.assertEqual({p["handle"] for p in payload["people"]}, {"alice", "carol"})
        with closing(db.connect(self.db_path)) as conn:
            self.assertIsNone(db.get_person_by_handle(conn, "absent"))
            self.assertEqual({t.name for t in db.list_tags(conn)}, {"keep", "new"})

    def test_link_conflicts_fail_before_editor_or_writes(self):
        entry = self._seed_entry(tags=("keep",))
        conflicts = (
            ("--add-tag", "x", "--tag", "y"),
            ("--remove-tag", "x", "--clear-tags"),
            ("--add-tag", "X", "--remove-tag", " x "),
            ("--add-person", "alice", "--person", "bob"),
            ("--remove-person", "alice", "--clear-people"),
            ("--add-person", "ALICE", "--remove-person", "alice"),
        )
        for flags in conflicts:
            with self.subTest(flags=flags), mock.patch.object(entries, "read_body_arg") as read:
                rc, _, _ = self._run("entry", "edit", str(entry.id), "--body", *flags)
                self.assertEqual(rc, 1)
                read.assert_not_called()
                with closing(db.connect(self.db_path)) as conn:
                    self.assertEqual(db.get_entry(conn, entry.id), entry)

    def test_add_person_uses_existing_confirmation_flow(self):
        entry = self._seed_entry(tags=("keep",))
        rc, _, _ = self._run(
            "entry",
            "edit",
            str(entry.id),
            "--add-person",
            "new",
            "--remove-tag",
            "keep",
            stdin=io.StringIO(),
        )
        self.assertEqual(rc, 1)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_entry(conn, entry.id), entry)
        rc, out, err = self._run(
            "--yes", "--json", "entry", "edit", str(entry.id), "--add-person", "new"
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["people"][0]["handle"], "new")

    def test_show_with_feedback_is_full_ordered_and_opt_in(self):
        entry = self._seed_entry("The interaction")
        with closing(db.connect(self.db_path)) as conn:
            first = db.create_feedback(conn, entry.id, "First coaching\n" + "detail " * 100)
            second = db.create_feedback(conn, entry.id, "Second coaching")
        rc, out, err = self._run("entry", "show", str(entry.id), "--with-feedback")
        self.assertEqual(rc, 0, err)
        self.assertIn(first.body, out)
        self.assertLess(out.index(entry.body), out.index(first.body))
        self.assertLess(out.index(first.body), out.index(second.body))
        rc, out, _ = self._run("--json", "entry", "show", str(entry.id), "--with-feedback")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], entry.id)
        self.assertEqual([f["id"] for f in payload["feedback"]], [first.id, second.id])
        rc, out, _ = self._run("--json", "entry", "show", str(entry.id))
        self.assertNotIn("feedback", json.loads(out))

    def test_show_with_no_feedback_and_missing_entry(self):
        entry = self._seed_entry()
        rc, out, _ = self._run("entry", "show", str(entry.id), "--with-feedback")
        self.assertEqual(rc, 0)
        self.assertIn("(no feedback)", out)
        rc, out, _ = self._run("--json", "entry", "show", str(entry.id), "--with-feedback")
        self.assertEqual(json.loads(out)["feedback"], [])
        rc, _, err = self._run("entry", "show", "9999", "--with-feedback")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_oldest_backlog_applies_filters_and_limit_after_sorting(self):
        coached = self._seed_entry("coached", occurred_at="2025-01-01", tags=("work",))
        newest = self._seed_entry("newest", occurred_at="2026-03-01", tags=("work",))
        oldest = self._seed_entry("oldest", occurred_at="2026-01-01", tags=("work",))
        tied = self._seed_entry("tied", occurred_at="2026-01-01", tags=("work",))
        self._seed_entry("other tag", occurred_at="2025-01-01")
        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, coached.id, "done")
        for extra, expected in (
            (["--oldest-first"], [oldest.id, tied.id]),
            ([], [newest.id, tied.id]),
        ):
            rc, out, err = self._run(
                "--json",
                "entry",
                "search",
                "--no-feedback",
                "--tag",
                "work",
                "--limit",
                "2",
                *extra,
            )
            self.assertEqual(rc, 0, err)
            self.assertEqual([e["id"] for e in json.loads(out)], expected)

    def test_exact_search_treats_percent_literally(self):
        entry = self._seed_entry("10% improvement")
        self._seed_entry("unrelated")
        rc, out, err = self._run("--json", "entry", "search", "--exact", "--query", "%")
        self.assertEqual(rc, 0, err)
        self.assertEqual([e["id"] for e in json.loads(out)], [entry.id])

    def test_invalid_capture_input_fails_before_reading_body(self):
        cases = (
            (entries, ("entry", "add", "--occurred-at", "nonsense")),
            (entries, ("entry", "add", "--db", str(self.tmpdir / "missing.sqlite3"))),
            (feedback, ("feedback", "add", "9999")),
        )
        for module, args in cases:
            with self.subTest(args=args), mock.patch.object(module, "read_body_arg") as read:
                rc, _, _ = self._run(*args)
                self.assertEqual(rc, 1)
                read.assert_not_called()

    def test_goal_close_cli_keeps_previous_outcome(self):
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Lead with the ask")
            closed = db.close_goal(conn, goal.id, outcome="Worked three times")
        rc, _, err = self._run("goal", "close", str(goal.id))
        self.assertEqual(rc, 0, err)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_goal(conn, goal.id), closed)
