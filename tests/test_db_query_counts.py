"""Listings load related rows in batches, not with queries per row."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from commscoach import db

from .db_case import DBTestCase


class QueryCountTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        db.create_person(self.conn, "alice")
        entries = [
            db.create_entry(self.conn, f"body {i}", tags=("work",), people=("alice",))
            for i in range(10)
        ]
        for _ in range(5):
            review = db.create_review(
                self.conn, "review", [entry.id for entry in entries], scope_limit=10
            )
            db.create_goal(self.conn, "goal", person_handle="alice", review_id=review.id)

    def _count_queries(self, call: Callable[[], Any]) -> int:
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            call()
        finally:
            self.conn.set_trace_callback(None)
        return len(statements)

    def test_listings_do_not_query_per_row(self) -> None:
        for name, call, budget in (
            ("list_entries", lambda: db.list_entries(self.conn), 3),
            ("list_reviews", lambda: db.list_reviews(self.conn), 6),
            ("list_goals", lambda: db.list_goals(self.conn), 5),
        ):
            with self.subTest(name):
                self.assertLessEqual(self._count_queries(call), budget)
