"""Shared CLI helpers: output, body reading, confirmation, and DB access.

- ``--db PATH`` / ``$COMMSCOACH_DB`` precedence is resolved by ``resolve_db_path``.
- ``read_body`` accepts a literal string, ``-`` (stdin), or ``@PATH`` (file).
- ``confirm`` prompts on stderr and treats EOF as "no".
- ``open_db`` is a context manager that closes the connection cleanly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from .. import db, paths


def print_err(message: str, *, stream: TextIO | None = None) -> None:
    """Write a single line to stderr (or an injected stream for tests)."""
    out = stream if stream is not None else sys.stderr
    print(message, file=out)


def print_json(obj: Any, *, stream: TextIO | None = None) -> None:
    """Serialize ``obj`` as a pretty, sorted JSON document with a trailing newline."""
    out = stream if stream is not None else sys.stdout
    json.dump(obj, out, indent=2, sort_keys=True, default=str)
    out.write("\n")


def read_body(value: str, *, stdin: TextIO | None = None) -> str:
    """Resolve a CLI body argument.

    The literal value ``-`` reads the full contents of stdin. A value starting
    with ``@`` is treated as a path: the trailing portion is read as UTF-8 from
    disk. Anything else is returned verbatim. ``stdin`` is injectable for tests.
    """
    if value == "-":
        source = stdin if stdin is not None else sys.stdin
        return source.read()
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        return path.read_text(encoding="utf-8")
    return value


def read_body_or_raise(value: str) -> str:
    """Resolve a body argument, mapping filesystem errors to ``db.DBError``."""
    try:
        return read_body(value)
    except OSError as exc:
        raise db.DBError(f"cannot read body: {exc}") from exc


def confirm(
    prompt: str,
    *,
    assume_yes: bool = False,
    stream: TextIO | None = None,
    input_fn: Callable[[str], str] = input,
) -> bool:
    """Prompt the user for a y/N confirmation.

    Returns ``True`` only when the user types ``y`` or ``yes`` (case-insensitive).
    EOF and any other input return ``False``. ``assume_yes`` skips the prompt
    entirely so ``--yes`` callers do not block on stdin.
    """
    if assume_yes:
        return True
    out = stream if stream is not None else sys.stderr
    out.write(f"{prompt} [y/N] ")
    out.flush()
    try:
        answer = input_fn("")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def resolve_db_path(args: argparse.Namespace) -> Path:
    """Apply ``--db`` then ``$COMMSCOACH_DB`` then the built-in default."""
    return paths.resolve_db_path(getattr(args, "db", None))


def non_negative_int(value: str) -> int:
    """Parse an argparse integer that cannot be negative."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


@contextmanager
def open_db(args: argparse.Namespace) -> Iterator[Any]:
    """Yield a ``sqlite3.Connection`` resolved from CLI/env/default, closing it on exit."""
    db_path = resolve_db_path(args)
    if not db_path.exists():
        raise db.DBError("run 'commscoach init' first")
    try:
        conn = db.connect(db_path)
    except sqlite3.Error as exc:
        raise db.DBError(f"cannot open database {db_path}: {exc}") from exc
    with closing(conn) as conn:
        db.validate_schema(conn)
        yield conn
