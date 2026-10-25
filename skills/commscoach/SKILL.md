---
name: commscoach
description: Coach a logged commscoach entry, synthesize and persist a review scoped to the latest entries, one person, one tag, or a date range, correct stored feedback and reviews on request, find the uncoached backlog, or report stored communication stats. Fetches CLI context, produces actionable Markdown, and closes the coaching loop through the CLI.
allowed-tools: Bash, Read
---

# Communications Coach

Use this skill when the user asks for coaching on `commscoach` data.

Choose the flow that matches the request:

- **Entry feedback flow**: the user names a numeric entry ID (e.g. "coach entry 42" or `/commscoach 42`). If the request is entry-specific but no entry ID is provided, ask for one before continuing.
- **Stored review flow**: the user asks for a broader review, such as "review recent comms", "review my recent communication", or "make a communication review". Do not require an entry ID for this flow. The request may also name a person, a tag, or a time window ("how am I doing with @alice?", "how is my conflict tag trending?", "how did last quarter go?"); that selection is the review's **scope** and it must be carried through both the context fetch and the stored record.
- **Coaching backlog flow**: the user asks to catch up on entries that have not received feedback.
- **Stats flow**: the user asks "how am I doing?" or requests activity, coaching-coverage, goal, or cadence numbers.
- **Correction flow**: the user explicitly asks to fix, reword, or delete a specific stored feedback note or review. Only enter this flow on an explicit request naming a stored record; never on the skill's own initiative and never as cleanup inside another flow.

## Step 1: Confirm `commscoach` is available

```bash
command -v commscoach >/dev/null || {
  echo "commscoach not on PATH; run 'uv tool install --editable --force .' in the project repo" >&2
  exit 1
}
```

If this fails, surface the message and stop.

Global flags `--db`, `--json`, and `--yes` work before or after commands, including nested commands. The examples below place them first.

## Entry Feedback Flow

Replace `<id>` in every command in this section with the numeric entry ID the user supplied.

### Step E1: Fetch entry context

```bash
commscoach context <id>
```

Notes:

- This subcommand always emits JSON; `--json` is optional and has no effect here.
- A missing entry or no configured `self` prints a clear stderr message and exits 1. Surface stderr to the user and stop. If the reason is `no self person configured`, suggest `commscoach person add me --self` (or `commscoach person self <handle>`) before retrying.

Parse stdout as JSON. The payload shape is exactly:

- `self`: `{id, handle, name, is_self, notes}` — the user being coached. `notes` contains durable coaching preferences, or `null`; use them to tailor feedback. The literal key is `self`, not `self_person`.
- `people`: non-self counterparties for this entry, each enriched as `{id, handle, name, is_self, notes, history}`. `notes` is private coaching context or `null`; `history` contains up to `--history` interactions with that person strictly before the target entry, oldest-first, as `[{entry, feedback}]`. Chronology uses occurrence time, falling back to creation time; equal-time and later interactions are excluded. Each history item's `feedback` is oldest-first. Feedback, notes, goals, and prior reviews are current coaching context and may have been recorded after the target interaction; do not imply they were known at the time. Use this field for who the user was talking to (`self` is already filtered out), relationship context, and repeated patterns.
- `entry`: `{id, body, source, occurred_at, created_at, updated_at, tags, people}` — the logged interaction. `tags` is a list of strings, `people` is the faithful link list (includes `self` if linked).
- `prior_feedback`: previously stored feedback for this entry, ordered oldest-first — `[{id, entry_id, body, created_at, updated_at}]`.
- `goals`: active goals, newest-first, capped by `--goal-limit` (default 10, zero omits goals). Each goal has `{id, title, detail, status, person, review_id, outcome, closed_at, created_at, updated_at, is_open}`. Treat a goal as relevant when its scope or linked person bears on this interaction; do not force every goal into the feedback.
- `goal_summary`: `{limit, count, total_count, truncated}` — active-goal coverage across the whole journal, including when the limit is zero. An empty `goals` array means there are no active goals only when `total_count` is zero. If truncated, acknowledge the partial coverage; increase `--goal-limit` when the request needs a complete assessment.
- `prior_reviews`: up to `--prior-review-limit` stored syntheses, oldest-first. Each review has `{id, body, scope_kind, scope_limit, scope_person, scope_person_handle, scope_tag, scope_since, scope_until, created_at, updated_at, entry_ids, entries}`. `scope_kind` is one of `latest`, `person`, `tag`, or `range`, and only the axis keys that kind owns are non-null. `scope_person` is a live lookup that becomes `null` once that person is deleted; `scope_person_handle` is the stored handle and survives, so use it when naming who a prior review was about. Use these for longer-running progress and recurring-pattern context without repeating them verbatim.

