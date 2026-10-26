"""Finding stored advice through literal review body search."""

import json
from contextlib import closing

from commscoach import db

from .cli_review_case import ReviewTestBase


class ReviewSearchTests(ReviewTestBase):
    def test_footer_respects_scope_literal_query_and_deleted_person(self):
        person = self._seed_person("alice")
        entries = [self._seed_entry(people=("alice",)).id for _ in range(2)]
        for _ in range(3):
            self._seed_review(
                "Try 10%_! less", entries, scope_kind="person", scope_person_handle="alice"
            )
        self._seed_review(
            "Try 100xyz! less", entries, scope_kind="person", scope_person_handle="alice"
        )
        self._seed_review("Try 10%_! less", entries, scope_kind="tag", scope_tag="Meeting")
        self._seed_review("Try 10%_! less", entries)
        with closing(db.connect(self.db_path)) as conn:
            db.delete_person(conn, person.id)
        flags = ("review", "search", "--query", "10%_!", "--scope", "person", "--person", "ALICE")
        for limit in (0, 1, 3, 4):
            with self.subTest(limit=limit):
                rc, out, err = self._run(*flags, "--limit", str(limit))
                self.assertEqual(rc, 0, err)
                if limit < 3:
                    self.assertIn(f"Showing {limit} of 3 matching reviews.", err)
                else:
                    self.assertNotIn("Showing", out + err)
                rc, out, err = self._run(*flags, "--limit", str(limit), "--json")
                self.assertEqual(rc, 0, err)
                self.assertEqual(len(json.loads(out)), min(limit, 3))
        rc, out, err = self._run(
            "review",
            "search",
            "--query",
            "10%_!",
            "--scope",
            "tag",
            "--tag",
            "MEETING",
            "--limit",
            "0",
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("Showing 0 of 1 matching reviews.", err)
        self.assertEqual(self._run(*flags, "--tag", "meeting")[1], "(no reviews)\n")

    def test_search_shows_matching_excerpt_and_full_json(self):
        entry = self._seed_entry()
        body = "Introduction. " * 60 + "Lead with the ASK next time."
        review = self._seed_review(body, [entry.id])
        rc, out, err = self._run("review", "search", "--query", "lead with the ask")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"[{review.id}]", out)
        self.assertIn("Lead with the ASK", out)
        self.assertIn("scope:latest", out)
        self.assertLess(len(out), 400)
        rc, out, err = self._run("review", "search", "--query", "ASK", "--json")
        self.assertEqual(rc, 0, err)
        _, shown, _ = self._run("review", "show", str(review.id), "--json")
        self.assertEqual(json.loads(out), [json.loads(shown)])

    def test_literal_matching_filters_before_limit_and_orders_by_id_on_ties(self):
        entry = self._seed_entry()
        first = self._seed_review("Ask for 10%_!", [entry.id])
        second = self._seed_review("ASK for 10%_!", [entry.id])
        self._seed_review("Ask for 100xyz!", [entry.id])
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE reviews SET created_at = '2026-01-01T00:00:00+00:00'")
        rc, out, err = self._run("review", "search", "--query", "10%_!", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in json.loads(out)], [second.id, first.id])
        rc, out, err = self._run("review", "search", "--query", "10%_!", "--limit", "1", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in json.loads(out)], [second.id])

    def test_scope_filters_combine_and_deleted_person_remains_searchable(self):
        person = self._seed_person("alice")
        entry = self._seed_entry(people=("alice",))
        self._seed_review("Ask clearly", [entry.id])
        scoped = self._seed_review(
            "Ask clearly",
            [entry.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        tagged = self._seed_review(
            "Ask clearly",
            [entry.id],
            scope_kind="tag",
            scope_tag="Conflict",
        )
        with closing(db.connect(self.db_path)) as conn:
            db.delete_person(conn, person.id)
        for flags, expected in (
            (("--person", "ALICE", "--scope", "person"), [scoped.id]),
            (("--tag", "CONFLICT", "--scope", "tag"), [tagged.id]),
            (("--person", "alice", "--tag", "conflict"), []),
            (("--person", "unknown"), []),
        ):
            with self.subTest(flags=flags):
                rc, out, err = self._run("review", "search", "--query", "ask", *flags, "--json")
                self.assertEqual(rc, 0, err)
                self.assertEqual([r["id"] for r in json.loads(out)], expected)

    def test_empty_results_zero_limit_and_invalid_queries(self):
        entry = self._seed_entry()
        self._seed_review("Ask clearly", [entry.id])
        rc, out, err = self._run("review", "search", "--query", "missing")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no reviews)", out)
        rc, out, err = self._run("review", "search", "--query", "ask", "--limit", "0", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), [])
        for query in ("", "   "):
            rc, _, err = self._run("review", "search", "--query", query)
            self.assertEqual(rc, 1)
            self.assertIn("non-empty", err)
        for flags in ((), ("--query", "ask", "--limit", "-1")):
            with self.assertRaises(SystemExit) as raised:
                self._run("review", "search", *flags)
            self.assertEqual(raised.exception.code, 2)

    def test_default_limit_is_twenty(self):
        entry = self._seed_entry()
        for _ in range(21):
            self._seed_review("Ask clearly", [entry.id])
        rc, out, err = self._run("review", "search", "--query", "ask", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(json.loads(out)), 20)
