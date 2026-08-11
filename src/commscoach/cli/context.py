"""The ``commscoach context`` command: skill-facing JSON for one entry."""

from __future__ import annotations

import argparse
from typing import Any

from .. import db
from .common import open_db, print_json
from .serializers import entry_to_dict, feedback_to_dict, person_to_dict


def register(subparsers: argparse._SubParsersAction) -> None:
    context_parser = subparsers.add_parser(
        "context",
        help="Emit the skill-facing JSON context for one entry (requires self).",
    )
    context_parser.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    context_parser.set_defaults(func=cmd_context)


def _context_to_dict(context: db.EntryContext) -> dict[str, Any]:
    return {
        "entry": entry_to_dict(context.entry),
        "self": person_to_dict(context.self_person),
        "people": [person_to_dict(p) for p in context.people],
        "prior_feedback": [feedback_to_dict(f) for f in context.prior_feedback],
    }


def cmd_context(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        context = db.get_entry_context(conn, args.entry_id)
    print_json(_context_to_dict(context))
    return 0
