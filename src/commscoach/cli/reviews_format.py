"""Serialization and rendering helpers for the ``review`` subcommands."""

from __future__ import annotations

import argparse
from typing import Any

from .. import db
from .common import (
    first_prose_line,
    format_scope,
    print_json,
    search_excerpt,
    truncate,
)
from .serializers import (
    entry_to_dict,
    feedback_to_dict,
    goal_to_dict,
    person_to_dict,
    review_to_dict,
)



def review_context_to_dict(context: db.ReviewContext) -> dict[str, Any]:
    return {
        "self": {**person_to_dict(context.self_person), "notes": context.self_notes},
        # The scope block grew additively in session 6: ``kind``/``limit``/
        # ``entry_count`` keep their original meaning so no existing skill
        # parse breaks, and the axis keys are ``null`` for the kinds that do
        # not own them.
        "scope": {
            "kind": context.scope_kind,
            "limit": context.scope_limit,
            "entry_count": len(context.entries),
            "total_entry_count": context.total_entry_count,
            "entries_truncated": len(context.entries) < context.total_entry_count,
            "person": (
                {
                    **person_to_dict(context.scope_person),
                    "notes": context.scope_person_notes,
                }
                if context.scope_person is not None
                else None
            ),
            "tag": context.scope_tag,
            "since": context.scope_since,
            "until": context.scope_until,
        },
        "entries": [
            {
                "entry": entry_to_dict(item.entry),
                "feedback": [feedback_to_dict(feedback) for feedback in item.feedback],
            }
            for item in context.entries
        ],
        "prior_reviews": [
            review_to_dict(review) for review in context.prior_reviews
        ],
        "goals": [goal_to_dict(goal) for goal in context.goals],
        "goal_summary": {
            "limit": context.goal_limit,
            "count": len(context.goals),
            "total_count": context.total_goal_count,
            "truncated": len(context.goals) < context.total_goal_count,
        },
    }


def format_review_human(review: db.Review) -> str:
    header_parts = [
        f"[{review.id}]",
        f"created {review.created_at}",
        f"scope {format_scope(review)}",
    ]
    if review.scope_limit is not None:
        header_parts.append(f"context limit {review.scope_limit}")
    if review.updated_at != review.created_at:
        header_parts.append(f"updated {review.updated_at}")
    lines = [" ".join(header_parts)]
    if review.entries:
        entry_ids = ", ".join(str(entry.id) for entry in review.entries)
        lines.append(f"entries: {entry_ids}")
    lines.append("---")
    lines.append(review.body)
    return "\n".join(lines)


def format_review_short(review: db.Review, *, query: str | None = None) -> str:
    entry_ids = ",".join(str(entry.id) for entry in review.entries)
    parts = [
        f"[{review.id}]",
        review.created_at,
        f"scope:{format_scope(review)}",
    ]
    if review.scope_limit is not None:
        parts.append(f"context-limit:{review.scope_limit}")
    if entry_ids:
        parts.append(f"entries:{entry_ids}")
    parts.append(
        search_excerpt(review.body, query) if query is not None
        else truncate(first_prose_line(review.body))
    )
    return " ".join(parts)


def emit_review(
    args: argparse.Namespace,
    review: db.Review,
    *,
    human_prefix: str | None = None,
) -> None:
    if args.json:
        print_json(review_to_dict(review))
        return
    if human_prefix:
        print(f"{human_prefix} review {review.id}")
    print(format_review_human(review))


def emit_reviews(
    args: argparse.Namespace, reviews: tuple[db.Review, ...], *, query: str | None = None,
) -> None:
    if args.json:
        print_json([review_to_dict(review) for review in reviews])
        return
    if not reviews:
        print("(no reviews)")
        return
    for review in reviews:
        print(format_review_short(review, query=query))
