"""Mutators run lookups and writes in one reentrant BEGIN IMMEDIATE transaction."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from commscoach import db
from commscoach.db import common

from .db_case import DBTestCase


class WriteTransactionTests(DBTestCase):
    def test_mutator_commits_and_leaves_no_open_transaction(self) -> None:
        db.create_entry(self.conn, "saved")
        self.assertFalse(self.conn.in_transaction)
        with closing(db.connect(self.db_path)) as other:
            (count,) = other.execute("SELECT COUNT(*) FROM entries").fetchone()
        self.assertEqual(count, 1)

    def test_nested_mutators_join_the_outer_transaction(self) -> None:
        with self.assertRaises(RuntimeError):
            with common.write_transaction(self.conn):
                entry = db.create_entry(self.conn, "first")
                db.create_feedback(self.conn, entry.id, "note")
                self.assertTrue(self.conn.in_transaction)
                raise RuntimeError("abort the whole unit")
        self.assertEqual(db.list_entries(self.conn), ())
        self.assertFalse(self.conn.in_transaction)

    def test_failed_lookup_rolls_back_and_releases_the_lock(self) -> None:
        with self.assertRaises(db.EntryNotFoundError):
            db.create_feedback(self.conn, 999, "orphan")
        self.assertFalse(self.conn.in_transaction)
        db.create_entry(self.conn, "still writable")

    def test_write_lock_is_taken_before_any_lookup(self) -> None:
        with closing(sqlite3.connect(self.db_path, timeout=0)) as other:
            other.execute("BEGIN IMMEDIATE")
            self.conn.execute("PRAGMA busy_timeout = 0")
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                db.create_feedback(self.conn, 999, "blocked")
            other.rollback()
        self.assertFalse(self.conn.in_transaction)
