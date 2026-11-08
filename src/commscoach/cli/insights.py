"""What the coach knows that is worth repeating: plans, rhythms, and momentum.

These helpers read the journal and return plain data; the home screen, the
``log`` nudges, ``person prep``, and ``recap`` decide how to show it.

- :func:`latest_plan` finds the newest "Try next time" a feedback note gave,
  optionally for one person, so the advice comes back when it is useful.
- :func:`rhythm` measures how often you usually talk to someone, so "gone
  quiet" means quiet *for that relationship*.
- :func:`advanced_run` counts the unbroken run of advanced checks ending a
  goal's trail, the signal that a habit has stuck.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import datetime

from .. import db
from . import prep, style

# A run of this many advanced checks in a row suggests the goal is a habit now.
GRADUATE_RUN = 5
# Rhythm needs a few gaps to mean anything.
RHYTHM_MIN_INTERACTIONS = 3
# Quiet once the silence is this many usual gaps long, and never sooner than
# QUIET_FLOOR_DAYS (a daily standup partner is not "quiet" after three days).
QUIET_FACTOR = 3
QUIET_FLOOR_DAYS = 14

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKERS = re.compile(r"\*\*|__|(?<![\w*])\*(?!\s)|(?<!\s)\*(?![\w*])|`")
_HEADING_MARK = re.compile(r"^\s{0,3}#{1,6}\s+")


def plain_inline(text: str) -> str:
    """Drop inline Markdown markers so an excerpt reads as prose."""
    text = _LINK.sub(r"\1", text)
    text = _HEADING_MARK.sub("", text)
    return _MARKERS.sub("", text)


_INLINE_HEADING = re.compile(r"(^|\s)#{1,6}\s+")


def readable(text: str, *, query: str | None = None) -> str:
    """A one-line excerpt with Markdown markers gone and ``query`` highlighted."""
    text = plain_inline(_INLINE_HEADING.sub(r"\1", " ".join(text.split())))
    if query and query.strip() and style.color():
        pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
        text = pattern.sub(lambda m: style.paint(m.group(0), "bold", "yellow"), text, count=1)
    return text


_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def flatten(body: str) -> str:
    """A Markdown body as one line of prose: ``What worked: • Clear agenda. • …``."""
    parts = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or set(stripped) <= set("-*_="):
            continue
        heading = _HEADING_MARK.match(line)
        if heading:
            title = line[heading.end() :].strip().rstrip("#").strip()
            parts.append(title if title.endswith((":", "?", "!", ".")) else f"{title}:")
        elif _BULLET.match(line):
            parts.append("• " + _BULLET.sub("", line).strip())
        else:
            parts.append(stripped)
    return plain_inline(" ".join(" ".join(parts).split()))


def excerpt(body: str) -> str:
    """The first prose line of a Markdown body, markers removed.

    Unlike :func:`commscoach.cli.common.first_prose_line`, a leading heading
    is skipped, so "## Situation / You asked…" excerpts as "You asked…".
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _HEADING_MARK.match(line) or set(stripped) <= set("-*_="):
            continue
        return " ".join(plain_inline(re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s+", "", line)).split())
    return ""


def key_advice(body: str) -> str:
    """The line of a feedback note worth remembering: its first "Try next time" item."""
    items = prep.section_items(body, prep._TRY)
    return plain_inline(items[0]) if items else excerpt(body)


@dataclass(frozen=True)
class Plan:
    """The most recent "Try next time" advice and where it came from."""

    items: tuple[str, ...]
    feedback: db.Feedback
    entry: db.Entry


def latest_plan(
    conn,  # type: ignore[no-untyped-def]
    *,
    person_handle: str | None = None,
    lookback: int = prep.LOOKBACK,
    before_entry: int | None = None,
) -> Plan | None:
    """The newest coaching note with a "Try next time" section, if any.

    ``before_entry`` skips that entry, so the plan shown right after logging
    is the one made *before* this conversation.
    """
    for entry in db.search_entries(conn, person_handle=person_handle, limit=lookback + 1):
        if entry.id == before_entry:
            continue
        for feedback in reversed(db.list_feedback(conn, entry.id)):
            items = prep.section_items(feedback.body, prep._TRY)
            if items:
                return Plan(
                    tuple(plain_inline(item) for item in items[: prep.MAX_ITEMS]),
                    feedback,
                    entry,
                )
    return None


@dataclass(frozen=True)
class Rhythm:
    """How often you talk to someone, and how long it has been."""

    usual_gap_days: float
    days_since: int
    interactions: int

    @property
    def quiet(self) -> bool:
        return self.days_since >= max(QUIET_FLOOR_DAYS, QUIET_FACTOR * self.usual_gap_days)

    def usual(self) -> str:
        gap = self.usual_gap_days
        if gap < 1.5:
            return "almost daily"
        if gap < 13:
            return f"every ~{round(gap)} days"
        if gap < 56:
            return f"every ~{round(gap / 7)} weeks"
        return f"every ~{round(gap / 30)} months"


def _day(iso: str):  # type: ignore[no-untyped-def]
    parsed = datetime.fromisoformat(iso)
    return (parsed.astimezone() if parsed.tzinfo else parsed).date()


def rhythm(times: tuple[str, ...], *, now: datetime | None = None) -> Rhythm | None:
    """Median gap between distinct conversation days, or ``None`` if too few."""
    days = sorted({_day(t) for t in times})
    if len(days) < RHYTHM_MIN_INTERACTIONS:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
    since = style.days_since(times[-1], now=now)
    return Rhythm(
        usual_gap_days=float(statistics.median(gaps)),
        days_since=max(0, since or 0),
        interactions=len(times),
    )


def weekly_counts(times: tuple[str, ...], today, weeks: int) -> list[int]:  # type: ignore[no-untyped-def]
    """Conversations per week for the last ``weeks`` weeks ending this week."""
    from datetime import timedelta

    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    counts = [0] * weeks
    for iso in times:
        day = _day(iso)
        if first <= day <= today + timedelta(days=6 - today.weekday()):
            index = (day - first).days // 7
            if 0 <= index < weeks:
                counts[index] += 1
    return counts


def advanced_run(checks) -> int:  # type: ignore[no-untyped-def]
    """How many advanced checks in a row end the trail (neutral ones are skipped)."""
    run = 0
    for check in reversed(checks):
        if check.verdict == "neutral":
            continue
        if check.verdict != "advanced":
            break
        run += 1
    return run


def ready_to_close(checks) -> bool:  # type: ignore[no-untyped-def]
    return advanced_run(checks) >= GRADUATE_RUN