### Step E2: Generate coaching feedback

Use the rubric below. Read every field of the payload. Address `self` as "you" and refer to counterparties by `@handle` (or by `name` when set). Do not invent facts not present in the payload.

#### Rubric

1. **Situation** — one or two sentences naming what happened, who was involved, and the source/medium (Slack, email, meeting, etc.) when present.
2. **What worked** — 1–3 concrete strengths in how you communicated, grounded in the entry body.
3. **What to improve** — 1–3 concrete weaknesses framed as observations, not judgments. Quote short fragments from the entry body when helpful.
4. **Goal check** — for each relevant open goal, write exactly one line judging whether this interaction **advanced**, **hurt**, or was **neutral** to it. Name the goal and cite concrete evidence from the entry body. Omit the entire Goal check section when `goals` is empty; when goals exist but none is relevant, omit it rather than manufacturing a connection.
5. **Try next time** — 1–3 specific, actionable suggestions. Prefer restating or sharpening an existing open goal over inventing a new one. Prefer phrasing the user can reuse verbatim in the next similar exchange.
6. **Follow-ups** — optional. Threads to revisit with the counterparty, or questions to consider before the next interaction.

#### Constraints

- Keep total output under ~400 words.
- Plain Markdown only. No HTML, no YAML front matter.
- If `prior_feedback` is non-empty, briefly note progress or recurring patterns. Do not restate earlier feedback verbatim.
- Tone: direct, supportive, specific. No filler, no praise sandwiches.
- Make sure the body does not contain a line equal to `__COMMSCOACH_FEEDBACK_EOF__` (the heredoc sentinel used in Step E3).

### Step E3: Store the feedback

Pipe the generated Markdown via stdin so multi-line content with quotes, backticks, and shell metacharacters survives unscathed. Replace the body between the sentinels with the Markdown produced in Step E2.

```bash
commscoach --json feedback add <id> - <<'__COMMSCOACH_FEEDBACK_EOF__'
<generated feedback markdown>
__COMMSCOACH_FEEDBACK_EOF__
```

Notes:

- Stdout is JSON: `{id, entry_id, body, created_at, updated_at}`.
- A non-zero exit means the entry id was invalid or the body was empty. Surface stderr and stop.

### Step E4: Report

Parse the JSON returned in Step E3 and print one line to the user:

```
Stored feedback <id> on entry <entry_id>.
```

Then display the generated coaching Markdown below that line so the user can read the feedback without re-fetching it.

## Stored Review Flow

Use this flow for requests that ask for a synthesis across a slice of communication entries — the latest ones, or one person, one tag, or one date range.

### Step R1: Fetch review context

#### Choose the scope

A review answers a question about a slice of the journal, and that slice is its scope. Decide the scope first, because it decides which `review context` command runs.

Scope is **single-axis**: the latest entries, *or* one person, *or* one tag, *or* one date range — never a combination. Flags from two axes exit 1 (`--person cannot be combined with --tag`).

| Request | Axis flag | Scope kind |
| --- | --- | --- |
| "review recent comms", "how have I been communicating lately?" | none | `latest` |
| "how am I doing with @alice?" | `--person alice` | `person` |
| "how is my conflict tag trending?" | `--tag conflict` | `tag` |
| "how did last quarter go?" | `--since <previous-quarter-start> --until <previous-quarter-end>` (explicit ISO dates) | `range` |

```bash
commscoach review context --limit 20 --prior-review-limit 3 --goal-limit 10
commscoach review context --person alice --limit 10
commscoach review context --tag conflict --limit 10
commscoach review context --since "12 weeks ago" --until today
```

Scope rules:

