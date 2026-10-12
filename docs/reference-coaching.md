# Coaching reference

[Back to the quick start](../README.md#quick-start)

See also: [storage, capture, tags, and review scopes](reference.md), [coaching and skill integration](reference-coaching.md), [goals, stats, and operations](reference-operations.md).

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

Revisit the experiments that came from a review:

```sh
commscoach review show 1 --with-goals
```

`--with-goals` includes all goals linked to that review, with their details, status,
and recorded outcomes. Active goals appear first; within active and closed groups,
goals are newest-first by creation time, then ID. Human output shows `(no goals)`
when none are linked. JSON adds a `goals` array only when the flag is supplied.

Before a conversation, see someone's notes, recent interactions, and active goals together:

```sh
commscoach person prep jdoe
commscoach person prep jdoe --recent 3 --reviews 1
commscoach person prep jdoe --include-general-goals
commscoach person show jdoe --recent 5
commscoach person show jdoe --recent 5 --with-feedback
commscoach --json person show jdoe --recent 5
```

`person prep HANDLE` combines private notes, all active goals linked to that person,
five recent interactions with full feedback, and two person-scoped reviews. Human
output puts goals before the interactions and shows each goal's full detail beneath
its title, so your current experiments are easy to act on. Override the counts with
`--recent N` and `--reviews N`; either accepts zero.
Its JSON output is the same as `person show HANDLE --recent 5 --with-feedback --reviews 2`
with the corresponding limits. General goals are excluded by default. Add
`--include-general-goals` to include active goals with no linked person alongside
this person's active goals, newest-first. Human output labels these goals
`(general)`; JSON includes them in `active_goals`. This includes goals detached
by deleting their person. Closed goals and goals linked to other people are excluded.
Goal details are already present in JSON, so its shape is unchanged.

`--recent N` adds the latest N interactions by occurrence time (falling back to creation time), plus all active goals linked to that person. General goals and goals linked to other people are excluded. `--recent 0` shows no interactions but still includes active goals. JSON adds `recent_entries` and `active_goals` arrays only when this option is supplied; plain `person show` retains its existing output.

Add `--with-feedback` alongside `--recent N` to include full coaching notes beneath each interaction, oldest-first, or `(no feedback)` when there are none. In JSON, this adds a `feedback` array to each item in `recent_entries`. Without the flag, the existing output is unchanged.

Add `--reviews N` to include the latest N reviews scoped to that person, with their full bodies, newest-first (creation time, then ID):

```sh
commscoach person show jdoe --recent 5 --with-feedback --reviews 2
commscoach person show jdoe --reviews 2
```

This option works independently of `--recent`. It includes person-scoped reviews, not general or tag reviews that happen to cover an interaction with that person. Human output shows `(no reviews)` when none match; JSON adds a `recent_reviews` array only when `--reviews` is supplied. `--reviews 0` returns an empty array, and negative limits are rejected. Renaming a person's handle carries their review history into this view as well.

`person rm HANDLE` reports how many private notes will be deleted, entry links
removed, and goals and person-scoped reviews detached before asking for confirmation.
Entries, feedback, goals, and reviews remain; person-scoped reviews keep the handle
as history. Deleting your self profile also explains that coaching context needs a
new self profile. `--yes` continues to bypass confirmation.

For a shorter terminal view, use `person prep HANDLE --brief`. It shows opening
prose previews of private notes, goal details, feedback, and reviews, each at most
180 characters. It includes the five newest active goals and only the latest
feedback note per interaction, reporting when more goals or feedback notes exist.
Goal title rows are shortened too. Commands beneath the previews open the full
records; an all-goals command appears when goals are omitted. These commands
preserve an explicit `--db` path and the all-goals command preserves
`--include-general-goals`. This is a text preview, not an AI summary. Existing
interaction/review count options still apply. Full prep and JSON remain complete.
Every interaction has a copyable `entry show ID --with-feedback` command,
including interactions that have no feedback yet.

Find a person's handle with `person list --query "John"`. This filters names and
handles using literal, case-insensitive substring matching, retaining self-first,
then handle-alphabetical order. `%`, `_`, and `!` are literal characters; blank
queries are rejected. An unmatched query returns an empty list.

Add `person list --with-activity` to show each person's last logged interaction,
interaction count, and active goal count. It combines with `--query` and retains
the usual ordering. Dates use occurrence time, falling back to creation time;
people with no interactions show `(none)` and zero. Counts include only goals
linked to that person and exclude done or dropped goals. With `--json`, the flag
adds an `activity` object containing `last_logged_interaction` (UTC timestamp or
`null`), `interaction_count`, and `active_goal_count`. Without the flag, output is
unchanged.

Read the evidence behind a review with `review show ID --with-entries`, optionally
combined with `--with-goals`. This shows the full current text and metadata of its
surviving linked entries, in the review's existing entry order. It is not a snapshot
of the text at review creation. Deleted entries are omitted; if none remain, the
view says so. Review JSON already includes complete linked entries and is unchanged.

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

Reviews may propose zero to three new experiments. When existing goals already
cover the useful next steps, the skill names those goals without creating duplicates.
Goals lacking relevant evidence in the reviewed slice are reported as not assessed
and left unchanged. New relationship-specific goals link to both the review and
the person, making them available in that person's conversation prep; broader
habits remain general goals.

For reviews and stats, requests such as "last quarter" use the previous completed
calendar period and report its explicit dates. For example, on October 4, 2026,
last quarter means July 1 through September 30, rather than a rolling 12-week window.

Both context commands include your self profile's private notes as `self.notes` (`null` when unset). Use them for durable coaching preferences, and edit or clear them with the existing person commands:

```sh
commscoach person edit me --notes "Help me be direct without sounding abrupt. Prefer short examples."
commscoach person edit me --notes            # edit existing preferences in $EDITOR
commscoach person edit me --clear-notes
```

Replace `me` with your self profile's handle. These notes are included even when the self profile is not linked to the entry.

Entry coaching history includes only interactions strictly before the target entry's occurrence time, falling back to creation time for entries without an occurrence date. Interactions at the same instant are excluded. The newest matching interactions are selected before applying `--history`, then returned oldest-first. Notes, feedback, active goals, and prior reviews remain current coaching context, not snapshots of what was known at the time.

Each person in entry context also has a `history_summary` with `limit`, `count`, `total_count`, and `truncated`. The total counts only eligible interactions strictly before the target, so the coach can distinguish "three prior interactions exist" from "three of 27 were included." `--history 0` still reports these totals, and a person with no eligible history has `total_count: 0` and `truncated: false`.

Like review context, `context ENTRY_ID` accepts `--goal-limit N` (default 10).
Before applying the limit, goals for the entry's non-self participants come first,
then general goals, then other goals. Each group is newest-first by creation time,
then ID. Entries without non-self participants retain global newest-first ordering.
Goals remain globally eligible; the top-level `goal_summary` reports `limit`,
`count`, `total_count`, and `truncated`. `--goal-limit 0` returns no goals while
still reporting how many active goals exist. Increase the limit when you need a
complete goal assessment:

```sh
commscoach context 42 --goal-limit 20
```

The skill is [scope](reference.md#review-scopes)-aware. It derives the scope from how you ask — "how am I doing with @alice?" becomes `--person alice`, "how is my conflict tag trending?" becomes `--tag conflict`, "how did last quarter go?" becomes a `--since`/`--until` range — and passes the same axis flags to `review add` that it passed to `review context`, so the stored review records the slice it was generated from. It reports the stored scope back to you. A scope that matches nothing is reported as an answer rather than stored as an empty review.

The skill also runs [`feedback edit` / `feedback rm` / `review edit` / `review rm`](#correcting-stored-coaching), but only when you explicitly ask it to change or remove a named record — never as cleanup inside another flow. Because it runs non-interactively it has to pass `--yes`, so it asks for confirmation in the conversation before either `rm`.
