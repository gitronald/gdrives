---
status: draft
branch:
---

# 007c — Add normalized comparison, held cells, partial keys, and plan predicates

Part of [007](../plan.md).

## Type-normalized comparison (`merge.py`)

Two cell strings can differ and mean the same value: `true` and `TRUE` in a `bool`
column, `3.0` and `3` in a `float` column. Compared as text, each reads as an edit, and a
sheet on which someone retyped a value in another spelling never settles.

- `normalize_cell(text, type_)` returns `to_cell(from_cell(text, type_))` when `text`
  parses as `type_`, and `text` unchanged when it does not.
- `merge(..., types=None)` takes a `{column: type}` mapping and compares base, local, and
  sheet cells in normalized form. Only comparison changes: a push still sends the local
  file's stored text, and a fold still takes the sheet's stored text, as with row keys.
- Parsing stays strict. `3.0` in an `int` column does not parse, so it is compared as text
  and reported by the schema check. No value is coerced.
- `plan_tab` passes the types the tab's schema declares. A tab with no schema behaves
  exactly as before.

## Holding invalid sheet cells

Today a sheet value that fails the tab's schema is folded into the merged rows, the merged
check reports it, and nothing is written for the tab: one mistyped cell on a shared sheet
blocks every other push and fold until someone fixes it.

- A tab option `on_invalid`, `"refuse"` (the default, today's behavior) or `"hold"`.
- `merge(..., schema=None, on_invalid="refuse")`. Under `"hold"`, a sheet cell that would
  be folded but fails its column's schema (type or `allowed`) is listed in
  `MergePlan.invalid` instead. The local file keeps its value and the base keeps its
  value, so the cell is reported again on every run until the sheet is corrected.
- A new sheet row with any invalid cell is flagged `remote_invalid` and not folded.
- `needs_attention` counts held cells and rows, so the run exits 2, and the report prints
  them with the reason (`'yes' is not a valid bool`).
- The local side is never held. An invalid local value refuses the tab under both
  settings, since the local file is the caller's own to fix.

## Composite keys with a blank component

`index_rows` refuses a row with any blank key cell. For a one-column key that is right.
For a composite key it rules out tables where one component is legitimately absent on some
rows and the remaining components still identify the row.

- `blank_keys`, `"refuse"` (the default) or `"partial"`, on `index_rows`, `parse_tab`,
  `read_tab`, `merge`, and as a tab config field.
- Under `"partial"` a row is refused only when every key cell is blank. Duplicate keys
  are refused as before, blank components included in the comparison.
- With a one-column key the two settings are the same.
- `apply_plan` and `verify` find rows through `row_key` and the table's `row_numbers`, so
  they need the option only where they read the tab again; the `Table` records the setting
  it was read with.

## Plan predicates

- `MergePlan.sheet_writes` (pushes or appends), `MergePlan.local_writes` (folded cells or
  rows), and `MergePlan.has_writes` (either). `_in_sync` in `sync.py` uses them.
