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


def goal_to_dict(goal: db.Goal) -> dict[str, Any]:
    return {
        "id": goal.id,
        "title": goal.title,
        "detail": goal.detail,
        "status": goal.status,
        "person": person_to_dict(goal.person) if goal.person is not None else None,
        "review_id": goal.review.id if goal.review is not None else None,
        "outcome": goal.outcome,
        "closed_at": goal.closed_at,
        "created_at": goal.created_at,
        "updated_at": goal.updated_at,
        "is_open": goal.is_open,
    }


def stats_to_dict(stats: db.Stats, *, weeks: int | None = None) -> dict[str, Any]:
    buckets = stats.weekly_buckets if weeks is None else stats.weekly_buckets[-weeks:]
    if weeks == 0:
        buckets = ()
    coverage = (
        stats.entries_with_feedback / stats.entry_count
        if stats.entry_count
        else 0.0
    )
    return {
        "entry_count": stats.entry_count,
        "people_count": stats.people_count,
        "tag_count": stats.tag_count,
        "feedback_count": stats.feedback_count,
        "review_count": stats.review_count,
        "entries_with_feedback": stats.entries_with_feedback,
        "feedback_coverage": coverage,
        "open_goal_count": stats.open_goal_count,
        "closed_goal_count": stats.closed_goal_count,
        "first_entry": stats.first_entry,
        "last_entry": stats.last_entry,
        "last_review": stats.last_review,
        "entries_per_week": stats.entries_per_week,
        "current_weekly_streak": stats.current_weekly_streak,
        "longest_weekly_streak": stats.longest_weekly_streak,
        "weekly_buckets": [
            {"week": week, "count": count} for week, count in buckets
        ],
        "top_tags": [
            {"name": name, "count": count} for name, count in stats.top_tags
        ],
        "top_sources": [
            {"name": name, "count": count} for name, count in stats.top_sources
        ],
        "top_people": [
            {"handle": handle, "count": count}
            for handle, count in stats.top_people
        ],
    }
