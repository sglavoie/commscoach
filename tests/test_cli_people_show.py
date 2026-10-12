"""Tests for ``commscoach person show/list`` and help output."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr

from commscoach import cli, db

from .cli_people_case import PeopleTestBase


class PersonShowTests(PeopleTestBase):
    def test_reviews_are_person_scoped_full_newest_first_and_optional(self):
        self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "Alice interaction", people=("alice",))
            older = db.create_review(conn, "Older advice", (entry.id,),
                                     scope_kind="person", scope_person_handle="alice")
            newest = db.create_review(conn, "Latest advice. " * 40, (entry.id,),
                                      scope_kind="person", scope_person_handle="alice")
            other = db.create_entry(conn, "Bob interaction", people=("bob",))
            db.create_review(conn, "Unrelated person advice", (other.id,),
                             scope_kind="person", scope_person_handle="bob")
            db.create_review(conn, "General review mentioning Alice", (entry.id,))
            # Equal creation times exercise the stable ID tie-breaker.
            with conn:
                conn.execute("UPDATE reviews SET created_at = '2026-01-01T00:00:00+00:00'")
        for extra in ((), ("--recent", "1", "--with-feedback")):
            with self.subTest(extra=extra):
                flags = ("person", "show", "ALICE", "--reviews", "2", *extra)
                rc, out, err = self._run(*flags)
                self.assertEqual(rc, 0, err)
                self.assertIn(newest.body, out)
                self.assertLess(out.index(newest.body), out.index(older.body))
                self.assertNotIn("Unrelated person advice", out)
                self.assertNotIn("General review", out)
                rc, out, err = self._run(*flags, "--json")
                self.assertEqual(rc, 0, err)
                payload = json.loads(out)
                self.assertEqual([r["id"] for r in payload["recent_reviews"]], [newest.id, older.id])
                self.assertEqual(payload["recent_reviews"][0]["body"], newest.body)
                self.assertEqual("recent_entries" in payload, bool(extra))
                rc, out, err = self._run("person", "show", "alice", *extra, "--json")
                self.assertEqual(rc, 0, err)
                del payload["recent_reviews"]
                self.assertEqual(payload, json.loads(out))
        rc, out, err = self._run("person", "show", "alice", "--reviews", "1", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in json.loads(out)["recent_reviews"]], [newest.id])
        self._run("person", "edit", "alice", "--handle", "ally")
        rc, out, err = self._run("person", "show", "ally", "--reviews", "1", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["recent_reviews"][0]["scope_person_handle"], "ally")

    def test_reviews_zero_empty_and_invalid_limits(self):
        self._seed_person("alice")
        for limit in ("0", "2"):
            rc, out, err = self._run("person", "show", "alice", "--reviews", limit)
            self.assertEqual(rc, 0, err)
            self.assertIn("Recent reviews:\n(no reviews)", out)
            rc, out, err = self._run("person", "show", "alice", "--reviews", limit, "--json")
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["recent_reviews"], [])
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "Alice interaction", people=("alice",))
            db.create_review(conn, "Existing advice", (entry.id,),
                             scope_kind="person", scope_person_handle="alice")
        rc, out, err = self._run("person", "show", "alice", "--reviews", "0", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["recent_reviews"], [])
        for limit in ("-1", "bad"):
            with self.subTest(limit=limit), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(["--db", str(self.db_path), "person", "show", "alice", "--reviews", limit])
            self.assertEqual(raised.exception.code, 2)
        rc, out, err = self._run("person", "show", "missing", "--reviews", "1")
        self.assertEqual(rc, 1)
        self.assertIn("missing", err)

    def test_recent_feedback_is_full_oldest_first_and_only_for_selected_entries(self):
        self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            older = db.create_entry(conn, "Older", people=("alice",), occurred_at="2026-01-01")
            coached = db.create_entry(conn, "Coached", people=("alice",), occurred_at="2026-02-01")
            bare = db.create_entry(conn, "Uncoached", people=("alice",), occurred_at="2026-03-01")
            other = db.create_entry(conn, "Other", people=("bob",), occurred_at="2026-04-01")
            db.create_feedback(conn, older.id, "Excluded old advice")
            db.create_feedback(conn, other.id, "Excluded unrelated advice")
            first = db.create_feedback(conn, coached.id, "First advice\n" + "Full coaching text. " * 30)
            second = db.create_feedback(conn, coached.id, "Second advice")
        flags = ("person", "show", "ALICE", "--recent", "2", "--with-feedback")
        rc, out, err = self._run(*flags)
        self.assertEqual(rc, 0, err)
        self.assertIn(first.body, out)
        self.assertLess(out.index("First advice"), out.index("Second advice"))
        self.assertIn("(no feedback)", out)
        self.assertNotIn("Excluded", out)
        rc, out, err = self._run(*flags, "--json")
        self.assertEqual(rc, 0, err)
        entries = json.loads(out)["recent_entries"]
        self.assertEqual([e["id"] for e in entries], [bare.id, coached.id])
        self.assertEqual(entries[0]["feedback"], [])
        self.assertEqual([f["id"] for f in entries[1]["feedback"]], [first.id, second.id])
        self.assertEqual(entries[1]["feedback"][0]["body"], first.body)
        rc, out, err = self._run("person", "show", "alice", "--recent", "2", "--json")
        self.assertEqual(rc, 0, err)
        self.assertTrue(all("feedback" not in e for e in json.loads(out)["recent_entries"]))

    def test_feedback_option_requires_recent_and_handles_empty_selection(self):
        self._seed_person("alice")
        rc, _, err = self._run("person", "show", "alice", "--with-feedback")
        self.assertEqual(rc, 1)
        self.assertIn("requires --recent", err)
        for limit in ("0", "5"):
            rc, out, err = self._run("person", "show", "alice", "--recent", limit, "--with-feedback")
            self.assertEqual(rc, 0, err)
            self.assertIn("(no entries)", out)
            rc, out, err = self._run("person", "show", "alice", "--recent", limit, "--with-feedback", "--json")
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["recent_entries"], [])

    def test_recent_view_limits_interactions_and_includes_only_linked_active_goals(self):
        alice = self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, alice.id, "Prefers short updates")
            newest = db.create_entry(conn, "latest conversation", people=("alice",), occurred_at="2026-02-01")
            db.create_entry(conn, "older conversation", people=("alice",), occurred_at="2026-01-01")
            db.create_entry(conn, "unrelated", people=("bob",), occurred_at="2026-03-01")
            db.create_feedback(conn, newest.id, "Good listening")
            goal = db.create_goal(conn, "Ask a question", person_handle="alice")
            closed = db.create_goal(conn, "Completed", person_handle="alice")
            db.close_goal(conn, closed.id)
            db.create_goal(conn, "Unrelated", person_handle="bob")
            db.create_goal(conn, "General goal")
        rc, out, err = self._run("person", "show", "ALICE", "--recent", "1", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["notes"], "Prefers short updates")
        self.assertEqual([e["id"] for e in payload["recent_entries"]], [newest.id])
        self.assertEqual([g["id"] for g in payload["active_goals"]], [goal.id])
        rc, out, err = self._run("person", "show", "alice", "--recent", "1")
        self.assertEqual(rc, 0, err)
        for text in ("Prefers short updates", "Recent interactions:", "latest conversation",
                     "has feedback", "Active goals:", "Ask a question"):
            self.assertIn(text, out)
        self.assertNotIn("older conversation", out)
        rc, out, err = self._run("person", "show", "alice", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(json.loads(out)), {"id", "handle", "name", "is_self", "notes"})
        rc, out, err = self._run("person", "show", "alice", "--recent", "0", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["recent_entries"], [])
        self.assertEqual([g["id"] for g in json.loads(out)["active_goals"]], [goal.id])

    def test_recent_view_empty_missing_and_negative_limit(self):
        self._seed_person("alice")
        rc, out, err = self._run("person", "show", "alice", "--recent", "5")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no entries)", out)
        self.assertIn("(no goals)", out)
        rc, _, err = self._run("person", "show", "ghost", "--recent", "5")
        self.assertEqual(rc, 1, err)
        self.assertIn("ghost", err)
        with self.assertRaises(SystemExit) as raised:
            self._run("person", "show", "alice", "--recent", "-1")
        self.assertEqual(raised.exception.code, 2)

    def test_show_existing_human(self) -> None:
        person = self._seed_person("alice", name="Alice Smith")
        rc, out, _ = self._run("person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertIn(f"[{person.id}]", out)
        self.assertIn("@alice", out)
        self.assertIn("Alice Smith", out)

    def test_show_case_insensitive_lookup(self) -> None:
        self._seed_person("alice")
        rc, out, _ = self._run("person", "show", "ALICE")
        self.assertEqual(rc, 0)
        self.assertIn("@alice", out)

    def test_show_missing_exits_one(self) -> None:
        rc, _, err = self._run("person", "show", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_show_json(self) -> None:
        person = self._seed_person("alice", name="Alice", is_self=True)
        rc, out, _ = self._run("--json", "person", "show", "alice")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], person.id)
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["name"], "Alice")
        self.assertTrue(payload["is_self"])
        self.assertIsNone(payload["notes"])

    def test_show_renders_notes_human_and_json(self) -> None:
        person = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "Prefers async updates\nAsk why.")

        rc, out, _ = self._run("person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("Notes:", out)
        self.assertIn("Prefers async updates\nAsk why.", out)

        rc, out, _ = self._run("--json", "person", "show", "alice")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["notes"], "Prefers async updates\nAsk why.")


class PersonListTests(PeopleTestBase):
    def test_list_empty(self) -> None:
        rc, out, _ = self._run("person", "list")
        self.assertEqual(rc, 0)
        self.assertIn("(no people)", out)

    def test_list_self_first_then_alphabetical(self) -> None:
        self._seed_person("zoe")
        self._seed_person("alice")
        me = self._seed_person("me", is_self=True)
        rc, out, _ = self._run("person", "list")
        self.assertEqual(rc, 0)
        idx_me = out.find(f"[{me.id}]")
        idx_alice = out.find("@alice")
        idx_zoe = out.find("@zoe")
        self.assertGreaterEqual(idx_me, 0)
        self.assertGreater(idx_alice, idx_me)
        self.assertGreater(idx_zoe, idx_alice)

    def test_list_json_empty(self) -> None:
        rc, out, _ = self._run("--json", "person", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_list_json_payload(self) -> None:
        self._seed_person("alice")
        me = self._seed_person("me", is_self=True)
        rc, out, _ = self._run("--json", "person", "list")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 2)
        # Self should be first per db.list_people contract.
        self.assertEqual(payload[0]["id"], me.id)
        self.assertTrue(payload[0]["is_self"])
        self.assertEqual(payload[1]["handle"], "alice")


class PersonHelpTests(PeopleTestBase):
    def test_person_without_subcommand_prints_help(self) -> None:
        rc, out, _ = self._run("person")
        self.assertEqual(rc, 0)
        self.assertIn("add", out)
        self.assertIn("show", out)
        self.assertIn("edit", out)
        self.assertIn("list", out)
        self.assertIn("rm", out)
        self.assertIn("self", out)


if __name__ == "__main__":
    unittest.main()
