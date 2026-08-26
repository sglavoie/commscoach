"""Shared setup for ``commscoach.db`` CRUD tests.

Tests use temporary DB files exclusively so the default user DB is never
mutated. Where timestamps need to differ deterministically, ``patch_timestamps``
feeds ``commscoach.db.common.utcnow_iso`` a fixed sequence so we never rely on
real clock drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from commscoach import db
from commscoach.db import common


def patch_timestamps(*values: str) -> Any:
    """Patch the DB clock so ``created_at``/``updated_at`` follow ``values``.

    Opaque fakes such as ``"e0"`` are not valid operands for date-filter tests;
    those tests must supply canonical ISO timestamps.
    """
    return mock.patch.object(
        common, "utcnow_iso", side_effect=iter(values).__next__
    )


class DBTestCase(unittest.TestCase):
    """Create an isolated DB and open a connection for the duration of a test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "data" / "commscoach.sqlite3"
        db.init_db(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
