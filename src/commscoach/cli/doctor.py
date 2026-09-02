"""The ``commscoach doctor`` command: tolerant SQLite diagnostics."""

from __future__ import annotations

import argparse
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import db, paths
from ..db import schema
from ..timeparse import TimeParseError, parse_when
from .common import print_json, resolve_db_path

_DB_CHECKS = (
    "schema_version",
    "integrity_check",
    "foreign_key_check",
    "journal_mode",
    "unparsable_occurred_at",
    "row_counts",
    "review_scope",
)
# ``review_scope`` is appended LAST on purpose. ``run_checks`` splices
# ``sync_artifacts`` in at a hard-coded index 6 on the missing/not-downloaded
# paths, which is exactly past the fourth DB check; inserting a new name
# anywhere earlier in this tuple would silently reorder the emitted results
# away from the healthy path's order.


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "doctor",
        help="Diagnose database integrity and sync hazards.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use SQLite's quick integrity check instead of the full check.",
    )
    parser.set_defaults(func=cmd_doctor)


def _result(
    name: str,
    status: str,
    message: str,
    *,
    details: Any | None = None,
) -> dict[str, Any]:
    check: dict[str, Any] = {"name": name, "status": status, "message": message}
    if details is not None:
        check["details"] = details
    return check


def _not_run_checks(reason: str) -> list[dict[str, Any]]:
    return [_result(name, "warn", f"not run: {reason}") for name in _DB_CHECKS]


def _permissions_check(db_path: Path) -> dict[str, Any]:
    try:
        file_mode = stat.S_IMODE(db_path.stat().st_mode)
        dir_mode = stat.S_IMODE(db_path.parent.stat().st_mode)
    except OSError as exc:
        return _result("permissions", "fail", f"cannot inspect permissions: {exc}")
    details = {
        "database": oct(file_mode),
        "directory": oct(dir_mode),
    }
    if file_mode == paths.DB_FILE_MODE and dir_mode == paths.DATA_DIR_MODE:
        return _result(
            "permissions", "ok", "database is 0600 and directory is 0700", details=details
        )
    return _result(
        "permissions",
        "warn",
        "expected database mode 0600 and directory mode 0700",
        details=details,
    )


def _sync_artifacts_check(db_path: Path) -> dict[str, Any]:
    sidecars = [path for path in paths.sidecar_paths(db_path) if path.exists()]
    conflicts = list(paths.conflict_copies(db_path))
    details = {
        "conflicted_copies": [str(path) for path in conflicts],
        "sidecars": [str(path) for path in sidecars],
    }
    if sidecars or conflicts:
        return _result(
            "sync_artifacts",
            "warn",
            "found conflicted copies or SQLite sidecars; stop writers and inspect them",
            details=details,
        )
    return _result("sync_artifacts", "ok", "no conflicted copies or sidecars", details=details)


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    absolute = db_path.resolve()
    uri = f"file:{quote(str(absolute), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _schema_version_check(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        version = db.get_schema_version(conn) if "_meta" in tables else None
    except (sqlite3.Error, ValueError) as exc:
        return _result("schema_version", "fail", f"cannot read schema version: {exc}")
    missing = sorted(schema.EXPECTED_TABLES - tables)
    details = {"expected": db.SCHEMA_VERSION, "found": version, "missing_tables": missing}
    if version == db.SCHEMA_VERSION and not missing:
        return _result("schema_version", "ok", f"schema version {version}", details=details)
    return _result(
        "schema_version",
        "fail",
        f"expected schema version {db.SCHEMA_VERSION}, found {version}",
        details=details,
    )


def _integrity_check(conn: sqlite3.Connection, *, quick: bool) -> dict[str, Any]:
    pragma = "quick_check" if quick else "integrity_check"
    try:
        messages = [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}")]
    except sqlite3.Error as exc:
        return _result("integrity_check", "fail", f"SQLite {pragma} failed: {exc}")
    if messages == ["ok"]:
        return _result("integrity_check", "ok", f"PRAGMA {pragma} returned ok")
    return _result(
        "integrity_check",
        "fail",
        f"PRAGMA {pragma} reported corruption",
        details=messages,
    )


