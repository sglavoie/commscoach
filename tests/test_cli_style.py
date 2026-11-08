"""Unit tests for the terminal presentation helpers in ``commscoach.cli.style``."""

from __future__ import annotations

import io
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from commscoach.cli import style


def _env(**values: str):
    return mock.patch.dict(os.environ, values, clear=False)


class ModeTests(unittest.TestCase):
    def test_plain_when_stdout_is_not_a_terminal(self) -> None:
        self.assertFalse(style.pretty(io.StringIO()))
        self.assertFalse(style.color(io.StringIO()))

    def test_pretty_can_be_forced_and_color_follows_force_color(self) -> None:
        with _env(COMMSCOACH_PRETTY="1"):
            self.assertTrue(style.pretty(io.StringIO()))
            self.assertFalse(style.color(io.StringIO()))
            with _env(FORCE_COLOR="1"):
                self.assertTrue(style.color(io.StringIO()))

    def test_plain_wins_over_pretty(self) -> None:
        with _env(COMMSCOACH_PRETTY="1", COMMSCOACH_PLAIN="1"):
            self.assertFalse(style.pretty(io.StringIO()))

    def test_paint_is_identity_without_color(self) -> None:
        self.assertEqual(style.paint("x", "bold"), "x")

    def test_visible_len_and_pad_ignore_ansi(self) -> None:
        painted = "\x1b[1mabc\x1b[0m"
        self.assertEqual(style.visible_len(painted), 3)
        self.assertEqual(style.visible_len(style.pad(painted, 5)), 5)


class RelativeTimeTests(unittest.TestCase):
    now = datetime(2026, 11, 8, 15, 0).astimezone()

    def _iso(self, delta: timedelta) -> str:
        return (self.now - delta).isoformat()

    def test_recent_instants(self) -> None:
        self.assertEqual(style.relative(self._iso(timedelta(seconds=5)), now=self.now), "just now")
        self.assertEqual(style.relative(self._iso(timedelta(minutes=7)), now=self.now), "7 min ago")
        self.assertEqual(style.relative(self._iso(timedelta(hours=3)), now=self.now), "3h ago")

    def test_days_and_weekdays(self) -> None:
        self.assertEqual(style.relative(self._iso(timedelta(hours=9)), now=self.now), "today 06:00")
        self.assertEqual(
            style.relative(self._iso(timedelta(days=1)), now=self.now), "yesterday 15:00"
        )
        self.assertEqual(
            style.relative(self._iso(timedelta(days=3)), now=self.now), "Thu (3 days ago) 15:00"
        )

    def test_midnight_is_a_bare_date(self) -> None:
        midnight = datetime(2026, 11, 7).astimezone().isoformat()
        self.assertEqual(style.relative(midnight, now=self.now), "yesterday")

    def test_older_dates_show_month_and_year_when_needed(self) -> None:
        self.assertEqual(style.relative(self._iso(timedelta(days=30)), now=self.now), "Oct 9")
        self.assertEqual(
            style.relative(self._iso(timedelta(days=400)), now=self.now), "Oct 4, 2025"
        )

    def test_future_and_garbage(self) -> None:
        tomorrow = datetime(2026, 11, 9).astimezone().isoformat()
        self.assertEqual(style.relative(tomorrow, now=self.now), "tomorrow")
        self.assertEqual(style.relative("not a date", now=self.now), "not a date")

    def test_span_and_ordinal(self) -> None:
        self.assertEqual(
            [style.span(d) for d in (0, 1, 5, 20, 90, 800)],
            ["today", "1 day", "5 days", "2 weeks", "3 months", "2 years"],
        )
        self.assertEqual(
            [style.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 102)],
            ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "102nd"],
        )


class ChartTests(unittest.TestCase):
    def test_sparkline_scales_to_peak_and_keeps_zero_flat(self) -> None:
        self.assertEqual(style.sparkline([0, 1, 2, 4]), "▁▃▅█")
        self.assertEqual(style.sparkline([0, 0]), "▁▁")
        self.assertEqual(style.sparkline([]), "")

    def test_bar_has_fixed_width(self) -> None:
        for fraction in (0, 0.27, 0.5, 1, 1.5):
            self.assertEqual(style.visible_len(style.bar(fraction, width=10)), 10)
        self.assertEqual(style.bar(1, width=4), "████")


class MarkdownTests(unittest.TestCase):
    def test_headings_bullets_quotes_and_rules(self) -> None:
        body = "## What worked\n- one\n- two\n\n> a quote\n\n---\n1. first"
        rendered = style.render_markdown(body, indent="  ", width=60)
        self.assertEqual(
            rendered.splitlines(),
            [
                "  What worked",
                "    • one",
                "    • two",
                "",
                "  │ a quote",
                "",
                "  " + "─" * 40,
                "    1. first",
            ],
        )

    def test_long_bullets_wrap_with_hanging_indent(self) -> None:
        rendered = style.render_markdown("- " + "word " * 20, width=40)
        lines = rendered.splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(lines[0].startswith("  • word"))
        self.assertTrue(lines[1].startswith("    word"))

    def test_emphasis_markers_stay_without_color(self) -> None:
        self.assertEqual(style.render_markdown("a **b** `c`"), "a **b** `c`")

    def test_emphasis_is_styled_with_color(self) -> None:
        with _env(COMMSCOACH_PRETTY="1", FORCE_COLOR="1"):
            rendered = style.render_markdown("a **b** `c`")
        self.assertNotIn("**", rendered)
        self.assertIn("\x1b[1mb\x1b[0m", rendered)


class ColorizerTests(unittest.TestCase):
    def test_colorizes_complete_lines_only_when_color_is_on(self) -> None:
        inner = io.StringIO()
        stream = style.ColorizingStream(inner)
        with _env(COMMSCOACH_PRETTY="1", FORCE_COLOR="1"):
            stream.write("[3] @jdoe · #1on1 · needs feedback\npartial")
            self.assertIn("\x1b[36m@jdoe\x1b[0m", inner.getvalue())
            self.assertIn("\x1b[35m#1on1\x1b[0m", inner.getvalue())
            self.assertNotIn("partial", inner.getvalue())
            stream.flush()
        self.assertTrue(inner.getvalue().endswith("partial"))

    def test_leaves_numbers_emails_and_headings_alone(self) -> None:
        with _env(COMMSCOACH_PRETTY="1", FORCE_COLOR="1"):
            for line in ("issue #42", "mail a@b.com", "## Heading"):
                self.assertEqual(style.colorize_line(line), line)


if __name__ == "__main__":
    unittest.main()
