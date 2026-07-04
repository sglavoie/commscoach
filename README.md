# commscoach

Personal communications coach: log interactions with people, attach context, generate feedback from an AI skill, and export everything to Markdown.

## Status

Ready to use. The CLI, SQLite schema, skill integration, packaging, and Markdown export are in place. v1 intentionally excludes import, encryption, FTS5, and non-Markdown exports.

## Layout

```
src/commscoach/   # Python package
tests/            # unittest suite (`uv run python -m unittest discover -s tests`)
data/             # legacy local data dir; SQLite DB defaults to iCloud Drive
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

`~/Library/Mobile Documents/com~apple~CloudDocs/commscoach/commscoach.sqlite3`

At runtime, `~` is resolved from the current user via `Path.home()`.

Overrides (in order of precedence):

1. `--db PATH` global CLI flag
2. `COMMSCOACH_DB` environment variable
3. The default path above

### Privacy

SQLite files under `data/` are **not** committed. `.gitignore` excludes `data/*.sqlite3*`; the `data/` directory itself is kept tracked via `data/.gitkeep`. The DB file is created with mode `0600` and its parent directory with mode `0700`. Treat the DB as private personal data.

## Quick start

```sh
commscoach init
commscoach person add me --self --name "Your Name"
commscoach person add jdoe --name "John Doe"
echo "Quarterly review with John. I rambled before getting to my ask." \
  | commscoach entry add - --tag 1on1 --tag review --person jdoe --source in-person
commscoach entry list
commscoach context 1                 # skill-facing JSON
commscoach feedback add 1 "Lead with the ask next time."
commscoach feedback list 1
commscoach export                    # Markdown to stdout
commscoach export --out export.md    # Markdown to file (mode 0600)
```

`entry add` body sources: a literal string, `-` for stdin, or `@PATH` for a UTF-8 file. The same convention applies to `entry edit --body` and `feedback add`.

## Skill integration

The `commscoach` skill at `~/scripts/skills/commscoach/SKILL.md` is symlinked into all four harnesses (`~/.claude`, `~/.claude-personal`, `~/.codex`, `~/.copilot`). Invoke it with an entry ID; it fetches the entry context via `commscoach context <id>`, drafts coaching feedback, and stores it via `commscoach --json feedback add <id> -`.

## Export

Markdown is the only export format. By default the rendered Markdown is written to stdout; pass `--out PATH` to write it to a file (the file is created with mode `0600`).

```sh
commscoach export                       # print Markdown to stdout
commscoach export --out export.md       # write Markdown to file (mode 0600)
```

The export groups one section per entry (newest first) with the entry's metadata, linked people, and any attached feedback notes (oldest first). No JSONL or import format is supported.
