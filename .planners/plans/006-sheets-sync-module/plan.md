---
id: 6
slug: sheets-sync-module
status: draft
branch:
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

### Package layout

`gdrives/sheets/` replaces `gdrives/sheets.py`. `__init__.py` re-exports every
name that is importable from `gdrives.sheets` today through `__all__`, so
`from gdrives.sheets import pull_values` and the CLI's lazy imports keep
working.

```
gdrives/sheets/
├── __init__.py    # Re-exports (__all__); the public surface
├── values.py      # spreadsheets.values.* wrappers, list_tabs, first_tab, render options
├── a1.py          # A1 and GridRange helpers (column_letter, a1_quote, split_a1, ...)
├── match.py       # find_rows, set_by_match, parse_pairs
├── rules.py       # Conditional format rules
├── files.py       # Local record files: CSV, TSV, and JSON read/write
├── retry.py       # with_retry and the retryable status sets
├── cells.py       # Canonical cell strings, column types, schema checks
├── table.py       # read_tab -> Table (header-named, keyed rows + row numbers)
├── merge.py       # Pure three-way merge -> MergePlan (no I/O)
├── apply.py       # apply_plan, verify, row inserts
├── structure.py   # Columns, tabs, and column widths (spreadsheets.batchUpdate)
├── config.py      # Load and validate the sync config
├── sync.py        # Orchestration: plan_tab, apply_tab, pull_tab, push_tab, report
└── commands.py    # run_* CLI entry points (existing ones move here)
```

The first step is a move with no behavior change. Tests that patch an internal
name (for example `pull_values` as seen by `set_by_match`) must patch it on the
defining submodule after the move; patches of `gdrives.sheets.run_*` made by the
CLI tests keep working because the CLI resolves those names on the package at
call time.

### Design

#### Records and cells

A row is `dict[str, str]` keyed by header name. Every comparison the merge makes
is between **canonical cell strings**, which are the strings the Sheets API
returns for a value written with `RAW` input:

| Value | Canonical string |
|---|---|
| `None` | `""` |
| `True` / `False` | `"TRUE"` / `"FALSE"` |
| Integer-valued float (`3.0`) | `"3"` |
| Anything else | `str(value)` |

`cells.to_cell(value)` and `cells.from_cell(text, type)` convert between typed
values and canonical strings. Types are **declared**, never sniffed, because an
all-empty column carries no type to infer. Supported names: `str` (the default),
`int`, `float`, `bool`, `date`, and `datetime`.

A cell that does not parse as its declared type is a schema problem, not a value
to coerce or drop. `cells.problems(rows, schema)` returns every problem found
(tab, row key, column, and the offending text), and a run with any problem
writes nothing.

#### Local files

`files.py` reads and writes records by extension:

- `.csv` / `.tsv`: every cell is a string, and leading zeros, booleans, and
  dates stay exactly as written. A UTF-8 byte-order mark is accepted on read;
  `"bom": true` writes one for spreadsheet apps that need it.
- `.json`: an array of objects with typed values, written byte-stably (fixed
  key order, two-space indent, and a final newline) so a no-op run leaves the
  file unchanged and version control shows only real changes.

All writes go through `gdrives.local.atomic_output`.

#### Reading a tab

`table.read_tab(service, spreadsheet_id, tab, columns, key)` reads the whole tab
in one request and returns a `Table`:

- Header cells are stripped of surrounding whitespace, and columns are resolved
  **by header name, never by position**. A wanted column that is missing, or a
  header that repeats a name, raises before anything else runs.
- Short rows are padded to the header width (the API truncates each row at its
  last non-empty cell). Rows that are entirely blank are skipped.
- A row wider than the header is reported.
- Columns on the sheet that were not asked for are recorded as `extra_columns`
  and are never read into records or written.
- `row_numbers` maps each row key to its 1-based spreadsheet row, for writes.
- A row that has data but a blank key is refused, with its row number listed.

