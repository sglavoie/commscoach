"""Tests for ``commscoach person add/prep/self``."""

from __future__ import annotations

import json
import shlex
from contextlib import closing
from unittest import mock

from commscoach import db

from .cli_people_case import PeopleTestBase


class PersonAddTests(PeopleTestBase):
    def test_activity_counts_and_dates_with_query_and_empty_people(self) -> None:
        self._seed_person("me", is_self=True)
        self._seed_person("alice", name="Alice Smith")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.create_entry(conn, "Earlier", people=["alice", "bob"],
                            occurred_at="2026-01-01T00:00:00+00:00")
            with mock.patch("commscoach.db.common.utcnow_iso", return_value="2026-02-01T00:00:00+00:00"):
                db.create_entry(conn, "Latest", people=["alice"])
            with mock.patch("commscoach.db.common.utcnow_iso", return_value="2026-03-01T00:00:00+00:00"):
                db.create_entry(conn, "Backfilled", people=["alice"],
                                occurred_at="2025-01-01T00:00:00+00:00")
            for _ in range(2):
                db.create_goal(conn, "Active", person_handle="alice")
            for dropped in (False, True):
                goal = db.create_goal(conn, "Closed", person_handle="alice")
                db.close_goal(conn, goal.id, dropped=dropped)
            db.create_goal(conn, "General")
        rc, out, err = self._run("person", "list", "--with-activity", "--json")
        self.assertEqual(rc, 0, err)
        rows = json.loads(out)
        self.assertEqual([row["handle"] for row in rows], ["me", "alice", "bob"])
        self.assertEqual(rows[0]["activity"], {
            "last_logged_interaction": None, "interaction_count": 0, "active_goal_count": 0,
        })
        self.assertEqual(rows[1]["activity"], {
            "last_logged_interaction": "2026-02-01T00:00:00+00:00",
            "interaction_count": 3, "active_goal_count": 2,
        })
        self.assertEqual(rows[2]["activity"]["interaction_count"], 1)
        rc, out, err = self._run("person", "list", "--query", "SMITH", "--with-activity")
        self.assertEqual(rc, 0, err)
        self.assertIn("last logged interaction:", out)
        self.assertIn("interactions: 3 · active goals: 2", out)
        self.assertNotIn("@bob", out)
        self.assertNotIn("activity", json.loads(self._run("person", "list", "--json")[1])[0])
        self.assertEqual(self._run("person", "list", "--query", "missing", "--with-activity")[1], "(no people)\n")

    def test_list_query_matches_names_handles_and_literal_characters(self) -> None:
        self._seed_person("jdoe", name="John Doe")
        self._seed_person("john_self", is_self=True)
        self._seed_person("john_other")
        self._seed_person("literal_%!", name=None)
        rc, out, err = self._run("person", "list", "--query", "JOHN", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([p["handle"] for p in json.loads(out)], ["john_self", "jdoe", "john_other"])
        self.assertIn("@literal_%!", self._run("person", "list", "--query", "_%!")[1])
        self.assertEqual(self._run("person", "list", "--query", "missing")[1], "(no people)\n")
        self.assertEqual(self._run("person", "list", "--query", " ")[0], 1)

    def test_brief_prep_previews_notes_goals_and_preserves_full_json(self) -> None:
        person = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "Complete private notes")
            db.create_goal(conn, "Experiment", detail="Full experiment detail", person_handle="alice")
            db.create_goal(conn, "General experiment", detail="General detail")
            entry = db.create_entry(conn, "Conversation", people=["alice"])
            feedback = db.create_feedback(conn, entry.id, "# Heading\n\nBrief advice " + "x" * 300 + " hidden ending")
            review = db.create_review(conn, "Brief review " + "y" * 300 + " hidden review ending", [entry.id], scope_kind="person", scope_person_handle="alice")
        rc, out, err = self._run("person", "prep", "alice", "--brief", "--include-general-goals")
        self.assertEqual(rc, 0, err)
        for text in ("Complete private notes", "Full experiment detail", "General detail",
                     f"Feedback [{feedback.id}]", f"Review [{review.id}]", "Brief advice", "Brief review"):
            self.assertIn(text, out)
        self.assertNotIn("hidden ending", out)
        self.assertNotIn("hidden review ending", out)
        self.assertIn(f"--db {self.db_path}", out)
        self.assertIn(f"entry show {entry.id} --with-feedback", out)
        self.assertIn(f"review show {review.id}", out)
        self.assertEqual(
            self._run("person", "prep", "alice", "--json"),
            self._run("person", "prep", "alice", "--brief", "--json"),
        )

    def test_brief_bounds_long_notes_goals_and_feedback_with_working_full_commands(self) -> None:
        # Exercise shell quoting in every recovery command.
        self.db_path = self.tmpdir / "journal's notes.sqlite3"
        db.init_db(self.db_path)
        person = self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, person.id, "# Notes\n\n" + "Context " * 60 + "NOTES_END")
            for index in range(7):
                db.create_goal(
                    conn, f"Experiment {index}", detail="Practice " * 60 + "DETAIL_END",
                    person_handle="alice" if index % 2 else None,
                    now=f"2026-10-0{index + 1}T00:00:00+00:00",
                )
            entry = db.create_entry(conn, "Conversation", people=["alice"])
            db.create_feedback(conn, entry.id, "Older advice")
            latest = db.create_feedback(conn, entry.id, "Latest advice")
        flags = ("--include-general-goals",)
        rc, out, err = self._run("person", "prep", "alice", "--brief", *flags)
        self.assertEqual(rc, 0, err)
        for hidden in ("NOTES_END", "DETAIL_END", "Older advice", "Experiment 0", "Experiment 1"):
            self.assertNotIn(hidden, out)
        self.assertIn("Showing 5 of 7 active goals", out)
        self.assertIn("Showing latest of 2 feedback notes", out)
        self.assertIn(f"Feedback [{latest.id}]: Latest advice", out)
        self.assertIn("(general)", out)
        self.assertEqual(
            self._run("person", "prep", "alice", *flags, "--json"),
            self._run("person", "prep", "alice", *flags, "--brief", "--json"),
        )
        full = self._run("person", "prep", "alice", *flags)[1]
        for text in ("NOTES_END", "DETAIL_END", "Older advice", "Experiment 0"):
            self.assertIn(text, full)
        recovered = []
        for line in out.splitlines():
            if line.strip().startswith(("Full notes:", "Full interaction:", "Full goal:", "All goals:")):
                argv = shlex.split(line.split(": ", 1)[1])
                self.assertEqual(argv[:3], ["commscoach", "--db", str(self.db_path)])
                rc, detail, err = self._run(*argv[3:])
                self.assertEqual(rc, 0, err)
                recovered.append(detail)
        self.assertIn("NOTES_END", "\n".join(recovered))
        self.assertIn("DETAIL_END", "\n".join(recovered))
        self.assertIn("Experiment 0", "\n".join(recovered))

    def test_brief_prep_opens_coached_and_uncoached_interactions(self) -> None:
        self.db_path = self.tmpdir / "journal's notes.sqlite3"
        db.init_db(self.db_path)
        self._seed_person("alice")
        with closing(db.connect(self.db_path)) as conn:
            uncoached = db.create_entry(conn, "Uncoached full text", people=["alice"])
            coached = db.create_entry(conn, "Coached full text", people=["alice"])
            db.create_feedback(conn, coached.id, "Advice")
        rc, out, err = self._run("person", "prep", "alice", "--brief")
        self.assertEqual(rc, 0, err)
        commands = [shlex.split(line.split(": ", 1)[1]) for line in out.splitlines()
                    if line.strip().startswith("Full interaction:")]
        self.assertEqual(len(commands), 2)
        self.assertEqual({argv[-2] for argv in commands}, {str(uncoached.id), str(coached.id)})
        for argv in commands:
            self.assertEqual(argv[:3], ["commscoach", "--db", str(self.db_path)])
            self.assertEqual(argv[-1], "--with-feedback")
            rc, detail, err = self._run(*argv[3:])
            self.assertEqual(rc, 0, err)
            self.assertIn("full text", detail)
        self.assertEqual(
            self._run("person", "prep", "alice", "--brief", "--json"),
            self._run("person", "prep", "alice", "--json"),
        )

    def test_brief_prep_empty_sections_and_zero_limits(self) -> None:
        self._seed_person("alice")
        rc, out, err = self._run("person", "prep", "alice", "--brief", "--recent", "0", "--reviews", "0")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no reviews)", out)
        self.assertIn("(no goals)", out)
        with closing(db.connect(self.db_path)) as conn:
            db.create_entry(conn, "Uncoached", people=["alice"])
        self.assertIn("(no feedback)", self._run("person", "prep", "alice", "--brief")[1])

    def test_add_minimal_creates_person(self) -> None:
        rc, out, _ = self._run("person", "add", "alice")
        self.assertEqual(rc, 0)
        self.assertIn("created", out)
        self.assertIn("@alice", out)
        with closing(db.connect(self.db_path)) as conn:
            person = db.get_person_by_handle(conn, "alice")
        assert person is not None
        self.assertEqual(person.handle, "alice")
        self.assertIsNone(person.name)
        self.assertFalse(person.is_self)

    def test_add_with_name_and_self(self) -> None:
        rc, out, _ = self._run(
            "person", "add", "me", "--name", "Sebastien", "--self"
        )
        self.assertEqual(rc, 0)
        self.assertIn("(self)", out)
        self.assertIn("Sebastien", out)
        with closing(db.connect(self.db_path)) as conn:
            person = db.get_person_by_handle(conn, "me")
        assert person is not None
        self.assertTrue(person.is_self)
        self.assertEqual(person.name, "Sebastien")

    def test_add_duplicate_handle_exits_one(self) -> None:
        self._seed_person("alice")
        rc, _, err = self._run("person", "add", "ALICE")
        self.assertEqual(rc, 1)
        self.assertIn("alice", err.lower())
        with closing(db.connect(self.db_path)) as conn:
            people = db.list_people(conn)
        self.assertEqual(len(people), 1)

    def test_add_second_self_exits_one(self) -> None:
        self._seed_person("me", is_self=True)
        rc, _, err = self._run("person", "add", "you", "--self")
        self.assertEqual(rc, 1)
        self.assertIn("self", err)
        with closing(db.connect(self.db_path)) as conn:
            people = db.list_people(conn)
        # only the original self exists
        self.assertEqual(len(people), 1)

    def test_add_empty_handle_exits_one(self) -> None:
        rc, _, err = self._run("person", "add", "   ")
        self.assertEqual(rc, 1)
        self.assertIn("non-empty", err)

    def test_add_json_output(self) -> None:
        rc, out, _ = self._run(
            "--json", "person", "add", "alice", "--name", "Alice"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["name"], "Alice")
        self.assertFalse(payload["is_self"])
        self.assertIsInstance(payload["id"], int)


