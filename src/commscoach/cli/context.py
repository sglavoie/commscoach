"""The ``commscoach context`` command: skill-facing JSON for one entry."""

from __future__ import annotations

import argparse
from typing import Any

from .. import db
from .common import non_negative_int, open_db, print_json
from .serializers import (
    entry_to_dict,
    feedback_to_dict,
    goal_to_dict,
    person_to_dict,
    review_to_dict,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    context_parser = subparsers.add_parser(
        "context",
        help="Emit the skill-facing JSON context for one entry (requires self).",
    )
    context_parser.add_argument("entry_id", type=int, metavar="ENTRY_ID")
    context_parser.add_argument(
        "--history",
        type=non_negative_int,
        default=3,
        metavar="N",
        help="Number of prior entries to include per linked person.",
    )
    context_parser.add_argument(
        "--prior-review-limit",
        type=non_negative_int,
        default=1,
        metavar="N",
        help="Number of prior review syntheses to include.",
    )
    context_parser.set_defaults(func=cmd_context)


def _context_to_dict(context: db.EntryContext) -> dict[str, Any]:
    return {
        "entry": entry_to_dict(context.entry),
        "self": person_to_dict(context.self_person),
        "people": [
            {
                **person_to_dict(person_context.person),
                "notes": person_context.notes,
                "history": [
                    {
                        "entry": entry_to_dict(item.entry),
                        "feedback": [
                            feedback_to_dict(feedback)
                            for feedback in item.feedback
                        ],
                    }
                    for item in person_context.history
                ],
            }
            for person_context in context.people
        ],
        "prior_feedback": [feedback_to_dict(f) for f in context.prior_feedback],
        "goals": [goal_to_dict(goal) for goal in context.goals],
        "prior_reviews": [
            review_to_dict(review) for review in context.prior_reviews
        ],
    }


def cmd_context(args: argparse.Namespace) -> int:
    with open_db(args) as conn:
        context = db.get_entry_context(
            conn,
            args.entry_id,
            history_limit=args.history,
            prior_review_limit=args.prior_review_limit,
        )
    print_json(_context_to_dict(context))
    return 0
