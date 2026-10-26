"""Plain-word entry searches match word prefixes under FTS5."""

from __future__ import annotations

import unittest

from commscoach import db
from commscoach.db.entries import _fts_match_query

from .db_case import DBTestCase


class FtsMatchQueryTests(unittest.TestCase):
    def test_plain_words_become_prefix_terms(self) -> None:
        self.assertEqual(_fts_match_query("meet"), '"meet"*')
        self.assertEqual(_fts_match_query(" lead  ask "), '"lead"* "ask"*')
        self.assertEqual(_fts_match_query("café"), '"café"*')

    def test_fts_syntax_passes_through(self) -> None:
        for query in ('"lead with"', "caf*", "meet -budget", "a OR b", "body:ask", "x_y"):
            with self.subTest(query=query):
                self.assertEqual(_fts_match_query(query), query)


class PrefixSearchTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        fts = self.conn.execute("SELECT value FROM _meta WHERE key = 'fts5'").fetchone()
        if fts is None or fts[0] != "1":
            self.skipTest("FTS5 unavailable")

    def test_word_prefix_matches_and_snippets_follow(self) -> None:
        meeting = db.create_entry(self.conn, "Long meeting about hiring")
        db.create_entry(self.conn, "Unrelated")
        found = db.search_entries(self.conn, query="meet")
        self.assertEqual([entry.id for entry in found], [meeting.id])
        snippets = db.search_entry_snippets(self.conn, "meet", [meeting.id])
        self.assertIn("meeting", snippets[meeting.id])
