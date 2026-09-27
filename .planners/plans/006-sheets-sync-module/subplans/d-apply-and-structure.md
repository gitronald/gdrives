---
status: draft
branch:
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
