"""Lossless, versioned JSON dump and restore support.

The archive format is deliberately independent of the CLI display serializers.
Unknown top-level keys and unknown row columns are ignored so readers remain
forward compatible. Columns added by a later format version are optional on
read, so an archive written before they existed still restores with those
values NULL. Missing known tables are treated as empty. A dump with a
newer ``format_version`` is rejected because its representation may no longer
be safe to interpret, while a ``schema_version`` mismatch emits a warning: a
dump from one schema version restored into another is the normal migration
path. Restores preserve IDs and run atomically with foreign-key enforcement on.
Timestamps are validated on restore and normalized to UTC, matching what the
CLI itself stores, so range filters and stats work on imported rows.
"""

from __future__ import annotations

import sqlite3
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .common import handle_key, normalize_date_bound, read_snapshot, write_transaction
from .errors import DBError
from .people import renormalize_handles
from .schema import get_schema_version
from .tags import normalize_tag, renormalize_tag_names

FORMAT = "commscoach-dump"
FORMAT_VERSION = 2


class DumpFormatError(DBError):
    """Raised when an archive cannot be interpreted or restored safely."""


class DatabaseNotEmptyError(DBError):
    """Raised when a restore target already contains user data."""


# Text the write paths refuse to store blank, so an archive cannot either.
_REQUIRED_TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "entries": ("body",),
    "feedback": ("body",),
    "reviews": ("body",),
    "goals": ("title",),
    "person_notes": ("body",),
}

_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "people": ("id", "handle", "name", "is_self", "created_at", "updated_at"),
    "entries": (
        "id",
        "body",
        "source",
        "occurred_at",
        "created_at",
        "updated_at",
    ),
    "tags": ("id", "name"),
    "feedback": ("id", "entry_id", "body", "created_at", "updated_at"),
    "reviews": (
        "id",
        "body",
        "scope_kind",
        "scope_limit",
        "scope_person_id",
        "scope_person_handle",
        "scope_tag",
        "scope_since",
        "scope_until",
        "created_at",
        "updated_at",
    ),
    "goals": (
        "id",
        "title",
        "detail",
        "status",
        "person_id",
        "review_id",
        "outcome",
        "closed_at",
        "created_at",
        "updated_at",
    ),
    "person_notes": ("person_id", "body", "created_at", "updated_at"),
}

# Columns added after a table was already exported by a released format
# version. Archives written before then simply lack the key, so restore treats
# it as NULL rather than rejecting the row for a missing column.
_OPTIONAL_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "reviews": frozenset(
        {
            "scope_person_id",
            "scope_person_handle",
            "scope_tag",
            "scope_since",
            "scope_until",
        }
    ),
}

# Timestamp columns restored as timezone-aware UTC ISO-8601 strings, and the
# review scope bounds, which may also be bare legacy UTC dates.
_TIMESTAMP_COLUMNS = frozenset({"created_at", "updated_at", "occurred_at", "closed_at"})
_DATE_BOUND_COLUMNS = frozenset({"scope_since", "scope_until"})

# Integer columns checked before insert, so a stringly typed id fails with its
# location instead of a bare constraint error. Nullable ones may also be null.
_INTEGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "people": ("id",),
    "entries": ("id",),
    "tags": ("id",),
    "feedback": ("id", "entry_id"),
    "reviews": ("id",),
    "goals": ("id",),
    "person_notes": ("person_id",),
}
_NULLABLE_INTEGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "reviews": ("scope_limit", "scope_person_id"),
    "goals": ("person_id", "review_id"),
}

_AUTOINCREMENT_TABLES = ("people", "entries", "tags", "feedback", "reviews", "goals")
_ALL_DATA_TABLES = (*_TABLE_COLUMNS, "entry_tags", "entry_people", "review_entries")


def _row_dict(row: sqlite3.Row, columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: row[column] for column in columns}


def _dump_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    columns = _TABLE_COLUMNS[table]
    order_column = "person_id" if table == "person_notes" else "id"
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_column}"
    ).fetchall()
    return [_row_dict(row, columns) for row in rows]


