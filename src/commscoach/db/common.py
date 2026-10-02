"""Primitives shared by every ``commscoach.db`` submodule."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .errors import DBError

# Sentinel for "argument not provided" in update_* APIs. Using a private
# object() means it can never collide with a real caller-supplied value.
UNSET: Any = object()


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for ``created_at``/``updated_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


def validate_range_bounds(
    since: str | None,
    until: str | None,
    *,
    since_name: str,
    until_name: str,
) -> None:
    """Reject reversed closed ISO date ranges; open ranges are valid."""
    if since is None or until is None:
        return
    try:
        normalized_since = date.fromisoformat(since).isoformat()
    except (TypeError, ValueError) as exc:
        raise DBError(f"{since_name} must be an ISO date (YYYY-MM-DD)") from exc
    try:
        normalized_until = date.fromisoformat(until).isoformat()
    except (TypeError, ValueError) as exc:
        raise DBError(f"{until_name} must be an ISO date (YYYY-MM-DD)") from exc
    if normalized_since > normalized_until:
        raise DBError(f"{since_name} must not be after {until_name}")