- Do not pass `--scope KIND`. It is an optional assertion that must agree with the axis flags, so passing it only adds a way to exit 1. Let the kind be derived from the flags.
- `--since` and `--until` accept ISO-8601 or relative dates ("12 weeks ago", "today"). Either bound alone selects `range`. Bounds are inclusive against when the entry occurred, falling back to when it was created.
- Resolve named calendar periods from the current local date before fetching context. "Last quarter" means the previous completed calendar quarter; "last month" and "last year" likewise mean completed calendar periods. Pass explicit first and last dates (the CLI includes both full local days), and state that window in the review. For example, on October 4, 2026, last quarter is `--since 2026-07-01 --until 2026-09-30`. Reserve rolling windows such as `--since "12 weeks ago" --until today` for requests for the past 12 weeks; use fiscal periods only when the user's fiscal calendar is known.
- Handles and tag names match case-insensitively. The tag is normalized before matching and stored normalized, so `--tag Conflict` both scopes and stores `conflict`.
- `--limit` caps the entries under every scope.
- To resolve a name the user gave loosely, `commscoach person list` and `commscoach tag list` are cheap lookups. Run one before guessing at a handle or tag.
- The skill does not run `tag rename`, `tag merge`, or `tag rm`. Those are destructive vocabulary changes and stay a manual CLI act.

Scope failures, which mean opposite things:

- **Unknown person handle** — exits 1. That is a typo, not a finding. Surface stderr and stop.
- **Reversed range** (`--since` after `--until`) — exits 1 on both `review context` and `review add`. Surface stderr and stop.
- **Scope matched nothing** — succeeds with an empty `entries` list and `scope.total_entry_count` of zero. That is a valid answer. Tell the user there are no entries in that scope yet (e.g. "no entries with @alice yet") and stop without generating or storing a review.

#### Choose the limits

Limits combine with any scope. Choose them from the user request:

- Default: `--limit 20 --prior-review-limit 3`.
- If the user asks for "last N" or "recent N" entries, use `--limit N`.
- If the user asks for a fresh review or to ignore history, use `--prior-review-limit 0`.
- If the user asks to compare against more or fewer prior reviews, adjust `--prior-review-limit`.
- If the user asks to consider more or fewer active goals, adjust `--goal-limit` (default 10).

Notes:

- This subcommand always emits JSON; `--json` is optional and has no effect here.
- A missing `self` prints a clear stderr message and exits 1. Surface stderr to the user and stop. Suggest `commscoach person add me --self` (or `commscoach person self <handle>`) before retrying.
- If `entries` is empty, inspect `scope.total_entry_count`: zero means no matching entries; a positive total means the limit omitted them. Report the distinction and stop without storing an empty review. The same distinction applies to a seemingly empty scope.
- `--limit` controls how many entries are gathered for the synthesis, under every scope. `--prior-review-limit` controls how many prior stored reviews are included for progress comparison.
- `--goal-limit` controls how many active goals are included for explicit goal-progress judgments.

Parse stdout as JSON. The payload shape is exactly:

- `self`: `{id, handle, name, is_self, notes}` — the user being reviewed. Address this person as "you" and tailor the review to their coaching preferences in `notes` when set.
- `scope`: `{kind, limit, entry_count, total_entry_count, entries_truncated, person, tag, since, until}` — review scope metadata. `kind` is the resolved scope kind (`latest`, `person`, `tag`, or `range`); `limit` is the source context limit used to gather entries; `entry_count` is the actual number of entries returned; `total_entry_count` counts matching entries before the limit and `entries_truncated` reports omissions. `person` is a `{id, handle, name, is_self}` object or `null`; `tag`, `since`, and `until` are strings or `null`. `kind`, `limit`, `entry_count`, `total_entry_count`, and `entries_truncated` are always present; the four axis keys are `null` for the kinds that do not own them.
- `entries`: entries in the scope, in oldest-to-newest synthesis order — `[{entry, feedback}]`.
- `entries[].entry`: `{id, body, source, occurred_at, created_at, updated_at, tags, people}`.
- `entries[].feedback`: stored coaching feedback for that entry, oldest-first — `[{id, entry_id, body, created_at, updated_at}]`.
- `prior_reviews`: stored reviews in oldest-to-newest order — `[{id, body, scope_kind, scope_limit, scope_person, scope_person_handle, scope_tag, scope_since, scope_until, created_at, updated_at, entry_ids, entries}]`. A prior review's `scope_limit` is its stored source context limit, not its covered-entry count. `scope_person` is a live lookup that becomes `null` once that person is deleted; `scope_person_handle` is the stored handle and survives, so use it when naming who a prior review was about.
- `goals`: active goals, newest-first, capped by `--goal-limit`. Each has `{id, title, detail, status, person, review_id, outcome, closed_at, created_at, updated_at, is_open}`.
- `goal_summary`: `{limit, count, total_count, truncated}` — active-goal coverage across the whole journal, including when the limit is zero. An empty `goals` array means there are no active goals only when `total_count` is zero. If truncated, acknowledge the partial coverage; increase `--goal-limit` when the request needs a complete assessment.

