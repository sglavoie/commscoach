"""SQLite persistence for commscoach: schema, CRUD, and aggregated contexts.

The package is split by table:

- :mod:`commscoach.db.schema` owns connections, DDL, and idempotent init.
- :mod:`commscoach.db.people`, :mod:`commscoach.db.entries`,
  :mod:`commscoach.db.feedback`, and :mod:`commscoach.db.reviews` own CRUD.
- :mod:`commscoach.db.context` aggregates the skill-facing read models.

Each public mutator opens its own transaction via ``with conn:`` so callers do
not need to manage transactions explicitly.
"""

from __future__ import annotations

from ..models import (
    Entry,
    EntryContext,
    Feedback,
    Person,
    Review,
    ReviewContext,
    ReviewEntryContext,
)
from .context import get_entry_context, get_review_context
from .entries import (
    create_entry,
    delete_entry,
    get_entry,
    list_entries,
    search_entries,
    update_entry,
)
from .errors import (
    DBError,
    DuplicateHandleError,
    DuplicateSelfError,
    EntryNotFoundError,
    FeedbackNotFoundError,
    MissingSelfError,
    PersonNotFoundError,
    ReviewNotFoundError,
)
from .feedback import create_feedback, get_feedback, list_feedback
from .people import (
    create_person,
    delete_person,
    get_person,
    get_person_by_handle,
    get_self_person,
    list_people,
    set_self,
    update_person,
)
from .reviews import create_review, delete_review, get_review, list_reviews
from .schema import (
    SCHEMA_VERSION,
    connect,
    get_schema_version,
    init_db,
    validate_schema,
)

__all__ = [
    "SCHEMA_VERSION",
    "DBError",
    "DuplicateHandleError",
    "DuplicateSelfError",
    "Entry",
    "EntryContext",
    "EntryNotFoundError",
    "Feedback",
    "FeedbackNotFoundError",
    "MissingSelfError",
    "Person",
    "PersonNotFoundError",
    "Review",
    "ReviewContext",
    "ReviewEntryContext",
    "ReviewNotFoundError",
    "connect",
    "create_entry",
    "create_feedback",
    "create_person",
    "create_review",
    "delete_entry",
    "delete_person",
    "delete_review",
    "get_entry",
    "get_entry_context",
    "get_feedback",
    "get_person",
    "get_person_by_handle",
    "get_review",
    "get_review_context",
    "get_schema_version",
    "get_self_person",
    "init_db",
    "list_entries",
    "list_feedback",
    "list_people",
    "list_reviews",
    "search_entries",
    "set_self",
    "update_entry",
    "update_person",
    "validate_schema",
]
