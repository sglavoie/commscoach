"""Tests for scope derivation, conflicts and flags on ``review context``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from datetime import datetime

from commscoach import cli

from .cli_review_scope_case import ScopedContextBase


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
