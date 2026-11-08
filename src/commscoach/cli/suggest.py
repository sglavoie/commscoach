"""Did-you-mean hints for mistyped subcommands, handles, and tags."""

from __future__ import annotations

import argparse
import difflib
import re
from collections.abc import Iterable

from .. import db

# Words people reach for that the CLI spells differently.
SYNONYMS = {
    "done": "close",
    "finish": "close",
    "complete": "close",
    "new": "add",
    "create": "add",
    "ls": "list",
    "delete": "rm",
    "remove": "rm",
    "del": "rm",
    "find": "search",
    "grep": "search",
    "view": "show",
    "get": "show",
    "open": "show",
    "update": "edit",
    "modify": "edit",
    "people": "person",
    "persons": "person",
    "entries": "entry",
    "goals": "goal",
    "tags": "tag",
    "reviews": "review",
    "note": "log",
    "add": "log",
}

_CHOICE_RE = re.compile(r"invalid choice: '?([^'\s]+)'? \(choose from (.+)\)")


def close_matches(word: str, choices: Iterable[str], *, limit: int = 2) -> list[str]:
    options = list(choices)
    picks = []
    synonym = SYNONYMS.get(word.lower())
    if synonym in options:
        picks.append(synonym)
    folded = {option.casefold(): option for option in options}
    for match in difflib.get_close_matches(word.casefold(), folded, n=limit, cutoff=0.6):
        if folded[match] not in picks:
            picks.append(folded[match])
    picks.extend(o for o in options if o.startswith(word) and word and o not in picks)
    return picks[:limit]


def _quoted(options: list[str]) -> str:
    return " or ".join(f"'{option}'" for option in options)


def choice_hint(message: str) -> str | None:
    """Turn argparse's invalid-choice message into a suggestion, if any fits."""
    match = _CHOICE_RE.search(message)
    if not match:
        return None
    word = match.group(1)
    choices = [c.strip().strip("'") for c in match.group(2).split(",")]
    picks = close_matches(word, choices)
    return f"did you mean {_quoted(picks)}?" if picks else None


class SuggestingParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose invalid-choice errors suggest the closest choice."""

    def error(self, message: str):  # type: ignore[no-untyped-def]
        hint = choice_hint(message)
        if hint:
            message = f"{message}\n  {hint}"
        super().error(message)


def not_found_hint(exc: db.DBError, conn) -> str | None:  # type: ignore[no-untyped-def]
    """Suggest an existing handle or tag for a not-found error."""
    if isinstance(exc, db.PersonNotFoundError) and isinstance(exc.ref, str):
        ref = exc.ref
        people = db.list_people(conn)
        picks = close_matches(ref, [p.handle for p in people])
        lowered = ref.casefold()
        for person in people:
            if person.name and lowered in person.name.casefold() and person.handle not in picks:
                picks.append(person.handle)
        if picks:
            return "did you mean " + " or ".join(f"@{h}" for h in picks[:3]) + "?"
        return None
    if isinstance(exc, db.TagNotFoundError):
        picks = close_matches(exc.name, [t.name for t in db.list_tags(conn)])
        if picks:
            return "did you mean " + " or ".join(f"#{t}" for t in picks) + "?"
    return None
