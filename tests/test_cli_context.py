"""Tests for the ``commscoach context`` skill-facing JSON command."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from commscoach import cli, db


class ContextTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "context.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_self(self, handle: str = "me") -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, is_self=True)

    def _seed_person(self, handle: str, *, name: str | None = None) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name)

    def _seed_entry(
        self,
        body: str = "seed body",
        *,
        people: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        source: str | None = None,
    ) -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(conn, body, source=source, tags=tags, people=people)

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)


class ContextSuccessTests(ContextTestBase):
    def test_history_summary_counts_only_eligible_interactions_before_limiting(self):
        self._seed_self()
        self._seed_person("alice")
        self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            first = db.create_entry(conn, "first", people=("alice",), occurred_at="2026-01-01")
            second = db.create_entry(conn, "second", people=("alice",), occurred_at="2026-01-02")
            fallback = db.create_entry(conn, "creation fallback", people=("alice",))
            with conn:
                conn.execute(
                    "UPDATE entries SET created_at = ? WHERE id = ?",
                    ("2026-01-03T00:00:00+00:00", fallback.id),
                )
            target = db.create_entry(
                conn, "target", people=("alice", "bob"), occurred_at="2026-02-01"
            )
            db.create_entry(conn, "same instant", people=("alice",), occurred_at="2026-02-01")
            db.create_entry(conn, "later", people=("alice",), occurred_at="2026-03-01")
            db.create_entry(conn, "unlinked", occurred_at="2026-01-01")
        for limit, ids in (
            (0, []),
            (1, [fallback.id]),
            (3, [first.id, second.id, fallback.id]),
            (5, [first.id, second.id, fallback.id]),
        ):
            with self.subTest(limit=limit):
                rc, out, err = self._run("context", str(target.id), "--history", str(limit))
                self.assertEqual(rc, 0, err)
                people = {p["handle"]: p for p in json.loads(out)["people"]}
                self.assertEqual([h["entry"]["id"] for h in people["alice"]["history"]], ids)
                self.assertEqual(
                    people["alice"]["history_summary"],
                    {
                        "limit": limit,
                        "count": len(ids),
                        "total_count": 3,
                        "truncated": len(ids) < 3,
                    },
                )
                self.assertEqual(
                    people["bob"]["history_summary"],
                    {
                        "limit": limit,
                        "count": 0,
                        "total_count": 0,
                        "truncated": False,
                    },
                )

    def test_self_notes_are_in_both_contexts_and_follow_edits_and_clearing(self):
        self._seed_self("me")
        entry = self._seed_entry("A conversation")
        commands = (("context", str(entry.id)), ("review", "context"))
        for expected in (
            None,
            "Be direct without sounding abrupt.",
            "Prefer short examples.",
            None,
        ):
            if expected is None:
                rc, _, err = self._run("person", "edit", "me", "--clear-notes")
            else:
                rc, _, err = self._run("person", "edit", "me", "--notes", expected)
            self.assertEqual(rc, 0, err)
            for command in commands:
                with self.subTest(command=command, notes=expected):
                    rc, out, err = self._run(*command)
                    self.assertEqual(rc, 0, err)
                    payload = json.loads(out)
                    self.assertEqual(payload["self"]["notes"], expected)
                    self.assertEqual(payload["self"]["handle"], "me")

    def test_context_emits_skill_shape(self) -> None:
        self._seed_self("me")
        self._seed_person("alice", name="Alice A.")
        self._seed_person("bob")
        entry = self._seed_entry(
            "hello",
            people=("alice", "bob"),
            tags=("review",),
            source="slack",
        )
        first = self._seed_feedback(entry.id, "first note")
        second = self._seed_feedback(entry.id, "second note")
        with closing(db.connect(self.db_path)) as conn:
            alice = db.get_person_by_handle(conn, "alice")
            assert alice is not None
            db.set_person_note(conn, alice.id, "Use concise asks")
            prior_entry = db.create_entry(
                conn,
                "prior hello",
                people=["alice"],
                occurred_at="2020-01-01T09:00:00Z",
            )
            history_feedback = db.create_feedback(conn, prior_entry.id, "Good follow-through")
            review = db.create_review(conn, "weekly synthesis", [prior_entry.id])
            goal = db.create_goal(conn, "Ask one question")

        rc, out, _ = self._run("context", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            set(payload.keys()),
            {
                "entry",
                "self",
                "people",
                "prior_feedback",
                "goals",
                "goal_summary",
                "prior_reviews",
            },
        )
        self.assertEqual(payload["entry"]["id"], entry.id)
        self.assertEqual(payload["entry"]["body"], "hello")
        self.assertEqual(payload["entry"]["tags"], ["review"])
        self.assertEqual(payload["self"]["handle"], "me")
        self.assertTrue(payload["self"]["is_self"])
        self.assertEqual(payload["people"][0]["handle"], "alice")
        people_handles = sorted(p["handle"] for p in payload["people"])
        self.assertEqual(people_handles, ["alice", "bob"])
        alice_payload = next(p for p in payload["people"] if p["handle"] == "alice")
        self.assertEqual(alice_payload["notes"], "Use concise asks")
        self.assertEqual(alice_payload["history"][0]["entry"]["id"], prior_entry.id)
        self.assertEqual(
            alice_payload["history"][0]["feedback"][0]["id"],
            history_feedback.id,
        )
        for person in payload["people"]:
            self.assertFalse(person["is_self"])
        self.assertEqual(
            [f["id"] for f in payload["prior_feedback"]],
            [first.id, second.id],
        )
        self.assertEqual(payload["prior_feedback"][0]["body"], "first note")
        self.assertEqual([item["id"] for item in payload["goals"]], [goal.id])
        self.assertEqual([item["id"] for item in payload["prior_reviews"]], [review.id])

    def test_context_excludes_self_from_people(self) -> None:
        me = self._seed_self("me")
        self._seed_person("alice")
        entry = self._seed_entry("with self linked", people=("me", "alice"))

        rc, out, _ = self._run("context", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        people_ids = [p["id"] for p in payload["people"]]
        self.assertNotIn(me.id, people_ids)
        people_handles = [p["handle"] for p in payload["people"]]
        self.assertEqual(people_handles, ["alice"])
        # entry.people retains the faithful link list, including self
        entry_people_handles = sorted(p["handle"] for p in payload["entry"]["people"])
        self.assertEqual(entry_people_handles, ["alice", "me"])

    def test_context_empty_prior_feedback(self) -> None:
        self._seed_self()
        entry = self._seed_entry("solo")
        rc, out, _ = self._run("context", str(entry.id))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["prior_feedback"], [])
        self.assertEqual(payload["people"], [])
        self.assertEqual(payload["goals"], [])
        self.assertEqual(payload["prior_reviews"], [])

    def test_context_limit_flags_disable_history_and_prior_reviews(self) -> None:
        self._seed_self()
        self._seed_person("alice")
        prior = self._seed_entry("prior", people=("alice",))
        current = self._seed_entry("current", people=("alice",))
        with closing(db.connect(self.db_path)) as conn:
            db.create_review(conn, "review", [prior.id])

        rc, out, _ = self._run(
            "context",
            str(current.id),
            "--history",
            "0",
            "--prior-review-limit",
            "0",
        )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["people"][0]["history"], [])
        self.assertEqual(payload["prior_reviews"], [])

    def test_entry_context_payload_contract(
        self,
    ) -> None:
        """Pin the context shape, including self notes and scoped prior reviews."""
        self._seed_self("me")
        self._seed_person("alice", name="Alice A.")
        entry = self._seed_entry("hello", people=("alice",), tags=("review",))
        feedback = self._seed_feedback(entry.id, "note")
        with closing(db.connect(self.db_path)) as conn:
            prior_entry = db.create_entry(conn, "prior", people=["alice"])
            review = db.create_review(conn, "weekly synthesis", [prior_entry.id])
            goal = db.create_goal(conn, "Ask one question")

        rc, out, _ = self._run("context", str(entry.id))

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            sorted(payload.keys()),
            ["entry", "goal_summary", "goals", "people", "prior_feedback", "prior_reviews", "self"],
        )
        self.assertEqual(
            sorted(payload["entry"].keys()),
            [
                "body",
                "created_at",
                "id",
                "occurred_at",
                "people",
                "source",
                "tags",
                "updated_at",
            ],
        )
        self.assertEqual(
            sorted(payload["self"].keys()), ["handle", "id", "is_self", "name", "notes"]
        )
        self.assertEqual(
            sorted(payload["people"][0].keys()),
            ["handle", "history", "history_summary", "id", "is_self", "name", "notes"],
        )
        self.assertEqual([f["id"] for f in payload["prior_feedback"]], [feedback.id])
        self.assertEqual([g["id"] for g in payload["goals"]], [goal.id])
        prior = payload["prior_reviews"][0]
        for key, expected in (
            ("id", review.id),
            ("body", "weekly synthesis"),
            ("scope_kind", "latest"),
            ("scope_limit", None),
            ("created_at", review.created_at),
            ("updated_at", review.updated_at),
            ("entry_ids", [prior_entry.id]),
        ):
            self.assertEqual(prior[key], expected, key)
        self.assertEqual([e["id"] for e in prior["entries"]], [prior_entry.id])

    def test_context_output_validates_as_json(self) -> None:
        self._seed_self()
        self._seed_person("alice")
        entry = self._seed_entry("body", people=("alice",))
        self._seed_feedback(entry.id, "note")
        rc, out, _ = self._run("context", str(entry.id))
        self.assertEqual(rc, 0)
        # The skill verification runs `python3 -m json.tool`; mirror that here.
        result = subprocess.run(
            ["python3", "-m", "json.tool"],
            input=out,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"json.tool rejected payload: {result.stderr}",
        )

    def test_goal_limits_report_global_totals_including_when_zero(self) -> None:
        self._seed_self()
        self._seed_person("alice")
        entry = self._seed_entry("solo")
        with closing(db.connect(self.db_path)) as conn:
            goals = [db.create_goal(conn, f"Goal {i}", person_handle="alice") for i in range(12)]
            for dropped in (False, True):
                goal = db.create_goal(conn, "closed")
                db.close_goal(conn, goal.id, dropped=dropped)
        for flags, count, limit in (
            ((), 10, 10),
            (("--goal-limit", "0"), 0, 0),
            (("--goal-limit", "2"), 2, 2),
            (("--goal-limit", "20"), 12, 20),
        ):
            with self.subTest(flags=flags):
                rc, out, err = self._run("context", str(entry.id), *flags)
                self.assertEqual(rc, 0, err)
                payload = json.loads(out)
                self.assertEqual(
                    payload["goal_summary"],
                    {
                        "limit": limit,
                        "count": count,
                        "total_count": 12,
                        "truncated": count < 12,
                    },
                )
                self.assertEqual(
                    [g["id"] for g in payload["goals"]], [g.id for g in reversed(goals)][:count]
                )


class ContextFailureTests(ContextTestBase):
    def test_context_missing_entry_exits_one(self) -> None:
        self._seed_self()
        rc, _, err = self._run("context", "9999")
        self.assertEqual(rc, 1)
        self.assertIn("9999", err)

    def test_context_missing_self_exits_one_clearly(self) -> None:
        entry = self._seed_entry("body")
        rc, _, err = self._run("context", str(entry.id))
        self.assertEqual(rc, 1)
        self.assertIn("self", err.lower())

    def test_context_non_integer_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--db", str(self.db_path), "context", "abc"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
