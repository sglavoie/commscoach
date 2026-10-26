"""Tests for ``commscoach entry list`` and ``commscoach entry search``."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr
from datetime import UTC, datetime, timedelta

from commscoach import cli, db
from commscoach.cli.common import format_stamp

from .cli_entry_case import EntryTestBase


class EntryListTests(EntryTestBase):
    def test_literal_search_shows_late_heading_match_and_preserves_json(self):
        body = (
            "Opening paragraph.\n"
            + "Background. " * 40
            + "\n## A 10%_ improvement\n"
            + "Afterwards. " * 30
        )
        entry = self._seed_entry(body, source="email", tags=("work",))
        for flags in (("--exact",), ()):
            with self.subTest(flags=flags):
                rc, out, err = self._run("entry", "search", "--query", "10%_", *flags)
                self.assertEqual(rc, 0, err)
                self.assertIn("## A 10%_ improvement", out)
                self.assertIn("…", out)
                self.assertIn("email · needs feedback", out)
                self.assertIn("#work", out)
                self.assertNotIn("Opening paragraph", out)
        rc, out, err = self._run("entry", "search", "--exact", "--query", "10%_", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)[0]["body"], body)
        rc, shown, err = self._run("entry", "show", str(entry.id), "--json")
        self.assertEqual(rc, 0, err)
        # Rows add the coaching status that list and search text output shows.
        self.assertEqual(json.loads(out), [{**json.loads(shown), "has_feedback": False}])

    def test_token_search_excerpts_follow_fts_matching(self):
        with closing(db.connect(self.db_path)) as conn:
            if conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()[0] != "1":
                self.skipTest("FTS5 unavailable")
        self._seed_entry(
            "Opening words. " * 60 + "\n## Café listening carefully\n" + "Afterwards. " * 60
        )
        for query in (
            '"listening carefully"',
            "listen*",
            "cafe",
            "absent OR listening",
            "listening NOT absent",
            "NEAR(cafe carefully)",
        ):
            with self.subTest(query=query):
                rc, out, err = self._run("entry", "search", "--query", query)
                self.assertEqual(rc, 0, err)
                self.assertIn("Café listening carefully", out)
                self.assertIn("…", out)
                self.assertEqual(len(out.splitlines()), 1)

    def test_search_without_fts_uses_literal_excerpt_and_no_query_keeps_preview(self):
        self._seed_entry("Opening words.\n" + "Background. " * 40 + "A late target passage.")
        with closing(db.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE _meta SET value = '0' WHERE key = 'fts5'")
        rc, out, err = self._run("entry", "search", "--query", "TARGET")
        self.assertEqual(rc, 0, err)
        self.assertIn("late target passage", out)
        self.assertNotIn("Opening words", out)
        for command in ("list", "search"):
            rc, out, err = self._run("entry", command)
            self.assertEqual(rc, 0, err)
            self.assertIn("Opening words", out)
            self.assertNotIn("late target passage", out)

    def test_list_and_search_show_date_source_and_live_feedback_status(self):
        entry = self._seed_entry(
            "## Situation\nA useful conversation",
            source="in-person",
            occurred_at="2026-06-01T18:30:00+00:00",
        )
        fallback = self._seed_entry("No occurrence time")
        with closing(db.connect(self.db_path)) as conn:
            feedback = db.create_feedback(conn, entry.id, "Useful coaching")
        for command in ("list", "search"):
            rc, out, err = self._run("entry", command)
            self.assertEqual(rc, 0, err)
            rows = out.splitlines()
            line = next(row for row in rows if row.startswith(f"[{entry.id}]"))
            self.assertIn(format_stamp(entry.occurred_at), line)
            self.assertIn("in-person · has feedback · A useful conversation", line)
            line = next(row for row in rows if row.startswith(f"[{fallback.id}]"))
            self.assertIn(format_stamp(fallback.created_at), line)
            self.assertIn("needs feedback", line)
            rc, out, err = self._run("entry", command, "--json")
            self.assertEqual(rc, 0, err)
            self.assertEqual(
                {row["id"]: row["has_feedback"] for row in json.loads(out)},
                {entry.id: True, fallback.id: False},
            )
        with closing(db.connect(self.db_path)) as conn:
            db.delete_feedback(conn, feedback.id)
        rc, out, err = self._run("entry", "search", "--query", "useful")
        self.assertEqual(rc, 0, err)
        self.assertIn("needs feedback", out)
        self.assertNotIn("has feedback", out)

    def test_rows_prefix_every_tag_and_person(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        self._seed_entry("body", tags=("ops", "work"), people=("alice", "bob"))
        rc, out, _ = self._run("entry", "list")
        self.assertEqual(rc, 0)
        self.assertIn("· #ops, #work · @alice, @bob", out)

    def test_list_empty(self) -> None:
        rc, out, _ = self._run("entry", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no entries)", out)

    def test_list_newest_first(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second")
        third = self._seed_entry("third")
        rc, out, _ = self._run("entry", "list")
        self.assertEqual(rc, 0)
        idx_third = out.find(f"[{third.id}]")
        idx_second = out.find(f"[{second.id}]")
        idx_first = out.find(f"[{first.id}]")
        self.assertGreater(idx_second, idx_third)
        self.assertGreater(idx_first, idx_second)

    def test_list_limit(self) -> None:
        for i in range(5):
            self._seed_entry(f"entry {i}")
        rc, out, err = self._run("entry", "list", "--limit", "2")
        self.assertEqual(rc, 0)
        # Two entries on stdout; the omitted-entries footer goes to stderr.
        non_empty = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(non_empty), 2)
        self.assertEqual(err.strip(), "Showing 2 of 5 entries.")
        rc, out, err = self._run("entry", "list", "--limit", "5")
        self.assertNotIn("Showing", out + err)

    def test_list_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "entry",
                        "list",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)

    def test_list_json_empty(self) -> None:
        rc, out, _ = self._run("--json", "entry", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_json_payload(self) -> None:
        first = self._seed_entry("first")
        second = self._seed_entry("second", tags=("hello",))
        rc, out, _ = self._run("--json", "entry", "list")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual([p["id"] for p in payload], [second.id, first.id])
        self.assertEqual(payload[0]["tags"], ["hello"])

    def test_list_filters_by_inclusive_date_bounds(self) -> None:
        included = self._seed_entry("included", occurred_at="2026-06-02T23:59:59")
        self._seed_entry("too early", occurred_at="2026-05-31T23:59:59")
        self._seed_entry("too late", occurred_at="2026-06-03T00:00:00")

        rc, out, _ = self._run("entry", "list", "--since", "2026-06-01", "--until", "2026-06-02")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{included.id}]", out)
        self.assertNotIn("too early", out)
        self.assertNotIn("too late", out)

    def test_list_bad_since_exits_one_and_names_flag(self) -> None:
        rc, _, err = self._run("entry", "list", "--since", "not-a-date")
        self.assertEqual(rc, 1)
        self.assertIn("--since", err)

    def test_list_rejects_reversed_date_range(self) -> None:
        rc, _, err = self._run("entry", "list", "--since", "2026-06-03", "--until", "2026-06-02")
        self.assertEqual(rc, 1)
        self.assertIn("--since must not be after --until", err)

    def test_list_accepts_single_date_bound(self) -> None:
        rc, _, err = self._run("entry", "list", "--since", "2026-06-03")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")


class EntrySearchTests(EntryTestBase):
    def test_search_rejects_reversed_date_range(self) -> None:
        rc, _, err = self._run("entry", "search", "--since", "2026-06-03", "--until", "2026-06-02")
        self.assertEqual(rc, 1)
        self.assertIn("--since must not be after --until", err)

    def test_search_by_query_substring_case_insensitive(self) -> None:
        match = self._seed_entry("Quarterly review meeting")
        self._seed_entry("Daily standup")
        rc, out, _ = self._run("entry", "search", "--query", "quarterly")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("standup", out.lower())

    def test_search_exact_finds_midword_substring(self) -> None:
        match = self._seed_entry("Quarterly review meeting")

        rc, out, _ = self._run("entry", "search", "--query", "arter", "--exact")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)

    def test_search_by_tag(self) -> None:
        match = self._seed_entry("body", tags=("review",))
        self._seed_entry("body", tags=("standup",))
        rc, out, _ = self._run("entry", "search", "--tag", "review")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)

    def test_search_by_person_handle_case_insensitive(self) -> None:
        self._create_person("alice")
        self._create_person("bob")
        match = self._seed_entry("with alice", people=("alice",))
        self._seed_entry("with bob", people=("bob",))
        rc, out, _ = self._run("entry", "search", "--person", "ALICE")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("with bob", out)

    def test_search_combined_filters(self) -> None:
        self._create_person("alice")
        match = self._seed_entry("kickoff sync", tags=("kickoff",), people=("alice",))
        self._seed_entry("kickoff solo", tags=("kickoff",))
        rc, out, _ = self._run(
            "entry",
            "search",
            "--query",
            "kickoff",
            "--tag",
            "kickoff",
            "--person",
            "alice",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("kickoff solo", out)

    def test_search_no_match_prints_empty_marker(self) -> None:
        self._seed_entry("body")
        rc, out, _ = self._run("entry", "search", "--query", "absent")
        self.assertEqual(rc, 0)
        self.assertIn("(no entries)", out)

    def test_search_json_returns_list(self) -> None:
        match = self._seed_entry("hello world")
        self._seed_entry("nothing")
        rc, out, _ = self._run("--json", "entry", "search", "--query", "hello")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], match.id)

    def test_search_limit_caps_results(self) -> None:
        for _ in range(4):
            self._seed_entry("hit")
        rc, out, _ = self._run("--json", "entry", "search", "--query", "hit", "--limit", "2")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 2)

    def test_search_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(
                    [
                        "--db",
                        str(self.db_path),
                        "entry",
                        "search",
                        "--limit",
                        "-1",
                    ]
                )
        self.assertEqual(ctx.exception.code, 2)

    def test_search_date_bounds_combine_with_existing_filters(self) -> None:
        self._create_person("alice")
        match = self._seed_entry(
            "match",
            occurred_at="2026-06-10T12:00:00",
            tags=("work",),
            people=("alice",),
        )
        self._seed_entry(
            "wrong date",
            occurred_at="2026-06-09T23:59:59",
            tags=("work",),
            people=("alice",),
        )

        rc, out, _ = self._run(
            "entry",
            "search",
            "--tag",
            "work",
            "--person",
            "alice",
            "--since",
            "2026-06-10",
            "--until",
            "2026-06-10",
        )

        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)
        self.assertNotIn("wrong date", out)

    def test_search_no_feedback_returns_coaching_backlog(self) -> None:
        backlog = self._seed_entry("needs coaching")
        reviewed = self._seed_entry("already reviewed")

        with closing(db.connect(self.db_path)) as conn:
            db.create_feedback(conn, reviewed.id, "done")

        rc, out, _ = self._run("entry", "search", "--no-feedback")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{backlog.id}]", out)
        self.assertNotIn("already reviewed", out)

    def test_search_accepts_relative_date_bound(self) -> None:
        yesterday = datetime.now().astimezone() - timedelta(days=1)
        match = self._seed_entry(
            "yesterday entry",
            occurred_at=yesterday.astimezone(UTC).isoformat(),
        )

        rc, out, _ = self._run("entry", "search", "--since", "yesterday")

        self.assertEqual(rc, 0)
        self.assertIn(f"[{match.id}]", out)

    def test_search_bad_until_exits_one_and_names_flag(self) -> None:
        rc, _, err = self._run("entry", "search", "--until", "not-a-date")
        self.assertEqual(rc, 1)
        self.assertIn("--until", err)


if __name__ == "__main__":
    unittest.main()
