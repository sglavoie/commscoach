"""Argparse wiring, the ``init`` subcommand, and the console-script entry point.

Exit codes follow the project contract:

- ``0`` success.
- ``1`` semantic user error (any ``db.DBError`` raised by a handler or a
  rejected confirmation), an unexpected SQLite error, or a closed output pipe.
- ``2`` argparse syntax error (raised by ``argparse`` itself).
- ``128 + N`` terminated by signal ``N``: ``130`` for Ctrl-C (SIGINT), and
  SIGTERM or SIGHUP likewise, after the usual editor-draft cleanup or recovery.
"""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .. import __version__, db
from . import (
    backup,
    context,
    doctor,
    entries,
    export,
    feedback,
    goals,
    people,
    porting,
    reviews,
    stats,
    tags,
)
from .common import print_err, print_json, resolve_db_path
from .editor import recover_editor_drafts

PROG = "commscoach"


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with global flags and subcommands."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Personal communications coach: track interactions, generate feedback.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    _add_global_flags(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    _register_init(subparsers)
    doctor.register(subparsers)
    backup.register(subparsers)
    entries.register(subparsers)
    tags.register(subparsers)
    people.register(subparsers)
    feedback.register(subparsers)
    goals.register(subparsers)
    context.register(subparsers)
    reviews.register(subparsers)
    export.register(subparsers)
    porting.register(subparsers)
    stats.register(subparsers)

    _reject_repeated_options(parser)

    # Argparse only recognizes a parent's options before its subcommand. Add
    # the same flags at each level, suppressing absent child defaults so a
    # flag parsed earlier is retained and the last explicit value wins.
    _add_global_flags_to_children(parser)

    return parser


def _add_global_flags(parser: argparse.ArgumentParser, *, child: bool = False) -> None:
    default = argparse.SUPPRESS if child else None
    parser.add_argument(
        "--db",
        default=default,
        type=_db_path_arg,
        metavar="PATH",
        help="Override the SQLite DB path (also reads $COMMSCOACH_DB).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS if child else False,
        help="Emit machine-readable JSON output for the command.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=argparse.SUPPRESS if child else False,
        help="Skip confirmation prompts.",
    )


def _db_path_arg(value: str) -> str:
    # An empty --db (typically an unset "$VAR") must not silently fall back to
    # the real default database.
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return value


class _StoreOnce(argparse._StoreAction):
    """A single-valued option that refuses a second value instead of dropping the first."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        seen = namespace.__dict__.setdefault("_options_seen", set())
        if self.dest in seen:
            raise argparse.ArgumentError(self, "may be given only once")
        seen.add(self.dest)
        super().__call__(parser, namespace, values, option_string)


def _reject_repeated_options(parser: argparse.ArgumentParser) -> None:
    """Make every subcommand's single-valued options error when repeated.

    Plain ``store`` keeps only the last value, so ``--person a --person b``
    would silently filter by ``b`` alone. Options meant to repeat use
    ``append``/``extend``; the global flags, added afterwards, keep their
    last-explicit-wins behaviour.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                for option in child._actions:
                    if type(option) is argparse._StoreAction and option.option_strings:
                        option.__class__ = _StoreOnce
                _reject_repeated_options(child)


def _add_global_flags_to_children(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _add_global_flags(child, child=True)
                _add_global_flags_to_children(child)


def _register_init(subparsers: argparse._SubParsersAction) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help="Create or reaffirm the SQLite DB (idempotent).",
    )
    init_parser.set_defaults(func=cmd_init)


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize the DB at the resolved path. Idempotent: safe to re-run."""
    resolved = db.init_db(resolve_db_path(args))
    if args.json:
        print_json(
            {
                "db_path": str(resolved),
                "schema_version": db.SCHEMA_VERSION,
            }
        )
    else:
        print(f"initialized {resolved}")
    return 0


class _Terminated(BaseException):
    """Raised from a SIGTERM/SIGHUP handler so ``finally`` blocks still run."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _raise_terminated(signum: int, _frame: object) -> None:
    raise _Terminated(signum)


@contextmanager
def _terminate_cleanly() -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into an exception for the duration of a command.

    Without this, a killed process (or a closed terminal) skips editor-draft
    cleanup and leaves private text in the temporary directory.
    """
    signums = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signums.append(signal.SIGHUP)
    previous = {}
    try:
        for signum in signums:
            previous[signum] = signal.signal(signum, _raise_terminated)
    except ValueError:  # not the main thread; keep default handling
        pass
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns an integer exit code."""
    # Footers and hints go to stderr after the listing they describe; when both
    # streams share a pipe, a block-buffered stdout would print them first.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        with _terminate_cleanly(), recover_editor_drafts():
            code = int(func(args) or 0)
        sys.stdout.flush()
        return code
    except db.DBError as exc:
        print_err(str(exc))
        return 1
    except sqlite3.Error as exc:
        print_err(f"database error: {exc}")
        return 1
    except BrokenPipeError:
        # The reader went away (e.g. `| head`). Point stdout at devnull so the
        # interpreter's final flush does not raise a second time.
        _silence_stdout()
        return 1
    except KeyboardInterrupt:
        print_err("interrupted")
        return 128 + signal.SIGINT
    except _Terminated as exc:
        print_err("terminated")
        return 128 + exc.signum


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass
