# Command and storage reference

[Back to the quick start](../README.md#quick-start)

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

The database is created with mode `0600`. Directories commscoach creates for it get mode `0700`, and the default data directory is kept at `0700`; an existing directory you choose with `--db` or `backup --out` keeps its permissions. Backups and file exports are also written with mode `0600`. Treat all of them as private personal data.

## Capture and dates

Handles are shown as `@handle`, and every command accepts a handle with or without that `@`. On `entry add` and `entry edit`, `--person` is repeatable or comma-separated like `--tag` (`--person jdoe,asmith`). Search and list filters such as `--person`, `--tag`, and `--source` take a single value; repeating one is a usage error (exit 2) rather than silently keeping the last value. Combining flags that contradict each other (`--tag` with `--clear-tags`, `--detail` with `--clear-detail`, or `--person` with `--tag` on a review scope) is a usage error too; other failures, such as an unknown handle, exit 1. Handles are case-insensitive, including accented letters (`Émile` is `émile`), and an accent typed as a separate combining mark matches its precomposed letter. They cannot contain spaces or commas; `doctor` flags any older handle that does, and any pair of older handles that differ only by accented case.

`entry add` body sources are a literal string, `-` for stdin, or `@PATH` for a UTF-8 file. On an interactive terminal, omitting the body opens `$VISUAL` or `$EDITOR` (falling back to `vi`). The same body-source convention applies to `entry edit --body`, `feedback add`, and `review add`; their omitted-body forms also use the editor where supported.

The entry editor includes optional prompts about what you wanted, what you said, how the other person responded, and what you want feedback on. These are `#>` instruction lines, so they are removed from the saved body; answer whichever help, or simply write your entry. When an edit opens stored text that itself has lines starting with `#>`, they appear escaped as `\#>` and are saved back unchanged; type `\#>` yourself to keep such a line in new text. Leaving only the prompts still counts as an empty draft.

Editor drafts are kept until the command succeeds. If the editor fails, you interrupt the command, the process is terminated (SIGTERM, or SIGHUP when the terminal closes), or the subsequent save fails, a draft with new text is retained with mode `0600` and its recovery path is printed to stderr (including with `--json`). Open that file to recover your text, or pass it back as `@PATH`; the `#>` instruction lines are removed when the draft is saved, so only your text remains (a draft that is not valid UTF-8 is kept byte-for-byte). Successful commands, empty drafts, and drafts left unchanged from their starting text are cleaned up automatically. Recovery files live in the system temporary directory, so recover them before system cleanup removes them.

`person edit HANDLE --notes` also opens the editor, pre-filled with that person's private notes (or blank when unset). It uses the same draft recovery. Empty drafts are rejected; use `--clear-notes` to remove notes. Non-interactive callers must supply text, `-`, or `@PATH`.

`--occurred-at`, `--since`, and `--until` accept ISO-8601 values and natural relative dates such as `today`, `yesterday`, `3 days ago`, `last monday`, and `now`, plus `tomorrow` for planned conversations. A weekday always means its most recent occurrence strictly before today, so on a Monday both `monday` and `last monday` mean seven days ago; `last` is optional. `now` takes no time of day; use `today 9am`. Stored timestamps are normalized to UTC. Entries are ordered by when they occurred, falling back to creation time.

Date filters use your system's local timezone. A bare date, `today`, `yesterday`, or `last monday` covers the whole local day, including daylight-saving transitions. Explicit times and elapsed-time expressions such as `today 9am`, `now`, or `3 days ago` retain their time; timestamps with an offset use that offset. Both bounds are inclusive, and a `--since` instant after `--until` is rejected. These rules apply to entry listings, searches, stats, and review scopes:

```sh
commscoach entry list --since today --until today
commscoach entry search --since "yesterday 9am" --until "yesterday 5pm"
```

Human-readable `entry list` and `entry search` rows show the local occurrence date/time (or creation time), source when present, feedback status, text preview, tags, and people:

```text
[42] · 2026-10-02 14:30 · in-person · needs feedback · Quarterly review with John. · #1on1, #review · @jdoe
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

`entry list --limit N` and `review list --limit N` cap the newest results shown. `--oldest-first` applies to `entry list`, `entry search`, `review list`, `review search`, and `feedback search`; for entries it orders matching entries by occurrence time (falling back to creation time), then ID, before applying `--limit`. Without it, results remain newest-first. By default, when SQLite has FTS5, `entry search --query` matches the start of words, accent-insensitively: `meet` also finds `meeting`, and several words must all appear. Queries made of symbols or other non-word text (`€50`, an emoji, `follow-up`) use substring matching instead, so they match literally. Queries using full-text syntax are passed through as written, so quote a phrase (`"lead with the ask"`) for exact words in order, or use `*`, `OR`, and `NOT`. Without FTS5 it falls back to substring matching. `--exact` performs a literal, case-insensitive substring search, including literal `%` and `_` characters. Feedback, review, goal, and person searches (and `--exact`) always use substring matching, ignoring case and accents the same way the full-text index does, so `cafe` finds `Café`.

When `entry search --limit N` omits matches, human output ends with a count such
as `Showing 5 of 23 matching entries.`, printed to stderr so piping the results
(for example to `wc -l`) counts only records. The total respects every search filter,
including full-text or literal matching and `--no-feedback`. `--limit 0` still
reports omitted matches. Complete results have no footer. With `--json`, stdout
remains a plain array and the same footer still goes to stderr, so a script can
tell a complete result from a truncated one.

The same omitted-match footer appears for `entry list`, `feedback search`, `person list`, `tag list`,
`review list`, `review search`, `goal search`, and `goal list` with `--limit N`,
using each command's active filters.
For example, `Showing 5 of 23 matching feedback notes.` means you can increase
`--limit` to see more advice. Counts refer to matching records, so multiple linked
people or entries do not inflate them. JSON output remains unchanged.

When `entry search --query` finds a match, human output shows the matching passage instead of just the entry's opening paragraph. Literal searches use an excerpt around the first match; token searches use SQLite's matching snippet, including for phrases, prefixes, and accent-insensitive matches. Ellipses mark omitted text. Searches without a query retain the opening preview, and JSON still returns the full entry with its existing fields.

`entry show --with-feedback` includes full feedback notes oldest-first, or an explicit empty marker when there are none. With `--json`, it adds a `feedback` array to the entry object; without this flag the JSON shape is unchanged.

When starting from a feedback search result, use `feedback show ID --with-entry`
to read the conversation behind that advice. It shows the linked entry's full
current text, date, source, tags, and people before the selected feedback note.
The entry is its current stored version, not a snapshot from when feedback was
written. JSON adds an `entry` object only when this flag is supplied; plain
`feedback show ID` is unchanged.

`entry rm ID` includes the counts of feedback notes to delete and review links to remove in its confirmation. The reviews themselves remain; when a review covered only this entry, the confirmation names it, and with `--yes` a stderr note says it now covers no entries. `--yes` continues to bypass confirmation.

`entry edit ID` replaces links with `--tag TAG` and `--person HANDLE` (both repeatable; tags also accept comma-separated values) and removes them all with `--clear-tags` or `--clear-people`. An empty `--tag ""` or `--person ""` (often an unset shell variable) is rejected instead of clearing links. The same goes for `entry add`, and for the `--person`, `--tag`, and `--source` filters on list, search, and context commands, which would otherwise silently match everything or nothing. Edits that leave every value as it was print `unchanged` and keep `updated_at`; this applies to every `edit` command:

```sh
commscoach entry edit 42 --tag 1on1,review --person jdoe
commscoach entry edit 42 --clear-tags
```

Incremental edits let you change links without replacing the existing lists:

```sh
commscoach entry edit 42 --add-tag follow-up --remove-tag draft
commscoach entry edit 42 --add-person alice --remove-person bob
```

These flags may be repeated; tags also accept comma-separated values. Tag normalization and case-insensitive person matching follow the existing commands. Adding an existing link or removing an absent link is harmless. Unknown people being added use the existing creation confirmation; removing a link never creates or deletes the person. You cannot add and remove the same normalized tag or person in one command, or mix incremental edits with replacement/clear flags for the same kind of link.

Entry creation validates the date and database before opening the editor. Feedback creation checks that its entry exists before asking for a body.
Review creation also checks the database, every covered entry ID, and the scoped person (when supplied) before opening the editor.

After an entry's body changes, existing feedback or linked reviews trigger a
short reminder that coaching may refer to the previous text, with copyable
commands to revisit it. The reminder goes to stderr, including with `--json`,
and preserves an explicit `--db` path. Metadata-only edits and unchanged bodies
produce no reminder. Existing coaching is left intact.

## Tags

Tags live in their own table with their own normalization (trimmed, lowercased, Unicode-composed, and without a leading `#`, so `--tag '#Review'` names `review`) and orphan collection, so the vocabulary stays maintainable instead of letting one typo split a theme across your stats permanently:

```sh
commscoach tag list                           # most-used first, with entry counts
commscoach tag list --limit 10
commscoach --json tag list
commscoach tag rename conflcit conflict       # keeps every entry link
commscoach --yes tag merge conflcit conflict  # fold one tag into another, then drop it
commscoach --yes tag rm writing               # unlink from every entry, then drop it
```

Tag names are normalized and matched case-insensitively, so `Conflict` and `conflict` are the same tag and a case-only rename returns the tag unchanged. Because `--tag` splits on commas, `tag rename` rejects a new name containing one; `doctor` flags any such tag created earlier.

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

"How did last quarter go?" (asked in October 2026: the previous calendar quarter)

```sh
commscoach review context --since 2026-07-01 --until 2026-09-30
commscoach review add "Q3 retrospective." --since 2026-07-01 --until 2026-09-30 --entry 4 5
commscoach review list --scope range
```

Either bound alone is enough to select the `range` scope; an unbounded range would just be `latest`. Two bounds in the wrong order (`--since` after `--until`) exit 1 on both `review context` and `review add`, before any context is emitted or any editor opens — it is the one empty scope that can only be a typo.

### What the scope changes

- **Entries** are narrowed by the axis and capped by `--limit`.
- **Prior reviews** are filtered to the same scope under `person` and `tag` only, so a review of @alice is measured against previous reviews of @alice. `latest` and `range` get the unfiltered recent listing instead: a date window is a one-off slice with no natural predecessor.
- **Goals** are never scope-filtered, only capped by `--goal-limit`. Person-scoped reviews prioritize that person's active goals, then general goals, then other people's goals before applying the limit. Each group is newest-first by creation time, then ID. Other review scopes retain global newest-first ordering.

A scope that matches nothing returns an empty entry list rather than failing — "no entries with @alice yet" is an answer, not an error. An unknown person handle or tag *is* an error, because that is a typo, and so is a reversed date range. `entry search --person`/`--tag` follow the same rule. (Tags exist only while some entry carries them, so `tag list` shows every name that can match.)

`review add` stores the scope you pass alongside the `--entry` IDs. When some of those entries fall outside that scope (say `--person bob` with an entry that does not involve @bob), it warns on stderr and still stores the review, since an entry may have been edited after its context was generated.

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

`review list` filters over stored rows, so its `--scope`, `--person`, and `--tag` combine with AND rather than deriving a single axis. `review list` and `review search` also take `--since`/`--until`, which bound when the review was written (not the date range a `range` review covers): `commscoach review list --since "30 days ago"`. Human output shows the scope inline as `scope:person:@alice`; Markdown export shows it as `- **Scope**: person @alice (context limit 10)`.

A person-scoped review stores the handle alongside the foreign key, so after `commscoach --yes person rm alice` the review is still findable by `review list --person alice` and still prints `@alice`. A handle that names neither a person nor any stored review is reported as `person 'x' not found` rather than an empty listing. `doctor`'s `review_scope` check reports those reviews as a warning — retained history, not corruption.

The JSON review shape carries that stored handle as its own `scope_person_handle` key, beside the `scope_person` object: `--json review show`, `--json review list`, and the `prior_reviews` block of `review context` all emit it. `scope_person` is a live lookup and becomes `null` once the person is deleted, so `scope_person_handle` is what still names them.

`commscoach person edit alice --handle ally` carries the rename into that stored handle, the same way `tag rename` carries a tag rename into tag-scoped reviews. The person's review history follows them: `review list --person ally` finds it, a `review context --person ally` counts it among the prior reviews, and every surface prints `@ally`. Delete them afterwards and `@ally` — the handle they last had — is what remains as history.

See also: [coaching and skill integration](reference-coaching.md) and [goals, stats, and operations](reference-operations.md).