`values.pull_values` gains `render=` and `date_time_render=` keyword options
(defaults unchanged), and `values.pull_many` wraps `values.batchGet` so several
tabs are read in one request. Reads are always per tab, never per cell: the
per-minute read quota is small enough that cell-by-cell reads exhaust it on a
tab of modest size.

#### Row keys

A key is a tuple of one or more named columns. Key cells are compared with
surrounding whitespace stripped and internal runs collapsed, so a trailing space
typed on the sheet does not split one row into two; the stored text is never
rewritten. A duplicate key within one side raises, naming the key and the side.

#### Three-way merge

`merge.merge(base, local, remote, key, columns, ...)` is a pure function over
three lists of records and returns a `MergePlan`. `base` is what both sides held
after the last applied sync.

Per cell, for a row present on both sides, with base `b`, local `l`, and sheet
`r`:

| Case | Condition | Result |
|---|---|---|
| In sync | `l == r` | Nothing to do; base becomes `l` |
| Local edit | `r == b`, `l != b` | Push `l` to the sheet; base becomes `l` |
| Sheet edit | `l == b`, `r != b` | Fold `r` into the local file; base becomes `r` |
| Conflict | All three differ | Report it and write neither side; base stays `b` |

A conflict leaves the base where it was on purpose, so the same cell is reported
on every run until a person makes the two sides agree.

Per row, read against the base:

| Row is | In base? | Result |
|---|---|---|
| Local only | No | New local row: append it to the sheet |
| Local only | Yes | Deleted on the sheet: flag `remote_deleted` |
| Sheet only | No | New sheet row: fold it into the local file |
| Sheet only | Yes | Deleted locally: flag `local_deleted` |

Deletions are flags. Neither side's rows are ever removed by a sync.

**Column ownership** overrides the cell rule:

- `local_owned` columns always push the local value. When the sheet also
  changed, the discarded sheet value is reported as an override.
- `sheet_owned` columns always fold the sheet value. A new local row is appended
  with its sheet-owned cells blank, since those values are assigned on the
  sheet.
- `owns_rows: true` makes the row set local-owned: a new sheet row is flagged
  `remote_added` instead of being folded in.
- Columns outside the `columns` projection are **carried**: they exist only in
  the local file, pass through a sync untouched, and never reach the sheet or
  the base.

Key columns are identity and cannot be owned. Ownership sets must not overlap
and must sit inside the projection; the merge raises otherwise.

`MergePlan` holds `pushes`, `appends`, `fold_cells`, `fold_rows`, `conflicts`,
`overrides`, `row_flags`, `new_local`, and `new_base`, plus
`needs_attention` (true when there are conflicts or row flags). `new_local`
keeps the local file's row order, with rows added on the sheet after it.

`--prefer local|sheet` resolves cell conflicts for one run in favor of the named
side and reports each as an override. Without it, conflicts are only reported.

#### Applying a plan

`sync.apply_tab` runs these steps in this order, and stops at the first failure:

1. **Re-read the tab** and compare its header and rows with the read the plan
   was computed from. Any difference aborts with nothing written. The Sheets API
   has no revision precondition, so this re-read is the only tie between the
   plan and the write.
2. **Push changed cells** in one `values.batchUpdate` call, each addressed by
   column letter and the row number from the fresh read.
3. **Write new rows** in one `spreadsheets.batchUpdate` call.
4. **Read the tab back** and check every pushed cell and every new row. A
   mismatch raises and leaves the local file and the base untouched.
5. **Write the local file**, then **write the base snapshot**.

The order is a safety property: a run that fails part way has not recorded a
sync that did not land, and the next run sees the partial sheet write as
already in sync.

New rows are never written with `values.append`. That call finds the table by
scanning from the top-left and treats the first blank row as its end, so a row
that someone cleared (instead of deleting) in the middle of a tab makes the
append land on top of the rows below the gap. New rows go to explicit row
numbers instead:

- By default, directly after the last non-empty row of the fresh read, growing
  the grid with `appendDimension` in the same request when they would not fit.
