"""Primitives shared by every ``commscoach.db`` submodule."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from .errors import DBError

# Sentinel for "argument not provided" in update_* APIs. Using a private
# object() means it can never collide with a real caller-supplied value.
UNSET: Any = object()


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for ``created_at``/``updated_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


def normalize_date_bound(value: str, *, name: str) -> str:
    """Accept legacy UTC dates and normalize timezone-aware timestamps."""
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError("timestamp needs a timezone")
        return stamp.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DBError(
            f"{name} must be an ISO date (YYYY-MM-DD) or timezone-aware ISO timestamp"
        ) from exc


def date_bound_timestamp(value: str, *, name: str, upper: bool = False) -> str:
    """Expand a legacy UTC date; retain the precision of timestamp bounds."""
    normalized = normalize_date_bound(value, name=name)
    if len(normalized) == 10:
        stamp = datetime.combine(
            date.fromisoformat(normalized),
            time.max if upper else time.min,
            tzinfo=timezone.utc,
        )
    else:
        stamp = datetime.fromisoformat(normalized)
    # Include both second-only and .000000 stored timestamps at an exact bound.
    return stamp.isoformat(timespec="microseconds" if upper else "auto")


def validate_range_bounds(
    since: str | None,
    until: str | None,
    *,
    since_name: str,
    until_name: str,
) -> None:
    """Reject reversed inclusive date/timestamp ranges; open ranges are valid."""
    if since is None or until is None:
        return
    normalized_since = datetime.fromisoformat(date_bound_timestamp(since, name=since_name))
    normalized_until = datetime.fromisoformat(
        date_bound_timestamp(until, name=until_name, upper=True)
    )
    if normalized_since > normalized_until:
        raise DBError(f"{since_name} must not be after {until_name}")
