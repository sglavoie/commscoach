"""Typed CRUD errors the CLI surfaces as exit-code-1 failures."""

from __future__ import annotations


class DBError(Exception):
    """Base class for CRUD errors the CLI surfaces as exit-code-1 failures."""


class EntryNotFoundError(DBError):
    def __init__(self, entry_id: int) -> None:
        super().__init__(f"entry {entry_id} not found")
        self.entry_id = entry_id


class PersonNotFoundError(DBError):
    def __init__(self, ref: int | str) -> None:
        super().__init__(f"person {ref!r} not found")
        self.ref = ref


class FeedbackNotFoundError(DBError):
    def __init__(self, feedback_id: int) -> None:
        super().__init__(f"feedback {feedback_id} not found")
        self.feedback_id = feedback_id


class ReviewNotFoundError(DBError):
    def __init__(self, review_id: int) -> None:
        super().__init__(f"review {review_id} not found")
        self.review_id = review_id


class TagNotFoundError(DBError):
    def __init__(self, name: str, *, hint: str | None = None) -> None:
        message = f"tag {name!r} not found"
        if hint is not None:
            message = f"{message}; {hint}"
        super().__init__(message)
        self.name = name


class DuplicateTagError(DBError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"tag {name!r} already exists;"
            " use 'commscoach tag merge' to combine the two"
        )
        self.name = name


class GoalNotFoundError(DBError):
    def __init__(self, goal_id: int) -> None:
        super().__init__(f"goal {goal_id} not found")
        self.goal_id = goal_id


class DuplicateHandleError(DBError):
    def __init__(self, handle: str) -> None:
        super().__init__(f"handle {handle!r} already exists")
        self.handle = handle


class DuplicateSelfError(DBError):
    def __init__(self) -> None:
        super().__init__("another person is already marked self")


class MissingSelfError(DBError):
    def __init__(self) -> None:
        super().__init__("no self person configured")
