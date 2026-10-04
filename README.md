# commscoach

Personal communications coach: log interactions with people, attach context, generate feedback from an AI skill, track coaching goals, and keep a durable personal archive.

## Status

Ready to use. The CLI, SQLite schema, stored feedback and reviews, scoped reviews, tag management, goals, diagnostics, backups, JSON export/import, stats, skill integration, and packaging are in place. Current scope intentionally excludes encryption.

## Everyday use

| When you want to… | Command |
| --- | --- |
| Get a short conversation brief | `commscoach person prep jdoe --brief` |
| Find a person by name | `commscoach person list --query "John"` |
| Read actionable goal details | `commscoach goal list --with-detail` |
| Compose a new goal's details in your editor | `commscoach goal add "Lead with the ask" --detail` |
| Revisit what worked | `commscoach goal list --status done --with-outcome` |
| Find past experiments and outcomes | `commscoach goal search --query "lead with the ask"` |
| Read a review with its evidence | `commscoach review show 1 --with-entries --with-goals` |
| Prepare for a conversation | `commscoach person prep jdoe` |
| Read broader relationship advice | `commscoach person show jdoe --reviews 2` |
| Find the oldest entries awaiting coaching | `commscoach entry search --no-feedback --oldest-first --limit 5` |
| Revisit an interaction and its advice | `commscoach entry show 42 --with-feedback` |
| Find past advice | `commscoach feedback search --query "lead with the ask"` |
| Find a broader review | `commscoach review search --query "lead with the ask"` |
| Review lingering goals | `commscoach goal list --oldest-first --limit 5` |

Replace the example handle and entry ID with your own. For first-time setup, see [Quick start](#quick-start).

## Install globally

```sh
uv tool install --editable --force .
command -v commscoach        # -> /Users/.../.local/bin/commscoach
commscoach --version         # -> commscoach 0.1.0
commscoach --help
```

The `--editable` install resolves the package directly from this source tree, so subsequent edits take effect without re-installing.

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

## Global flags

The global flags `--db`, `--json`, and `--yes` can appear before or after a command, including a nested command:

```sh
commscoach --json context 42
commscoach context 42 --json
commscoach entry list --db ~/journal.sqlite3 --json
```

Use `commscoach --help` for top-level help and `commscoach <command> --help`
for subcommand-specific options.


## Reference

The default database is `~/.local/share/commscoach/commscoach.sqlite3` (or under
`$XDG_DATA_HOME` when set). Override it with `--db PATH` or `COMMSCOACH_DB`.
Database files, exports, and backups contain private personal data.

- [Capture, editing, and dates](docs/reference.md#capture-and-dates)
- [Finding coaching, conversation prep, and people](docs/reference.md#finding-past-coaching-and-relationship-context)
- [Goals and stats](docs/reference.md#goals-and-stats)
- [Tags](docs/reference.md#tags) and [review scopes](docs/reference.md#review-scopes)
- [Correcting stored coaching](docs/reference.md#correcting-stored-coaching)
- [AI skill integration](docs/reference.md#skill-integration)
- [Database location and privacy](docs/reference.md#default-database)
- [Diagnostics and backups](docs/reference.md#diagnostics-and-backups)
- [Export and import](docs/reference.md#export)
- [Schema migrations](docs/reference.md#schema-version-and-migration)

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
