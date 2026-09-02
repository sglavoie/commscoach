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
    Goal,
    Person,
    PersonContext,
    PersonHistoryItem,
    PersonNote,
    Review,
    ReviewContext,
    ReviewEntryContext,
    Stats,
)
from .backup import backup_database, prune_backups
from .context import get_entry_context, get_review_context
from .dump import (
    FORMAT_VERSION,
    DatabaseNotEmptyError,
    DumpFormatError,
    dump_to_dict,
    load_from_dict,
    validate_dump,
)
from .entries import (
    create_entry,
    delete_entry,
    get_entry,
    list_entries,
    list_entries_with_person,
    search_entries,
    update_entry,
)
from .errors import (
    DBError,
    DuplicateHandleError,
    DuplicateSelfError,
    EntryNotFoundError,
    FeedbackNotFoundError,
    GoalNotFoundError,
    MissingSelfError,
    PersonNotFoundError,
    ReviewNotFoundError,
)
from .feedback import (
    create_feedback,
    delete_feedback,
    get_feedback,
    list_feedback,
    update_feedback,
)
from .goals import (
    close_goal,
    create_goal,
    delete_goal,
    get_goal,
    list_goals,
    update_goal,
)
from .notes import (
    clear_person_note,
    get_person_note,
    get_person_notes,
    set_person_note,
)
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
from .reviews import (
    create_review,
    delete_review,
    get_review,
    list_reviews,
    update_review,
)
from .schema import (
    SCHEMA_VERSION,
    connect,
    get_schema_version,
    init_db,
    validate_schema,
)
from .stats import get_stats

__all__ = [
    "SCHEMA_VERSION",
    "DBError",
    "DatabaseNotEmptyError",
    "DuplicateHandleError",
    "DuplicateSelfError",
    "DumpFormatError",
    "Entry",
    "EntryContext",
    "EntryNotFoundError",
    "Feedback",
    "FeedbackNotFoundError",
    "FORMAT_VERSION",
    "Goal",
    "GoalNotFoundError",
    "MissingSelfError",
    "Person",
    "PersonContext",
    "PersonHistoryItem",
    "PersonNote",
    "PersonNotFoundError",
    "Review",
    "ReviewContext",
    "ReviewEntryContext",
    "ReviewNotFoundError",
    "Stats",
    "backup_database",
    "connect",
    "close_goal",
    "clear_person_note",
    "create_entry",
    "create_feedback",
    "create_goal",
    "create_person",
    "create_review",
    "delete_entry",
    "delete_feedback",
    "delete_goal",
    "delete_person",
    "delete_review",
    "dump_to_dict",
    "get_entry",
    "get_entry_context",
    "get_feedback",
    "get_goal",
    "get_person",
    "get_person_by_handle",
    "get_person_note",
    "get_person_notes",
    "get_review",
    "get_review_context",
    "get_schema_version",
    "get_self_person",
    "get_stats",
    "init_db",
    "list_entries",
    "list_entries_with_person",
    "list_feedback",
    "list_goals",
    "list_people",
    "list_reviews",
    "load_from_dict",
    "prune_backups",
    "search_entries",
    "set_person_note",
    "set_self",
    "update_entry",
    "update_feedback",
    "update_goal",
    "update_person",
    "update_review",
    "validate_dump",
    "validate_schema",
]
