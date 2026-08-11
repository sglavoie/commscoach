"""Tag normalization, upsert, and orphan collection."""

from __future__ import annotations

import sqlite3
from typing import Iterable


def normalize_tag(name: str) -> str:
    """Normalize a tag for storage: stripped, lowercased."""
    return name.strip().lower()


def normalize_tag_list(tags: Iterable[str]) -> list[str]:
    """Normalize and de-duplicate a tag iterable, dropping empties."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = normalize_tag(tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def upsert_tag(conn: sqlite3.Connection, name: str) -> int:
    """Insert tag if missing and return its id."""
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row["id"]


def gc_orphan_tags(conn: sqlite3.Connection) -> None:
    """Drop tag rows that no entry references anymore.

    Called after entry deletion or tag replacement so the ``tags`` table stays
    in sync with active links instead of accumulating dead names.
    """
    conn.execute(
        "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM entry_tags)"
    )
