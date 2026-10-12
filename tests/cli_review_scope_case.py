"""Shared setup for the scoped ``review context`` CLI tests."""

from __future__ import annotations

import json

from .cli_review_case import ReviewTestBase


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
