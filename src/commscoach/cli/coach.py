"""``commscoach coach``: get coaching without leaving the terminal.

The coaching itself is the ``/commscoach`` skill running in Claude Code. This
command starts it for you: ``commscoach coach 42`` opens Claude Code on
``/commscoach 42``, and a bare ``commscoach coach`` picks the oldest entry still
waiting for coaching. ``--print`` runs it headless and prints the result,
allowing only ``commscoach`` commands.

``COMMSCOACH_COACH_CMD`` replaces the launcher; it is split like a shell line
and ``{prompt}`` is replaced by the request (appended when absent), so
``COMMSCOACH_COACH_CMD='my-claude --model opus'`` works.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys

from .. import db
from . import style
from .common import open_db, resolve_db_path, row_id

ENV_COMMAND = "COMMSCOACH_COACH_CMD"
ENV_PROMPT = "COMMSCOACH_COACH_PROMPT"
ALLOWED_TOOLS = "Bash(commscoach:*)"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "coach",
        help="Open the /commscoach skill in Claude Code on an entry (default: oldest uncoached).",
        description=(
            "Start the /commscoach coaching skill in Claude Code. Without ENTRY_ID, "
            "coaches the oldest entry still waiting for feedback."
        ),
    )
    parser.add_argument("entry_id", nargs="?", type=row_id, metavar="ENTRY_ID")
    parser.add_argument(
        "--print",
        "-p",
        action="store_true",
        dest="headless",
        help="Run headless and print the coaching (only commscoach commands are allowed).",
    )
    parser.set_defaults(func=cmd_coach)


def find_claude() -> str | None:
    return shutil.which("claude")


def launcher() -> list[str] | None:
    """The command that runs Claude Code, or ``None`` when none is available."""
    custom = os.environ.get(ENV_COMMAND, "").strip()
    if custom:
        return shlex.split(custom)
    found = find_claude()
    return [found] if found else None


def available() -> bool:
    return launcher() is not None


def prompt_after_log_enabled() -> bool:
    value = os.environ.get(ENV_PROMPT)
    return value is None or value.strip().lower() not in {"0", "false", "no", "off"}


def request(args: argparse.Namespace, entry_id: int) -> str:
    text = f"/commscoach {entry_id}"
    if getattr(args, "db", None):
        text += f" (use --db {shlex.quote(str(resolve_db_path(args)))} on every commscoach command)"
    return text


def build_command(base: list[str], prompt: str, *, headless: bool) -> list[str]:
    if any("{prompt}" in word for word in base):
        command = [word.replace("{prompt}", prompt) for word in base]
    else:
        command = [*base, prompt]
    if headless and not any("{prompt}" in word for word in base):
        command[len(base) : len(base)] = ["-p", "--allowedTools", ALLOWED_TOOLS]
    return command


def run(args: argparse.Namespace, entry_id: int, *, headless: bool = False) -> int:
    base = launcher()
    if base is None:
        raise db.DBError(
            "Claude Code ('claude') is not on PATH; install it, set "
            f"{ENV_COMMAND}, or ask your coach directly: /commscoach {entry_id}"
        )
    command = build_command(base, request(args, entry_id), headless=headless)
    sys.stdout.flush()
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        raise db.DBError(f"could not start {command[0]}: {exc.strerror or exc}") from exc


def cmd_coach(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        if args.entry_id is not None:
            if db.get_entry(conn, args.entry_id) is None:
                raise db.EntryNotFoundError(args.entry_id)
            entry_id = args.entry_id
        else:
            waiting = db.search_entries(conn, without_feedback=True, oldest_first=True, limit=1)
            if not waiting:
                print(style.good("✓") + " All caught up: every entry has coaching")
                return 0
            entry_id = waiting[0].id
            if not args.json and style.pretty():
                print(style.muted(f"Coaching the oldest entry waiting: {entry_id}"))
    return run(args, entry_id, headless=args.headless)


def offer_after_log(args: argparse.Namespace, entry_id: int) -> int | None:
    """Ask "Coach it now?" after ``log`` on an interactive terminal.

    Returns the coach's exit code when it ran, ``None`` when it was not offered
    or the answer was no.
    """
    if (
        getattr(args, "json", False)
        or not style.pretty()
        or not prompt_after_log_enabled()
        or not available()
        or not _interactive()
    ):
        return None
    try:
        answer = input(style.paint("Coach it now? ", "bold") + style.muted("[Y/n] "))
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if answer.strip().lower() not in {"", "y", "yes"}:
        return None
    return run(args, entry_id)


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False
