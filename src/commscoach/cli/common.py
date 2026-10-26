"""Shared CLI helpers: output, body reading, confirmation, and DB access.

- ``--db PATH`` / ``$COMMSCOACH_DB`` precedence is resolved by ``resolve_db_path``.
- ``read_body`` accepts a literal string, ``-`` (stdin), or ``@PATH`` (file).
- ``confirm`` prompts on stderr and treats EOF as "no".
- ``open_db`` is a context manager that closes the connection cleanly; its
  ``allow_legacy_schema`` flag lets ``backup`` snapshot a pre-migration store.
- ``prose_lines``/``first_prose_line``/``truncate``/``format_stamp`` build the
  human-readable previews shared by the ``list`` subcommands.
- ``add_date_bounds``/``parse_date_bounds`` declare and resolve the shared
  ``--since``/``--until`` pair used by the ``entry`` and ``review`` groups.
- ``format_scope`` renders a stored review's scope axis for every surface that
  shows one: the ``review`` listings and the Markdown export.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime, time
from pathlib import Path
from typing import Any, TextIO

from .. import db, paths
from ..db.common import search_fold
from ..timeparse import TimeParseError, parse_bound, parse_when


def command_prefix(args: argparse.Namespace) -> list[str]:
    """Return ``commscoach [--db PATH]`` for follow-up commands shown to the user."""
    command = ["commscoach"]
    if getattr(args, "db", None):
        command.extend(("--db", args.db))
    return command


def print_err(message: str, *, stream: TextIO | None = None) -> None:
    """Write a single line to stderr (or an injected stream for tests)."""
    out = stream if stream is not None else sys.stderr
    print(message, file=out)


def print_json(obj: Any, *, stream: TextIO | None = None) -> None:
    """Serialize ``obj`` as a pretty, sorted JSON document with a trailing newline."""
    out = stream if stream is not None else sys.stdout
    # Unescaped like the JSON export, so names and notes stay readable.
    json.dump(obj, out, ensure_ascii=False, indent=2, sort_keys=True, default=str)
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


def _fold_with_offsets(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` for search, remembering each folded character's source index."""
    folded: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        piece = search_fold(char)
        folded.append(piece)
        offsets.extend([index] * len(piece))
    return "".join(folded), offsets


def contains_folded(text: str, query: str) -> bool:
    """Report whether ``query`` occurs in ``text`` under the substring searches' folding."""
    return search_fold(query) in search_fold(text)


def search_excerpt(body: str, query: str, *, width: int = 180) -> str:
    """Show context around the first literal match, preserving matched headings.

    Matching folds case and accents like the database searches do; offsets are
    mapped back to ``body`` so the excerpt is cut from the original text. Long
    queries remain whole even when they exceed the budget.
    """
    folded, offsets = _fold_with_offsets(body)
    position = folded.find(search_fold(query)) if query else -1
    if position < 0:
        return truncate(first_prose_line(body), width=width)
    match = offsets[position]
    budget = max(width, len(query))
    start = max(0, match - (budget - len(query)) // 2)
    end = min(len(body), start + budget)
    start = max(0, end - budget)
    excerpt = " ".join(body[start:end].split())
    return ("…" if start else "") + excerpt + ("…" if end < len(body) else "")


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


def _format_bound(value: str | None) -> str:
    """Render a stored range bound as the local day or time the user typed.

    Bounds are stored as UTC instants, so ``--until 2026-09-01`` is kept as the
    last microsecond of that local day, which in UTC can fall on 09-02. Whole
    local days render as a bare date; legacy date-only bounds pass through.
    """
    if not value:
        return ""
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo is None:
        return value
    local = stamp.astimezone()
    if local.time() in (time.min, time.max):
        return local.date().isoformat()
    return local.strftime("%Y-%m-%d %H:%M")


def format_scope(review: db.Review, *, separator: str = ":") -> str:
    """Render a review's scope kind with its axis: ``person:@alice``, ``tag:conflict``.

    A ``range`` renders both bounds around ``..`` with an open side left empty
    (``range:2026-07-01..``). ``latest`` has no axis and renders as the bare
    kind. ``separator`` is the only thing that varies between surfaces: the
    ``review`` listings use the default ``:`` while the Markdown export passes
    a space, so ``person @alice`` reads as prose next to its
    ``(context limit N)`` suffix. Keeping one renderer means the human, short,
    and export forms cannot drift apart.

    ``person`` prefers the live ``scope_person``, so a renamed person is named
    by their current handle, and falls back to the denormalized
    ``scope_person_handle``, so a review of a since-deleted person still names
    them by the handle they last had. ``update_person`` keeps the two in sync on
    rename; preferring the live row means a row that somehow drifted -- an
    archive written before that fix and restored by ``import``, say -- still
    renders the truth rather than the stale copy.
    """
    kind = review.scope_kind
    if kind == "person":
        handle = (
            review.scope_person.handle
            if review.scope_person is not None
            else review.scope_person_handle
        )
        return f"{kind}{separator}@{handle}" if handle else kind
    if kind == "tag" and review.scope_tag:
        return f"{kind}{separator}{review.scope_tag}"
    if kind == "range" and (review.scope_since or review.scope_until):
        since, until = _format_bound(review.scope_since), _format_bound(review.scope_until)
        return f"{kind}{separator}{since}..{until}"
    return kind


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
    except UnicodeDecodeError as exc:
        raise db.DBError(f"cannot read body: not valid UTF-8 ({exc.reason})") from exc


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
            f"{what} required when stdin is not a terminal:"
            " pass the text, '-' to read stdin, or '@PATH'"
        )
    return compose(template=template, preamble=preamble)


# ``const`` of the edit commands' ``--body``: a bare flag opens the editor. A
# sentinel rather than "" so an explicit ``--body ""`` is rejected as empty
# instead of silently opening the editor.
EDIT_IN_EDITOR: Any = object()


def read_replacement_body(value: Any, *, command: str) -> str | None:
    """Read an edit command's ``--body`` before the database is opened.

    Returns the given text (literal, ``-`` or ``@PATH``), or ``None`` for a bare
    ``--body``, meaning the caller opens the editor on the stored text.
    """
    if value is None:
        raise db.DBError(f"{command} requires --body")
    if value is EDIT_IN_EDITOR:
        return None
    return read_body_or_raise(value)


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
        return parse_bound(value, upper=flag == "--until", now=now)
    except (OverflowError, ValueError) as exc:
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
        help=f"Include {noun} from DATE (local day or exact ISO-8601/relative time).",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="DATE",
        help=f"Include {noun} through DATE (whole local day, or exact time).",
    )


def parse_date_bounds(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve and validate shared CLI date bounds."""
    now = datetime.now()
    since = (
        parse_date_bound(args.since, flag="--since", now=now) if args.since is not None else None
    )
    until = (
        parse_date_bound(args.until, flag="--until", now=now) if args.until is not None else None
    )
    db.validate_range_bounds(since, until, since_name="--since", until_name="--until")
    return since, until


def confirm(
    prompt: str,
    *,
    assume_yes: bool = False,
    stream: TextIO | None = None,
    input_fn: Callable[[str], str] | None = None,
    isatty: Callable[[], bool] | None = None,
) -> bool:
    """Prompt the user for a y/N confirmation.

    Returns ``True`` only when the user types ``y`` or ``yes`` (case-insensitive).
    EOF and any other input return ``False``. ``assume_yes`` skips the prompt
    entirely so ``--yes`` callers do not block on stdin.

    When the default (``input``) would read a stdin that is not a terminal, the
    answer is "no" without reading: a pipe that stays open (an agent harness,
    a background job) would otherwise block forever on a prompt nobody sees.
    ``isatty`` defaults to ``sys.stdin.isatty`` and is injectable for tests.
    """
    if assume_yes:
        return True
    out = stream if stream is not None else sys.stderr
    out.write(f"{prompt} [y/N] ")
    if input_fn is None:
        if not (isatty or _stdin_isatty)():
            # Same output as EOF: callers say how to proceed (``--yes``).
            out.write("\n")
            out.flush()
            return False
        input_fn = input
    out.flush()
    try:
        answer = input_fn("")
    except EOFError:
        # End the prompt line so a following error message starts cleanly.
        out.write("\n")
        out.flush()
        return False
    return answer.strip().lower() in {"y", "yes"}


def _stdin_isatty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):  # replaced or closed stdin
        return False


def resolve_db_path(args: argparse.Namespace) -> Path:
    """Apply ``--db`` then ``$COMMSCOACH_DB`` then the built-in default."""
    try:
        return paths.resolve_db_path(getattr(args, "db", None))
    except paths.LegacyDatabaseMigrationRequired as exc:
        raise db.DBError(str(exc)) from exc


# SQLite stores integers as signed 64-bit values; anything larger would
# surface as an OverflowError traceback instead of a usage error.
_SQLITE_INT_MAX = 2**63 - 1


def _bounded_int(value: str, *, minimum: int, below: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if parsed < minimum:
        raise argparse.ArgumentTypeError(below)
    if parsed > _SQLITE_INT_MAX:
        raise argparse.ArgumentTypeError(f"must be at most {_SQLITE_INT_MAX}")
    return parsed


def non_negative_int(value: str) -> int:
    """Parse an argparse integer that cannot be negative."""
    return _bounded_int(value, minimum=0, below="must be non-negative")


def positive_int(value: str) -> int:
    """Parse an argparse integer that must be at least 1."""
    return _bounded_int(value, minimum=1, below="must be at least 1")


def row_id(value: str) -> int:
    """Parse a row id argument; ids are positive and fit in SQLite's INTEGER."""
    return _bounded_int(value, minimum=1, below="ids are positive integers")


def handle_arg(value: str) -> str:
    """Accept a person handle as typed, including the ``@handle`` form shown in output."""
    value = value.strip()
    return value[1:] if value.startswith("@") else value


def handle_list_arg(value: str) -> list[str]:
    """Split a comma-separated ``--person`` value like ``--tag`` does."""
    return [handle_arg(part) for part in value.split(",") if part.strip()]


def covered_entry_id(value: str) -> int:
    """Parse one ``--entry`` value, explaining the usual ordering mistake.

    ``--entry`` takes every following word, so a body written after it is
    read as an id; say how to fix that instead of a bare "invalid int".
    """
    try:
        return row_id(value)
    except argparse.ArgumentTypeError as exc:
        if value.lstrip("-").isdigit():
            raise
        raise argparse.ArgumentTypeError(
            f"invalid entry id {value!r} (put the body before --entry, or after '--')"
        ) from exc


@contextmanager
def open_db(
    args: argparse.Namespace,
    *,
    allow_legacy_schema: bool = False,
) -> Iterator[Any]:
    """Yield a ``sqlite3.Connection`` resolved from CLI/env/default, closing it on exit.

    Every command that reads or writes rows needs the current schema, so the
    default runs ``db.validate_schema``. ``allow_legacy_schema`` relaxes that
    to ``db.validate_backup_source`` — "a commscoach database of some version"
    — and exists for exactly one caller, ``backup``, which must be able to
    snapshot a database *before* the ``init`` that migrates it. Do not widen
    it to a command that touches columns: a pre-v4 database has none of the
    ``reviews`` scope columns.
    """
    db_path = resolve_db_path(args)
    if not db_path.exists():
        if paths.icloud_placeholder_for(db_path).exists():
            raise db.DBError(
                f"{db_path} is an iCloud placeholder (not downloaded). "
                "Open its folder in Finder or run 'brctl download', then retry. "
                "Do NOT run 'commscoach init' — it would create an empty database here."
            )
        raise db.InitRequiredError(_init_command(args))
    try:
        conn = db.connect(db_path)
    except sqlite3.Error as exc:
        raise db.DBError(f"cannot open database {db_path}: {exc}") from exc
    with closing(conn) as conn:
        try:
            if allow_legacy_schema:
                db.validate_backup_source(conn)
            else:
                db.validate_schema(conn)
        except db.InitRequiredError as exc:
            raise db.InitRequiredError(_init_command(args), reason=exc.reason) from exc
        yield conn


def _init_command(args: argparse.Namespace) -> str:
    """Return the ``init`` command for the database this invocation targets."""
    return shlex.join([*command_prefix(args), "init"])
