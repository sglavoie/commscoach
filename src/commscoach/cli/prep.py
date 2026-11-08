"""The "Before you talk" briefing at the top of ``person prep`` on a terminal.

Prep used to open with every stored note in full. The briefing distills it
into what you would want on an index card a minute before the conversation:
how recently you talked, the goals to keep in mind, what the last coaching
told you to try, open follow-ups, and your private note on the person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .. import db
from . import pretty_views, style
from .common import first_prose_line, parse_date_bound, plural

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TRY = re.compile(r"try next time|next time|try this|to practice", re.IGNORECASE)
_FOLLOW = re.compile(r"follow[- ]?ups?|open threads|to revisit", re.IGNORECASE)
LOOKBACK = 10
MAX_ITEMS = 3


def section_items(body: str, pattern: re.Pattern[str]) -> list[str]:
    """Items under the first Markdown heading matching ``pattern``.

    List items are returned one per element; a section written as prose
    yields its paragraphs instead. Whitespace is normalized.
    """
    items: list[str] = []
    inside = False
    paragraph: list[str] = []
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            if inside:
                break
            inside = bool(pattern.search(heading.group(1)))
            continue
        if not inside:
            continue
        if item := _ITEM.match(line):
            if paragraph:
                items.append(" ".join(paragraph))
                paragraph = []
            items.append(" ".join(item.group(1).split()))
        elif line.strip():
            if items and not paragraph and line.startswith((" ", "\t")):
                items[-1] += " " + " ".join(line.split())  # wrapped list item
            else:
                paragraph.append(" ".join(line.split()))
        elif paragraph:
            items.append(" ".join(paragraph))
            paragraph = []
    if paragraph:
        items.append(" ".join(paragraph))
    return items


@dataclass
class Briefing:
    person: db.Person
    interactions: int = 0
    this_month: int = 0
    last_talked: str | None = None
    goals: tuple[db.Goal, ...] = ()
    try_next: list[str] = field(default_factory=list)
    try_source: tuple[db.Feedback, db.Entry] | None = None
    follow_ups: list[str] = field(default_factory=list)
    note: str | None = None


def gather(conn, person: db.Person, note: db.PersonNote | None) -> Briefing:  # type: ignore[no-untyped-def]
    activity = db.get_people_activity(conn).get(person.id)
    today = datetime.now().date()
    briefing = Briefing(
        person=person,
        interactions=activity.interaction_count if activity else 0,
        last_talked=activity.last_logged_interaction if activity else None,
        this_month=db.count_search_entries(
            conn,
            person_handle=person.handle,
            since=parse_date_bound(today.replace(day=1).isoformat(), flag="--since"),
        ),
        goals=db.list_goals(conn, person_handle=person.handle, include_general=True),
        note=first_prose_line(note.body) if note is not None else None,
    )
    for entry in db.search_entries(conn, person_handle=person.handle, limit=LOOKBACK):
        for feedback in reversed(db.list_feedback(conn, entry.id)):
            if not briefing.try_next:
                items = section_items(feedback.body, _TRY)
                if items:
                    briefing.try_next = items[:MAX_ITEMS]
                    briefing.try_source = (feedback, entry)
            if not briefing.follow_ups:
                briefing.follow_ups = section_items(feedback.body, _FOLLOW)[:MAX_ITEMS]
        if briefing.try_next and briefing.follow_ups:
            break
    return briefing


def render(briefing: Briefing) -> str:
    person = briefing.person
    title = f"{person.name} ({'@' + person.handle})" if person.name else f"@{person.handle}"
    lines = [style.heading(f"Before you talk to {title}")]
    width = style.term_width()
    label_w = 13

    def row(label: str, values: list[str]) -> None:
        if not values:
            return
        lead = "  " + style.muted(label.ljust(label_w))
        for index, value in enumerate(values):
            lines.append((lead if index == 0 else " " * (label_w + 2)) + value)

    if briefing.last_talked:
        talked = style.join(
            [
                style.relative(briefing.last_talked),
                plural(briefing.interactions, "conversation"),
                f"{briefing.this_month} this month",
            ]
        )
    else:
        talked = "no conversation logged yet"
    row("Last talked", [talked])
    row(
        "Remember",
        [
            f"{pretty_views.goal_glyph(goal)} {goal.title}"
            + (style.muted("  (general)") if goal.person is None else "")
            for goal in sorted(briefing.goals, key=lambda g: g.person is None)[:4]
        ]
        or [style.muted("no active goal; set one with 'commscoach goal add'")],
    )
    if briefing.try_next:
        room = width - label_w - 4
        row("Last advice", [f"→ {style.clip(item, room)}" for item in briefing.try_next])
        feedback, entry = briefing.try_source  # type: ignore[misc]
        lines.append(
            " " * (label_w + 2)
            + style.muted(
                f"from feedback {feedback.id} on entry {entry.id}, "
                f"{style.relative(entry.occurred_at or entry.created_at)}"
            )
        )
    row("Follow up", [f"○ {style.clip(item, width - label_w - 4)}" for item in briefing.follow_ups])
    if briefing.note:
        row("Your notes", [style.paint(style.clip(briefing.note, width - label_w - 2), "italic")])
    return "\n".join(lines)
