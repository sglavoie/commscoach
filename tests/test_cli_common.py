"""Tests for the shared CLI preview helpers in ``commscoach.cli.common``."""

from __future__ import annotations

import unittest

from commscoach.cli.common import (
    first_prose_line,
    format_stamp,
    prose_lines,
    search_excerpt,
    truncate,
)


class TruncateTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(truncate("short"), "short")

    def test_text_at_width_is_unchanged(self) -> None:
        text = "x" * 80
        self.assertEqual(truncate(text), text)

    def test_longer_text_is_clipped_with_ellipsis(self) -> None:
        result = truncate("x" * 81)
        self.assertEqual(result, "x" * 77 + "...")
        self.assertEqual(len(result), 80)

    def test_custom_width(self) -> None:
        self.assertEqual(truncate("abcdefgh", width=5), "ab...")


class ProseLinesTests(unittest.TestCase):
    def test_headings_are_dropped(self) -> None:
        body = "## Situation\nThe standup ran long.\n"
        self.assertEqual(prose_lines(body), ["The standup ran long."])

    def test_consecutive_lines_collapse_into_one_paragraph(self) -> None:
        body = "## Situation\nfirst line\nsecond line\n\n## Impact\nthird line"
        self.assertEqual(
            prose_lines(body), ["first line second line", "third line"]
        )

    def test_horizontal_rules_and_blank_lines_are_dropped(self) -> None:
        body = "alpha\n\n---\n\n***\n\n___\n\nbeta"
        self.assertEqual(prose_lines(body), ["alpha", "beta"])

    def test_whitespace_is_normalized(self) -> None:
        body = "  spaced   out\ttext  "
        self.assertEqual(prose_lines(body), ["spaced out text"])

    def test_heading_only_body_falls_back_to_first_line(self) -> None:
        self.assertEqual(prose_lines("## Situation\n### Impact\n"), ["## Situation"])

    def test_empty_body_yields_empty_string(self) -> None:
        self.assertEqual(prose_lines(""), [""])

    def test_dashed_list_item_is_kept(self) -> None:
        self.assertEqual(prose_lines("- a point"), ["- a point"])


class FirstProseLineTests(unittest.TestCase):
    def test_returns_first_paragraph(self) -> None:
        body = "# Title\nreal sentence\n\nlater paragraph"
        self.assertEqual(first_prose_line(body), "real sentence")

    def test_empty_body_returns_empty_string(self) -> None:
        self.assertEqual(first_prose_line(""), "")


class SearchExcerptTests(unittest.TestCase):
    def test_late_match_has_context_and_ellipses(self):
        body = "Opening paragraph. " * 30 + "Lead with the ASK. " + "Closing text. " * 30
        excerpt = search_excerpt(body, "lead with the ask")
        self.assertIn("Lead with the ASK.", excerpt)
        self.assertTrue(excerpt.startswith("…"))
        self.assertTrue(excerpt.endswith("…"))
        self.assertLessEqual(len(excerpt), 182)

    def test_matches_headings_multiline_and_literal_characters(self):
        for query in ("10%_!", "## Advice", "ask\nfirst"):
            with self.subTest(query=query):
                body = "Intro. " * 100 + query + " End. " * 100
                self.assertIn(" ".join(query.split()), search_excerpt(body, query))

    def test_unicode_does_not_shift_offsets_or_match_different_case(self):
        body = "İ" * 200 + "Ä " + "padding " * 50 + "ä target"
        self.assertTrue(search_excerpt(body, "ä").endswith("ä target"))

    def test_short_body_and_long_query_are_not_truncated(self):
        self.assertEqual(search_excerpt("Ask first.", "ask"), "Ask first.")
        query = "a" * 250
        self.assertIn(query, search_excerpt("before " + query + " after", query))


class FormatStampTests(unittest.TestCase):
    def test_utc_stamp_is_rendered_as_local_minutes(self) -> None:
        result = format_stamp("2026-06-21T01:49:27.111944+00:00")
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_malformed_stamp_is_returned_verbatim(self) -> None:
        self.assertEqual(format_stamp("not a timestamp"), "not a timestamp")

    def test_empty_stamp_is_returned_verbatim(self) -> None:
        self.assertEqual(format_stamp(""), "")


if __name__ == "__main__":
    unittest.main()
