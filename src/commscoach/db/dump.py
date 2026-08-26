"""Lossless, versioned JSON dump and restore support.

The archive format is deliberately independent of the CLI display serializers.
Unknown top-level keys and unknown row columns are ignored so readers remain
forward compatible. Missing known tables are treated as empty. A dump with a
newer ``format_version`` is rejected because its representation may no longer
be safe to interpret, while a ``schema_version`` mismatch emits a warning: a
dump from one schema version restored into another is the normal migration
path. Restores preserve IDs and run atomically with foreign-key enforcement on.
"""

from __future__ import annotations

import sqlite3
import warnings
from collections.abc import Mapping
from typing import Any

from .errors import DBError
from .schema import get_schema_version

FORMAT = "commscoach-dump"
FORMAT_VERSION = 1


class DumpFormatError(DBError):
    """Raised when an archive cannot be interpreted or restored safely."""


class DatabaseNotEmptyError(DBError):
    """Raised when a restore target already contains user data."""


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

_AUTOINCREMENT_TABLES = ("people", "entries", "tags", "feedback", "reviews", "goals")
_ALL_DATA_TABLES = (*_TABLE_COLUMNS, "entry_tags", "entry_people", "review_entries")


def _row_dict(row: sqlite3.Row, columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: row[column] for column in columns}


def _dump_rows(
    conn: sqlite3.Connection, table: str
) -> list[dict[str, Any]]:
    columns = _TABLE_COLUMNS[table]
    order_column = "person_id" if table == "person_notes" else "id"
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_column}"
    ).fetchall()
    return [_row_dict(row, columns) for row in rows]


def dump_to_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a deterministic, lossless JSON-compatible representation."""
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
                "SELECT person_id FROM entry_people"
                " WHERE entry_id = ? ORDER BY person_id",
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
    }


def _require_int(value: Any, location: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DumpFormatError(f"{location} must be an integer")


def _validate_row(table: str, index: int, row: Any) -> None:
    location = f"{table}[{index}]"
    if not isinstance(row, Mapping):
        raise DumpFormatError(f"{location} must be an object")
    missing = [column for column in _TABLE_COLUMNS[table] if column not in row]
    if missing:
        raise DumpFormatError(f"{location} is missing column {missing[0]!r}")

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
                    raise DumpFormatError(
                        f"{entry_location} is missing column {key!r}"
                    )
                _require_int(linked_entry[key], f"{entry_location}.{key}")


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

    for table in _TABLE_COLUMNS:
        rows = data.get(table, [])
        if not isinstance(rows, list):
            raise DumpFormatError(f"{table} must be an array")
        for index, row in enumerate(rows):
            _validate_row(table, index, row)


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
        tuple(row[column] for column in columns),
    )


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
                "INSERT INTO review_entries (review_id, entry_id, position)"
                " VALUES (?, ?, ?)",
                (review["id"], linked_entry["entry_id"], linked_entry["position"]),
            )
    for row in data.get("person_notes", []):
        _insert_row(conn, "person_notes", row)
    for row in data.get("goals", []):
        _insert_row(conn, "goals", row)


def load_from_dict(
    conn: sqlite3.Connection,
    data: Any,
    *,
    dry_run: bool = False,
) -> None:
    """Restore ``data`` into an empty database, preserving every stored ID.

    ``dry_run`` executes the same inserts and constraint checks, then rolls the
    savepoint back. No merge or destructive force mode is provided.
    """
    validate_dump(data)
    if _database_has_data(conn):
        raise DatabaseNotEmptyError(
            "database is not empty; import requires an empty database"
        )

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

    savepoint = "commscoach_dump_import"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for table in _AUTOINCREMENT_TABLES:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        _restore_rows(conn, data)
    except (sqlite3.Error, KeyError, TypeError) as exc:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise DumpFormatError(f"cannot import dump: {exc}") from exc
    if dry_run:
        conn.execute(f"ROLLBACK TO {savepoint}")
    conn.execute(f"RELEASE {savepoint}")
