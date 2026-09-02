"""Shared CLI helpers: output, body reading, confirmation, and DB access.

- ``--db PATH`` / ``$COMMSCOACH_DB`` precedence is resolved by ``resolve_db_path``.
- ``read_body`` accepts a literal string, ``-`` (stdin), or ``@PATH`` (file).
- ``confirm`` prompts on stderr and treats EOF as "no".
- ``open_db`` is a context manager that closes the connection cleanly.
- ``prose_lines``/``first_prose_line``/``truncate``/``format_stamp`` build the
  human-readable previews shared by the ``list`` subcommands.
- ``add_date_bounds``/``parse_date_bounds`` declare and resolve the shared
  ``--since``/``--until`` pair used by the ``entry`` and ``review`` groups.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from .. import db, paths
from ..timeparse import TimeParseError, parse_when


def print_err(message: str, *, stream: TextIO | None = None) -> None:
    """Write a single line to stderr (or an injected stream for tests)."""
    out = stream if stream is not None else sys.stderr
    print(message, file=out)


def print_json(obj: Any, *, stream: TextIO | None = None) -> None:
    """Serialize ``obj`` as a pretty, sorted JSON document with a trailing newline."""
    out = stream if stream is not None else sys.stdout
    json.dump(obj, out, indent=2, sort_keys=True, default=str)
    out.write("\n")


_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def truncate(text: str, *, width: int = 80) -> str:
    """Clip ``text`` to ``width`` characters, marking the cut with an ellipsis."""
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def prose_lines(body: str) -> list[str]:
    """Split a markdown body into whitespace-normalized prose paragraphs.

    ATX headings, horizontal rules, and blank lines are dropped; each surviving
    run of consecutive lines collapses into one paragraph. A body made only of
    headings falls back to its first non-empty line so a preview is never empty.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if not line.strip() or _HEADING_RE.match(line) or _RULE_RE.match(line):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(" ".join(line.split()))
    if current:
        paragraphs.append(" ".join(current))
    if not paragraphs:
        return [body.strip().split("\n", 1)[0]]
    return paragraphs


def first_prose_line(body: str) -> str:
    """Return the first prose paragraph of ``body``, or an empty string."""
    lines = prose_lines(body)
    return lines[0] if lines else ""


def format_stamp(iso: str) -> str:
    """Render a stored ISO-8601 UTC timestamp as local ``YYYY-MM-DD HH:MM``.

    Unparseable values are returned verbatim so a malformed or legacy row never
    breaks a listing.
    """
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


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


def read_body_arg(
    value: str | None,
    *,
    what: str,
    template: str,
    preamble: str,
    isatty: Callable[[], bool],
    compose: Callable[..., str],
) -> str:
    """Resolve an optional body argument, composing interactively when omitted."""
    if value is not None:
        return read_body_or_raise(value)
    if not isatty():
        raise db.DBError(
            f"{what} required: pass BODY, '-' for stdin, or '@PATH'"
        )
    return compose(template=template, preamble=preamble)


def parse_when_or_raise(value: str, *, now: datetime | None = None) -> str:
    """Parse an occurrence timestamp, mapping failures to ``db.DBError``."""
    try:
        return parse_when(value, now=now)
    except TimeParseError as exc:
        raise db.DBError(str(exc)) from exc


def parse_date_bound(
    value: str,
    *,
    flag: str,
    now: datetime | None = None,
) -> str:
    """Parse a CLI date bound and name ``flag`` in any semantic error."""
    try:
        return parse_when(value, now=now)[:10]
    except TimeParseError as exc:
        raise db.DBError(f"{flag}: {exc}") from exc


def add_date_bounds(parser: argparse.ArgumentParser, *, noun: str = "entries") -> None:
    """Declare the shared ``--since`` / ``--until`` flags on ``parser``.

    Lives here rather than in one command module because ``entry list``,
    ``entry search``, and the ``review`` scope surface must all accept the same
    grammar; ``noun`` only varies the help text.
    """
    parser.add_argument(
        "--since",
        default=None,
        metavar="DATE",
        help=f"Include {noun} on or after DATE (ISO-8601 or relative).",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="DATE",
        help=f"Include {noun} through DATE (ISO-8601 or relative).",
    )


def parse_date_bounds(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve the flags ``add_date_bounds`` declared into ISO date bounds."""
    since = (
        parse_date_bound(args.since, flag="--since")
        if args.since is not None
        else None
    )
    until = (
        parse_date_bound(args.until, flag="--until")
        if args.until is not None
        else None
    )
    return since, until


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
    try:
        return paths.resolve_db_path(getattr(args, "db", None))
    except paths.LegacyDatabaseMigrationRequired as exc:
        raise db.DBError(str(exc)) from exc


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
        if paths.icloud_placeholder_for(db_path).exists():
            raise db.DBError(
                f"{db_path} is an iCloud placeholder (not downloaded). "
                "Open its folder in Finder or run 'brctl download', then retry. "
                "Do NOT run 'commscoach init' — it would create an empty database here."
            )
        raise db.DBError("run 'commscoach init' first")
    try:
        conn = db.connect(db_path)
    except sqlite3.Error as exc:
        raise db.DBError(f"cannot open database {db_path}: {exc}") from exc
    with closing(conn) as conn:
        db.validate_schema(conn)
        yield conn
