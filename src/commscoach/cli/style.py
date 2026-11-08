"""Terminal presentation: color, relative times, Markdown rendering, and charts.

Human output has two modes:

- **plain**, the stable line format scripts and tests read. It is used whenever
  stdout is not a terminal, and is byte-for-byte what earlier versions printed.
- **pretty**, used when stdout is a terminal. Timestamps read as "yesterday" or
  "3 days ago", Markdown bodies are rendered and wrapped, ``stats`` draws
  charts, and commands add short nudges about what to do next.

``COMMSCOACH_PRETTY=1`` forces pretty output (``less -R`` friendly with
``FORCE_COLOR=1``); ``COMMSCOACH_PLAIN=1`` forces plain output on a terminal.
Color follows the usual conventions on top of pretty mode: ``NO_COLOR`` and
``TERM=dumb`` turn it off, ``FORCE_COLOR`` turns it on. ``--json`` output is
never affected.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from typing import TextIO

_TRUTHY_OFF = {"", "0", "false", "no"}


def _env_on(name: str) -> bool:
    value = os.environ.get(name)
    return value is not None and value.strip().lower() not in _TRUTHY_OFF


def _isatty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def pretty(stream: TextIO | None = None) -> bool:
    """Report whether human output should use the pretty terminal layout."""
    if _env_on("COMMSCOACH_PLAIN"):
        return False
    if _env_on("COMMSCOACH_PRETTY"):
        return True
    return _isatty(stream if stream is not None else sys.stdout)


def color(stream: TextIO | None = None) -> bool:
    """Report whether pretty output may also use ANSI color."""
    if not pretty(stream):
        return False
    if _env_on("FORCE_COLOR") or _env_on("CLICOLOR_FORCE"):
        return True
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    return _isatty(stream if stream is not None else sys.stdout)


# ---------------------------------------------------------------------------
# Color

_CODES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "gray": "90",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def paint(text: str, *styles: str, stream: TextIO | None = None) -> str:
    """Wrap ``text`` in ANSI styles when color is enabled for ``stream``."""
    if not styles or not text or not color(stream):
        return text
    codes = ";".join(_CODES[name] for name in styles)
    return f"\x1b[{codes}m{text}\x1b[0m"


def visible_len(text: str) -> int:
    """Length of ``text`` as displayed, ignoring ANSI escapes."""
    return len(_ANSI_RE.sub("", text))


def pad(text: str, width: int) -> str:
    """Left-align ``text`` to ``width`` display columns, ANSI-aware."""
    return text + " " * max(0, width - visible_len(text))


def clip(text: str, width: int) -> str:
    """Clip plain ``text`` to ``width`` columns with a single-character ellipsis."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)].rstrip() + "…"


def term_width(*, cap: int = 100) -> int:
    """Terminal width for wrapping, capped so prose stays readable."""
    return max(40, min(cap, shutil.get_terminal_size((88, 24)).columns))


def handle(name: str) -> str:
    return paint(f"@{name}", "cyan")


def tag(name: str) -> str:
    return paint(f"#{name}", "magenta")


def ident(value: int | str) -> str:
    return paint(str(value), "dim")


def heading(text: str) -> str:
    return paint(text, "bold")


def muted(text: str) -> str:
    return paint(text, "dim")


def good(text: str) -> str:
    return paint(text, "green")


def warn(text: str) -> str:
    return paint(text, "yellow")


def bad(text: str) -> str:
    return paint(text, "red")


SEP = " · "


def sep() -> str:
    return muted(SEP)


def join(parts: Iterable[str]) -> str:
    return sep().join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Time