def dump_to_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a deterministic, lossless JSON-compatible representation."""
    with read_snapshot(conn):
        return _dump_snapshot(conn)


def _dump_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    people = _dump_rows(conn, "people")
    entries = _dump_rows(conn, "entries")
    for entry in entries:
        entry_id = entry["id"]
        entry["tag_ids"] = [
            row[0]
            for row in conn.execute(
                "SELECT tag_id FROM entry_tags WHERE entry_id = ? ORDER BY tag_id",
                (entry_id,),
            ).fetchall()
        ]
        entry["person_ids"] = [
            row[0]
            for row in conn.execute(
                "SELECT person_id FROM entry_people WHERE entry_id = ? ORDER BY person_id",
                (entry_id,),
            ).fetchall()
        ]

    reviews = _dump_rows(conn, "reviews")
    for review in reviews:
        review["entries"] = [
            {"entry_id": row["entry_id"], "position": row["position"]}
            for row in conn.execute(
                "SELECT entry_id, position FROM review_entries"
                " WHERE review_id = ? ORDER BY position",
                (review["id"],),
            ).fetchall()
        ]

    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "schema_version": get_schema_version(conn),
        "people": people,
        "entries": entries,
        "tags": _dump_rows(conn, "tags"),
        "feedback": _dump_rows(conn, "feedback"),
        "reviews": reviews,
        "goals": _dump_rows(conn, "goals"),
        "person_notes": _dump_rows(conn, "person_notes"),
        # Optional: lets a restore keep handing out fresh IDs instead of
        # reusing those of rows deleted before the dump.
        "sequences": {
            row["name"]: row["seq"]
            for row in conn.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
            if row["name"] in _AUTOINCREMENT_TABLES
        },
    }


def _require_int(value: Any, location: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DumpFormatError(f"{location} must be an integer")


def _normalize_timestamp(value: Any, location: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if stamp.tzinfo is not None:
                try:
                    return stamp.astimezone(UTC).isoformat()
                except OverflowError:
                    pass
    raise DumpFormatError(f"{location} must be a timezone-aware ISO-8601 timestamp")


def _normalize_bound(value: Any, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DumpFormatError(f"{location} must be an ISO date or timestamp")
    try:
        return normalize_date_bound(value, name=location)
    except DBError as exc:
        raise DumpFormatError(str(exc)) from exc


def _row_values(table: str, row: Mapping[str, Any], location: str) -> tuple[Any, ...]:
    optional = _OPTIONAL_TABLE_COLUMNS.get(table, frozenset())
    values = []
    for column in _TABLE_COLUMNS[table]:
        value = row.get(column) if column in optional else row[column]
        if column in _TIMESTAMP_COLUMNS:
            value = _normalize_timestamp(value, f"{location}.{column}")
        elif column in _DATE_BOUND_COLUMNS:
            value = _normalize_bound(value, f"{location}.{column}")
        values.append(value)
    return tuple(values)


def _validate_row(table: str, index: int, row: Any) -> None:
    location = f"{table}[{index}]"
    if not isinstance(row, Mapping):
        raise DumpFormatError(f"{location} must be an object")
    optional = _OPTIONAL_TABLE_COLUMNS.get(table, frozenset())
    missing = [
        column for column in _TABLE_COLUMNS[table] if column not in row and column not in optional
    ]
    if missing:
        raise DumpFormatError(f"{location} is missing column {missing[0]!r}")
    _row_values(table, row, location)
    for column in _INTEGER_COLUMNS[table]:
        _require_int(row[column], f"{location}.{column}")
    for column in _NULLABLE_INTEGER_COLUMNS.get(table, ()):
        if row.get(column) is not None:
            _require_int(row[column], f"{location}.{column}")
    for column in _REQUIRED_TEXT_COLUMNS.get(table, ()):
        if not isinstance(row[column], str) or not row[column].strip():
            raise DumpFormatError(f"{location}.{column} must be a non-empty string")

    # Mirror the write paths' own normalization so a hand-edited archive
    # cannot restore a person or tag that no command can address afterwards.
    # Older handles with '@', commas, or spaces are still accepted: the CLI
    # used to allow them, and a database must re-import its own export.
    if table == "people":
        handle = row["handle"]
        if not isinstance(handle, str) or not handle.strip() or handle != handle.strip():
            raise DumpFormatError(f"{location}.handle must be a non-empty, trimmed string")
    elif table == "tags":
        name = row["name"]
        # Exports from before NFC and '#' normalization keep importing; the
        # restore renormalizes those names afterwards.
        if not isinstance(name, str) or not normalize_tag(name) or name.strip().lower() != name:
            raise DumpFormatError(
                f"{location}.name must be a non-empty, trimmed, lowercase tag name"
            )

    if table == "entries":
        for key in ("tag_ids", "person_ids"):
            values = row.get(key, [])
            if not isinstance(values, list):
                raise DumpFormatError(f"{location}.{key} must be an array")
            for value_index, value in enumerate(values):
                _require_int(value, f"{location}.{key}[{value_index}]")
    elif table == "reviews":
        linked_entries = row.get("entries", [])
        if not isinstance(linked_entries, list):
            raise DumpFormatError(f"{location}.entries must be an array")
        for entry_index, linked_entry in enumerate(linked_entries):
            entry_location = f"{location}.entries[{entry_index}]"
            if not isinstance(linked_entry, Mapping):
                raise DumpFormatError(f"{entry_location} must be an object")
            for key in ("entry_id", "position"):
                if key not in linked_entry:
                    raise DumpFormatError(f"{entry_location} is missing column {key!r}")
                _require_int(linked_entry[key], f"{entry_location}.{key}")


def dump_row_counts(data: Mapping[str, Any]) -> dict[str, int]:
    """Return the number of rows per table in a validated dump."""
    return {table: len(data.get(table) or ()) for table in _TABLE_COLUMNS}


def validate_dump(data: Any) -> None:
    """Validate the known archive envelope and row structure.

    Database constraints remain authoritative for values and relationships;
    ``load_from_dict`` converts any resulting SQLite error to
    :class:`DumpFormatError` after rolling the transaction back.
    """
    if not isinstance(data, Mapping):
        raise DumpFormatError("dump must be a JSON object")
    if data.get("format") != FORMAT:
        raise DumpFormatError(f"format must be {FORMAT!r}")
    version = data.get("format_version")
    _require_int(version, "format_version")
    if version > FORMAT_VERSION:
        raise DumpFormatError(
            f"unsupported dump format version {version}; maximum is {FORMAT_VERSION}"
        )
    if version < 1:
        raise DumpFormatError(f"unsupported dump format version {version}")
    _require_int(data.get("schema_version"), "schema_version")
    sequences = data.get("sequences", {})
    if not isinstance(sequences, Mapping):
        raise DumpFormatError("sequences must be an object")
    for name, seq in sequences.items():
        _require_int(seq, f"sequences.{name}")

    for table in _TABLE_COLUMNS:
        rows = data.get(table, [])
        if not isinstance(rows, list):
            raise DumpFormatError(f"{table} must be an array")
        for index, row in enumerate(rows):
            _validate_row(table, index, row)

    # The LOWER(handle) index only catches ASCII case, so "Émile" and "émile"
    # would both restore as people no command could tell apart.
    seen_handles: dict[str, int] = {}
    for index, person in enumerate(data.get("people", [])):
        key = handle_key(person["handle"])
        if key in seen_handles:
            raise DumpFormatError(
                f"people[{index}].handle {person['handle']!r} duplicates"
                f" people[{seen_handles[key]}].handle; rename one with"
                " 'commscoach person edit HANDLE --handle NEW' in the source database"
                " and export again"
            )
        seen_handles[key] = index


def _database_has_data(conn: sqlite3.Connection) -> bool:
    return any(
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        for table in _ALL_DATA_TABLES
    )


def _insert_row(
    conn: sqlite3.Connection,
    table: str,
    row: Mapping[str, Any],
) -> None:
    columns = _TABLE_COLUMNS[table]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        _row_values(table, row, table),
    )


def _restore_sequences(conn: sqlite3.Connection, sequences: Mapping[str, int]) -> None:
    for table in _AUTOINCREMENT_TABLES:
        seq = sequences.get(table)
        if seq is None:
            continue
        updated = conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?",
            (seq, table),
        )
        if updated.rowcount == 0:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, seq))


def _restore_rows(conn: sqlite3.Connection, data: Mapping[str, Any]) -> None:
    for table in ("people", "entries", "tags"):
        for row in data.get(table, []):
            _insert_row(conn, table, row)

    for entry in data.get("entries", []):
        for tag_id in entry.get("tag_ids", []):
            conn.execute(
                "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry["id"], tag_id),
            )
        for person_id in entry.get("person_ids", []):
            conn.execute(
                "INSERT INTO entry_people (entry_id, person_id) VALUES (?, ?)",
                (entry["id"], person_id),
            )

    for row in data.get("feedback", []):
        _insert_row(conn, "feedback", row)
    for review in data.get("reviews", []):
        _insert_row(conn, "reviews", review)
        for linked_entry in review.get("entries", []):
            conn.execute(
                "INSERT INTO review_entries (review_id, entry_id, position) VALUES (?, ?, ?)",
                (review["id"], linked_entry["entry_id"], linked_entry["position"]),
            )
    for row in data.get("person_notes", []):
        _insert_row(conn, "person_notes", row)
    for row in data.get("goals", []):
        _insert_row(conn, "goals", row)
    _restore_sequences(conn, data.get("sequences", {}))


def load_from_dict(
    conn: sqlite3.Connection,
    data: Any,
    *,
    dry_run: bool = False,
) -> None:
    """Restore ``data`` into an empty database, preserving every stored ID.

    The one exception: a tag stored under older normalization rules is folded
    into the tag it now equals, as ``init`` does for an existing database.

    ``dry_run`` executes the same inserts and constraint checks, then rolls the
    savepoint back. No merge or destructive force mode is provided.
    """
    validate_dump(data)
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if not foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if not foreign_keys:
        raise DumpFormatError("foreign-key enforcement must be enabled before import")

    current_schema = get_schema_version(conn)
    dump_schema = data["schema_version"]
    if dump_schema != current_schema:
        warnings.warn(
            f"dump schema version {dump_schema} differs from database schema version"
            f" {current_schema}; importing known columns",
            RuntimeWarning,
            stacklevel=2,
        )

    # Hold the write lock from the emptiness check on, so a concurrent import
    # cannot pass the same check and interleave its rows with these.
    with write_transaction(conn):
        if _database_has_data(conn):
            raise DatabaseNotEmptyError("database is not empty; import requires an empty database")
        savepoint = "commscoach_dump_import"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            for table in _AUTOINCREMENT_TABLES:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
            _restore_rows(conn, data)
            renormalize_handles(conn)
            renormalize_tag_names(conn)
        except BaseException as exc:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            if isinstance(exc, DBError):
                raise
            if isinstance(exc, (sqlite3.Error, KeyError, TypeError, ValueError, OverflowError)):
                raise DumpFormatError(f"cannot import dump: {exc}") from exc
            raise
        if dry_run:
            conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
