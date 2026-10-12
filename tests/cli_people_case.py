"""Shared setup for the ``commscoach person`` CLI tests."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from commscoach import cli, db


class PeopleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.db_path = self.tmpdir / "people.sqlite3"
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

    def _seed_person(
        self, handle: str, *, name: str | None = None, is_self: bool = False
    ) -> db.Person:
        with closing(db.connect(self.db_path)) as conn:
            return db.create_person(conn, handle, name=name, is_self=is_self)
