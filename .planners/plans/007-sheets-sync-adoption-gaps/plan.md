---
id: 7
slug: sheets-sync-adoption-gaps
status: active
branch: feature/sheets-sync-adoption-gaps
created: 2026-09-27T10:24:14-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/44
---

# Close the gaps that keep a caller's own sync code from moving onto gdrives.sheets

## Plan

### Goal

Let a caller whose local side is not a flat file of strings use the whole sync stack, and
not only its lowest layer.

`gdrives.sheets` has two levels. The primitives (`read_tab`, `merge`, `apply_plan`) work
on records of canonical strings. The orchestration (`plan_tab`, `apply_tab`, `pull_tab`,
`push_tab`, `run_target`) adds the checks, the write order, the bootstrap, and the report,
and it reads and writes the local side and the base as files named in the config.

A caller can use the orchestration only when its data is one `.csv`, `.tsv`, or `.json`
file per tab. A caller holding typed rows in memory, a file that holds several tabs, or a
local side computed by code has to drop to the primitives, and then rebuilds what the
orchestration already does: converting typed values to and from cell strings, ordering the
writes, saving the base, and reporting. This plan closes that gap.

Three reviews in [`implementation-notes/`](implementation-notes/) widened it. `001`
checked the first draft against the code. `002` and `003` each followed a downstream
caller that had written its own sync code and tried to replace it with 0.11.0: one ran a
keyed three-way sync, the other replaced whole tabs. `004` checked the three against the
code and the live API, and `005` checked two assumptions the plan still rested on. What
they found falls in three groups, and the plan covers all three within
`gdrives.sheets`:

- defects in the released sync that change what a run writes: where new rows and new
  columns land, the formatting new rows take, and line endings;
- the additions of the first draft, corrected where it misread the code; and
- smaller additions a caller needs before it can delete its own code: typed dates, hooks
  that see the columns and the plan, the cells a run wrote, tabs named by `sheetId`, and
  a few tools.

