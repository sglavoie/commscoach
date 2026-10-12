"""Shared setup for the ``commscoach feedback`` CLI tests."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db


class FeedbackTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "feedback.sqlite3"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self,
        *args: str,
        stdin: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        out_buf, err_buf = io.StringIO(), io.StringIO()
        full_args = ["--db", str(self.db_path), *args]
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", stdin):
                    rc = cli.main(full_args)
            else:
                rc = cli.main(full_args)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _seed_entry(self, body: str = "seed body") -> db.Entry:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_entry(conn, body)

    def _seed_feedback(self, entry_id: int, body: str) -> db.Feedback:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_feedback(conn, entry_id, body)