Two asymmetries in this payload are deliberate and change how you read it:

- `prior_reviews` is scope-filtered for `person` and `tag` only, so a review of @alice is measured against previous reviews of @alice. Under `latest` and `range` it is the unfiltered recent listing instead — do not read those priors as same-scope predecessors.
- `goals` is never scope-filtered, only capped by `--goal-limit`. Judge relevance per goal yourself; a person- or tag-scoped review is still measured against your general goals.

### Step R2: Generate review Markdown

Read every returned entry, its feedback, the linked people, tags, source, timing, and prior reviews. Do not invent facts not present in the payload. When `scope.entries_truncated` is true, state the coverage (for example, "latest 20 of 63 matching interactions") and limit conclusions to that sample. If the user requested the entire scope, increase `--limit` to cover it before synthesis. If `goal_summary.truncated` is true, fetch the complete active list with `commscoach --json goal list` before proposing new experiments, so omitted goals are not duplicated. Only goals fetched in Step R1 and assessed against the reviewed evidence are eligible for closure.

When `scope.kind` is not `latest`, the scope is the subject of the review:

- Name the scope in the opening sentence, and keep the whole synthesis inside that slice.
- Under `person`, refer to the counterparty by `@handle` (or `name` when set) and read the strengths and risks as patterns *in that relationship*.
- Under `tag`, the theme is the subject; under `range`, the window is.

Generate a concise Markdown synthesis with these sections:

1. **Recurring strengths** — patterns that appear across multiple entries or that are especially consequential.
2. **Risks to watch** — recurring communication risks, friction points, or missed opportunities.
3. **Progress since prior reviews** — compare against `prior_reviews` when present; otherwise state that there are no prior stored reviews yet. Under `person` and `tag` scope these are same-scope predecessors, so compare directly. Under `latest` and `range` they are general recent reviews, so compare loosely and say that is what you are comparing against.
4. **Goal progress** — one line per goal from `goals`, judging it **advanced**, **stalled**, **achieved**, **no longer relevant**, or **not assessed—no relevant evidence**. Name the goal ID and title and ground assessments in the reviewed entries. Use **not assessed** when the entries do not provide enough relevant evidence, including goals about another person; absence from this slice does not establish stalled progress or irrelevance. If `goal_summary.total_count` is zero, say there are no active goals; otherwise describe any omitted goals as unassessed.
5. **Next experiments** — 0–3 new actions to try next. Propose a new experiment only when the evidence supports a useful action beyond the active goals. Write each new experiment as a goal title of roughly 80 characters or less, followed by an optional detail paragraph that explains when or how to practice it. When continuing existing goals is the useful next step, say so and name their IDs without creating new goal records.

Constraints:

- Plain Markdown only. No HTML, no YAML front matter.
- Ground claims in the provided entries, feedback, and prior reviews. Use short quotes only when they add clarity.
- Keep the review focused on patterns across entries, not a per-entry recap.
- Do not present an already active goal as a new experiment. Evaluate it under Goal progress and refer to its ID when recommending continued practice. Every proposed new experiment must be materially distinct from the active goals.
- Never propose more than three new goals in one review.
- Tone: direct, supportive, specific. No filler, no praise sandwiches.
- Make sure the body does not contain a line equal to `__COMMSCOACH_REVIEW_EOF__` (the heredoc sentinel used in Step R3).

### Step R3: Store the review

