---
status: active
branch: feature/sheets-sync-adoption-h-docs-and-release
---

# 007h — Write the guide sections, check the exports, and state the live budget

Part of [007](../plan.md). Step 8 of the umbrella's implementation order, and the last.
Each earlier step adds its config fields, its rows in the guide's tables, and its
changelog lines. This step writes what spans them, and checks the whole against what
was built.

Notes applied: D5, D7, D10, M6, M9, R15, and the probe finding P13.

## Guide sections (`docs/sheets-sync.md`)

### Moving an existing sync over [D5, M6, D10]

For a caller that already has local files, a base, or both, made by its own code. It
says what the first run will report and why, so that a preview full of edits is
expected and not alarming:

- **Unformatted reads.** Tabs are read with `UNFORMATTED_VALUE` (`table.py:79-85`). A
  base or a file saved from displayed values differs wherever a cell has a number
  format: `50%` reads `0.5`, `$1,234.50` reads `1234.5`, and a 3 shown as `3.00` reads
  `3` [P13]. Each such cell is reported as a sheet edit once and folded in.
- **Starting over instead.** A caller that does not want those folds deletes its base
  and makes a first sync, with `--adopt` when the local file should win.
- **Typed dates.** A declared `date` or `datetime` column reads as ISO 8601 (step b),
  so a file that holds display text differs in every row of it.
- **Blank keys.** A blank key cell is refused on every side, the base included
  (`cells.py:138`), which is stricter than an engine that only refused duplicates.
  `blank_keys: "partial"` is for a composite key; for a one-column key the refusal is
  right and the rows have to be fixed.
- **Line endings.** Files are written with LF unless the tab says otherwise.
- **An existing layout.** `--config PATH` and a target's `base` field place the config
  and the base snapshots where a caller already keeps them.
- **Look first.** A preview shows all of it before anything is written.

Note D5 reasoned the examples from the code. They were checked live on 2026-09-27
([note 005](../implementation-notes/005-serial-and-format-probe.md)), so the section
is written from what the API returned.

### A caller's own retry [D7]

Since 0.11.0 the value wrappers retry inside themselves. A caller that wrapped
`pull_values` or `batch_update_values` in its own retry, as it had to before, now makes
up to its own attempts times five calls, and a persistent 5xx stalls for minutes before
it surfaces. The section says to remove the caller's retry, and how to see the
library's (`retry_notices`, step g). The same sentence is added to the 0.11.0 changelog
entry that announced the change.

### Pulling and pushing the same files [M9]

A workflow that pulls the tabs over the local files, reviews the diff, and pushes back
only when the local side was edited does not fit `sync` mode: a row deleted on the
sheet is flagged on every run until a person deletes it on the other side. `pull` and
`push` fit, and a tab has one mode.

- The way through is two targets that name the same spreadsheet and the same files,
  one with `pull` tabs and one with `push` tabs. The loader allows it, because a `push`
  tab is not counted as a writer of its local file (`config.py:334`).
- The section shows the config and the two commands.
- A tab mode that allows both stays out of scope, and applying row deletions in `sync`
  mode stays open from plan 006.

### Sections that earlier steps started

Each gets its reference rows in its own step and its prose here:

| Section | From |
|---|---|
| How cells are read: typed dates, and that a pushed date or number is text on the sheet, which plan [010](../../010-sheets-typed-writes/plan.md) takes up | b |
| Holding invalid cells, and partial keys | c |
| Hooks: `validate`, `check`, and `warn`, which are library only, so a caller with checks in code keeps a command of its own [D3] | d |
| Writing a store, with a worked example | e |
| Links, and using the cells a run wrote | f |

## Docs checked against the Logs

Plan 006's guide was written from the design sections, and one of them had been
refined by a decision recorded only in a Log. Before this step's PR is opened, each
claim in the guide that came from a subplan is read against that subplan's Log, and
each example in the guide is run.

## Exports and project docs

- `gdrives/sheets/__init__.py`: every public name a step added is imported and in
  `__all__`. A test lists the names this plan promises and imports each.
- The project `CLAUDE.md`: `stores.py` in the package structure, the new command and
  options, and the paragraph on sync.
- `README.md`: the command list.

## Changelog

Each step wrote its own lines under `[Unreleased]`. This step reads the section as one,
orders it, and checks it against the umbrella's
[Compatibility](../plan.md#compatibility) table: every row there has a line under
Changed. Promotion to a version is part of the release, which the owner cuts.

## Live suite budget [R15]

The suite made 55 writes and 66 reads before this plan, against limits of 60 a minute
each, and waits out the refusals.

- Each new live case makes at most 6 writes and 6 reads. The plan adds five cases (the
  table in the umbrella's [Testing](../plan.md#testing)), so at most 30 of each.
- The single tab listing per run (step g) takes a read out of every sync, pull, and
  push the suite runs.
- This step records the suite's totals, and the wall time of two runs back to back,
  which must both pass.
- The probe of note 004 left no tab behind. A run that fails in teardown can; a
  leftover `itest_` tab is listed for the owner and never deleted by a session.

## Tests

- The export test above.
- The guide's config examples are loaded by `parse_config` in a test, so an example
  with a field the loader refuses fails the suite.