class PersonPrepTests(PeopleTestBase):
    def test_prep_shows_details_and_optionally_includes_only_active_general_goals(self):
        self._seed_person("alice")
        self._seed_person("bob")
        detail = "Practice in three meetings.\nStart with the decision needed."
        with closing(db.connect(self.db_path)) as conn:
            linked = db.create_goal(conn, "Be direct", person_handle="alice", detail=detail)
            general = db.create_goal(conn, "Pause first", detail="Take one breath.")
            db.create_goal(conn, "Bob only", person_handle="bob")
            closed = db.create_goal(conn, "Completed general")
            db.close_goal(conn, closed.id)
            dropped = db.create_goal(conn, "Dropped general")
            db.close_goal(conn, dropped.id, dropped=True)
        rc, out, err = self._run("person", "prep", "alice")
        self.assertEqual(rc, 0, err)
        self.assertIn("  Practice in three meetings.\n  Start with the decision needed.", out)
        self.assertNotIn("Pause first", out)
        rc, out, err = self._run("person", "show", "alice", "--recent", "0")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Practice in three meetings.", out)
        args = ("person", "prep", "ALICE", "--include-general-goals", "--recent", "0", "--reviews", "0")
        rc, out, err = self._run(*args)
        self.assertEqual(rc, 0, err)
        self.assertIn("Pause first", out)
        self.assertIn("(general)", out)
        self.assertIn("  Take one breath.", out)
        for excluded in ("Bob only", "Completed general", "Dropped general"):
            self.assertNotIn(excluded, out)
        rc, out, err = self._run(*args, "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual([g["id"] for g in payload["active_goals"]], [general.id, linked.id])
        self.assertEqual(payload["active_goals"][1]["detail"], detail)
        self.assertEqual(payload["recent_entries"], [])

    def test_prep_defaults_match_full_show_with_goals_first(self):
        alice = self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, alice.id, "Prefers short updates")
            for day in range(1, 7):
                entry = db.create_entry(
                    conn, f"Conversation {day}", people=("alice",),
                    occurred_at=f"2026-01-0{day}",
                )
                db.create_feedback(conn, entry.id, f"Advice {day}")
            for number in range(3):
                db.create_review(
                    conn, f"Review {number}", (entry.id,),
                    scope_kind="person", scope_person_handle="alice",
                )
            goal = db.create_goal(conn, "Ask a question", person_handle="alice")
            closed = db.create_goal(conn, "Completed", person_handle="alice")
            db.close_goal(conn, closed.id)
            db.create_goal(conn, "General goal")
            db.create_goal(conn, "Bob goal", person_handle="bob")
        rc, out, err = self._run("person", "prep", "ALICE", "--json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        rc, full, err = self._run(
            "person", "show", "alice", "--recent", "5", "--with-feedback",
            "--reviews", "2", "--json",
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(payload, json.loads(full))
        self.assertEqual(len(payload["recent_entries"]), 5)
        self.assertEqual(len(payload["recent_reviews"]), 2)
        self.assertEqual([g["id"] for g in payload["active_goals"]], [goal.id])
        rc, out, err = self._run("person", "prep", "alice")
        self.assertEqual(rc, 0, err)
        for text in ("Prefers short updates", "Ask a question", "Advice 6", "Review 2"):
            self.assertIn(text, out)
        self.assertLess(out.index("Active goals:"), out.index("Recent interactions:"))
        self.assertEqual(out.count("Active goals:"), 1)
        self.assertNotIn("Conversation 1", out)
        self.assertNotIn("Review 0", out)

    def test_prep_limits_empty_results_and_errors(self):
        self._seed_person("alice")
        rc, out, err = self._run("person", "prep", "alice")
        self.assertEqual(rc, 0, err)
        for text in ("(no goals)", "(no entries)", "(no reviews)"):
            self.assertIn(text, out)
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "Conversation", people=("alice",))
            db.create_review(
                conn, "Advice", (entry.id,),
                scope_kind="person", scope_person_handle="alice",
            )
            goal = db.create_goal(conn, "Practice", person_handle="alice")
        for limit in ("0", "1"):
            rc, out, err = self._run(
                "person", "prep", "alice", "--recent", limit, "--reviews", limit, "--json",
            )
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertEqual(len(payload["recent_entries"]), int(limit))
            self.assertEqual(len(payload["recent_reviews"]), int(limit))
            self.assertEqual(payload["active_goals"][0]["id"], goal.id)
        for flag in ("--recent", "--reviews"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as raised:
                self._run("person", "prep", "alice", flag, "-1")
            self.assertEqual(raised.exception.code, 2)
        rc, _, err = self._run("person", "prep", "missing")
        self.assertEqual(rc, 1)
        self.assertIn("missing", err)


class PersonSelfTests(PeopleTestBase):
    def test_self_promotes_first_person(self) -> None:
        person = self._seed_person("me")
        rc, out, _ = self._run("person", "self", "me")
        self.assertEqual(rc, 0)
        self.assertIn("self ->", out)
        self.assertIn("@me", out)
        with closing(db.connect(self.db_path)) as conn:
            self_row = db.get_self_person(conn)
        assert self_row is not None
        self.assertEqual(self_row.id, person.id)
        self.assertTrue(self_row.is_self)

    def test_self_atomic_switch_keeps_single_self(self) -> None:
        old = self._seed_person("old", is_self=True)
        new = self._seed_person("new")
        rc, _, _ = self._run("person", "self", "new")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            old_after = db.get_person(conn, old.id)
            new_after = db.get_person(conn, new.id)
            count = conn.execute(
                "SELECT COUNT(*) FROM people WHERE is_self = 1"
            ).fetchone()[0]
        assert old_after is not None and new_after is not None
        self.assertFalse(old_after.is_self)
        self.assertTrue(new_after.is_self)
        self.assertEqual(count, 1)

    def test_self_missing_handle_exits_one(self) -> None:
        rc, _, err = self._run("person", "self", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_self_idempotent_when_already_self(self) -> None:
        self._seed_person("me", is_self=True)
        rc, _, _ = self._run("person", "self", "me")
        self.assertEqual(rc, 0)
        with closing(db.connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM people WHERE is_self = 1"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_self_json_output(self) -> None:
        self._seed_person("me")
        rc, out, _ = self._run("--json", "person", "self", "me")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["is_self"])
        self.assertEqual(payload["handle"], "me")
