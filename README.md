# commscoach

Personal communications coach: log interactions with people, attach context, generate feedback from an AI skill, track coaching goals, and keep a durable personal archive.

## Status

Ready to use. The CLI, SQLite schema, stored feedback and reviews, scoped reviews, tag management, goals, diagnostics, backups, JSON export/import, stats, skill integration, and packaging are in place. Current scope intentionally excludes encryption.

## Everyday use

| When you want to… | Command |
| --- | --- |
| Prepare for a conversation | `commscoach person show jdoe --recent 5 --with-feedback` |
| Include broader relationship advice | `commscoach person show jdoe --recent 5 --with-feedback --reviews 2` |
| Find the oldest entries awaiting coaching | `commscoach entry search --no-feedback --oldest-first --limit 5` |
| Revisit an interaction and its advice | `commscoach entry show 42 --with-feedback` |
| Find past advice | `commscoach feedback search --query "lead with the ask"` |
| Find a broader review | `commscoach review search --query "lead with the ask"` |
| Review lingering goals | `commscoach goal list --oldest-first --limit 5` |

Replace the example handle and entry ID with your own. For first-time setup, see [Quick start](#quick-start).

## Layout

```
src/commscoach/   # Python package
tests/            # Test suite (`uv run pytest -q`)
```

## Development

```sh
uv venv
uv sync
uv run commscoach --help
uv run pytest -q
```

## Install globally

```sh
uv tool install --editable --force .
command -v commscoach        # -> /Users/.../.local/bin/commscoach
commscoach --version         # -> commscoach 0.1.0
commscoach --help
```

The `--editable` install resolves the package directly from this source tree, so subsequent edits take effect without re-installing.

## Global flags

The global flags `--db`, `--json`, and `--yes` can appear before or after a command, including a nested command:

```sh
commscoach --json context 42
commscoach context 42 --json
commscoach entry list --db ~/journal.sqlite3 --json
```

Use `commscoach --help` for top-level help and `commscoach <command> --help`
for subcommand-specific options.

## Default database

By default, commscoach stores its database outside cloud-synced folders:

```text
$XDG_DATA_HOME/commscoach/commscoach.sqlite3
```

When `XDG_DATA_HOME` is unset, that resolves to:

```text
~/.local/share/commscoach/commscoach.sqlite3
```

At runtime, `~` is resolved from the current user via `Path.home()`.

Overrides (in order of precedence):

1. `--db PATH` global CLI flag
2. `COMMSCOACH_DB` environment variable
3. The default path above

### Why not iCloud?

SQLite databases are a poor fit for file-sync conflict resolution. iCloud can evict the database and leave a zero-byte `.icloud` placeholder; treating the missing materialized file as a new store risks creating an empty database over the real one. Two Macs can also write before sync converges, producing a conflicted copy and splitting the journal across files. For those reasons, commscoach never selects the former iCloud path implicitly. If it finds a legacy database there and no new default database exists, it stops with explicit migration commands instead of opening or replacing it.

### Privacy

The database is created with mode `0600` and its parent directory with mode `0700`. Backups and file exports are also written with mode `0600`. Treat all of them as private personal data.

## Quick start

```sh
commscoach init
commscoach person add me --self --name "Your Name"
commscoach person add jdoe --name "John Doe"
echo "Quarterly review with John. I rambled before getting to my ask." \
  | commscoach entry add - --occurred-at yesterday --tag 1on1,review --person jdoe --source in-person
commscoach entry list
commscoach tag list
commscoach context 1                 # skill-facing JSON
commscoach feedback add 1 "Lead with the ask next time."
commscoach feedback list 1
commscoach goal add "Lead with the ask" --person jdoe
commscoach review context --limit 20 --prior-review-limit 3 # skill-facing review JSON
commscoach review add "Review: lead with the ask across recent 1:1s." --limit 20 --entry 1
commscoach review context --person jdoe --limit 10          # scoped to one person
commscoach review list
commscoach stats
commscoach doctor
commscoach backup
commscoach export                    # Markdown to stdout
commscoach export --format json --out commscoach.json
```

## Capture and dates

`entry add` body sources are a literal string, `-` for stdin, or `@PATH` for a UTF-8 file. On an interactive terminal, omitting the body opens `$VISUAL` or `$EDITOR` (falling back to `vi`). The same body-source convention applies to `entry edit --body`, `feedback add`, and `review add`; their omitted-body forms also use the editor where supported.

The entry editor includes optional prompts about what you wanted, what you said, how the other person responded, and what you want feedback on. These are `#>` instruction lines, so they are removed from the saved body; answer whichever help, or simply write your entry. Leaving only the prompts still counts as an empty draft.

Editor drafts are kept until the command succeeds. If the editor fails, you interrupt the command, or the subsequent save fails, a nonempty draft is retained with mode `0600` and its recovery path is printed to stderr (including with `--json`). Open that file to recover your text; it preserves the editor contents, including any `#>` instruction lines. Successful commands and empty drafts are cleaned up automatically. Recovery files live in the system temporary directory, so recover them before system cleanup removes them.

`person edit HANDLE --notes` also opens the editor, pre-filled with that person's private notes (or blank when unset). It uses the same draft recovery. Empty drafts are rejected; use `--clear-notes` to remove notes. Non-interactive callers must supply text, `-`, or `@PATH`.

`--occurred-at`, `--since`, and `--until` accept ISO-8601 values and natural relative dates such as `today`, `yesterday`, `3 days ago`, `last monday`, and `now`. Stored timestamps are normalized to UTC. Entries are ordered by when they occurred, falling back to creation time.

Date filters use your system's local timezone. A bare date, `today`, `yesterday`, or `last monday` covers the whole local day, including daylight-saving transitions. Explicit times and elapsed-time expressions such as `today 9am`, `now`, or `3 days ago` retain their time; timestamps with an offset use that offset. Both bounds are inclusive, and a `--since` instant after `--until` is rejected. These rules apply to entry listings, searches, stats, and review scopes:

```sh
commscoach entry list --since today --until today
commscoach entry search --since "yesterday 9am" --until "yesterday 5pm"
```

Human-readable `entry list` and `entry search` rows show the local occurrence date/time (or creation time), source when present, feedback status, text preview, tags, and people:

```text
[42] · 2026-10-02 14:30 · in-person · needs feedback · Quarterly review with John. · @jdoe
```

The status becomes `has feedback` when an entry has at least one stored feedback note. Use `entry search --no-feedback` to see only the coaching backlog. Entry JSON output keeps its existing shape.

Work through the oldest uncoached interactions first, or revisit an entry with its full coaching notes:

```sh
commscoach entry search --no-feedback --oldest-first --limit 5
commscoach entry show 42 --with-feedback
commscoach entry search --exact --query "10%"
commscoach entry search --source email --person jdoe --since "30 days ago"
```

`entry search --source` matches the complete stored channel or medium case-insensitively (`email` matches `Email`, but not `email-thread`). It combines with the existing query, tag, person, date, and feedback filters, and is applied before ordering and limiting results.

`--oldest-first` applies to `entry search` and orders matching entries by occurrence time (falling back to creation time), then ID, before applying `--limit`. Without it, results remain newest-first. `--exact` performs a literal, case-insensitive substring search, including literal `%` and `_` characters.

When `entry search --query` finds a match, human output shows the matching passage instead of just the entry's opening paragraph. Literal searches use an excerpt around the first match; token searches use SQLite's matching snippet, including for phrases, prefixes, and accent-insensitive matches. Ellipses mark omitted text. Searches without a query retain the opening preview, and JSON still returns the full entry with its existing fields.

`entry show --with-feedback` includes full feedback notes oldest-first, or an explicit empty marker when there are none. With `--json`, it adds a `feedback` array to the entry object; without this flag the JSON shape is unchanged.

`entry rm ID` includes the counts of feedback notes to delete and review links to remove in its confirmation. The reviews themselves remain. `--yes` continues to bypass confirmation.

Incremental edits let you change links without replacing the existing lists:

```sh
commscoach entry edit 42 --add-tag follow-up --remove-tag draft
commscoach entry edit 42 --add-person alice --remove-person bob
```

These flags may be repeated; tags also accept comma-separated values. Tag normalization and case-insensitive person matching follow the existing commands. Adding an existing link or removing an absent link is harmless. Unknown people being added use the existing creation confirmation; removing a link never creates or deletes the person. You cannot add and remove the same normalized tag or person in one command, or mix incremental edits with replacement/clear flags for the same kind of link.

Entry creation validates the date and database before opening the editor. Feedback creation checks that its entry exists before asking for a body.
Review creation also checks the database, every covered entry ID, and the scoped person (when supplied) before opening the editor.

## Tags

Tags live in their own table with their own normalization and orphan collection, so the vocabulary stays maintainable instead of letting one typo split a theme across your stats permanently:

```sh
commscoach tag list                           # most-used first, with entry counts
commscoach tag list --limit 10
commscoach --json tag list
commscoach tag rename conflcit conflict       # keeps every entry link
commscoach --yes tag merge conflcit conflict  # fold one tag into another, then drop it
commscoach --yes tag rm writing               # unlink from every entry, then drop it
```

Tag names are normalized and matched case-insensitively, so `Conflict` and `conflict` are the same tag and a case-only rename returns the tag unchanged.

`tag rename` onto a name that already exists is an error rather than an implicit merge; the message points at `tag merge`. `tag merge` moves every entry link onto the destination (dropping the duplicate when an entry already carried both tags), deletes the source, and prints the destination with its recomputed entry count.

`tag merge` and `tag rm` are destructive and prompt for confirmation. `--yes` works before or after the command: `commscoach tag rm writing --yes`.

`tag rename` and `tag merge` also rewrite the stored scope of any review scoped to that tag, so the review keeps naming the tag it actually covered. `tag rm` does not: a review scoped to a deleted tag keeps the old name as history.

## Review scopes

A review answers a question about a slice of the journal, and that slice is its **scope**. The scope is stored on the review, so a synthesis permanently records what it was about.

Scope is single-axis: a review covers the latest entries, *or* one person, *or* one tag, *or* one date range — never a combination. Flags from two axes are rejected (`--person cannot be combined with --tag`).

`review context` (the skill-facing JSON) and `review add` (the stored record) take the same axis flags, and the scope kind is derived from whichever flag you pass. `--scope KIND` is optional and acts as an assertion: when supplied it must agree with the axis flags, so `--scope tag --person alice` exits 1 instead of silently picking one.

Axis flags narrow entries exactly the way `entry search --person`, `--tag`, `--since`, and `--until` do — handles and tag names match case-insensitively, and the date bounds are inclusive against when the entry occurred, falling back to when it was created. `--limit` caps the entries under every scope.

Pass `review add` the same scope flags you passed `review context`, so the stored review matches the context it was generated from.

New range scopes record the resolved UTC timestamps in `since`/`until` (and stored `scope_since`/`scope_until`). When using moving relative times such as `now` or `2 weeks ago`, pass the resolved bounds from the context to `review add` to preserve the exact window. Existing reviews with date-only bounds retain their original UTC calendar-day meaning; no schema migration is needed.

### `latest` — no axis flag

"How have I been communicating lately?"

```sh
commscoach review context --limit 20 --prior-review-limit 3
commscoach review add "Fewer tangents than last month; still burying the ask." --limit 20 --entry 1 2 3
commscoach review list --scope latest
```

### `person` — `--person HANDLE`

"How am I doing with @alice?"

```sh
commscoach review context --person alice --limit 10
commscoach --json review add "Alice sync summary" --person alice --limit 10 --entry 1 2
commscoach review list --scope person --person alice
```

Entries are the ones @alice is linked to, and prior reviews are the previous person-scoped reviews of @alice.
The context also includes Alice's current private notes in `scope.person.notes` (`null` when unset), so the review can use the relationship context you have recorded. Other scopes keep `scope.person` as `null`; stored review records are unchanged.

### `tag` — `--tag NAME`

"How is my `conflict` tag trending?"

```sh
commscoach review context --tag conflict --limit 10
commscoach --json review add "Conflict patterns" --tag conflict --limit 10 --entry 1 2
commscoach review list --scope tag --tag conflict
```

The tag is normalized before matching and stored normalized, so `--tag Conflict` both scopes and stores `conflict`.

### `range` — `--since` and/or `--until`

"How did last quarter go?"

```sh
commscoach review context --since "12 weeks ago" --until today
commscoach review add "Q3 retrospective." --since "12 weeks ago" --until today --entry 4 5
commscoach review list --scope range
```

Either bound alone is enough to select the `range` scope; an unbounded range would just be `latest`. Two bounds in the wrong order (`--since` after `--until`) exit 1 on both `review context` and `review add`, before any context is emitted or any editor opens — it is the one empty scope that can only be a typo.

### What the scope changes

- **Entries** are narrowed by the axis and capped by `--limit`.
- **Prior reviews** are filtered to the same scope under `person` and `tag` only, so a review of @alice is measured against previous reviews of @alice. `latest` and `range` get the unfiltered recent listing instead: a date window is a one-off slice with no natural predecessor.
- **Goals** are never scope-filtered, only capped by `--goal-limit`. A relationship or theme review should still be measured against your general goals.

A scope that matches nothing returns an empty entry list rather than failing — "no entries with @alice yet" is an answer, not an error. An unknown person handle *is* an error, because that is a typo, and so is a reversed date range.

`review context` reports the scope in its own JSON block:

```json
"scope": {
  "kind": "person", "limit": 10, "entry_count": 2,
  "total_entry_count": 2, "entries_truncated": false,
  "person": {"id": 2, "handle": "alice", "name": "Alice", "is_self": false, "notes": null},
  "tag": null, "since": null, "until": null
}
```

`kind`, `limit`, `entry_count`, `total_entry_count`, and `entries_truncated` are always present; the four axis keys are `null` for the kinds that do not own them. `total_entry_count` counts all entries matching the scope before `--limit`; `entries_truncated` tells the coach whether some were omitted. For example, `entry_count: 20` and `total_entry_count: 63` means the review sees the latest 20 of 63 matching interactions.

The top-level `goal_summary` reports `limit`, `count`, `total_count`, and `truncated` for active goals. Its total remains global under every review scope, and excludes done or dropped goals. Zero limits return empty arrays while still reporting totals and whether records were omitted. These counts describe the context at generation time; they are not added to stored review records.

### Finding stored reviews

`review list` filters over stored rows, so its `--scope`, `--person`, and `--tag` combine with AND rather than deriving a single axis. Human output shows the scope inline as `scope:person:@alice`; Markdown export shows it as `- **Scope**: person @alice (context limit 10)`.

A person-scoped review stores the handle alongside the foreign key, so after `commscoach --yes person rm alice` the review is still findable by `review list --person alice` and still prints `@alice`. `doctor`'s `review_scope` check reports those reviews as a warning — retained history, not corruption.

The JSON review shape carries that stored handle as its own `scope_person_handle` key, beside the `scope_person` object: `--json review show`, `--json review list`, and the `prior_reviews` block of `review context` all emit it. `scope_person` is a live lookup and becomes `null` once the person is deleted, so `scope_person_handle` is what still names them.

`commscoach person edit alice --handle ally` carries the rename into that stored handle, the same way `tag rename` carries a tag rename into tag-scoped reviews. The person's review history follows them: `review list --person ally` finds it, a `review context --person ally` counts it among the prior reviews, and every surface prints `@ally`. Delete them afterwards and `@ally` — the handle they last had — is what remains as history.

## Finding past coaching and relationship context

Search feedback bodies without remembering which entry they belong to:

```sh
commscoach feedback search --query "lead with the ask"
commscoach feedback search --query "ask" --person jdoe --limit 5
commscoach --json feedback search --query "10%"
```

Feedback search uses literal, case-insensitive substring matching, including literal `%` and `_` characters. It returns the newest feedback first (creation time, then ID), with a default limit of 20. `--person` matches people linked to the feedback's entry, case-insensitively; an unknown handle is an error. The query must contain non-whitespace text. `--limit 0` returns an empty result. Human output shows feedback and entry IDs plus a preview; JSON returns the existing feedback objects.

Search previews show an excerpt around the first matching passage, including matches in headings or late in a long note. Ellipses mark omitted text; JSON always contains the full body.

Find broader advice in stored reviews with the same literal body matching:

```sh
commscoach review search --query "lead with the ask"
commscoach review search --query "ask" --person jdoe --limit 5
commscoach review search --query "conflict" --tag work --json
```

Review search returns newest-first results (creation time, then ID), with a default limit of 20 and matching excerpts in human output. `--scope`, `--person`, and `--tag` combine with AND and have the same meaning as `review list`: they filter the review's stored scope, not the people or tags on its covered entries. Reviews about a deleted person remain searchable by their preserved handle; an unmatched handle returns no reviews. Empty queries are rejected, `--limit 0` returns an empty result, and JSON returns complete review objects.

Before a conversation, see someone's notes, recent interactions, and active goals together:

```sh
commscoach person show jdoe --recent 5
commscoach person show jdoe --recent 5 --with-feedback
commscoach --json person show jdoe --recent 5
```

`--recent N` adds the latest N interactions by occurrence time (falling back to creation time), plus all active goals linked to that person. General goals and goals linked to other people are excluded. `--recent 0` shows no interactions but still includes active goals. JSON adds `recent_entries` and `active_goals` arrays only when this option is supplied; plain `person show` retains its existing output.

Add `--with-feedback` alongside `--recent N` to include full coaching notes beneath each interaction, oldest-first, or `(no feedback)` when there are none. In JSON, this adds a `feedback` array to each item in `recent_entries`. Without the flag, the existing output is unchanged.

Add `--reviews N` to include the latest N reviews scoped to that person, with their full bodies, newest-first (creation time, then ID):

```sh
commscoach person show jdoe --recent 5 --with-feedback --reviews 2
commscoach person show jdoe --reviews 2
```

This option works independently of `--recent`. It includes person-scoped reviews, not general or tag reviews that happen to cover an interaction with that person. Human output shows `(no reviews)` when none match; JSON adds a `recent_reviews` array only when `--reviews` is supplied. `--reviews 0` returns an empty array, and negative limits are rejected. Renaming a person's handle carries their review history into this view as well.

## Correcting stored coaching

Feedback notes and review bodies hold AI-generated text, so they are the records most likely to be wrong or badly worded. Both are now editable in place instead of forcing you to delete the whole entry:

```sh
commscoach feedback edit 1 --body "Good directness, but soften the opener."
commscoach feedback edit 1 --body @fixed.md
commscoach feedback edit 1 --body                 # opens $EDITOR with the current body
commscoach --yes feedback rm 1

commscoach review edit 1 --body "Corrected summary"
commscoach review edit 1 --body                   # opens $EDITOR with the current body
commscoach --yes review rm 1
```

`--body` follows the usual body-source convention — literal text, `-` for stdin, or `@PATH` for a UTF-8 file. Passing the flag with no value opens `$VISUAL`/`$EDITOR` pre-filled with the current text. An edit updates `updated_at` and leaves `created_at` alone.

`review edit` rewrites the body only. A review's scope and covered-entry list are its provenance — the record of what it was generated from — so correcting either means deleting the review and storing a new one.

`feedback rm` and `review rm` prompt for confirmation; `--yes` works before or after the command.

## Skill integration

The skill source lives at `~/scripts/skill_commscoach/unloaded_SKILL.md`. Install or link that file into the harness where you want to invoke it; the former documented path `~/scripts/skills/commscoach/SKILL.md` does not exist.

Invoke the skill with an entry ID for entry-specific coaching, ask for a recent communications review, ask to catch up on uncoached entries, or ask "how am I doing?" Entry coaching uses `commscoach context <id>` and stores feedback via `commscoach --json feedback add <id> -`. Reviews use `commscoach review context` to gather entries, prior syntheses, and active goals; after storing the review, the skill creates its next experiments as linked goals and closes achieved or irrelevant goals only when the reviewed entries support that judgment.

Both context commands include your self profile's private notes as `self.notes` (`null` when unset). Use them for durable coaching preferences, and edit or clear them with the existing person commands:

```sh
commscoach person edit me --notes "Help me be direct without sounding abrupt. Prefer short examples."
commscoach person edit me --notes            # edit existing preferences in $EDITOR
commscoach person edit me --clear-notes
```

Replace `me` with your self profile's handle. These notes are included even when the self profile is not linked to the entry.

Entry coaching history includes only interactions strictly before the target entry's occurrence time, falling back to creation time for entries without an occurrence date. Interactions at the same instant are excluded. The newest matching interactions are selected before applying `--history`, then returned oldest-first. Notes, feedback, active goals, and prior reviews remain current coaching context, not snapshots of what was known at the time.

Each person in entry context also has a `history_summary` with `limit`, `count`, `total_count`, and `truncated`. The total counts only eligible interactions strictly before the target, so the coach can distinguish "three prior interactions exist" from "three of 27 were included." `--history 0` still reports these totals, and a person with no eligible history has `total_count: 0` and `truncated: false`.

Like review context, `context ENTRY_ID` accepts `--goal-limit N` (default 10). Goals remain global and newest-first; the top-level `goal_summary` reports `limit`, `count`, `total_count`, and `truncated`. `--goal-limit 0` returns no goals while still reporting how many active goals exist. Increase the limit when you need a complete goal assessment:

```sh
commscoach context 42 --goal-limit 20
```

The skill is [scope](#review-scopes)-aware. It derives the scope from how you ask — "how am I doing with @alice?" becomes `--person alice`, "how is my conflict tag trending?" becomes `--tag conflict`, "how did last quarter go?" becomes a `--since`/`--until` range — and passes the same axis flags to `review add` that it passed to `review context`, so the stored review records the slice it was generated from. It reports the stored scope back to you. A scope that matches nothing is reported as an answer rather than stored as an empty review.

The skill also runs [`feedback edit` / `feedback rm` / `review edit` / `review rm`](#correcting-stored-coaching), but only when you explicitly ask it to change or remove a named record — never as cleanup inside another flow. Because it runs non-interactively it has to pass `--yes`, so it asks for confirmation in the conversation before either `rm`.

## Goals and stats

Goals make coaching experiments durable instead of leaving them in review Markdown:

```sh
commscoach goal add "Lead with the ask" --detail "Practice in the next three 1:1s."
commscoach goal list
commscoach goal list --oldest-first --limit 5
commscoach goal show 1
commscoach goal edit 1 --person jdoe
commscoach goal edit 1 --detail                 # edit existing detail in $EDITOR
commscoach goal close 1 --outcome               # compose or revise the outcome in $EDITOR
commscoach goal close 1 --outcome "Led with the decision needed in three meetings."
commscoach goal close 2 --dropped --outcome "Role changed; this scenario no longer applies."
```

`goal close` marks a goal done; add `--dropped` when it is no longer relevant. Goal details and outcomes also accept `-` for stdin or `@PATH`.

For `goal edit --detail` and `goal close --outcome`, passing the flag without a value opens `$VISUAL`/`$EDITOR`, pre-filled with the existing text (or blank when unset). These commands use the same draft recovery as entry editing and reject empty editor drafts. Use `goal edit --clear-detail` to remove detail. Non-interactive callers must supply text, `-`, or `@PATH`; omitting the flag entirely keeps the existing behavior.

Human-readable goal listings show active goals' age in whole elapsed days since creation, making lingering experiments easier to spot. Age does not reset when a goal is reopened. `--oldest-first` orders by creation time and ID before applying `--limit`; with `--status all`, active goals still come before closed goals. The default remains newest-first, and goal JSON fields are unchanged.

Repeating `goal close` preserves the saved outcome when `--outcome` is omitted. An unchanged repeat leaves timestamps alone; supplying a corrected outcome updates the goal while retaining its original closure time. Changing between done and dropped records a new closure time.

Use `stats` for stored activity and coverage numbers rather than reconstructing them from exports:

```sh
commscoach stats
commscoach --json stats --since "30 days ago" --until today --weeks 8
```

The summary includes entry, people, tag, feedback, review, and goal counts; feedback coverage; first/last activity; entries per week; current and longest weekly streaks; weekly buckets; and top tags, sources, and people.

Human-readable stats also show the number of entries awaiting feedback. When it is nonzero, a copyable command opens the five oldest uncoached entries, carrying over an explicit `--db` and the resolved date bounds. Stats JSON retains its existing fields.

## Schema version and migration

The current schema version is **4**. Version 4 rebuilds the `reviews` table to carry the scope columns described under [Review scopes](#review-scopes): `scope_person_id`, `scope_person_handle`, `scope_tag`, `scope_since`, and `scope_until`, each guarded by a CHECK constraint tying it to its `scope_kind`.

An existing database migrates automatically on the next `commscoach init`. The migration copies every review into a new table, then verifies the result with `PRAGMA integrity_check` and `PRAGMA foreign_key_check` before committing, and rolls the whole thing back if anything fails. Every pre-existing review becomes `scope_kind = 'latest'`, which is what it already was.

`init` is safe to run against an existing database, but take a backup first anyway — the migration rewrites a table:

```sh
commscoach backup --out ~/Backups/commscoach-pre-v4.sqlite3
commscoach init
commscoach --json init            # {"db_path": "...", "schema_version": 4}
commscoach doctor
```

`backup` is the one command that runs on a database of any schema version, which is what makes that first line work: the snapshot it writes is still stamped at the old version, with the old `reviews` shape and rows intact, so it restores to the state you had before migrating. Every other command still requires the current schema and answers `run 'commscoach init' first` until you migrate.

## Diagnostics and backups

`doctor` opens the store read-only and checks schema version, SQLite integrity, foreign keys, journal mode, permissions, unparsable timestamps, row counts, sync artifacts, and review scopes. A failing check exits 1; warnings are reported without treating the whole run as failed.

The `review_scope` check warns when a person-scoped review names a person who has since been deleted. That is expected history rather than corruption: the denormalized handle is kept so the review still records who it was about.

```sh
commscoach doctor
commscoach doctor --quick              # PRAGMA quick_check instead of integrity_check
commscoach --json doctor
```

`backup` uses SQLite's online backup API to create a consistent `0600` copy. Without `--out`, it writes a timestamped file under the database's `backups/` directory. `--keep N` prunes older standard timestamped backups in that output directory. It accepts any schema version so it can snapshot a database before [migrating](#schema-version-and-migration) it; it still refuses a file that is not a commscoach database.

```sh
commscoach backup
commscoach backup --out ~/Backups/commscoach.sqlite3
commscoach backup --keep 7
```

## Export

Markdown export is human-readable and lossy. JSON export is complete and restorable. Both write to stdout by default; pass `--out PATH` to create a mode-`0600` file.

```sh
commscoach export                       # print Markdown to stdout
commscoach export --out export.md       # write Markdown to file (mode 0600)
commscoach export --format json --out commscoach.json
```

Markdown export includes goals, stored reviews, entries, linked people, and feedback in a readable report, but it is not an archive format. Use `--format json` for backup portability: it preserves the complete schema data and relationships needed by `import`.

Each exported review names its scope axis, so a synthesis stays self-describing outside the database:

```markdown
### Review 1

- **Created**: 2026-09-02T01:21:06.703175+00:00
- **Scope**: person @alice (context limit 10)
- **Covered entry IDs**: 1, 2
```

`latest`, `tag conflict`, and `range 2026-07-01..2026-09-01` are the other three shapes. A review whose scoped person was deleted still renders `person @alice` from the stored handle, and the JSON archive round-trips that handle through `import`.

Import restores a complete JSON export into an empty initialized database. It never merges with or replaces existing records. Validate the whole restore, including constraints, with `--dry-run` before writing:

```sh
commscoach --db /path/to/empty.sqlite3 init
commscoach --db /path/to/empty.sqlite3 import --dry-run commscoach.json
commscoach --db /path/to/empty.sqlite3 import commscoach.json
cat commscoach.json | commscoach --db /path/to/empty.sqlite3 import -
```
