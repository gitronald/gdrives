---
id: 6
slug: sheets-sync-module
status: active
branch: feature/sheets-sync-module
created: 2026-09-27T00:16:35-07:00
concluded:
pr:
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
| [`a-package-split.md`](subplans/a-package-split.md) | Move `sheets.py` into the `gdrives/sheets/` package, with the import surface unchanged | 1 | draft |
| [`b-read-layer.md`](subplans/b-read-layer.md) | Canonical cells and column types, local record files, reading a tab as keyed records, row keys, and retry | 2 | draft |
| [`c-merge-engine.md`](subplans/c-merge-engine.md) | The pure three-way merge, column ownership, and `MergePlan` | 3 | draft |
| [`d-apply-and-structure.md`](subplans/d-apply-and-structure.md) | Applying a plan with its guards, row writes, and the column, tab, and width helpers | 4 | draft |
| [`e-config-and-orchestration.md`](subplans/e-config-and-orchestration.md) | The config file, base snapshots, whole-tab pull and push, the library API, and the credential announcement | 5 | draft |
| [`f-cli-and-docs.md`](subplans/f-cli-and-docs.md) | The `sheets-sync`, `sheets-pull`, and `sheets-push` commands, and the docs | 6 | draft |

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
