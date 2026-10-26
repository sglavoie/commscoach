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
        for query in ('"lead with"', "caf*", "a OR b", "body:ask", "^start", "(a b)"):
            with self.subTest(query=query):
                self.assertEqual(_fts_match_query(query), query)

    def test_other_text_is_left_to_substring_search(self) -> None:
        for query in (
            "😀",
            "€50",
            "follow-up",
            "meet -budget",
            "x_y",
            "...",
            '"😀"',
            "会議",
            "カフェ",
            "ประชุม",
        ):
            with self.subTest(query=query):
                self.assertIsNone(_fts_match_query(query))


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

    def test_symbols_fts_would_drop_are_matched_literally(self) -> None:
        emoji = db.create_entry(self.conn, "Great call 😀 today")
        euro = db.create_entry(self.conn, "Budget is €50 now")
        db.create_entry(self.conn, "I have 50 apples")
        for query, expected in (("😀", [emoji.id]), ("€50", [euro.id])):
            with self.subTest(query=query):
                found = db.search_entries(self.conn, query=query)
                self.assertEqual([entry.id for entry in found], expected)
                self.assertEqual(db.count_search_entries(self.conn, query=query), 1)
                self.assertEqual(db.search_entry_snippets(self.conn, query, expected), {})

    def test_words_in_unspaced_scripts_match_inside_a_sentence(self) -> None:
        meeting = db.create_entry(self.conn, "今日の会議はよかった")
        db.create_entry(self.conn, "会社")
        found = db.search_entries(self.conn, query="会議")
        self.assertEqual([entry.id for entry in found], [meeting.id])
        self.assertEqual(db.count_search_entries(self.conn, query="会議"), 1)

    def test_plain_words_fold_beyond_the_tokenizer(self) -> None:
        street = db.create_entry(self.conn, "Walked down the Straße")
        spelled = db.create_entry(self.conn, "Strassen are long")
        db.create_entry(self.conn, "Unrelated")
        for query in ("strasse", "STRAẞE", "straße"):
            with self.subTest(query=query):
                found = db.search_entries(self.conn, query=query, oldest_first=True)
                self.assertEqual([entry.id for entry in found], [street.id, spelled.id])

    def test_folded_matches_keep_word_prefix_semantics(self) -> None:
        db.create_entry(self.conn, "Großstraße plans")
        prefix = db.create_entry(self.conn, "Straßenbahn ride")
        found = db.search_entries(self.conn, query="strasse")
        self.assertEqual([entry.id for entry in found], [prefix.id])
