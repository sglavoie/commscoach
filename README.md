# commscoach

Personal communications coach: log interactions with people, attach context, generate feedback from an AI skill, track coaching goals, and keep a durable personal archive.

## Status

Ready to use. The CLI, SQLite schema, stored feedback and reviews, goals, diagnostics, backups, JSON export/import, stats, skill integration, and packaging are in place. Current scope intentionally excludes encryption.

## Layout

```
src/commscoach/   # Python package
tests/            # unittest suite (`uv run python -m unittest discover -s tests`)
```

## Development

```sh
uv venv
uv sync
uv run commscoach --help
uv run python -m unittest discover -s tests
```

## Install globally

```sh
uv tool install --editable --force .
command -v commscoach        # -> /Users/.../.local/bin/commscoach
commscoach --version         # -> commscoach 0.1.0
commscoach --help
```

The `--editable` install resolves the package directly from this source tree, so subsequent edits take effect without re-installing.

## Global flag ordering

The global flags `--db`, `--json`, and `--yes` are declared on the top-level parser and **must precede the subcommand**:

```sh
commscoach --json context 42         # correct
commscoach context 42 --json         # exit 2: unrecognized arguments: --json
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
commscoach context 1                 # skill-facing JSON
commscoach feedback add 1 "Lead with the ask next time."
commscoach feedback list 1
commscoach goal add "Lead with the ask" --person jdoe
commscoach review context --limit 20 --prior-review-limit 3 # skill-facing review JSON
commscoach review add "Review: lead with the ask across recent 1:1s." --limit 20 --entry 1
commscoach review list
commscoach stats
commscoach doctor
commscoach backup
commscoach export                    # Markdown to stdout
commscoach export --format json --out commscoach.json
```

## Capture and dates

`entry add` body sources are a literal string, `-` for stdin, or `@PATH` for a UTF-8 file. On an interactive terminal, omitting the body opens `$VISUAL` or `$EDITOR` (falling back to `vi`). The same body-source convention applies to `entry edit --body`, `feedback add`, and `review add`; their omitted-body forms also use the editor where supported.

`--occurred-at`, `--since`, and `--until` accept ISO-8601 values and natural relative dates such as `today`, `yesterday`, `3 days ago`, `last monday`, and `now`. Stored timestamps are normalized to UTC. Date filters are inclusive, and entries are ordered by when they occurred, falling back to creation time.

## Skill integration

The skill source lives at `~/scripts/skill_commscoach/unloaded_SKILL.md`. Install or link that file into the harness where you want to invoke it; the former documented path `~/scripts/skills/commscoach/SKILL.md` does not exist.

Invoke the skill with an entry ID for entry-specific coaching, ask for a recent communications review, ask to catch up on uncoached entries, or ask "how am I doing?" Entry coaching uses `commscoach context <id>` and stores feedback via `commscoach --json feedback add <id> -`. Reviews use `commscoach review context` to gather entries, prior syntheses, and active goals; after storing the review, the skill creates its next experiments as linked goals and closes achieved or irrelevant goals only when the reviewed entries support that judgment.

## Goals and stats

Goals make coaching experiments durable instead of leaving them in review Markdown:

```sh
commscoach goal add "Lead with the ask" --detail "Practice in the next three 1:1s."
commscoach goal list
commscoach goal show 1
commscoach goal edit 1 --person jdoe
commscoach goal close 1 --outcome "Led with the decision needed in three meetings."
commscoach goal close 2 --dropped --outcome "Role changed; this scenario no longer applies."
```

`goal close` marks a goal done; add `--dropped` when it is no longer relevant. Goal details and outcomes also accept `-` for stdin or `@PATH`.

Use `stats` for stored activity and coverage numbers rather than reconstructing them from exports:

```sh
commscoach stats
commscoach --json stats --since "30 days ago" --until today --weeks 8
```

The summary includes entry, people, tag, feedback, review, and goal counts; feedback coverage; first/last activity; entries per week; current and longest weekly streaks; weekly buckets; and top tags, sources, and people.

## Diagnostics and backups

`doctor` opens the store read-only and checks schema version, SQLite integrity, foreign keys, journal mode, permissions, unparsable timestamps, row counts, and sync artifacts. A failing check exits 1; warnings are reported without treating the whole run as failed.

```sh
commscoach doctor
commscoach doctor --quick              # PRAGMA quick_check instead of integrity_check
commscoach --json doctor
```

`backup` uses SQLite's online backup API to create a consistent `0600` copy. Without `--out`, it writes a timestamped file under the database's `backups/` directory. `--keep N` prunes older standard timestamped backups in that output directory.

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

Import restores a complete JSON export into an empty initialized database. It never merges with or replaces existing records. Validate the whole restore, including constraints, with `--dry-run` before writing:

```sh
commscoach --db /path/to/empty.sqlite3 init
commscoach --db /path/to/empty.sqlite3 import --dry-run commscoach.json
commscoach --db /path/to/empty.sqlite3 import commscoach.json
cat commscoach.json | commscoach --db /path/to/empty.sqlite3 import -
```
