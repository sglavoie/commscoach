"""Shared setup for the ``commscoach goal`` CLI tests."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db


class GoalTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "goals.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str, stdin: io.StringIO | None = None) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        argv = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is None:
                rc = cli.main(argv)
            else:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(argv)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_person(self, handle: str = "alex") -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle)

    def _seed_review(self) -> db.Review:
        with closing(db.connect(self.db_path)) as conn:
            entry = db.create_entry(conn, "entry")
            return db.create_review(conn, "review", [entry.id])

    def _seed_goal(self, title: str = "Practice brevity") -> db.Goal:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_goal(conn, title)