Most additions are optional and keep today's behavior by default. The ones that do not
are listed under [Compatibility](#compatibility).

### Scope

In scope:

1. A public typed codec: rows of typed values to records and back.
2. Python types accepted wherever a column type is declared.
3. Comparison of cells normalized by their declared type, in `merge`.
4. Holding an invalid sheet cell for a person instead of refusing the whole tab.
5. Composite keys with a blank component.
6. A store protocol for the local side and the base, with the file store as the default.
7. `push_rows`: a whole-tab push of records held in memory.
8. Helpers to clear and to find link formatting on written cells.
9. `MergePlan` write predicates, and a non-blocking `warn` hook beside `validate`.

Added from the notes (the note behind each is in brackets):

10. New rows placed by the `insert_above` column as it will be after the run's pushes,
    with the row named in the preview [D1].
11. Added columns placed at their position in the projection [D2].
12. Inserted rows formatted like the row above them [D4].
13. LF line endings in the files a sync or a pull writes, and a `newline` tab field
    [D6, M4].
14. `date` and `datetime` columns read from the sheet's serial numbers as ISO 8601 [M5].
15. A blocking `check` hook that sees both sides' columns [D3], and `carry=` on `merge`
    [D9].
16. The cells a run wrote, by row and column [D11].
17. A tab named by its `sheetId`, which survives a rename [M7], and one tab listing per
    run.
18. Tools: `get_column_widths` and `sheets-widths` [M12], `--bom` and `--slug` for
    `sheets-pull --all-tabs` [M8], and a notice when a call is retried [D8].
19. Guide sections: moving an existing sync over [D5, M6, D10], a caller's own retry
    [D7], and pulling and pushing the same files [M9].

Out of scope:

- A DataFrame dependency. Records stay plain dicts, as in plan 006.
- Syncing formatting. Item 8 is a standalone helper and an opt-in step of a run; the
  merge still moves values only.
- Setting links. `linked_cells` reports a link and its target; writing one stays the
  caller's [D12].
- Applying row deletions, which remains open from plan 006.
- Writing a typed column as dates and numbers, not text. It was plan 006's open
  question on `RAW`, and is plan [010](../010-sheets-typed-writes/plan.md), which
  builds on steps b and c here.
- CLI flags for the new tab options. `sheets-sync`, `sheets-pull`, and `sheets-push`
  reach them through config fields. The only CLI additions are those of item 18.
- A tab mode that both pulls and pushes [M9], and a schema setting that refuses an
  undeclared column [D3]. The guide covers the first and the `check` hook the second.
- A store factory for `pull_all_tabs`. It keeps writing one file per tab.
- Authentication [M1 to M3], which is plan
  [008](../008-oauth-token-and-consent-safety/plan.md), and file upload and spreadsheet
  creation [M10, M11], which are plan
  [009](../009-drive-upload-and-sheet-create/plan.md). 008 goes first: M1 breaks a
  caller at upgrade time, before it changes any code.

### Subplans

The plan passed 500 lines once the notes were applied, so the design is in `subplans/`,
one file per implementation step, as in plan 006. This file keeps what every step
shares. The draft's design sections moved there verbatim (`b8ff5c0`) and were then
edited, so the diff of each file shows what the notes changed.

These are **sidecar files, not `planners` subplans**. They carry no `id` or `sub`
frontmatter, so they do not appear in `.planners/README.md` and `planners validate` does
not check them. The table below is the status of record for the pieces.

| Subplan | Scope | Items | Notes applied | Status |
|---|---|---|---|---|
| [`a-sync-fixes.md`](subplans/a-sync-fixes.md) | Where new rows and columns land, the formatting of inserted rows, and line endings | 10 to 13 | D1, D2, D4, D6, M4 | active |
| [`b-typed-cells.md`](subplans/b-typed-cells.md) | The typed codec, Python classes as column types, and typed dates read from the sheet | 1, 2, 14 | R2, R4, R9, R13, M5, P9 to P12 | active |
| [`c-merge-additions.md`](subplans/c-merge-additions.md) | Partial blank keys, normalized comparison, held cells, `carry=`, and the plan predicates | 3, 4, 5, 9, 15 | R1, R6, R7, R10, D9 | active |
| [`d-checks-and-hooks.md`](subplans/d-checks-and-hooks.md) | `CheckContext`, the blocking `check` hook, and the non-blocking `warn` hook | 9, 15 | R11, D3 | active |
| [`e-stores.md`](subplans/e-stores.md) | The store protocol for the local side and the base | 6 | R2, R5, R12, D9 | active |
| [`f-push-rows-and-links.md`](subplans/f-push-rows-and-links.md) | `push_rows`, the cells a run wrote, and link formatting | 7, 8, 16 | R3, R7, R8, D11 to D14, P1 to P8 | active |
| [`g-tabs-and-tools.md`](subplans/g-tabs-and-tools.md) | Tabs by `sheetId`, one tab listing per run, column widths, `--all-tabs` options, and the retry notice | 17, 18 | M7, M8, M12, D8 | draft |
| [`h-docs-and-release.md`](subplans/h-docs-and-release.md) | The guide, the changelog, the exports, and the live suite's request budget | 19 | D5, D7, D10, M6, M9, R15, P13 | draft |

Each subplan keeps its own Log. Entries that concern the whole effort go in this file's
Log.

### Decisions

Settled on 2026-09-27, before any work started. Those marked owner were put to the
owner; the rest were proposals of the draft or follow from a note, and are recorded
here so that a step does not reopen them by accident.

| # | Decision | Alternative set aside | From |
|---|---|---|---|
| 1 | The plan covers all of `gdrives.sheets`; auth and Drive writes are plans 008 and 009 | One plan for every note | owner |
| 2 | One umbrella with sidecar subplans, an umbrella branch, and a stack of step PRs | `planners` subplans; sequential plans | owner |
| 3 | LF line endings, formatting inherited from the row above, and normalized comparison are defaults, not options | Each as an opt-in | owner |
| 4 | Item 8 is designed from the live probe in note `004`, which overrules D12 and part of D13 and D14 | Designing from the notes as written | owner, P1 to P8 |
| 5 | A new sheet row with any invalid cell is held whole | Folding it with the invalid cells blank, which writes a row that does not match the sheet | draft |
| 6 | A sheet cell blanked in a `required` column is held | Folding it, after which the local file fails its own check | draft |
| 7 | `on_invalid` is a sync tab option read by the orchestration; `merge` takes `schema=` and holds what fails it | `on_invalid` on `merge`, where `"refuse"` means nothing | R6 |
| 8 | `TabConfig.local` becomes `Path \| None`, listed as a visible change | Store arguments on every orchestration function, with a placeholder path on the tab | R2, R5 |
| 9 | Classes are normalized by a public `column_type`; `ColumnSchema.type` stays `str` | Widening the field to `str \| type` | R9 |
| 10 | `validate` keeps its rows-only signature; `check` and `warn` take a `CheckContext` | Detecting a hook's arity; changing `validate` | R11, D3 |
| 11 | A typed date column costs a second read, of the declared columns only | Reading the whole tab with `SERIAL_NUMBER`, which changes undeclared date columns | M5 |
| 12 | `clear_links` is a tab field for `push` and `sync` tabs | A helper the caller runs by hand after every run | P3 |
| 13 | A typed date cell is converted when the serial read gives a number, and otherwise keeps the first read's value; a date-time rounds to the millisecond | Trusting what the API returns for text; rounding to the second | owner, P9, P10 |
| 14 | Every grid read goes through `pull_grid`, which requires a `fields` mask and reports `httplib2`'s decode errors per tab | Reproducing D14's failure first | owner, P8 |
| 15 | A pushed date or number stays text on the sheet in this plan, and the guide says so. Writing typed values is plan 010 | A per-column `USER_ENTERED` write inside this plan | owner |

### Compatibility

Every addition is a new function, a new optional argument, or a new config field with a
default that keeps today's behavior, with the exceptions below. The draft named the
first only [R2]. Each gets a line under Changed in the changelog, written in the step
that makes the change. The release is a minor version.

| Change | Who sees it | Step |
|---|---|---|
| A tab with a schema compares cells by declared type, so a difference of spelling is no longer an edit | Every sync tab with a schema | c |
| Delimited local files and bases are written with LF | A file 0.11.0 wrote flips once, on its next changed write. `newline: "crlf"` keeps CRLF. `write_values_csv` and `sheets-get -o` keep CRLF | a |
| Rows inserted with `insert_above` take the formatting of the row above | A tab whose rows below the insert point are formatted directly | a |
| New rows are placed by the `insert_above` column as it will be after the run's pushes | A tab whose `insert_above` column is in the projection | a |
| Columns added by `add_missing` land at their place in the projection, not at the right edge | Every `--add-missing` run | a |
| A `date` or `datetime` column reads as ISO 8601 whatever the sheet displays | A tab that declares one. A base saved from display text reports each such cell as a sheet edit, once | b |
| `TabConfig.local` is `Path \| None` | A caller that reads `tab.local` under a strict type checker | e |
| `write_records` lists every cell that does not parse, not the first | A caller that matches the message text | b |

### Testing

Coverage is gated at 100%, so each step lands with its tests.

- Items 1 to 5 and 9 are pure and get unit tests: the codec round trip for each type, each
  refusal, normalized comparison for each of the four cell cases, held cells and rows
  under both settings, and partial keys including duplicates that differ only in a blank
  component.
- Items 6 and 7 run against `FakeSheetGrid`. The existing `plan_tab`, `apply_tab`,
  `pull_tab`, and `push_tab` tests must pass unchanged over `FileStore`, which is the
  check that the refactor kept behavior. New tests use `MemoryStore` and a store whose
  `write` raises, to confirm the order of writes and what a failed run leaves behind.
- Item 8 needs `FakeSheetGrid` to model what the probe found: a whole-cell URL or domain
  written as a value gains a cell link, `repeatCell` and `updateCells` honour a `fields`
  mask over `userEnteredFormat.textFormat.link` and `textFormatRuns`, and
  `spreadsheets.get` with `includeGridData` returns `hyperlink` and the runs. The fake's
  link rule is pinned by one live test, so the two cannot drift apart unnoticed.
- The fake also gains a `SERIAL_NUMBER` read (step b) and records the
  `inheritFromBefore` it is sent (step a).
- A step that changes what a run writes (all of step a, and item 3) gets a test that
  fails on the old behavior, written first.

**Live suite.** It makes 55 writes and 66 reads against limits of 60 a minute each, and
waits out the refusals [R15]. New live cases are limited to what the fake cannot pin,
and each shares the module's one temporary tab:

| Step | Live case |
|---|---|
| a | `insert_above` with a pushed match, and the format the new row takes; a column placed mid-header |
| b | A date cell and a date-time cell read as serial numbers, beside an ISO string in the same column |
| c | A sync of a partial-key tab |
| f | `push_rows` with `clear_links` over a whole-cell URL and a link on part of a cell |
| g | A tab found by `sheetId` after a rename |

The sync through a custom store reaches no API surface a file store does not, so it
runs against `FakeSheetGrid` only [R15]. Step g's single tab listing per run takes reads
out of every sync test. Each step's Log records the suite's request counts before and
after, and step h states the budget. Subagents stay off the live suite; one run, at the
end of a step, by the orchestrating session.

### Implementation order

Each step is its own branch and PR, stacked in this order, and leaves the package
releasable. Step a changes only released behavior and is sized to ship by itself if a
release is wanted before the rest.

| Step | Subplan | Depends on |
|---|---|---|
| 1 | a: sync fixes | — |
| 2 | b: typed cells | — |
| 3 | c: merge additions | b (`normalize_cell`, `column_type`) |
| 4 | d: checks and hooks | c (the plan in the context) |
| 5 | e: stores | b, c (`carry=`), d (`TabReport.warnings`) |
| 6 | f: `push_rows` and links | d, e |
| 7 | g: tabs and tools | f (one listing serves `push_rows` too) |
| 8 | h: docs and release | all |

```
a (fixes) -> b (cells) -> c (merge) -> d (hooks) -> e (stores) -> f (push, links) -> g (tabs, tools) -> h (docs)
```

Branches follow plan 006: `feature/sheets-sync-adoption-gaps` is the umbrella branch and
this plan's `branch`, with a draft PR into `dev` that is this plan's `pr`. Each step is
`feature/sheets-sync-adoption-<letter>-<slug>`, cut from the step below it, with a draft
PR onto that branch. The stack merges bottom-up into the umbrella branch.

Two things carried over from plan 006's retrospective:

- CI triggers on PRs into `dev` and `main` only, so the first commit on the umbrella
  branch adds it to the workflow's `pull_request` branches, and the last one removes it.
  Every step PR then gets a CI run.
- A config field lands in the step that adds its feature, with its row in the guide's
  tab field table, so no step ships a feature that a config cannot reach. Step h writes
  the guide's longer sections and checks them against the Logs, not the design text.

### Open questions

None is left. Each was settled before any work started, and is recorded under
[Decisions](#decisions):

- From the draft: normalized comparison by default (3), held rows (5), `required`
  under `hold` (6), and a store for `pull_all_tabs` (out of scope).
- **What a serial-number read returns**, and the precision of a date-time serial:
  checked live [P9, P10], and the rule made per cell (13).
- **Whether an unbounded grid read can fail**: closed without reproducing D14's
  failure, by masking every grid read and turning the error into a per-tab one (14).
- **Pushed dates and numbers land as text**: documented here, and taken up by plan
  [010](../010-sheets-typed-writes/plan.md) (15).
- **Unformatted reads and a saved base** [D5]: the guide's examples were checked live
  [P13].

A question that comes up during the work goes in the Log of the step that met it.

## Log

### 2026-09-27 — renamed, widened from the notes, and split

The plan was `007-sheets-sync-typed-stores`, "Add typed records, pluggable stores, and
in-memory pushes to sheets sync". Three notes reviewed it on the day it was written
(`001` to `003`), and the scope they describe is wider than the name, so it was renamed
(`d4abc6f`) and its design split into sidecar subplans (`b8ff5c0`, a verbatim move).

The notes were then checked against the code at `6b20b57`, whose `gdrives/` tree is
identical to `v0.11.0`, and against the live API on two temporary tabs of the test
spreadsheet, both deleted afterwards (13 reads and 14 writes). Note
[`004`](implementation-notes/004-notes-review-and-link-probe.md) records the verdict on
each claim. In short:

- Every reference to the code in R1 to R15, D1 to D11, and M1 to M9 holds.
- The draft's item 8 was right that a `RAW` write links URL-like text. D12 says the
  opposite and is overruled. D13 holds in part, and D14's failure was not reproduced.
- One thing no note had: `updateCells` with `userEnteredFormat.textFormat.link` in its
  `fields` mask writes a URL with no link, in the same request.

The owner settled scope, structure, the three default changes, and the probe
(decisions 1 to 4). Plans 008 and 009 were added for the notes that fall outside
`gdrives.sheets` (`8fa364f`, `8531d3e`).

### 2026-09-27 — the open questions closed

Four questions were left open by the rewrite. The owner settled each, and a second
probe answered the two that were questions of fact
([note `005`](implementation-notes/005-serial-and-format-probe.md): one temporary tab,
deleted afterwards, 4 reads and 5 writes).

- **Serial reads.** Text comes back as text, a date as a whole number, and a date-time
  as a float that keeps the millisecond [P9, P10]. Step b's design stands, and its
  rule is now per cell so that it does not rest on that (decision 13). The probe also
  found that display text can name the wrong day [P12], which the draft's reasons for
  typed dates did not include.
- **Grid reads.** D14's failure was not reproduced and is not pursued. Every grid read
  goes through `pull_grid`, masked, with `httplib2`'s decode errors reported per tab
  (decision 14). Those errors are not `HttpError`s, so today one would stop a whole
  run.
- **Pushed dates as text.** Documented in this plan; plan
  [010](../010-sheets-typed-writes/plan.md) was added for writing typed values
  (`6fb7606`, decision 15).
- **Number formats.** `50%` reads `0.5` and a 3 shown as `3.00` reads `3` [P13], as D5
  had reasoned.

### 2026-09-27 — plan 008 runs first, and may overlap this one

Decided by the owner on 2026-09-27T11:47:03-07:00, before either plan was activated:
plan [008](../008-oauth-token-and-consent-safety/plan.md) is implemented first, and
this plan may be in progress at the same time, in its own worktree. 008's Log has the
same entry from its side.

The two share no design. They share files, and on this plan's side the code overlap is
all in step g:

| File | This plan | Plan 008 |
|---|---|---|
| `gdrives/sheets/commands.py` | Step g: retry notices, `--all-tabs` options, and `sheets-widths` | The credential line moves out of this file, and prints on a preview |
| `gdrives/cli.py` | Step g: one command and two options | `gdrives login`, and the credential line on every command |
| `CHANGELOG.md`, `README.md`, the project `CLAUDE.md` | Lines added by every step | Lines added |
| `docs/sheets-sync.md` | Steps a to h | Its section on credentials, if 008 changes what it says |

What this plan does about it:

- Steps a to f touch `gdrives/sheets/` outside `commands.py`, and need nothing from
  008. They start whether or not 008 has merged.
- **Before step g starts, check whether 008 has merged into `dev`.** If it has, merge
  `dev` into the umbrella branch first, so that step g builds on the credential helper
  where 008 left it. If it has not, step g goes ahead, and whichever plan merges second
  resolves `commands.py` and `cli.py`.
- Step g wraps the `sheets-*` commands in `retry_notices`, and 008 adds a credential
  line to the same commands. Both print to stderr before a run's first request, so
  step g's tests assert on their own line and not on stderr as a whole.
- Step h reads the guide's section on credentials against what 008 shipped, as it
  reads every other section against the Logs.
- 008 has no live tests, so this plan has the Sheets quota to itself.
- This plan reaches `dev` through one umbrella PR at its end. If 008 ships as a release
  of its own before then, its changelog lines are already promoted, and this plan's
  lines are the only ones under `[Unreleased]`.