- With `insert_above`, directly above the first row whose named column holds one
  of the listed values, using `insertDimension` and `updateCells` in the same
  request so no edit can land between opening the rows and filling them.

`sync` mode writes with `RAW` input only. The merge depends on a written string
reading back identical, and `USER_ENTERED` rewrites values on the way in (`01`
becomes `1`, and `TRUE` becomes a checkbox value), which would make the same
cell push again on every run.

#### Base snapshots and the first sync

The base is one CSV of canonical strings per tab, holding the merged columns
only. It is meant to be committed alongside the local file, so everyone syncing
the same target shares one base.

A tab with no base yet is bootstrapped by one of two explicit choices:

- `"bootstrap": "local"` (the default): the local file is taken as the base.
  Sheet-only cell edits fold in, and nothing is written to the sheet on that
  first run.
- `--adopt`: the tab is rewritten from the local file and that becomes the base.
  It requires `--apply`, and the preview lists every cell the rewrite would
  change on the sheet.

#### Whole-tab pull and push

`pull` and `push` are for tabs with one source of truth, where a merge is more
than is needed.

- `pull_tab` reads the tab and replaces the local file. An empty tab is refused
  and the local file is left alone. When a `key` is configured, the preview
  reports rows added, removed, and changed against the current local file, and
  a drop in row count is called out.
- `push_tab` clears the tab's values and rewrites them from the local file, in
  header order. The preview reports what the sheet holds that the local file
  does not, because that is what the push discards. It runs the same re-read
  guard and read-back check as `sync`. `push` may use `USER_ENTERED`.

`sheets-pull` also works without a config, for a one-off dump:
`gdrives sheets-pull <sheet> --all-tabs -o out/` writes one file per tab, named
from the tab title by `safe_filename` (moved to `gdrives/local.py`, and still
importable from `gdrives.download`). `--skip <tab>` leaves a tab out, which
protects a local file that shares a name with a tab but is produced elsewhere.
Two tabs whose file names collide are refused.

#### Structure

`structure.py` wraps the `spreadsheets.batchUpdate` requests that sync needs:

- `add_columns` and `delete_columns`, by header name. Inserts are ordered left
  to right and deletes right to left, so the indices inside one request stay
  valid. `sheets-sync --add-missing` adds local columns the sheet lacks, and
  `--drop-extra` deletes sheet columns outside the projection. Both are
  separate, explicit steps that run before the merge.
- `ensure_tabs` creates tabs that are configured but missing. It never deletes
  a tab from an existing spreadsheet: a tab the config does not name belongs to
  whoever added it.
- `set_column_widths`, keyed by header name, so widths follow a column when it
  moves.

#### Retry

`retry.with_retry` retries with exponential backoff and jitter:

- Reads and `values.batchUpdate` (idempotent): on 429, 500, 502, 503, and 504.
- Row inserts, row appends, and column inserts and deletes (not idempotent): on
  429 only. A 5xx on one of these may have been applied, and a retry would
  apply it twice.

#### Config

