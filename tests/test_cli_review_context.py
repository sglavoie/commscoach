"""Tests for ``commscoach review context`` and the shared scope flag surface.

Split out of ``test_cli_review.py`` when the scope surface landed; the stored
review CRUD commands live in ``test_cli_review_crud.py``.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import closing, redirect_stderr
from datetime import datetime

from commscoach import cli, db

from .cli_review_case import ReviewTestBase


class ReviewContextTests(ReviewTestBase):
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
        feedback_by_entry = {
            item["entry"]["id"]: item["feedback"] for item in payload["entries"]
        }
        self.assertEqual(feedback_by_entry[second.id][0]["body"], "second note")
        self.assertEqual(feedback_by_entry[third.id], [])
        self.assertEqual([row["id"] for row in payload["prior_reviews"]], [prior.id])
        self.assertEqual(payload["prior_reviews"][0]["entry_ids"], [first.id])
        self.assertEqual([row["id"] for row in payload["goals"]], [goal.id])
        self.assertEqual(payload["goal_summary"], {
            "limit": 10, "count": 1, "total_count": 1, "truncated": False,
        })

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

        rc, out, _ = self._run(
            "review", "context", "--prior-review-limit", "1"
        )

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

        rc, out, _ = self._run(
            "review", "context", "--prior-review-limit", "0"
        )

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
        self.assertEqual(json.loads(out)["goal_summary"], {
            "limit": 0, "count": 0, "total_count": 1, "truncated": True,
        })

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
        self.assertEqual(payload["goal_summary"], {
            "limit": 1, "count": 1, "total_count": 2, "truncated": True,
        })
        self.assertEqual(payload["scope"]["total_entry_count"], 0)
        self.assertFalse(payload["scope"]["entries_truncated"])


class ScopedContextBase(ReviewTestBase):
    """Seeds two people, two tagged entries with alice, and one unrelated entry."""

    def setUp(self) -> None:
        super().setUp()
        self._seed_self("me")
        self.alice = self._seed_person("alice", name="Alice")
        self._seed_person("bob")
        self.first = self._seed_entry(
            "Pushed back on scope creep",
            people=("alice",),
            tags=("conflict",),
            occurred_at="2026-07-05",
        )
        self.second = self._seed_entry(
            "Follow-up 1:1, agreed a smaller slice",
            people=("alice",),
            tags=("conflict",),
            occurred_at="2026-08-20",
        )
        self.unrelated = self._seed_entry(
            "Unrelated note", people=("bob",), occurred_at="2026-01-10"
        )

    def _scope(self, *args: str) -> dict:
        rc, out, err = self._run("review", "context", *args)
        self.assertEqual(rc, 0, err)
        return json.loads(out)


class ScopeDerivationTests(ScopedContextBase):
    def test_completeness_uses_same_scope_before_limit(self) -> None:
        cases = [
            ((), 3),
            (("--person", "ALICE"), 2),
            (("--tag", "Conflict"), 2),
            (("--since", "2026-07-01T00:00:00+00:00"), 2),
            (("--until", self.first.occurred_at), 2),
            (("--since", self.first.occurred_at,
              "--until", self.second.occurred_at), 2),
            (("--tag", "missing"), 0),
        ]
        for flags, total in cases:
            for limit in (0, 1, total, total + 1):
                with self.subTest(flags=flags, limit=limit):
                    payload = self._scope(*flags, "--limit", str(limit))
                    self.assertEqual(payload["scope"]["total_entry_count"], total)
                    self.assertEqual(payload["scope"]["entry_count"], min(limit, total))
                    self.assertEqual(payload["scope"]["entries_truncated"], limit < total)

    def test_no_axis_flag_derives_latest(self) -> None:
        payload = self._scope()
        self.assertEqual(payload["scope"]["kind"], "latest")
        self.assertIsNone(payload["scope"]["person"])
        self.assertIsNone(payload["scope"]["tag"])
        self.assertIsNone(payload["scope"]["since"])
        self.assertIsNone(payload["scope"]["until"])
        self.assertEqual(payload["scope"]["entry_count"], 3)

    def test_person_flag_derives_person_scope(self) -> None:
        payload = self._scope("--person", "alice", "--limit", "10")
        self.assertEqual(payload["scope"]["kind"], "person")
        self.assertEqual(payload["scope"]["person"]["handle"], "alice")
        self.assertEqual(payload["scope"]["person"]["name"], "Alice")
        self.assertEqual(payload["scope"]["entry_count"], 2)
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [self.first.id, self.second.id],
        )

    def test_tag_flag_derives_tag_scope_normalized(self) -> None:
        payload = self._scope("--tag", "Conflict")
        self.assertEqual(payload["scope"]["kind"], "tag")
        self.assertEqual(payload["scope"]["tag"], "conflict")
        self.assertIsNone(payload["scope"]["person"])
        self.assertEqual(payload["scope"]["entry_count"], 2)

    def test_since_alone_derives_range_with_open_until(self) -> None:
        payload = self._scope("--since", "2026-07-01")
        self.assertEqual(payload["scope"]["kind"], "range")
        self.assertEqual(
            datetime.fromisoformat(payload["scope"]["since"]).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-07-01T00:00:00"),
        )
        self.assertIsNone(payload["scope"]["until"])
        self.assertEqual(payload["scope"]["entry_count"], 2)

    def test_until_alone_derives_range_with_open_since(self) -> None:
        payload = self._scope("--until", "2026-07-01")
        self.assertEqual(payload["scope"]["kind"], "range")
        self.assertIsNone(payload["scope"]["since"])
        self.assertEqual(
            datetime.fromisoformat(payload["scope"]["until"]).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-07-01T23:59:59.999999"),
        )
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [self.unrelated.id],
        )

    def test_since_and_until_together_derive_one_range(self) -> None:
        payload = self._scope("--since", "2026-07-01", "--until", "2026-08-01")
        self.assertEqual(payload["scope"]["kind"], "range")
        self.assertEqual(
            datetime.fromisoformat(payload["scope"]["since"]).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-07-01T00:00:00"),
        )
        self.assertEqual(
            datetime.fromisoformat(payload["scope"]["until"]).astimezone().replace(tzinfo=None),
            datetime.fromisoformat("2026-08-01T23:59:59.999999"),
        )
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [self.first.id],
        )

    def test_relative_date_grammar_resolves_to_iso(self) -> None:
        payload = self._scope("--since", "2 weeks ago")
        self.assertEqual(payload["scope"]["kind"], "range")
        self.assertRegex(payload["scope"]["since"], r"^\d{4}-\d{2}-\d{2}T.+\+00:00$")

    def test_relative_date_keyword_is_accepted(self) -> None:
        payload = self._scope("--until", "last monday")
        self.assertRegex(payload["scope"]["until"], r"^\d{4}-\d{2}-\d{2}T.+\+00:00$")

    def test_unparseable_date_exits_one_naming_the_flag(self) -> None:
        rc, _, err = self._run("review", "context", "--since", "not a date")
        self.assertEqual(rc, 1)
        self.assertIn("--since", err)

    def test_unknown_person_handle_exits_one(self) -> None:
        rc, _, err = self._run("review", "context", "--person", "nobody")
        self.assertEqual(rc, 1)
        self.assertIn("nobody", err)

    def test_person_scope_excludes_another_persons_entries(self) -> None:
        payload = self._scope("--person", "bob", "--limit", "5")
        self.assertEqual(payload["scope"]["kind"], "person")
        self.assertEqual(payload["scope"]["person"]["handle"], "bob")
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]],
            [self.unrelated.id],
        )

    def test_tag_scope_matching_nothing_returns_empty_entries(self) -> None:
        payload = self._scope("--tag", "nosuchtag")
        self.assertEqual(payload["scope"]["kind"], "tag")
        self.assertEqual(payload["scope"]["tag"], "nosuchtag")
        self.assertEqual(payload["entries"], [])
        self.assertEqual(payload["scope"]["entry_count"], 0)


class ScopeConflictTests(ScopedContextBase):
    def _expect_conflict(self, *args: str) -> str:
        rc, out, err = self._run("review", "context", *args)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        return err

    def test_person_with_tag_names_both_flags(self) -> None:
        err = self._expect_conflict("--person", "alice", "--tag", "conflict")
        self.assertIn("--person", err)
        self.assertIn("--tag", err)

    def test_person_with_since_names_both_flags(self) -> None:
        err = self._expect_conflict("--person", "alice", "--since", "2026-07-01")
        self.assertIn("--person", err)
        self.assertIn("--since", err)

    def test_person_with_until_names_both_flags(self) -> None:
        err = self._expect_conflict("--person", "alice", "--until", "2026-07-01")
        self.assertIn("--person", err)
        self.assertIn("--until", err)

    def test_tag_with_since_names_both_flags(self) -> None:
        err = self._expect_conflict("--tag", "conflict", "--since", "2026-07-01")
        self.assertIn("--tag", err)
        self.assertIn("--since", err)

    def test_tag_with_until_names_both_flags(self) -> None:
        err = self._expect_conflict("--tag", "conflict", "--until", "2026-07-01")
        self.assertIn("--tag", err)
        self.assertIn("--until", err)

    def test_person_with_tag_and_both_bounds_names_every_flag(self) -> None:
        err = self._expect_conflict(
            "--person",
            "alice",
            "--tag",
            "conflict",
            "--since",
            "2026-07-01",
            "--until",
            "2026-08-01",
        )
        for flag in ("--person", "--tag", "--since", "--until"):
            self.assertIn(flag, err)

    def test_conflict_is_reported_before_a_bad_date_is_parsed(self) -> None:
        err = self._expect_conflict("--person", "alice", "--since", "not a date")
        self.assertIn("--person", err)


class ReversedRangeTests(ScopedContextBase):
    """A closed range whose bounds are reversed is rejected, not emptied."""

    def test_reversed_range_exits_one_naming_both_bound_flags(self) -> None:
        rc, out, err = self._run(
            "review", "context", "--since", "2026-09-01", "--until", "2026-08-01"
        )

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("--since must not be after --until", err)

    def test_reversed_range_emits_no_context_payload(self) -> None:
        """The pre-repair behavior was an exit-0 empty scope; assert it is gone."""
        rc, out, _ = self._run(
            "review",
            "context",
            "--scope",
            "range",
            "--since",
            "2026-09-01",
            "--until",
            "2026-08-01",
        )

        self.assertEqual(rc, 1)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)

    def test_reversed_relative_bounds_are_rejected_after_resolution(self) -> None:
        """Both flags resolve through ``parse_date_bound`` before being ordered."""
        rc, _, err = self._run(
            "review", "context", "--since", "today", "--until", "2 weeks ago"
        )

        self.assertEqual(rc, 1)
        self.assertIn("must not be after", err)

    def test_equal_bounds_are_a_valid_single_day_range(self) -> None:
        payload = self._scope("--since", "2026-07-05", "--until", "2026-07-05")

        self.assertEqual(payload["scope"]["kind"], "range")
        self.assertEqual(
            [item["entry"]["id"] for item in payload["entries"]], [self.first.id]
        )


class ScopeKindFlagTests(ScopedContextBase):
    def test_explicit_scope_matching_the_derived_kind_is_accepted(self) -> None:
        payload = self._scope("--scope", "person", "--person", "alice")
        self.assertEqual(payload["scope"]["kind"], "person")
        self.assertEqual(payload["scope"]["person"]["handle"], "alice")

    def test_explicit_scope_latest_with_no_axis_flag_is_accepted(self) -> None:
        payload = self._scope("--scope", "latest")
        self.assertEqual(payload["scope"]["kind"], "latest")

    def test_explicit_scope_range_matching_bounds_is_accepted(self) -> None:
        payload = self._scope("--scope", "range", "--since", "2026-07-01")
        self.assertEqual(payload["scope"]["kind"], "range")

    def test_scope_contradicting_the_axis_flag_exits_one(self) -> None:
        rc, _, err = self._run(
            "review", "context", "--person", "alice", "--scope", "tag"
        )
        self.assertEqual(rc, 1)
        self.assertIn("--scope tag", err)
        self.assertIn("--person", err)

    def test_scope_latest_with_an_axis_flag_exits_one(self) -> None:
        rc, _, err = self._run(
            "review", "context", "--scope", "latest", "--tag", "conflict"
        )
        self.assertEqual(rc, 1)
        self.assertIn("--scope latest", err)
        self.assertIn("--tag", err)

    def test_scope_person_without_an_axis_flag_names_the_missing_flag(self) -> None:
        rc, _, err = self._run("review", "context", "--scope", "person")
        self.assertEqual(rc, 1)
        self.assertIn("--person", err)

    def test_scope_range_without_bounds_names_both_bound_flags(self) -> None:
        rc, _, err = self._run("review", "context", "--scope", "range")
        self.assertEqual(rc, 1)
        self.assertIn("--since", err)
        self.assertIn("--until", err)

    def test_unknown_scope_value_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "review",
                    "context",
                    "--scope",
                    "everything",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)


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

        self.assertEqual(
            [row["id"] for row in payload["prior_reviews"]], [about_alice.id]
        )
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
