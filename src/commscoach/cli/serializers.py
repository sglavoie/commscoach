"""JSON-shape converters shared by the command modules."""

from __future__ import annotations

from typing import Any

from .. import db


def person_to_dict(person: db.Person) -> dict[str, Any]:
    return {
        "id": person.id,
        "handle": person.handle,
        "name": person.name,
        "is_self": person.is_self,
    }


def entry_to_dict(entry: db.Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "body": entry.body,
        "source": entry.source,
        "occurred_at": entry.occurred_at,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "tags": list(entry.tags),
        "people": [person_to_dict(p) for p in entry.people],
    }


def feedback_to_dict(feedback: db.Feedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "entry_id": feedback.entry_id,
        "body": feedback.body,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
    }


def review_to_dict(review: db.Review) -> dict[str, Any]:
    return {
        "id": review.id,
        "body": review.body,
        "scope_kind": review.scope_kind,
        "scope_limit": review.scope_limit,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
        "entry_ids": [entry.id for entry in review.entries],
        "entries": [entry_to_dict(entry) for entry in review.entries],
    }