_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _local(iso: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone()


def _day_label(day: date, today: date) -> str:
    delta = (today - day).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta == -1:
        return "tomorrow"
    if 1 < delta < 7:
        return f"{_WEEKDAY[day.weekday()]} ({delta} days ago)"
    if -7 < delta < -1:
        return f"{_WEEKDAY[day.weekday()]} (in {-delta} days)"
    label = f"{_MONTH[day.month - 1]} {day.day}"
    return label if day.year == today.year else f"{label}, {day.year}"


def relative(iso: str, *, now: datetime | None = None) -> str:
    """Render a stored timestamp the way a person would say it.

    A timestamp at exactly local midnight is treated as a bare date (that is
    how ``--occurred-at yesterday`` and ``2026-11-02`` are stored), so it never
    shows a made-up ``00:00``. Recent instants read as minutes or hours ago.
    """
    local = _local(iso)
    if local is None:
        return iso
    current = now if now is not None else datetime.now(local.tzinfo)
    if local.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone()
    date_only = local.time() == time.min
    if not date_only:
        seconds = (current - local).total_seconds()
        if 0 <= seconds < 60:
            return "just now"
        if 0 <= seconds < 3600:
            return f"{int(seconds // 60)} min ago"
        if 0 <= seconds < 6 * 3600:
            return f"{int(seconds // 3600)}h ago"
    label = _day_label(local.date(), current.date())
    if date_only or not (-7 < (current.date() - local.date()).days < 7):
        return label
    return f"{label} {local:%H:%M}"


def short_when(iso: str, *, now: datetime | None = None) -> str:
    """A compact :func:`relative` for table columns: ``2h ago``, ``3d ago``, ``Nov 1``."""
    local = _local(iso)
    if local is None:
        return iso
    current = now if now is not None else datetime.now(local.tzinfo)
    if local.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone()
    if local.time() != time.min:
        seconds = (current - local).total_seconds()
        if 0 <= seconds < 60:
            return "just now"
        if 0 <= seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if 0 <= seconds < 6 * 3600:
            return f"{int(seconds // 3600)}h ago"
    delta = (current.date() - local.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if 1 < delta < 7:
        return f"{delta}d ago"
    return _day_label(local.date(), current.date())


def days_since(iso: str | None, *, now: datetime | None = None) -> int | None:
    """Whole local calendar days between ``iso`` and now, or ``None``."""
    if not iso:
        return None
    local = _local(iso)
    if local is None:
        return None
    current = now if now is not None else datetime.now(local.tzinfo)
    if local.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone()
    return (current.date() - local.date()).days


def span(days: int) -> str:
    """Render a duration in days as ``today``, ``3 days``, ``2 weeks``, ``4 months``."""
    if days <= 0:
        return "today"
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 60:
        return f"{days // 7} weeks"
    if days < 730:
        return f"{days // 30} months"
    return f"{days // 365} years"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Charts

_SPARKS = "▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[int]) -> str:
    """Draw ``values`` as a one-line bar chart; zero is a flat baseline."""
    if not values:
        return ""
    top = max(values)
    if top <= 0:
        return _SPARKS[0] * len(values)
    return "".join(
        _SPARKS[0] if value <= 0 else _SPARKS[max(1, round(value / top * (len(_SPARKS) - 1)))]
        for value in values
    )


def bar(fraction: float, *, width: int = 20) -> str:
    """Draw a progress bar for ``fraction`` (0..1) using eighth-block precision."""
    fraction = min(1.0, max(0.0, fraction))
    eighths = round(fraction * width * 8)
    full, rest = divmod(eighths, 8)
    partial = " ▏▎▍▌▋▊▉"[rest] if rest else ""
    filled = "█" * full + partial
    return paint(filled, "green") + muted("░" * (width - len(filled)))


# ---------------------------------------------------------------------------
# Markdown

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_MD_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_MD_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])")


def _inline(text: str) -> str:
    """Style inline Markdown spans; markers are removed only when color shows them."""
    if not color():
        return text
    text = _MD_CODE.sub(lambda m: paint(m.group(1), "cyan"), text)
    text = _MD_BOLD.sub(lambda m: paint(m.group(1) or m.group(2), "bold"), text)
    return _MD_ITALIC.sub(lambda m: paint(m.group(1), "italic"), text)


