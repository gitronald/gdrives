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

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-h-docs-and-release`, cut from step g's branch
after plan 008 and the 0.12.0 release had been merged up the stack, with a draft PR
onto it. Written at 2026-09-27T14:33:37-07:00.

| Commit | Part |
|---|---|
| `6102071` | The guide's three sections, its corrections, and the README |
| `6128b3f` | `tests/test_sheets_guide.py` |
| `f89612c` | The changelog |

**Where the guide departs from this file's design.** Two statements of the design
were run against `FakeSheetGrid` before they were written, and neither held as
worded:

- **Typed dates.** The design says a file that holds display text in a declared
  `date` column differs in every row of it. It does not get that far: the check of
  the local rows refuses the file, and nothing is written. The guide says to rewrite
  the column before declaring it, and that an undeclared column reads as before.
  Step b's Log had the same finding from the side of the base.
- **Starting over.** The design says a caller that does not want the folds deletes
  its base. A bootstrap takes the local file as the base, so the same cells fold in.
  Only `--adopt` avoids them, and it writes the local file's text over the sheet: a
  cell holding the number 0.5 then holds the text `50%`. The guide says both, and
  says that folding is the way through.

The section on a caller's retry gives the waits as they are in `retry.py` (1, 2, 4,
and 8 seconds, each with up to a second of jitter, so 15 to 19 seconds for one call
that keeps failing). A wrapper of five attempts holds a run for more than a minute,
which is what the guide says where the design said minutes.

**The guide read against the Logs.** Each correction, and the Log it came from:

| Guide | Was | From |
|---|---|---|
| Where new rows go | Silent on an `insert_above` column the tab lacks | a |
| Dates | Silent on the two reads not being one moment, which a pull does not guard | b |
| Holding invalid sheet values | Silent on overrides, and on a tab with `owns_rows` | c |
| Hooks | `push_rows` not named, and nothing on what the context holds at each stage | d, f |
| Links | The cost of a sync with pushes and no new rows left out the read of the grid size, and nothing said the header row is cleared too | f |
| Requests | Said a run lists the tabs once, which a run that creates a tab does not | g |
| Credentials and scopes | Read against plan 008: the order of stderr on a preview that waits, a broader cached token, and `gdrives login` | 008 |

Three things were wrong in the guide's structure and came from no Log: the contents
had no entry for Links, the paragraph on carried columns had ended up inside the
section on `sheet_id`, which step g added above it, and two paragraphs had lines cut
short by earlier edits.

**Every example runs, as a test.** The design asked for the examples to be run and
for two tests. `tests/test_sheets_guide.py` has those two and makes the run a test
too, so that an example cannot go stale after this step:

- The names each step added to `gdrives.sheets` are listed by step and imported, with
  `slug` in `gdrives.local`, and the fields and properties added to classes that
  existed. The list is the 39 names that `__all__` gained since 0.12.0, and `Records`.
- Every JSON example of the guide and of the README goes through `parse_config`. An
  example that shows one tab, or the output of `sheets-widths`, is wrapped in a
  target first.
- The names in the guide's two field tables are the loader's own sets of fields.
- Every `gdrives` command line of the guide and of the README is parsed by the
  command-line parser, with `--help` added so that nothing runs. It checks that the
  command and its options exist, and not the values given to them.
- The guide's six Python examples run in order in one namespace against
  `FakeSheetGrid`. The last one's `check` refuses the column that the links example
  reads, which is the hook doing what the example says.

**The changelog.** `[Unreleased]` holds this plan's lines only, since plan 008's
were promoted in 0.12.0. The blank lines between the groups under Added are gone.
Added keeps the order of the steps, which is an order of subject. Changed is ordered
by who meets the change: what a sync compares and reads, what it writes and where,
the report, the requests, and then the library's types.

- Every row of the umbrella's Compatibility table has a line under Changed.
- Four lines under Changed have no row in that table, each written by the step that
  made the change: a preview refusing an `insert_above` column the tab lacks (a), the
  `in sync` line (c), the equality of `ApplyResult` (f), and one tab listing per run
  (g). The table is left as it was designed.
- Two lines named 0.11.0 as the version whose behavior changed. 0.12.0 shipped in
  between with the same behavior, so they say `an earlier version`.
- The 0.11.0 entry on the wrappers' retry has the sentence on a caller's own.
- One line was added for the guide's sections. Nothing is promoted to a version.

**Seen and left.**

- `ColumnType`, `Check`, and `Validate` are type aliases a caller's annotations could
  use, and none is exported. `Validate` was not in 0.11.0 either, and the package's
  surface test exports what a module defines as a class, a function, or a constant.
- The project `CLAUDE.md` is not tracked, so it cannot travel with this PR. Its
  lines are drafted and left with the owner.

**Live suite budget.** Two runs of the whole Sheets suite back to back, by the
orchestrating session, with requests counted at the HTTP layer:

| Run | Result | Wall time | Writes | Reads | Refused and sent again |
|---|---|---|---|---|---|
| 1 | 25 passed | 123 s | 79 | 97 | 7 reads |
| 2 | 25 passed | 152 s | 79 | 97 | 6 reads |

| | Writes | Reads |
|---|---|---|
| Before this plan | 55 | 66 |
| The five new cases, as they are now | 24 | 32 |
| The read the single listing took out of an older case | | -1 |
| After | 79 | 97 |

- This step adds no live case and changes no request, and the totals are step g's.
- The plan stays inside the 30 of each that this file allowed for its five cases in
  writes, and is 2 over in reads. Two cases are over the 6 reads allowed for one:
  the sync that places a row and a column (9), and the push with `clear_links` (9).
  The Logs of steps a and f say what each read is for.
- Both counts are over the limit of 60 a minute, so every run waits on refusals. The
  refusals fell on reads only, in one or two tests of a run, and cost the second run
  29 seconds more than the first.
- The test spreadsheet held the three `itest_` tabs of plan 006 before the runs and
  holds the same three after them. No run of this plan left a tab.