**Pass `review add` the same axis flags you passed `review context` in Step R1**, so the stored review records the slice it was actually generated from. `review add` derives its scope kind from those flags the same way `review context` does; do not pass `--scope KIND`, and never fall back to no axis flag when the context was scoped.

Build the covered entry ID list from `entries[].entry.id`, preserving the context order. Store the review with `--limit <scope.limit>` from the fetched context so `scope_limit` records the source context limit used for the synthesis. Covered entries are the explicit `--entry` IDs; do not treat `scope.limit` as the covered-entry count.

Pipe the generated Markdown via stdin so multi-line content with quotes, backticks, and shell metacharacters survives unscathed. Replace the body between the sentinels with the Markdown produced in Step R2.

```bash
commscoach --json review add - [AXIS FLAGS] --limit <scope.limit> --entry <entry_id_1> <entry_id_2> ... <<'__COMMSCOACH_REVIEW_EOF__'
<generated review markdown>
__COMMSCOACH_REVIEW_EOF__
```

`[AXIS FLAGS]` is exactly what Step R1 used — one of:

```bash
commscoach --json review add - --limit 20 --entry 1 2 3                            # latest
commscoach --json review add - --person alice --limit 10 --entry 1 2               # person
commscoach --json review add - --tag conflict --limit 10 --entry 1 2               # tag
commscoach --json review add - --since 2026-06-01 --until 2026-09-01 --entry 4 5   # range
```

Notes:

- `--entry` accepts one or more IDs; repeating `--entry` is also valid.
- For a `range` scope, use the resolved ISO timestamps from `scope.since` / `scope.until` over re-sending a relative phrase, so the stored bounds match the context exactly.
- Stdout is JSON: `{id, body, scope_kind, scope_limit, scope_person, scope_person_handle, scope_tag, scope_since, scope_until, created_at, updated_at, entry_ids, entries}`. Check that `scope_kind` is the kind you intended.
- A non-zero exit means the body was empty, an entry id was invalid, or the scope arguments were invalid. Surface stderr and stop.

### Step R3b: Persist the loop

Parse the stored review JSON and retain its `id` as `<review_id>`. Also retain the IDs of the active goals fetched in Step R1; only those pre-existing goals are eligible to close in this run.

For each new experiment in Next experiments, create an active goal linked to the stored review. Continuing an existing goal creates no new record; when there are no new experiments, skip goal creation and still assess eligible closures below. Use the short goal title as `<title>` and pipe its optional detail paragraph through stdin:

```bash
commscoach --json goal add "<title>" --review <review_id> --detail - <<'__COMMSCOACH_GOAL_DETAIL_EOF__'
<experiment detail paragraph>
__COMMSCOACH_GOAL_DETAIL_EOF__
```

If the experiment has no detail paragraph, omit `--detail -` rather than piping an empty body. Parse and retain each created goal's JSON result.

For a relationship-specific experiment, also pass `--person <handle>` using the existing person identified in the context. In a person-scoped review, use `scope.person.handle` when the experiment concerns that relationship. Keep broadly applicable habits general by omitting `--person`, even when they arose in a person-scoped review. A goal can link to only one person; do not choose an arbitrary person for a shared experiment. Retain `--review <review_id>` in every case and check the returned goal's `person` and `review_id` before reporting success. For example, a new experiment about Alice uses `commscoach --json goal add "Ask Alice to confirm the next step" --review <review_id> --person alice`.

For every pre-existing goal judged **achieved**, close it as done and pipe an outcome grounded in the reviewed entries:

```bash
commscoach --json goal close <goal_id> --outcome - <<'__COMMSCOACH_GOAL_OUTCOME_EOF__'
<evidence-grounded outcome>
__COMMSCOACH_GOAL_OUTCOME_EOF__
```

For every pre-existing goal judged **no longer relevant**, add `--dropped`; `--dropped` means the goal is no longer relevant, not that it failed:

```bash
commscoach --json goal close <goal_id> --dropped --outcome - <<'__COMMSCOACH_GOAL_OUTCOME_EOF__'
<evidence-grounded reason it is no longer relevant>
__COMMSCOACH_GOAL_OUTCOME_EOF__
```

Persistence rules:

