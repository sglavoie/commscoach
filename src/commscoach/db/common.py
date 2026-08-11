"""Primitives shared by every ``commscoach.db`` submodule."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Sentinel for "argument not provided" in update_* APIs. Using a private
# object() means it can never collide with a real caller-supplied value.
UNSET: Any = object()


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for ``created_at``/``updated_at`` columns."""
    return datetime.now(timezone.utc).isoformat()
