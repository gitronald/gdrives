---
id: 6
slug: sheets-sync-module
status: done
branch: feature/sheets-sync-module
created: 2026-09-27T00:16:35-07:00
concluded: 2026-09-27T08:23:53-07:00
pr: https://github.com/gitronald/gdrives/pull/36
---

# Add a sheets package with keyed two-way sync between Sheets and local files

## Plan

### Goal

Give `gdrives` a standard way to keep a Google Sheet tab and a local file in
step, in either direction or both, so that a project declares *what* to sync in
a small config file instead of writing its own read, diff, write, and
verification code.

Today `gdrives.sheets` stops at cell ranges: read a range, overwrite a range,
append rows, and set cells on a row found by a key (`sheets-set`). Anything
larger is left to the caller, and every caller ends up rebuilding the same
pieces: turning a value grid into header-named records, matching rows by key,
working out which side changed, writing only the changed cells, and checking
that the write landed. This plan moves those pieces into the package.

Three sync modes cover the cases, chosen per tab:

| Mode | Source of truth | What a run does |
|---|---|---|
| `pull` | the sheet | Replaces the local file with the tab's contents |
| `push` | the local file | Replaces the tab's contents with the local file |
| `sync` | both | Three-way merge by row key against a saved base snapshot |

### Scope

In scope:

- Convert `gdrives/sheets.py` (1,027 lines) into a `gdrives/sheets/` package,
  with the existing import surface unchanged.
- A table layer: read a tab as header-named, keyed records.
- A pure three-way merge engine with column ownership.
- An apply step with a re-read guard, batched writes, and read-back
  verification.
- Whole-tab `pull` and `push` with a diff preview.
- Structural helpers: add and delete columns by header name, create missing
  tabs, and set column widths by header name.
- Retry with backoff for Sheets API calls.
- Read render options (`FORMATTED_VALUE`, `UNFORMATTED_VALUE`, date rendering)
  and multi-tab reads in one request.
- A config file naming sync targets, and the CLI commands `sheets-sync`,
  `sheets-pull`, and `sheets-push`.
- Declared column types and a light column schema (type, required, allowed
  values) validated before anything is written.

Out of scope (possible follow-ups):

- Creating a spreadsheet, or converting an uploaded `.xlsx` into a native Sheet.
  Targets are existing native Sheets.
- Syncing formulas, cell formatting, hyperlink styling, data validation, or
  conditional format rules. Sync moves values only.
- Propagating row deletions automatically. They are reported, never applied.
- Following a change to a key column's value. A changed key reads as one row
  removed and another added; `sheets-set` remains the tool for that edit.
- A DataFrame dependency. Records are plain `list[dict[str, ...]]`, which
  converts to and from a DataFrame in one call on the caller's side.
- Moving to a `gdrives sheets <verb>` sub-group. The flat `sheets-*` naming
  stays, for consistency with the existing commands.

### Subplans

Split on 2026-09-27, before any work started: the plan had reached 491 lines, which left
no room for a Log. This file keeps what every subplan shares: the goal, the scope, the
testing approach, the implementation order, and the open questions. The design is in
`subplans/`.

These are **sidecar files, not `planners` subplans**. They carry no `id` or `sub`
frontmatter, so they do not appear in `.planners/README.md` and `planners validate` does
not check them. The table below is the status of record for the pieces.

