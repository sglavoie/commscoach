"""Command-line interface for commscoach.

:mod:`commscoach.cli.app` owns the parser and the ``commscoach`` console-script
entry point; :mod:`commscoach.cli.common` owns the shared helpers; each other
module owns one command group's parser, handlers, and output formatting.
"""

from __future__ import annotations

from .app import PROG, build_parser, cmd_init, main
from .common import (
    confirm,
    open_db,
    print_err,
    print_json,
    read_body,
    resolve_db_path,
)

__all__ = [
    "PROG",
    "build_parser",
    "cmd_init",
    "confirm",
    "main",
    "open_db",
    "print_err",
    "print_json",
    "read_body",
    "resolve_db_path",
]
