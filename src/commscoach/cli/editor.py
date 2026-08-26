"""Securely compose Markdown bodies in the user's preferred editor."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .. import db

COMMENT_MARKER = "#>"


def editor_command() -> list[str]:
    """Return ``$VISUAL``, ``$EDITOR``, or ``vi`` as a command vector."""
    configured = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        command = shlex.split(configured)
    except ValueError as exc:
        raise db.DBError(f"invalid editor command: {exc}") from exc
    if not command:
        raise db.DBError("editor command must not be empty")
    return command


def strip_comment_lines(text: str) -> str:
    """Remove editor instruction lines without treating Markdown as comments."""
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.startswith(COMMENT_MARKER)
    )


def compose_in_editor(
    *,
    template: str,
    preamble: str,
    runner: Callable[[list[str]], int] | None = None,
) -> str:
    """Open a secure temporary Markdown file and return its composed body."""
    fd, raw_path = tempfile.mkstemp(prefix="commscoach-", suffix=".md")
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        comment_preamble = "\n".join(
            f"{COMMENT_MARKER} {line}" if line else COMMENT_MARKER
            for line in preamble.splitlines()
        )
        initial = comment_preamble
        if template:
            initial = f"{initial}\n\n{template}" if initial else template
        if initial and not initial.endswith("\n"):
            initial += "\n"
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(initial)

        command = [*editor_command(), str(path)]
        exit_code = (runner or subprocess.call)(command)
        if exit_code != 0:
            raise db.DBError(f"editor exited with status {exit_code}")

        body = strip_comment_lines(path.read_text(encoding="utf-8")).strip()
        if not body:
            raise db.DBError("editor produced an empty body")
        return body
    except OSError as exc:
        raise db.DBError(f"cannot compose body in editor: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
