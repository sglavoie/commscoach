"""Tests for ``commscoach review context`` and prior-review scoping."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr

from commscoach import cli, db

from .cli_review_case import ReviewTestBase
from .cli_review_scope_case import ScopedContextBase


class ReviewContextTests(ReviewTestBase):
    def test_person_review_notes_follow_edits_and_clearing(self) -> None:
        self._seed_self()
        person = self._seed_person("alice")
        other = self._seed_person("bob")
        with closing(db.connect(self.db_path)) as conn:
            db.set_person_note(conn, other.id, "Unrelated private notes")
        for expected in (None, "Prefers concise asks", "Updated preferences", None):
            with self.subTest(notes=expected):
                with closing(db.connect(self.db_path)) as conn:
                    if expected is None:
                        db.clear_person_note(conn, person.id)
                    else:
                        db.set_person_note(conn, person.id, expected)
                    context = db.get_review_context(
                        conn, scope_kind="person", person_handle="ALICE"
                    )
                    self.assertEqual(context.scope_person_notes, expected)
                rc, out, err = self._run("review", "context", "--person", "ALICE")
                self.assertEqual(rc, 0, err)
                self.assertEqual(json.loads(out)["scope"]["person"]["notes"], expected)
                self.assertNotIn("Unrelated private notes", out)
        for flags in ((), ("--tag", "conflict"), ("--since", "2026-01-01")):
            rc, out, err = self._run("review", "context", *flags)
            self.assertEqual(rc, 0, err)
            self.assertIsNone(json.loads(out)["scope"]["person"])

    def test_context_emits_review_synthesis_shape(self) -> None:
        self._seed_self("coach")
        first = self._seed_entry("first")
        second = self._seed_entry("second")
        third = self._seed_entry("third")
        self._seed_feedback(second.id, "second note")
        prior = self._seed_review("prior review", [first.id], scope_limit=1)
        with closing(db.connect(self.db_path)) as conn:
            goal = db.create_goal(conn, "Practice concise openings")

        rc, out, _ = self._run("review", "context", "--limit", "2")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            set(payload.keys()),
            {"self", "scope", "entries", "prior_reviews", "goals", "goal_summary"},
        )
        self.assertEqual(payload["self"]["handle"], "coach")
        self.assertEqual(
            payload["scope"],
            {
                "kind": "latest",
                "limit": 2,
                "entry_count": 2,
                "total_entry_count": 3,
                "entries_truncated": True,
                "person": None,
                "tag": None,
                "since": None,
                "until": None,
            },
        )
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [second.id, third.id],
        )
        feedback_by_entry = {item["entry"]["id"]: item["feedback"] for item in payload["entries"]}
        self.assertEqual(feedback_by_entry[second.id][0]["body"], "second note")
        self.assertEqual(feedback_by_entry[third.id], [])
        self.assertEqual([row["id"] for row in payload["prior_reviews"]], [prior.id])
        self.assertEqual(payload["prior_reviews"][0]["entry_ids"], [first.id])
        self.assertEqual([row["id"] for row in payload["goals"]], [goal.id])
        self.assertEqual(
            payload["goal_summary"],
            {
                "limit": 10,
                "count": 1,
                "total_count": 1,
                "truncated": False,
            },
        )

    def test_context_missing_self_exits_one(self) -> None:
        self._seed_entry("orphan")
        rc, _, err = self._run("review", "context")
        self.assertEqual(rc, 1)
        self.assertIn("self", err.lower())

    def test_context_negative_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "context",
                    "--limit",
                    "-1",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_context_prior_review_limit_one(self) -> None:
        self._seed_self("coach")
        entry = self._seed_entry("entry")
        self._seed_review("first review", [entry.id])
        self._seed_review("second review", [entry.id])
        self._seed_review("third review", [entry.id])
        latest = self._seed_review("latest review", [entry.id])

        rc, out, _ = self._run("review", "context", "--prior-review-limit", "1")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            [row["id"] for row in payload["prior_reviews"]],
            [latest.id],
        )

    def test_context_prior_review_limit_zero(self) -> None:
        self._seed_self("coach")
        entry = self._seed_entry("entry")
        self._seed_review("prior review", [entry.id])

        rc, out, _ = self._run("review", "context", "--prior-review-limit", "0")

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["prior_reviews"], [])

    def test_context_negative_prior_review_limit_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "context",
                    "--prior-review-limit",
                    "-1",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_context_goal_limit_zero(self) -> None:
        self._seed_self("coach")
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "goal")

        rc, out, _ = self._run("review", "context", "--goal-limit", "0")

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["goals"], [])
        self.assertEqual(
            json.loads(out)["goal_summary"],
            {
                "limit": 0,
                "count": 0,
                "total_count": 1,
                "truncated": True,
            },
        )

    def test_goal_completeness_excludes_closed_goals(self) -> None:
        self._seed_self()
        with closing(db.connect(self.db_path)) as conn:
            db.create_goal(conn, "first")
            db.create_goal(conn, "second")
            done = db.create_goal(conn, "done")
            db.close_goal(conn, done.id)
        rc, out, err = self._run("review", "context", "--goal-limit", "1")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["goal_summary"],
            {
                "limit": 1,
                "count": 1,
                "total_count": 2,
                "truncated": True,
            },
        )
        self.assertEqual(payload["scope"]["total_entry_count"], 0)
        self.assertFalse(payload["scope"]["entries_truncated"])


class ScopedPriorReviewTests(ScopedContextBase):
    def test_person_scope_sees_only_person_scoped_prior_reviews(self) -> None:
        general = self._seed_review("general", [self.unrelated.id])
        about_alice = self._seed_review(
            "about alice",
            [self.first.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        payload = self._scope("--person", "alice")

        self.assertEqual([row["id"] for row in payload["prior_reviews"]], [about_alice.id])
        self.assertNotIn(general.id, [row["id"] for row in payload["prior_reviews"]])

    def test_prior_reviews_carry_the_new_scope_keys(self) -> None:
        self._seed_review(
            "about alice",
            [self.first.id],
            scope_kind="person",
            scope_person_handle="alice",
        )

        payload = self._scope("--person", "alice")

        prior = payload["prior_reviews"][0]
        self.assertEqual(prior["scope_kind"], "person")
        self.assertEqual(prior["scope_person"]["handle"], "alice")
        self.assertEqual(prior["scope_person_handle"], "alice")
        self.assertIsNone(prior["scope_tag"])

    def test_prior_reviews_keep_the_deleted_person_handle_json(self) -> None:
        """A prior review outlives its scoped person; the payload must say who.

        The context is taken unscoped on purpose: a ``--person alice`` context
        cannot be built once alice is gone, so an unfiltered ``latest`` context
        is the only surface that still reaches her orphaned review.
        """
        about_alice = self._seed_review(
            "about alice",
            [self.first.id],
            scope_kind="person",
            scope_person_handle="alice",
        )
        with closing(db.connect(self.db_path)) as conn:
            db.delete_person(conn, self.alice.id)

        payload = self._scope()

        prior = payload["prior_reviews"][0]
        self.assertEqual(prior["id"], about_alice.id)
        self.assertEqual(prior["scope_kind"], "person")
        self.assertIsNone(prior["scope_person"])
        self.assertEqual(prior["scope_person_handle"], "alice")

    def test_range_scope_sees_unfiltered_prior_reviews(self) -> None:
        general = self._seed_review("general", [self.unrelated.id])

        payload = self._scope("--since", "2026-07-01")

        self.assertEqual([row["id"] for row in payload["prior_reviews"]], [general.id])


if __name__ == "__main__":
    unittest.main()
