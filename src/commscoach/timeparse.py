"""Small, deterministic parser for entry occurrence timestamps."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo


class TimeParseError(ValueError):
    """Raised when a timestamp does not match the supported closed grammar."""


_ACCEPTED_FORMS = (
    "ISO-8601; now; today/yesterday/tomorrow [TIME]; "
    "[last ]WEEKDAY [TIME]; or N minutes/hours/days/weeks ago"
)
_KEYWORD_RE = re.compile(
    r"^(now|today|yesterday|tomorrow)(?:\s+(.+))?$", re.IGNORECASE
)
_WEEKDAY_RE = re.compile(
    r"^(?:last\s+)?"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(.+))?$",
    re.IGNORECASE,
)
_AGO_RE = re.compile(
    r"^(\d+)\s*"
    r"(m(?:in(?:ute)?s?)?|h(?:rs?|ours?)?|d(?:ays?)?|w(?:eeks?)?)"
    r"\s+ago$",
    re.IGNORECASE,
)
_CLOCK_24_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_CLOCK_12_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.IGNORECASE)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _error(text: str) -> TimeParseError:
    return TimeParseError(f"cannot parse timestamp {text!r}; accepted forms: {_ACCEPTED_FORMS}")


def _reference_now(now: datetime | None) -> datetime:
    # Keep system-local values naive until conversion: astimezone() on ``now``
    # supplies a fixed offset, which is wrong for dates across a DST change.
    return now if now is not None else datetime.now()


def _local_timezone(reference: datetime) -> tzinfo | None:
    return reference.tzinfo


def _parse_clock(text: str, original: str) -> time:
    match = _CLOCK_24_RE.fullmatch(text.strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        second = int(match.group(3) or 0)
        if hour < 24 and minute < 60 and second < 60:
            return time(hour, minute, second)
        raise _error(original)

    match = _CLOCK_12_RE.fullmatch(text.strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if 1 <= hour <= 12 and minute < 60:
            if hour == 12:
                hour = 0
            if match.group(3).lower() == "pm":
                hour += 12
            return time(hour, minute)
    raise _error(original)


def _at_local_time(reference: datetime, day_offset: int, clock: time) -> datetime:
    target_date = (reference + timedelta(days=day_offset)).date()
    return datetime.combine(target_date, clock, tzinfo=_local_timezone(reference))


def _canonical(dt: datetime, reference: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_timezone(reference))
    return dt.astimezone(timezone.utc).isoformat()


def parse_when(text: str, *, now: datetime | None = None) -> str:
    """Parse a supported human timestamp into a UTC ISO-8601 string."""
    try:
        return _parse_when(text, now=now)
    except OverflowError as exc:
        # e.g. "999999999 days ago" or a year-1 timestamp with a positive offset.
        raise TimeParseError(f"timestamp {text!r} is out of range") from exc


def _parse_when(text: str, *, now: datetime | None) -> str:
    if not isinstance(text, str):
        raise _error(str(text))
    value = text.strip()
    if not value:
        raise _error(text)
    reference = _reference_now(now)

    iso_value = value[:-1] + "Z" if value.endswith("z") else value
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        pass
    else:
        return _canonical(parsed, reference)

    match = _KEYWORD_RE.fullmatch(value)
    if match:
        keyword = match.group(1).lower()
        clock_text = match.group(2)
        if keyword == "now":
            if clock_text is not None:
                raise _error(text)
            return _canonical(reference, reference)
        clock = _parse_clock(clock_text, text) if clock_text else time()
        offsets = {"now": 0, "today": 0, "yesterday": -1, "tomorrow": 1}
        return _canonical(_at_local_time(reference, offsets[keyword], clock), reference)

    match = _WEEKDAY_RE.fullmatch(value)
    if match:
        target_weekday = _WEEKDAYS[match.group(1).lower()]
        days_back = (reference.weekday() - target_weekday) % 7 or 7
        clock = _parse_clock(match.group(2), text) if match.group(2) else time()
        return _canonical(_at_local_time(reference, -days_back, clock), reference)

    match = _AGO_RE.fullmatch(value)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()[0]
        delta = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return _canonical(reference - delta, reference)

    raise _error(text)


def parse_bound(text: str, *, upper: bool = False, now: datetime | None = None) -> str:
    """Resolve a local calendar day or an exact instant to an inclusive UTC bound.

    Bare dates, day keywords, and weekdays cover whole local days. Explicit
    clocks, ``now``, and elapsed-time expressions retain their exact time.
    """
    reference = _reference_now(now)
    value = text.strip()
    parsed = datetime.fromisoformat(parse_when(value, now=reference))
    try:
        date.fromisoformat(value)
        day_only = True
    except ValueError:
        day_only = value.lower() in {"today", "yesterday", "tomorrow"}
        weekday = _WEEKDAY_RE.fullmatch(value)
        day_only = day_only or bool(weekday and weekday.group(2) is None)
    if day_only:
        local_day = parsed.astimezone(reference.tzinfo).date()
        if upper:
            local_day += timedelta(days=1)
        boundary = datetime.combine(local_day, time(), tzinfo=reference.tzinfo)
        parsed = boundary.astimezone(timezone.utc)
        if upper:
            parsed -= timedelta(microseconds=1)
    return parsed.isoformat()
