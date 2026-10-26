# Goals, stats, and operations reference

[Back to the quick start](../README.md#quick-start)

See also: [storage, capture, tags, and review scopes](reference.md), [coaching and skill integration](reference-coaching.md), [goals, stats, and operations](reference-operations.md).

## Goals and stats

Goals make coaching experiments durable instead of leaving them in review Markdown:

```sh
commscoach goal add "Lead with the ask" --detail "Practice in the next three 1:1s."
commscoach goal add "Lead with the ask" --detail # compose detail in $EDITOR
commscoach goal list
commscoach goal list --oldest-first --limit 5
commscoach goal show 1
commscoach goal edit 1 --person jdoe
commscoach goal edit 1 --detail                 # edit existing detail in $EDITOR
commscoach goal edit 1 --outcome                # correct outcome, preserving status
commscoach goal edit 1 --clear-outcome
commscoach goal close 1 --outcome               # compose or revise the outcome in $EDITOR
commscoach goal close 1 --outcome "Led with the decision needed in three meetings."
commscoach goal close 2 --dropped --outcome "Role changed; this scenario no longer applies."
```

`goal close` marks a goal done; add `--dropped` when it is no longer relevant. Goal details and outcomes also accept `-` for stdin or `@PATH`.

For `goal add --detail`, `goal edit --detail`, and `goal close --outcome`, passing the flag without a value opens `$VISUAL`/`$EDITOR`, pre-filled with the existing text (or blank for a new goal or unset field). These commands use the same draft recovery as entry editing and reject empty editor drafts. Goal creation validates the database, title, person, and review references before opening the editor. Use `goal edit --clear-detail` to remove detail. Non-interactive callers must supply text, `-`, or `@PATH`; omitting the flag entirely keeps the existing behavior.

`goal edit --outcome` supports the same text, stdin, file, and editor forms. It
changes the outcome without changing status or closure time; `--clear-outcome`
removes it and cannot be combined with `--outcome`. This also works for active
goals. An explicit `--status` on the same edit still performs the requested status
transition. Goal edits validate the goal ID, supplied title, and person reference
before opening either editor.

Human-readable goal listings show active goals' age in whole elapsed days since creation, making lingering experiments easier to spot. Age does not reset when a goal is reopened. `--oldest-first` orders by creation time and ID before applying `--limit`; with `--status all`, active goals still come before closed goals. The default remains newest-first, and goal JSON fields are unchanged.

Repeating `goal close` preserves the saved outcome when `--outcome` is omitted. An unchanged repeat leaves timestamps alone; supplying a corrected outcome updates the goal while retaining its original closure time. Changing between done and dropped records a new closure time.

Likewise, repeating `goal edit ID --status done` (or the current active/dropped
status) leaves timestamps alone. Editing other fields alongside an unchanged
status updates `updated_at` while preserving the original closure time. Reopening
a goal clears its closure time; changing to a different closed status records a
new closure time. These status edits preserve the saved outcome.

Use `stats` for stored activity and coverage numbers rather than reconstructing them from exports:

```sh
commscoach stats
commscoach --json stats --since "30 days ago" --until today --weeks 8
```

The summary includes entry, people, tag, feedback, review, and goal counts; feedback coverage; first/last activity; entries per week; current and longest weekly streaks; weekly buckets; and top tags, sources, and people.

Human-readable stats also show the number of entries awaiting feedback. When it is nonzero, a copyable command opens the five oldest uncoached entries, carrying over an explicit `--db` and the resolved date bounds. Stats JSON retains its existing fields.

Show the full experiment beneath each goal title with `goal list --with-detail`.
JSON already contains goal details and is unchanged by this flag.

Revisit completed experiments with `goal list --status done --with-outcome`.
`--with-outcome` works with both `goal list` and `goal search`, and can be combined
with `--with-detail`. It prints the full recorded outcome or `(no outcome recorded)`
under each goal. Search omits the outcome excerpt when the full outcome is shown.
JSON already includes outcomes and is unchanged by this flag.

Search titles, details, and recorded outcomes together:

```sh
commscoach goal search --query "lead with the ask"
commscoach goal search --query "10%" --status done --person jdoe
commscoach goal search --query "ask" --oldest-first --limit 5 --json
```

Matching is literal and case-insensitive, including `%`, `_`, and `!`. Empty or
whitespace-only queries are rejected. Search defaults to all statuses and a limit
of 20, with active goals first and newest-first within the active and closed groups.
`--status`, `--person`, and the query combine before ordering and limiting results.
Both `goal list` and `goal search` reject an unknown person handle, including with
`--limit 0`; an existing person with no matching goals returns an empty list.
Person matching remains case-insensitive. `--oldest-first` reverses
creation-time order within those groups. `--limit 0` returns no results. Human output
labels matching excerpts by title, detail, or outcome; JSON returns complete goal
objects. Add `--with-detail` to include full details in human search results too.

## Schema version and migration

The current schema version is **4**. Version 4 rebuilds the `reviews` table to carry the scope columns described under [Review scopes](reference.md#review-scopes): `scope_person_id`, `scope_person_handle`, `scope_tag`, `scope_since`, and `scope_until`, each guarded by a CHECK constraint tying it to its `scope_kind`.

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

`backup` uses SQLite's online backup API to create a consistent `0600` copy. Without `--out`, it writes a timestamped file under the database's `backups/` directory. `--keep N` prunes older standard timestamped backups (`commscoach-YYYYMMDDTHHMMSSZ.sqlite3`) in that output directory; other files, including the live database, are never pruned. Two backups in the same second get a `-2` suffix instead of overwriting each other. It accepts any schema version so it can snapshot a database before [migrating](#schema-version-and-migration) it; it still refuses a file that is not a commscoach database.

```sh
commscoach backup
commscoach backup --out ~/Backups/commscoach.sqlite3
commscoach backup --keep 7
```

## Export

Markdown export is human-readable and lossy. JSON export is complete and restorable. Both write to stdout by default; pass `--out PATH` to create a mode-`0600` file.

File exports are written to a private temporary file beside the destination and
replace it atomically after writing succeeds. A failed write leaves an existing
export intact. The active database and its SQLite sidecars cannot be export
destinations, including through symbolic or hard links. Other destination
symlinks are replaced by the export file; their targets remain untouched.

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
