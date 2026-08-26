"""Tests for the closed timestamp grammar."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from commscoach.timeparse import TimeParseError, parse_when


NOW = datetime(
    2026,
    8,
    25,
    14,
    30,
    tzinfo=timezone(timedelta(hours=-4)),
)


class ParseWhenTests(unittest.TestCase):
    def test_iso_8601_forms(self) -> None:
        cases = {
            "2026-08-20": "2026-08-20T04:00:00+00:00",
            "2026-08-20T14:30": "2026-08-20T18:30:00+00:00",
            "2026-08-20T14:30:00Z": "2026-08-20T14:30:00+00:00",
            "2026-08-20T14:30:00+02:00": "2026-08-20T12:30:00+00:00",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_when(value, now=NOW), expected)

    def test_relative_day_keywords_and_clocks(self) -> None:
        cases = {
            "now": "2026-08-25T18:30:00+00:00",
            "TODAY": "2026-08-25T04:00:00+00:00",
            "yesterday 9am": "2026-08-24T13:00:00+00:00",
            "tomorrow 14:05:06": "2026-08-26T18:05:06+00:00",
            "today 12pm": "2026-08-25T16:00:00+00:00",
            "today 12am": "2026-08-25T04:00:00+00:00",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_when(value, now=NOW), expected)

    def test_weekdays_are_strictly_in_the_past(self) -> None:
        self.assertEqual(
            parse_when("tuesday", now=NOW),
            "2026-08-18T04:00:00+00:00",
        )
        self.assertEqual(
            parse_when("last monday 9:15pm", now=NOW),
            "2026-08-25T01:15:00+00:00",
        )

    def test_amount_ago_forms(self) -> None:
        cases = {
            "15 minutes ago": "2026-08-25T18:15:00+00:00",
            "2h ago": "2026-08-25T16:30:00+00:00",
            "3 days ago": "2026-08-22T18:30:00+00:00",
            "2weeks ago": "2026-08-11T18:30:00+00:00",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_when(value, now=NOW), expected)

    def test_day_arithmetic_tracks_dst_offset_changes(self) -> None:
        now = datetime(2026, 3, 8, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(
            parse_when("1 day ago", now=now),
            "2026-03-07T17:00:00+00:00",
        )

    def test_rejected_forms_name_the_grammar(self) -> None:
        for value in (
            "yesterdya",
            "next week",
            "",
            "25:00",
            "3 months ago",
            "Aug 20",
            "in 2h",
            "2h",
        ):
            with self.subTest(value=value), self.assertRaises(TimeParseError) as raised:
                parse_when(value, now=NOW)
            self.assertIn("accepted forms", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
