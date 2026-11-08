# commscoach

Personal communications coach: log interactions with people, attach context, generate feedback from an AI skill, track coaching goals, and keep a durable personal archive.

## Status

Ready to use. The CLI, SQLite schema, stored feedback and reviews, scoped reviews, tag management, goals, diagnostics, backups, JSON export/import, stats, skill integration, and packaging are in place. Current scope intentionally excludes encryption.

## Everyday use

| When you want to… | Command |
| --- | --- |
| See what needs attention today | `commscoach` |
| Log a conversation in one line | `commscoach log "Asked @jdoe for budget #1on1" -s slack` |
| Get coached on an entry without leaving the terminal | `commscoach coach 42` |
| See how the week went | `commscoach recap` |
| See your habits at a glance | `commscoach stats` |
| Get a short conversation brief | `commscoach person prep jdoe --brief` |
| Find a person by name | `commscoach person list --query "John"` |
| See recorded relationship activity | `commscoach person list --with-activity` |
| Read actionable goal details | `commscoach goal list --with-detail` |
| Compose a new goal's details in your editor | `commscoach goal add "Lead with the ask" --detail` |
| Revisit what worked | `commscoach goal list --status done --with-outcome` |
| Correct a goal's outcome without changing its status | `commscoach goal edit 1 --outcome` |
| Find past experiments and outcomes | `commscoach goal search --query "lead with the ask"` |
| See whether a goal is moving | `commscoach goal show 3` |
| Read a review with its evidence | `commscoach review show 1 --with-entries --with-goals` |
| Prepare for a conversation | `commscoach person prep jdoe` |
| Read broader relationship advice | `commscoach person show jdoe --reviews 2` |
| Find the oldest entries awaiting coaching | `commscoach entry search --no-feedback --oldest-first --limit 5` |
| Revisit an interaction and its advice | `commscoach entry show 42 --with-feedback` |
| Find past advice | `commscoach feedback search --query "lead with the ask"` |
| Read advice alongside its conversation | `commscoach feedback show 1 --with-entry` |
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
commscoach log "Slack thread with @jdoe; I led with the ask #followup" -s slack  # same, in one line
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

Confirmation prompts only read answers from a terminal; when stdin is a pipe or file they decline, so scripts and agents pass `--yes`.

Use `commscoach --help` for top-level help and `commscoach <command> --help`
for subcommand-specific options.

## On a terminal

Bare `commscoach` opens a home screen: entries waiting for coaching, the
experiment your last coaching set, goals in play (and the ones ready to close),
people who have gone quiet for that relationship's usual rhythm, an entry from
this day in the past with how far you have come since, and your weekly rhythm.
An empty journal gets a setup checklist instead.

When stdout is a terminal, human output is designed to be read: times say
"yesterday" or "3h ago", ids, handles, and tags are colored, Markdown feedback is
rendered and wrapped, `stats` draws a coverage bar and a weekly sparkline,
`person prep` opens with a "Before you talk" briefing, and logging an entry
says how often you have talked to that person lately, what you planned to try
last time, which goal applies, and offers to coach it right away with
`commscoach coach` (Claude Code). `commscoach recap` tells the past week or
month as a short story.

Piped output, `--json`, and the files `export` writes keep the stable plain
format. Override the detection with `COMMSCOACH_PRETTY=1` (for example
`COMMSCOACH_PRETTY=1 FORCE_COLOR=1 commscoach entry list | less -R`) or
`COMMSCOACH_PLAIN=1`; `NO_COLOR` keeps the layout but drops color. See
[Terminal output](docs/reference.md#terminal-output).

## Shell completion

TAB completes subcommands and options, plus person handles, tag names, and
entry, goal, feedback, and review IDs read from your database (zsh and fish
also show each ID's excerpt or title):

```sh
eval "$(commscoach completion bash)"   # in ~/.bashrc
eval "$(commscoach completion zsh)"    # in ~/.zshrc, after compinit
commscoach completion fish > ~/.config/fish/completions/commscoach.fish
```

For zsh you can instead save the output as `_commscoach` in a directory on
`$fpath`. Completion honors `--db PATH` on the line and `$COMMSCOACH_DB`, and
only ever opens the database read-only. See
[shell completion](docs/reference-operations.md#shell-completion) for details.


## Reference

The default database is `~/.local/share/commscoach/commscoach.sqlite3` (or under
`$XDG_DATA_HOME` when set). Override it with `--db PATH` or `COMMSCOACH_DB`.
Database files, exports, and backups contain private personal data.

- [Capture, editing, and dates](docs/reference.md#capture-and-dates)
- [Quick capture with `log`](docs/reference.md#quick-capture-with-log) and [terminal output](docs/reference.md#terminal-output)
- [Finding coaching, conversation prep, and people](docs/reference-coaching.md#finding-past-coaching-and-relationship-context)
- [Goals and stats](docs/reference-operations.md#goals-and-stats), including [goal checks](docs/reference-operations.md#goal-checks-and-progress-trails)
- [Tags](docs/reference.md#tags) and [review scopes](docs/reference.md#review-scopes)
- [Correcting stored coaching](docs/reference-coaching.md#correcting-stored-coaching)
- [AI skill integration](docs/reference-coaching.md#skill-integration)
- [Database location and privacy](docs/reference.md#default-database)
- [Diagnostics and backups](docs/reference-operations.md#diagnostics-and-backups)
- [Export and import](docs/reference-operations.md#export)
- [Schema migrations](docs/reference-operations.md#schema-version-and-migration)
- [Shell completion](docs/reference-operations.md#shell-completion)

## Layout

```
src/commscoach/   # Python package
skills/commscoach/ # AI coaching skill
tests/            # Test suite (`uv run pytest -q`)
```

## Development

```sh
uv venv
uv sync
uv run commscoach --help
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

CI runs the same lint, format, and test checks on Linux and macOS for Python 3.11–3.14, then builds the wheel and runs the installed `commscoach --version`.

## License

[MIT](LICENSE)