`gdrives-sheets.json` is found from the working directory upward, the same way
`.env` is, or named with `--config`. Paths inside it are relative to the file.

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "base": "sheets-base/roster",
    "tabs": {
      "Members": {
        "mode": "sync",
        "local": "data/members.csv",
        "key": ["member_id"],
        "columns": ["member_id", "name", "status", "paid", "notes"],
        "local_owned": ["status"],
        "sheet_owned": ["notes"],
        "owns_rows": false,
        "schema": {
          "member_id": {"required": true},
          "paid": {"type": "bool"},
          "status": {"allowed": ["active", "closed"]}
        },
        "insert_above": {"status": ["closed"]},
        "widths": {"notes": 320}
      },
      "Summary": {"mode": "push", "local": "output/summary.csv"}
    }
  }
}
```

| Field | Level | Meaning |
|---|---|---|
| `spreadsheet` | target | Sheet URL, file ID, or Drive path (resolved by `resolve_file_id`) |
| `base` | target | Directory for base snapshots; default `sheets-base/<target>` |
| `input_option` | target | `RAW` (default) or `USER_ENTERED`; `sync` tabs require `RAW` |
| `mode` | tab | `sync` (default), `pull`, or `push` |
| `local` | tab | Local file; the extension picks the format |
| `key` | tab | Key columns; required for `sync`, optional for `pull` and `push` |
| `columns` | tab | Projection: the columns the sheet carries; default is every local column |
| `local_owned`, `sheet_owned`, `owns_rows` | tab | Ownership, as described above |
| `schema` | tab | Per column: `type`, `required`, and `allowed` |
| `bootstrap` | tab | `local` (default); `--adopt` is a flag, not a config value |
| `insert_above` | tab | One `{column: value or [values]}` pair |
| `widths` | tab | Column widths in pixels, by header name |
| `bom` | tab | Write a byte-order mark to a CSV or TSV |

The loader validates everything before any network call and reports every
problem at once: unknown fields, a missing or empty key, key or owned columns
outside the projection, overlapping ownership, a malformed `insert_above`, and
`USER_ENTERED` on a `sync` tab.

#### CLI

```bash
gdrives sheets-sync roster                       # Preview every tab of the target
gdrives sheets-sync roster --tab Members         # Preview one tab
gdrives sheets-sync roster --apply               # Write sheet, local file, and base
gdrives sheets-sync roster --apply --adopt       # First sync: rewrite the tab from local
gdrives sheets-sync roster --apply --add-missing # Add local columns the sheet lacks
gdrives sheets-sync roster --apply --drop-extra  # Delete sheet columns outside the projection
gdrives sheets-sync roster --apply --prefer local  # Resolve conflicts toward local
gdrives sheets-pull roster --apply               # Replace local files for the pull tabs
gdrives sheets-push roster --apply               # Replace the push tabs from local files
gdrives sheets-pull <sheet> --all-tabs -o out/   # One-off dump, no config
```

Every command **previews by default** and writes only with `--apply`. The
report lists pushes, folds, new rows, overrides, conflicts, and row flags per
tab, with each cell's before and after values passed through
`gdrives.local.printable`.

Exit codes let automation tell the outcomes apart:

| Code | Meaning |
|---|---|
| 0 | In sync, or every change applied |
| 1 | Error: config, schema, API, or a failed guard |
| 2 | Needs a person: conflicts or row flags remain |

`--apply` with conflicts present still applies the changes that do not
conflict, then exits 2.

#### Library API

The CLI is a thin layer over functions that take a service and plain values, so
a caller with its own config or its own record source uses the same engine:

```python
from gdrives.sheets import merge, read_tab, apply_plan, verify

table = read_tab(service, spreadsheet_id, "Members", columns, key)
plan = merge(base, local, table.rows, key, columns, sheet_owned={"notes"})
```

`sync.plan_tab` and `sync.apply_tab` accept an optional `validate` callable
(`rows -> list[str]`) that runs on the local rows and again on the merged
result, for checks a declarative schema cannot express.

#### Announcing the credential

`gdrives.auth.describe_credentials(scopes)` reports which credential a call will
use (OAuth token, service account, or ADC) and whether it will first need
interactive consent. The write commands print that to stderr before the first
API call, so a run waiting on a consent prompt does not look hung, and a write
is not attributed to an identity the user did not expect.

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

### Docs

- `README.md`: a "Sync a Sheet with a local file" section.
- `docs/sheets-sync.md`: the guide, covering the three modes, the merge rules,
  ownership, the base snapshot, and the first sync. It also records one usage
  rule: a column the sheet owns must not be given a default by whatever
  generates the local file, because a default cannot tell "never filled in"
  from "cleared on purpose", and would be pushed back over a deliberate blank.
- `.claude/CLAUDE.md`: package structure and the Commands block.
- `CHANGELOG.md`: entries under `[Unreleased]`.

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
   tests, and the docs above.

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
  to an umbrella with one subplan per step.
