"""Shared setup for the ``commscoach export`` CLI tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from commscoach import cli, db


class ExportTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "export.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_person(
        self,
        handle: str,
        *,
        name: str | None = None,
        is_self: bool = False,
    ) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name, is_self=is_self)

    def _seed_entry(
        self,
        body: str = "seed body",
        *,
        source: str | None = None,
        occurred_at: str | None = None,
        tags: tuple[str, ...] = (),
        people: tuple[str, ...] = (),
    ) -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(
                conn,
                body,
                source=source,
                occurred_at=occurred_at,
                tags=tags,
                people=people,
            )

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)

    def _seed_review(
        self,
        body: str,
        entry_ids: list[int],
        *,
        scope_kind: str = "latest",
        scope_limit: int | None = 20,
        scope_person_handle: str | None = None,
        scope_tag: str | None = None,
        scope_since: str | None = None,
        scope_until: str | None = None,
    ) -> db.Review:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_review(
                conn,
                body,
                entry_ids,
                scope_kind=scope_kind,
                scope_limit=scope_limit,
                scope_person_handle=scope_person_handle,
                scope_tag=scope_tag,
                scope_since=scope_since,
                scope_until=scope_until,
            )

    def _seed_goal(
        self,
        title: str,
        *,
        detail: str | None = None,
        person_handle: str | None = None,
        review_id: int | None = None,
    ) -> db.Goal:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_goal(
                conn,
                title,
                detail=detail,
                person_handle=person_handle,
                review_id=review_id,
            )