| Subplan | Scope | Step | Status |
|---|---|---|---|
| [`a-package-split.md`](subplans/a-package-split.md) | Move `sheets.py` into the `gdrives/sheets/` package, with the import surface unchanged | 1 | done, [#37](https://github.com/gitronald/gdrives/pull/37) |
| [`b-read-layer.md`](subplans/b-read-layer.md) | Canonical cells and column types, local record files, reading a tab as keyed records, row keys, and retry | 2 | done, [#38](https://github.com/gitronald/gdrives/pull/38) |
| [`c-merge-engine.md`](subplans/c-merge-engine.md) | The pure three-way merge, column ownership, and `MergePlan` | 3 | done, [#39](https://github.com/gitronald/gdrives/pull/39) |
| [`d-apply-and-structure.md`](subplans/d-apply-and-structure.md) | Applying a plan with its guards, row writes, and the column, tab, and width helpers | 4 | done, [#40](https://github.com/gitronald/gdrives/pull/40) |
| [`e-config-and-orchestration.md`](subplans/e-config-and-orchestration.md) | The config file, base snapshots, whole-tab pull and push, the library API, and the credential announcement | 5 | done, [#41](https://github.com/gitronald/gdrives/pull/41) |
| [`f-cli-and-docs.md`](subplans/f-cli-and-docs.md) | The `sheets-sync`, `sheets-pull`, and `sheets-push` commands, and the docs | 6 | done, [#42](https://github.com/gitronald/gdrives/pull/42) |

```
a (package) -> b (read) -> c (merge) -> d (apply) -> e (config, sync) -> f (CLI, docs)
```

Each subplan keeps its own Log. Entries that concern the whole effort go in this file's
Log.

### Testing

Coverage is gated at 100%, so each step lands with its tests.

- `merge.py`, `cells.py`, and `config.py` are pure and get exhaustive unit
  tests: the four cell cases, the four row cases, each ownership rule, carried
  columns, key normalization, duplicate and blank keys, and each config error.
- A stateful fake, `FakeSheetGrid` in `tests/helpers.py`, holds a grid per tab
  and applies `values.update`, `values.batchUpdate`, `insertDimension`,
  `appendDimension`, `deleteDimension`, and `updateCells`. The re-read guard,
  the read-back check, and the write order need a sheet that changes between
  calls, which the response-replaying `FakeSheetsService` cannot model. It
  follows the pattern `FakeDocsService` already uses, including an
  `edit_externally` hook.
- Failure-order tests assert what is left on disk when each step fails: a
  failed guard or a failed read-back leaves the local file and the base
  untouched.
- Live tests in `tests/test_sheets_integration.py` run on a temporary tab of
  `GDRIVES_TEST_SPREADSHEET_ID` (`addSheet` / `deleteSheet`) and cover a full
  sync round trip, a row write past the grid's last row, `insert_above`, and
  column add and delete.

### Implementation order

Each step is its own branch and PR, and leaves the package releasable.

1. **Package split.** Move `sheets.py` into `gdrives/sheets/` with re-exports.
   No behavior change; the existing tests pass with only patch targets moved.
2. **Read layer.** `retry.py`, render options and `pull_many` in `values.py`,
   `cells.py`, `files.py`, and `table.py`.
3. **Merge engine.** `merge.py` and its tests. No I/O.
4. **Apply.** `apply.py`, `structure.py`, and `FakeSheetGrid`.
5. **Config and orchestration.** `config.py`, `sync.py`, the report, and
   `describe_credentials`.
6. **CLI and docs.** `sheets-sync`, `sheets-pull`, `sheets-push`, the live
   tests, and the docs listed in [`f-cli-and-docs.md`](subplans/f-cli-and-docs.md#docs).

### Open questions

- **Base location.** `sheets-base/<target>/` is visible and committed by
  default. `.gdrives/` is the existing cache directory and is commonly ignored,
  so the base must not default to a path inside it.
- **Deleting rows.** A `--delete-rows` flag that applies flagged deletions is a
  natural follow-up once the flags have proven reliable. It stays out of this
  plan.
- **Numeric cells under `RAW`.** A number written with `RAW` is stored as text,
  so sheet formulas over a synced column see strings. If that matters for a
  target, a per-column `USER_ENTERED` write with a normalized read-back
  comparison is the likely answer; decide after step 4.
- **Size.** If steps 4 and 5 grow past what one plan can log, convert this plan
  to an umbrella with one subplan per step. Split on 2026-09-27, along those
  lines; see [Subplans](#subplans).

## Log

### 2026-09-27 — split into an umbrella and six sidecar subplans

The plan had reached 491 lines before any work started, so it was split inside the plan
folder, one subplan per implementation step. Sections moved to `subplans/` verbatim, with
headings raised to the top level and cross-references repointed. The bare `### Design`
heading, which only grouped the sections that moved, was removed. Nothing else was dropped
or condensed.

### 2026-09-27 — implementation started, branch layout

The plan calls for one branch and PR per step, and the plan file records a single
`branch`. The two are reconciled with an umbrella branch and a stack:

- `feature/sheets-sync-module` is the umbrella branch and the plan's `branch`. Its draft
  PR into `dev` is the plan's `pr`, and it carries the entries in this Log.
- Each step has its own branch, `feature/sheets-sync-<letter>-<slug>`, cut from the
  previous step's branch, with a draft PR onto that branch. Step a is cut from the
  umbrella branch. Reviewing and merging the stack bottom-up lands each step in order.

Each step is built by one implementing agent, reviewed by three independent reviewers
(conformance to the subplan, correctness, and test quality), and each reviewer's findings
are checked by a separate verifier before a fix pass. The three project checks and the
test suite with its coverage floor are run again outside the agents before a step is
pushed.

### 2026-09-27 — all six steps built, in review

Each step is built on its own branch with a draft PR, stacked in order: #37 (a), #38 (b),
#39 (c), #40 (d), #41 (e), and #42 (f). None is merged. Each subplan's Log records what
was built, the decisions made where the plan was silent, and what review found. The suite
grew from 785 tests to 1543, with coverage at 100% throughout.

What review found across the six steps:

- Step d: a missing test for an invalid `insert_above` on a plan with no new rows.
- Step e: `apply_tab` wrote tab structure before the last schema and `validate` check
  could fail. Fixed so that every check runs before the first write.
- Steps a, b, c, and f: no findings.

Open questions, as they stand:

- **Base location.** Settled: the default is `sheets-base/<target>`, and a base directory
  inside `.gdrives/` is a config error.
- **Numeric cells under `RAW`.** Unchanged. Values are written as literal strings, which
  read back identical, and sheet formulas over a synced column see text. A per-column
  `USER_ENTERED` write remains a possible follow-up.
- **Deleting rows.** Unchanged: flagged, never applied.

Raised during the work, for a decision before or after merge:

- **Bootstrap and `--adopt`.** A bootstrap run with `--apply` saves a base, after which
  local rows the sheet lacks are flagged on every run and `--adopt` is refused until the
  base is deleted. The guide documents it. Not saving a base on a bootstrap run that has
  row flags would avoid it.
- **A preview's exit code** is 0 whenever the run can go ahead, so it does not tell
  automation that changes are pending.
- **The `merge` name.** `gdrives.sheets.merge` is the function, which shadows the
  submodule as a package attribute.
- **The credential line** is printed by the three new commands only.
- **Live test quota.** The live suite now exceeds 60 writes per minute, so a full run
  waits on the quota and takes about two minutes. One temporary tab shared by the module,
  cleared between tests, would cut the writes.

### 2026-09-27 — close: review of the whole stack, and the merge

The six step PRs were reviewed once more as one diff against `dev` (50 files, about
13,400 added lines) before anything merged. Five finders read it: two for correctness,
split by module, and one each for reuse and efficiency, test coverage and edge cases, and
docs and rule consistency. They raised 10 candidates. Verifiers confirmed 5, rejected 4 as
documented design, and one more came from a final read of the guide. The review is posted
on [#36](https://github.com/gitronald/gdrives/pull/36#issuecomment-5857168631).

**Review follow-up.**

Fixed:

- **Local file header names were not stripped.** `read_records` used a file's column names
  as written, while a tab's header cells are read stripped, so a CSV headed `id ,name` was
  refused with `lacks column(s) ['id']`. Column names are now stripped in all three
  formats, and names that are equal once stripped, or blank, stop the run. Tests: eight
  cases in `tests/test_sheets_records.py`, and a sync with a padded local header in
  `tests/test_sheets_sync.py` (`30cfb03`).
- **The guide overstated `local_owned`.** It said the local value always wins. A row added
  on the sheet is folded in with the sheet's values in every column, as
  [`c-merge-engine.md`](subplans/c-merge-engine.md) records. The code is unchanged and the
  guide now states the exception (`29e5b7c`).

Conscious no-ops, all measured against `FakeSheetGrid`, none of which changes what a run
writes:

- `run_target` lists the spreadsheet's tabs once per tab: a preview of a 5-tab target
  makes 5 `spreadsheets.get` and 5 `values.get` requests where 1 and 5 would do.
- Creating a missing tab lists the tabs three times in a sync, and twice in a push.
- `delete_columns` fetches the `sheetId` again directly after `add_columns` did.
- A push preview converts the grid to canonical strings about four times, which is about
  0.15 seconds for a 10,000 by 20 tab.

A shared tab listing has to be refreshed after a tab is created, and the larger saving is
reading every tab's values in one `values.batchGet`, as `pull_all_tabs` does. Both change
how the per-tab functions read, so they are left for a follow-up plan.

Rejected as documented design: the `bootstrap` key with one allowed value, key comparison
that normalizes whitespace only, and the `local_owned` fold as a code defect. The row
lists on `ApplyResult` (`pushed_rows` and `appended_rows`) were rejected as dead code
because they are part of the library result, but nothing in this repo reads them.

Checks after the fixes: ruff and pyrefly clean, 1552 tests pass with the live suite, and
coverage is 100%.

**Merge.** The stack landed on the umbrella branch bottom-up, as merge commits. #37 merged
as it stood. Each later PR was retargeted from the branch below it to the umbrella branch
and then merged (#38 to #42), so each step is one merge commit in order. The umbrella PR,
#36, carries the result into `dev`.

## Retrospective

- **A stack keeps each step reviewable, and costs a retarget per PR at the end.** Six
  PRs of one step each were easier to review than one PR of 13,400 lines. CI did not run
  on them, since it triggers on PRs into `dev` and `main` only, so the checks were run by
  hand before each push, and the first CI run on the code was the umbrella PR's, after
  the stack had merged. Adding the umbrella branch to the workflow's `pull_request`
  branches for the life of a stack would give every step a CI run.
- **Reviewing a step against its subplan cannot find what the subplan leaves out.** Step
  b built both readers, the tab's and the local file's, and its subplan gave the header
  rule for the tab only. Each per-step review checked the code against that text and
  passed. The mismatch showed when a reviewer followed a local file through to the sync
  in step e. A design section that names a rule for one side of a comparison should name
  it for the other side too.
- **The guide was written from the design, and the design had moved.** The `local_owned`
  wording matched the subplan's design section, and the decision that refined it was in
  the Log. Docs written at the end of a plan should be checked against the Logs, not the
  design sections alone.
- **Request counts were never a stated goal, so nothing measured them.** Every step met
  its subplan, and the run still lists the tabs once per tab. A request budget per
  command in the plan (requests for a preview of N tabs) would have made it a test.
- **Splitting the plan before work started paid for itself.** The umbrella file stayed
  under 300 lines with a full Log, and each subplan's Log holds the decisions for its own
  module, which is where a reader of that module looks.
- **A stateful fake was the right investment.** The guard, the read-back, and the
  failure-order tests all need a sheet that changes between calls. The live tests then
  only had to pin the fake's assumptions, which kept the live suite small enough to fit
  the write quota, if slowly.
