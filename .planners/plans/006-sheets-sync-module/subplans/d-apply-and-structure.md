---
status: active
branch: feature/sheets-sync-d-apply-and-structure
---

# 006d — Apply a merge plan and edit sheet structure

Part of [006](../plan.md). Step 4 of the umbrella's implementation order. This is
the step that writes to a sheet. Its tests need the stateful fake described in the
umbrella's Testing section.

## Applying a plan

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

## Structure

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

## Log

### 2026-09-27 — implementation

This step builds the sheet side of the five-step list above: the re-read guard, the cell
pushes, the new-row writes, and the read-back check (steps 1 to 4). Writing the local
file and the base (step 5) belongs to `sync.apply_tab` in
[`e-config-and-orchestration.md`](e-config-and-orchestration.md).

New modules `apply.py` and `structure.py`. `values.py` gained `tab_grid` and `TabGrid`
(the tab's `sheetId` and grid size in one `spreadsheets.get`), and `Table` gained
`last_row`.

Decisions on points the sections above leave open:

- **`apply_plan(service, spreadsheet_id, table, plan, *, insert_above=None)`** takes the
  `Table` the plan was computed from and returns an `ApplyResult` (counts, and the rows
  written as they sit after the apply).
- **Exceptions.** `ApplyError(ValueError)` is the base, with `SheetChangedError` for the
  guard and `ReadBackError` for the read-back. A plan that does not fit its table (a push
  to a row or column the table lacks, a new row whose key the tab already has) is a
  caller error and raises a plain `ValueError` before any request.
- **An empty plan makes no request at all,** not even the guard read.
- **The guard** compares the header, the projection rows, and the row numbers, and its
  message lists rows removed, added, edited, and moved. A change in a column outside the
  projection does not trip it.
- **Pushes go before row inserts,** so the fresh read's row numbers are still true when
  the pushes use them.
- **New rows** are written with `updateCells` and `stringValue`, one request per run of
  adjacent projection columns, so columns outside the projection and blank header gaps
  are never touched. A blank cell is sent as empty cell data.
- **The last non-empty row** is the last row holding a value in any column, including
  columns outside the projection and cells past the header.
- **`insert_above`** may name any header column, in the projection or not. Rows go above
  the first matching row, top to bottom, and after the last row when none matches.
  Inserted rows take the formatting of the row they sit above.
- **`add_columns`** inserts after the last named header column by default, so stray
  cells to the right move over instead of landing under a new header. `before=` inserts
  before a named column.
- **`ensure_tabs`** creates a repeated title once instead of refusing it.

`FakeSheetGrid` keeps a grid per tab, truncates reads as the API does, returns the API's
400 for a read or write outside the grid, rolls a failed batch back whole, and has
`edit_externally` and `fail` hooks. It does not model `values.append`, `values.clear`, or
`USER_ENTERED` parsing. `tests/test_sheets_grid.py` tests the fake itself.

The failure-order tests assert the recorded call order and the final grid: a failed guard
writes nothing, a failed push inserts no row, a failed grid read writes nothing, a failed
row write keeps the pushes and skips the read-back, and a failed read-back raises after
the writes. Eight hand-made mutations of `apply.py` (row and grid offsets, run grouping,
the read-back comparison) each failed at least one test.

Three live tests pin the fake's assumptions: a push and an append past a shrunk grid
(with `"01"`, `"007"`, `"TRUE"`, and `"=1+2"` read back as written), an `insert_above`,
and a column add then delete.

Review: the conformance and correctness reviewers reported no findings. The test-quality
reviewer found one gap, confirmed by a verifier: no test paired an invalid `insert_above`
with a plan that has no new rows. The code was already right, so the fix is a test
(`03c6087`). The orchestrating session also read `apply.py` and `structure.py`.

### 2026-09-27 — live test quota

The live suite now makes more writes than the spreadsheet's quota of 60 per minute
allows back to back. The temporary-tab fixture called the API directly with no retry, so
a rate limit in its teardown left the tab behind. That happened during steps a and d and
left three tabs on the test spreadsheet. The fixture now sends its `addSheet` and
`deleteSheet` through `with_retry` with waits long enough to outlast the quota window.

**Numeric cells under `RAW`** (the umbrella's open question, due after this step): not
changed. Every value is written as a literal string, and the live test confirms those
read back identical, which is what the merge depends on. A per-column `USER_ENTERED`
write stays a possible follow-up.

Checks rerun after the workflow and the fixture change: ruff and pyrefly clean, 1241
tests pass, and coverage is 100%.

Commits: `1f6864a`, `ee6f999`, `f1760f5`, `604aa4e`, `68161e7`, `03c6087`, and the
fixture change.