- Close a goal only when evidence in the reviewed entries supports **achieved** or **no longer relevant**. Do not close on aspiration, weak inference, or prior-review text alone.
- Leave **not assessed** goals unchanged; it is a review judgment, not a stored goal status.
- Never close a goal created in this same run, even if its wording overlaps an old goal. Only IDs captured from Step R1 may be closed.
- Create at most three new goals per review.
- If any goal mutation fails, surface stderr, report which review and goal mutations already succeeded, and stop instead of pretending the loop was fully persisted.

### Step R4: Report

Parse the JSON returned in Step R3 and print one line to the user, naming the scope that was stored:

```
Stored review <id> (scope <scope_kind><scope detail>) covering <entry_count> entries: <entry_ids>.
```

Render `<scope detail>` from the stored axis: nothing for `latest`, ` @<scope_person_handle>` for `person`, ` <scope_tag>` for `tag`, and ` <scope_since>..<scope_until>` for `range` (omitting an absent bound).

Then display the generated review Markdown below that line so the user can read the synthesis without re-fetching it.

Also report the IDs and titles of goals created, and the IDs of goals closed as done or dropped. If none were created or closed, say so explicitly.

## Correcting stored coaching

Feedback notes and review bodies hold AI-generated text, so they are the records most likely to be wrong or badly worded. Both are editable in place.

Run these **only** when the user explicitly asks to change or remove a specific stored record. Never as cleanup, and never as a step inside the Entry Feedback Flow or the Stored Review Flow.

### Editing a body

```bash
commscoach --json feedback edit <feedback_id> --body - <<'__COMMSCOACH_FEEDBACK_EOF__'
<corrected feedback markdown>
__COMMSCOACH_FEEDBACK_EOF__
```

```bash
commscoach --json review edit <review_id> --body - <<'__COMMSCOACH_REVIEW_EOF__'
<corrected review markdown>
__COMMSCOACH_REVIEW_EOF__
```

Notes:

- `--body` follows the usual body-source convention: literal text, `-` for stdin, or `@PATH` for a UTF-8 file. Prefer `-` with a heredoc, as in the add flows.
- Do **not** pass `--body` with no value. That opens `$EDITOR` pre-filled with the current body, which this skill cannot drive.
- `feedback edit` and `review edit` require `--body`; omitting it exits 1.
- An edit updates `updated_at` and leaves `created_at` alone.
- `review edit` rewrites the body only. A review's scope and covered-entry list are its provenance — the record of what it was generated from — and are not editable. If the user wants either changed, say so and offer to delete the review and store a new one; do not quietly rewrite the body instead.
- Stdout is the updated record: `{id, entry_id, body, created_at, updated_at}` for feedback, the full review shape for a review. Report the id and show the corrected Markdown.

### Deleting a record

Deletion is not undoable from the CLI. `feedback rm` and `review rm` prompt for confirmation, and this skill runs non-interactively, so it must pass the global `--yes` — which removes that prompt. Replace it with an explicit confirmation in the conversation:

1. Fetch the record (`commscoach --json feedback show <id>` or `commscoach --json review show <id>`).
2. Tell the user the id and quote its first line or two, so they can see what will be lost.
3. Wait for an explicit go-ahead. Do not proceed on an ambiguous reply.
4. Only then run the delete.

```bash
commscoach --yes --json feedback rm <feedback_id>
commscoach --yes --json review rm <review_id>
```

Stdout is `{"deleted": true, "id": <id>}`. Report that line back.

## Coaching Backlog Flow

When the user asks to catch up on uncoached communication, fetch up to ten entries with no stored feedback:

```bash
commscoach entry search --no-feedback --oldest-first --limit 10
```

Show the returned entries in their CLI order and ask which entry ID to coach. Do not claim an empty result means there are no entries at all; it means the current backlog query found no entries without feedback. Once the user selects an ID, use the Entry Feedback Flow.

## Stats Flow

When the user asks "how am I doing?" or requests journal activity, feedback coverage, goal counts, or cadence, use the stored aggregate instead of re-deriving numbers from entry text:

```bash
commscoach --json stats
```

Add `--since`, `--until`, `--top`, or `--weeks` after `stats` when the user specifies a window or output limit. Resolve named calendar periods to explicit dates using the review scope rules above and state the interpreted window. Parse the JSON and report the actual numbers, including zero values; do not estimate or recompute them from other payloads.
