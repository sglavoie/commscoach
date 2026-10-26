"""Primitives shared by every ``commscoach.db`` submodule."""

from __future__ import annotations

import functools
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from typing import Any, TypeVar

from .errors import DBError

_F = TypeVar("_F", bound=Callable[..., Any])
# Connections (by id) currently inside a write_transaction, so nested
# mutators join the outer transaction instead of committing it early.
_ACTIVE_WRITES: set[int] = set()


def search_fold(text: str) -> str:
    """Fold case and accents for substring search, as the FTS5 index does.

    SQLite's own ``LIKE``/``NOCASE`` fold ASCII only, so without this ``école``
    would miss ``ÉCOLE`` and ``cafe`` would miss ``café`` in every search that
    does not go through FTS5. Registered on each connection by ``connect``.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


_WORD = re.compile(r"[^\W_]+")


@functools.lru_cache(maxsize=64)
def _folded_words(query: str) -> tuple[str, ...]:
    return tuple(search_fold(word) for word in query.split())


def word_prefixes_match(text: str, query: str) -> bool:
    """Report whether every word of ``query`` starts a word of ``text``, both folded.

    This is the plain-word FTS5 prefix search (``meet`` finds ``meeting``)
    under :func:`search_fold`, which also folds what FTS5's ``unicode61``
    tokenizer leaves alone, such as ``ß`` matching ``ss``.
    """
    tokens = _WORD.findall(search_fold(text))
    return all(any(token.startswith(word) for token in tokens) for word in _folded_words(query))


def _word_prefixes_sql(text: Any, query: Any, skip_ascii: Any) -> Any:
    if not isinstance(text, str) or not isinstance(query, str):
        return 0
    if skip_ascii and text.isascii():
        return 0
    return int(word_prefixes_match(text, query))


def _search_fold_sql(text: Any) -> Any:
    return search_fold(text) if isinstance(text, str) else text


def handle_key(text: str) -> str:
    """Return the identity of a handle: Unicode case-insensitive, accents kept."""
    return text.casefold()


def _handle_key_sql(text: Any) -> Any:
    return handle_key(text) if isinstance(text, str) else text


def register_functions(conn: sqlite3.Connection) -> None:
    """Make the Unicode-aware helpers below available to SQL on ``conn``.

    ``search_fold(text)`` folds case and accents. ``handle_key(text)`` is
    Unicode case-insensitive identity for handles (``Émile`` is ``émile``)
    without folding accents, which ``LOWER`` cannot do.
    ``word_prefixes(text, query, skip_ascii)`` is :func:`word_prefixes_match`;
    a true ``skip_ascii`` answers no for ASCII ``text``, for callers whose
    full-text index already decides those rows exactly.
    """
    conn.create_function("search_fold", 1, _search_fold_sql, deterministic=True)
    conn.create_function("handle_key", 1, _handle_key_sql, deterministic=True)
    conn.create_function("word_prefixes", 3, _word_prefixes_sql, deterministic=True)


def contains_pattern(query: str) -> str:
    """Return a folded ``LIKE ... ESCAPE '!'`` pattern matching ``query`` anywhere.

    Pair it with ``search_fold(column) LIKE ? ESCAPE '!'``; ``%`` and ``_`` in
    the query match literally.
    """
    literal = search_fold(query).replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{literal}%"


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


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Run several reads against one snapshot of the database.

    Each SELECT otherwise sees whatever was committed when it ran, so a
    concurrent write between queries could make related reads disagree.
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    finally:
        conn.rollback()


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