def _wrap(text: str, width: int, first: str, rest: str) -> list[str]:
    # Wrap before styling so ANSI codes never count toward the width.
    lines = textwrap.wrap(
        text,
        width=max(20, width),
        initial_indent=first,
        subsequent_indent=rest,
        break_on_hyphens=False,
        break_long_words=False,
    )
    return [_inline(line) for line in lines] or [first.rstrip()]


def render_markdown(body: str, *, indent: str = "", width: int | None = None) -> str:
    """Render a Markdown body for a terminal: headings, bullets, quotes, wrapping.

    Deliberately small: it understands the shapes coaching notes use (ATX
    headings, lists, quotes, rules, fenced code, emphasis) and leaves anything
    else as wrapped prose.
    """
    width = (width or term_width()) - len(indent)
    out: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if paragraph:
            out.extend(_wrap(" ".join(paragraph), width, indent, indent))
            paragraph.clear()

    for raw in body.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(indent + "  " + paint(line, "cyan"))
            continue
        if not line.strip():
            flush()
            if out and out[-1] != "":
                out.append("")
            continue
        if match := _MD_HEADING.match(line):
            flush()
            if out and out[-1] != "":
                out.append("")
            level = len(match.group(1))
            text = match.group(2)
            out.append(indent + paint(text, "bold", "blue" if level <= 2 else "bold"))
            continue
        if _MD_RULE.match(line):
            flush()
            out.append(indent + muted("─" * min(width, 40)))
            continue
        if match := _MD_BULLET.match(line):
            flush()
            depth = len(match.group(1).expandtabs(4)) // 2
            marker = match.group(2)
            glyph = "•" if marker in "-*+" else marker
            lead = indent + "  " * depth + "  "
            first = lead + glyph + " "
            out.extend(_wrap(match.group(3), width + len(indent), first, " " * len(first)))
            continue
        if match := _MD_QUOTE.match(line):
            flush()
            bar_ = indent + muted("│ ")
            for wrapped in textwrap.wrap(match.group(1), width=max(20, width - 2)) or [""]:
                out.append(bar_ + paint(wrapped, "italic"))
            continue
        paragraph.append(line.strip())
    flush()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Line colorizer for the plain-format rows that have no dedicated pretty view

_ROW_ID = re.compile(r"^(\s*)\[(\d+)\]")
_HANDLE_TOKEN = re.compile(r"(?<![\w@./])@[^\s,·]+")
_TAG_TOKEN = re.compile(r"(?<![\w#&])#(?=[^\s,#]*[^\W\d_])[^\s,#·]+")
_STATUS_WORDS = (
    ("needs feedback", "yellow"),
    ("has feedback", "green"),
    (" active ", "green"),
    (" done ", "blue"),
    (" dropped ", "gray"),
    ("[OK]", "green"),
    ("[WARN]", "yellow"),
    ("[FAIL]", "red"),
)


def colorize_line(line: str) -> str:
    """Lightly color one plain output line: row ids, handles, tags, statuses."""
    if "\x1b[" in line or not line.strip():
        return line
    line = _ROW_ID.sub(lambda m: f"{m.group(1)}{paint(f'[{m.group(2)}]', 'dim')}", line)
    line = _HANDLE_TOKEN.sub(lambda m: paint(m.group(0), "cyan"), line)
    line = _TAG_TOKEN.sub(lambda m: paint(m.group(0), "magenta"), line)
    for word, hue in _STATUS_WORDS:
        if word in line:
            line = line.replace(word, paint(word, hue), 1)
    return line.replace(SEP, paint(SEP, "dim"))


class ColorizingStream:
    """A stdout proxy that colors complete lines with :func:`colorize_line`."""

    def __init__(self, inner: TextIO) -> None:
        self._inner = inner
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        if "\n" in self._pending:
            *lines, self._pending = self._pending.split("\n")
            self._inner.write("".join(colorize_line(line) + "\n" for line in lines))
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._inner.write(colorize_line(self._pending))
            self._pending = ""
        self._inner.flush()

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)
