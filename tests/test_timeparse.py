"""Tests for the closed timestamp grammar."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from commscoach.timeparse import TimeParseError, parse_bound, parse_when

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
        # NOW is a Tuesday: the same weekday means a week ago, with or without "last".
        self.assertEqual(parse_when("last tuesday", now=NOW), parse_when("tuesday", now=NOW))

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

    def test_hour_and_minute_arithmetic_is_elapsed_time_across_dst(self) -> None:
        # 03:30 EST, 2.5 hours after clocks fell back from 02:00 EDT.
        fall = datetime(2026, 11, 1, 3, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(parse_when("3 hours ago", now=fall), "2026-11-01T05:30:00+00:00")
        # 04:00 EDT, one hour after clocks sprang forward from 02:00 EST.
        spring = datetime(2026, 3, 8, 4, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(parse_when("2 hours ago", now=spring), "2026-03-08T06:00:00+00:00")
        self.assertEqual(parse_when("90 minutes ago", now=spring), "2026-03-08T06:30:00+00:00")

    def test_zero_days_ago_keeps_the_repeated_fall_back_hour(self) -> None:
        # 01:30 EST, the second 01:30 of the night clocks fell back.
        now = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(parse_when("0 days ago", now=now), parse_when("now", now=now))
        self.assertEqual(parse_when("0 days ago", now=now), "2026-11-01T06:30:00+00:00")

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
            "now 9am",
        ):
            with self.subTest(value=value), self.assertRaises(TimeParseError) as raised:
                parse_when(value, now=NOW)
            self.assertIn("accepted forms", str(raised.exception))

    def test_out_of_range_values_raise_time_parse_error(self) -> None:
        for value in ("999999999 days ago", "0001-01-01T00:00:00+05:00"):
            with self.subTest(value=value), self.assertRaises(TimeParseError) as raised:
                parse_when(value, now=NOW)
            self.assertIn("out of range", str(raised.exception))


class ParseBoundTests(unittest.TestCase):
    def test_bare_days_cover_the_whole_local_day(self):
        for text, day in (
            ("today", "2026-08-25"),
            ("yesterday", "2026-08-24"),
            ("last monday", "2026-08-24"),
            ("2026-08-24", "2026-08-24"),
        ):
            with self.subTest(text=text):
                start = datetime.fromisoformat(parse_bound(text, now=NOW))
                end = datetime.fromisoformat(parse_bound(text, upper=True, now=NOW))
                self.assertEqual(start.astimezone(NOW.tzinfo).isoformat(), day + "T00:00:00-04:00")
                self.assertEqual(end - start, timedelta(days=1, microseconds=-1))

    def test_clock_and_elapsed_time_expressions_stay_exact(self):
        for text in (
            "now",
            "2 hours ago",
            "3 days ago",
            "today 9am",
            "last monday 10:30",
            "2026-08-20T14:30:00.123456+02:00",
        ):
            with self.subTest(text=text):
                expected = parse_when(text, now=NOW)
                self.assertEqual(parse_bound(text, now=NOW), expected)
                self.assertEqual(parse_bound(text, upper=True, now=NOW), expected)

    def test_positive_offset_day_is_not_shifted_to_previous_local_date(self):
        now = datetime(2026, 8, 25, 12, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertEqual(parse_bound("today", now=now), "2026-08-24T15:00:00+00:00")
        self.assertEqual(
            parse_bound("today", upper=True, now=now), "2026-08-25T14:59:59.999999+00:00"
        )


if __name__ == "__main__":
    unittest.main()
