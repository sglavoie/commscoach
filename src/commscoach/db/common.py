"""Primitives shared by every ``commscoach.db`` submodule."""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from typing import Any, TypeVar

from .errors import DBError

_F = TypeVar("_F", bound=Callable[..., Any])
# Connections (by id) currently inside a write_transaction, so nested
# mutators join the outer transaction instead of committing it early.
_ACTIVE_WRITES: set[int] = set()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a block as one ``BEGIN IMMEDIATE`` transaction; reentrant.

    Taking the write lock up front means existence checks and the writes that
    depend on them see the same state, and a second writer waits on the busy
    timeout at BEGIN instead of failing part-way. Work the caller left pending
    is committed first, as ``with conn:`` would have committed it.
    """
    key = id(conn)
    if key in _ACTIVE_WRITES:
        yield
        return
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    _ACTIVE_WRITES.add(key)
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        _ACTIVE_WRITES.discard(key)


def writes(func: _F) -> _F:
    """Run a ``(conn, ...)`` mutator, lookups included, in :func:`write_transaction`."""

    @functools.wraps(func)
    def wrapper(conn: sqlite3.Connection, *args: Any, **kwargs: Any) -> Any:
        with write_transaction(conn):
            return func(conn, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


# Sentinel for "argument not provided" in update_* APIs. Using a private
# object() means it can never collide with a real caller-supplied value.
UNSET: Any = object()


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for ``created_at``/``updated_at`` columns."""
    return datetime.now(UTC).isoformat()


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
        return stamp.astimezone(UTC).isoformat()
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
            tzinfo=UTC,
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
