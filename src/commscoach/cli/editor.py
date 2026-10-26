"""Securely compose Markdown bodies in the user's preferred editor."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator

from .. import db

COMMENT_MARKER = "#>"
_drafts: ContextVar[list[tuple[Path, str]] | None] = ContextVar("editor_drafts", default=None)


def _finish_draft(path: Path, *, recover: bool, template: str = "") -> None:
    """Keep failed drafts holding new text, without masking the original error.

    A draft that is empty or still equal to its starting ``template`` holds
    nothing worth recovering (e.g. the user quit the editor with ``:cq``), and
    keeping it would only leave a private copy of the original text behind.
    """
    try:
        if recover:
            raw = path.read_bytes()
            # Preserve undecodable text too: a broken encoding must not lose work.
            body = strip_comment_lines(raw.decode("utf-8", errors="replace")).strip()
            if body and body != template.strip():
                path.chmod(0o600)
                print(f"Draft saved for recovery: {path}", file=sys.stderr)
                return
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"Could not finalize draft {path}: {exc}", file=sys.stderr)


@contextmanager
def recover_editor_drafts() -> Iterator[None]:
    """Retain editor files until the entire CLI command succeeds."""
    drafts: list[tuple[Path, str]] = []
    token = _drafts.set(drafts)
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        _drafts.reset(token)
        for path, template in drafts:
            _finish_draft(path, recover=not succeeded, template=template)


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
        line for line in text.splitlines(keepends=True) if not line.startswith(COMMENT_MARKER)
    )


def compose_in_editor(
    *,
    template: str,
    preamble: str,
    runner: Callable[[list[str]], int] | None = None,
) -> str:
    """Compose privately; failed drafts survive and CLI drafts await save."""
    fd, raw_path = tempfile.mkstemp(prefix="commscoach-", suffix=".md")
    path = Path(raw_path)
    drafts = _drafts.get()
    if drafts is not None:
        drafts.append((path, template))
    succeeded = False
    try:
        os.fchmod(fd, 0o600)
        comment_preamble = "\n".join(
            f"{COMMENT_MARKER} {line}" if line else COMMENT_MARKER for line in preamble.splitlines()
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
        succeeded = True
        return body
    except (OSError, UnicodeError) as exc:
        raise db.DBError(f"cannot compose body in editor: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if drafts is None:
            _finish_draft(path, recover=not succeeded, template=template)