def _foreign_key_check(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        violations = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
    except sqlite3.Error as exc:
        return _result("foreign_key_check", "fail", f"foreign-key check failed: {exc}")
    if not violations:
        return _result("foreign_key_check", "ok", "no foreign-key violations")
    return _result(
        "foreign_key_check",
        "fail",
        f"found {len(violations)} foreign-key violation(s)",
        details=violations,
    )


def _journal_mode_check(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        mode = str(row[0]).lower() if row is not None else "unknown"
    except sqlite3.Error as exc:
        return _result("journal_mode", "fail", f"cannot read journal mode: {exc}")
    if mode == "delete":
        return _result("journal_mode", "ok", "journal mode is DELETE", details=mode)
    return _result(
        "journal_mode",
        "warn",
        f"journal mode is {mode.upper()}, expected DELETE for whole-file sync safety",
        details=mode,
    )


def _unparsable_occurred_at_check(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = conn.execute(
            "SELECT id, occurred_at FROM entries WHERE occurred_at IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as exc:
        return _result(
            "unparsable_occurred_at", "fail", f"cannot inspect occurrence timestamps: {exc}"
        )
    invalid: list[dict[str, Any]] = []
    for row in rows:
        try:
            parse_when(row["occurred_at"])
        except (TimeParseError, TypeError, ValueError, AttributeError):
            invalid.append({"entry_id": row["id"], "value": row["occurred_at"]})
    if not invalid:
        return _result("unparsable_occurred_at", "ok", "all occurrence timestamps parse")
    return _result(
        "unparsable_occurred_at",
        "warn",
        f"found {len(invalid)} unparsable occurrence timestamp(s)",
        details=invalid,
    )


def _review_scope_check(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report person-scoped reviews whose person row is gone.

    Never a ``fail``: ``person rm`` nulls ``scope_person_id`` by design and the
    denormalized ``scope_person_handle`` survives, so a dangling link is
    expected history rather than corruption. The one failure mode worth
    reporting is a database that never reached schema v4 -- the column is
    missing, sqlite3 raises, and that becomes a ``fail`` like every other check
    here.
    """
    try:
        rows = conn.execute(
            "SELECT id, scope_person_handle FROM reviews "
            "WHERE scope_kind = 'person' AND scope_person_id IS NULL "
            "ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        return _result("review_scope", "fail", f"cannot inspect review scopes: {exc}")
    if not rows:
        return _result(
            "review_scope", "ok", "every person-scoped review still resolves to a person"
        )
    dangling = [
        {"review_id": row["id"], "scope_person_handle": row["scope_person_handle"]}
        for row in rows
    ]
    return _result(
        "review_scope",
        "warn",
        f"{len(dangling)} person-scoped review(s) name a deleted person; this is "
        "retained history, not corruption -- the handle is kept so the review "
        "still says who it was about",
        details=dangling,
    )


def _row_counts_check(conn: sqlite3.Connection) -> dict[str, Any]:
    counts: dict[str, int] = {}
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            escaped = table.replace('"', '""')
            counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])
    except sqlite3.Error as exc:
        return _result("row_counts", "fail", f"cannot count rows: {exc}", details=counts)
    return _result("row_counts", "ok", "row counts collected", details=counts)


def run_checks(db_path: Path, *, quick: bool = False) -> list[dict[str, Any]]:
    """Run all diagnostics without relying on schema-validating ``open_db``."""
    placeholder = paths.icloud_placeholder_for(db_path)
    if not db_path.exists():
        if placeholder.exists():
            guidance = (
                f"{db_path} is an iCloud placeholder (not downloaded). Open its folder "
                "in Finder or run 'brctl download', then retry. Do NOT run "
                "'commscoach init' — it would create an empty database here."
            )
            checks = [_result("db_present", "warn", guidance)]
            checks.append(_result("permissions", "warn", "not run: database is not downloaded"))
            checks.extend(_not_run_checks("database is not downloaded"))
            checks.insert(6, _sync_artifacts_check(db_path))
            return checks
        checks = [_result("db_present", "fail", f"database does not exist: {db_path}")]
        checks.append(_result("permissions", "warn", "not run: database is missing"))
        checks.extend(_not_run_checks("database is missing"))
        checks.insert(6, _sync_artifacts_check(db_path))
        return checks

    checks = [_result("db_present", "ok", f"database exists: {db_path}")]
    checks.append(_permissions_check(db_path))
    try:
        conn = _open_read_only(db_path)
    except sqlite3.Error as exc:
        checks.extend(_not_run_checks(f"cannot open database: {exc}"))
        checks.insert(6, _sync_artifacts_check(db_path))
        checks[2] = _result("schema_version", "fail", f"cannot open database: {exc}")
        return checks
    with closing(conn):
        checks.append(_schema_version_check(conn))
        checks.append(_integrity_check(conn, quick=quick))
        checks.append(_foreign_key_check(conn))
        checks.append(_journal_mode_check(conn))
        checks.append(_sync_artifacts_check(db_path))
        checks.append(_unparsable_occurred_at_check(conn))
        checks.append(_row_counts_check(conn))
        checks.append(_review_scope_check(conn))
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    checks = run_checks(db_path, quick=args.quick)
    overall = "fail" if any(item["status"] == "fail" for item in checks) else (
        "warn" if any(item["status"] == "warn" for item in checks) else "ok"
    )
    if args.json:
        print_json({"db_path": str(db_path), "status": overall, "checks": checks})
    else:
        for item in checks:
            print(f"[{item['status'].upper()}] {item['name']}: {item['message']}")
        print(f"overall: {overall}")
    return 1 if overall == "fail" else 0
