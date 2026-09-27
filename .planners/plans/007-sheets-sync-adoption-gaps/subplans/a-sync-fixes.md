---
status: draft
branch:
---

# 007a — Fix where a sync puts new rows and columns, their formatting, and line endings

Part of [007](../plan.md). Step 1 of the umbrella's implementation order. It changes
released behavior only and adds no concept, so it can ship by itself. Every change here
is a visible one, listed in the umbrella's [Compatibility](../plan.md#compatibility)
table, and each gets a test that fails on 0.11.0, written first.

Notes applied: D1, D2, D4, D6, M4.

## `insert_above` placement [D1]

`apply_plan` finds the insert row by testing each fresh row's value in the
`insert_above` column (`apply.py:311-322`). The pushes of the same plan are sent before
the rows are inserted, so by the time the rows land the column may hold other values
than the ones tested.

The case that breaks: the column is in the projection, and one run both pushes a row's
value to a matching one and appends a new row. Rows 2 to 9 are open and rows 10 on are
closed; the run closes row 5 and adds a row. The new row belongs above row 5, the first
closed row once the run is done. 0.11.0 places it above row 10, below a closed row.

- `insert_point(table, plan, insert_above) -> int | None` in `apply.py`, pure. It
  returns the 1-based spreadsheet row the new rows go above, or None when no row
  matches and they go after `last_row`. A row's value in the column is the plan's push
  to that cell when there is one, else the value read.
- `apply_plan` calls it on the fresh read. Pushes do not move rows, so the row numbers
  of the fresh read are still true when the insert is sent.
- When the column is outside the projection the plan has no pushes to it, and the
  result is what 0.11.0 computes.
- A fold never changes the sheet, so folds are not applied.
- `plan_tab` calls it on the grid it read and stores the result in
  `TabReport.insert_row`. `format_report` prints it with the new rows:
  `new rows for the sheet (2), above row 5: ...`, or `after row 40` when nothing
  matches. The preview and the apply use the one function, so a preview shows where
  the rows will go.
- For a column outside the projection, `plan_tab` takes the column from the whole-tab
  parse of the same grid, which `_sheet_side` already has. No request is added.

## Column placement [D2]

`_restructure` calls `add_columns` with no position (`sync.py:620-623`), so every
missing column lands after the header's last named column. A caller that treats the
local file's header as the sheet's header expects each new column at its place in the
projection.

- `place_columns(service, spreadsheet_id, tab, columns)` in `structure.py`. `columns`
  is the wanted order. Each column of it the header lacks is added directly after the
  nearest earlier column of `columns` that the header has or that this call has just
  placed, or at the front when there is none.
- One header read, one grid read, and one `batchUpdate`. The inserts are sent right to
  left by position, so each index is still true when its request runs, and each group's
  header cells are written in the same request.
- Positions are taken on the sheet's header, not the local one. A column that
  `drop_extra` removes in the same run is still on the sheet when the new ones are
  placed, since deletes run after adds, and an index from the local header would land
  the new column short of its place.
- Columns the sheet already has are never moved. When the sheet's order differs from
  the projection's, a new column still goes after its nearest earlier projection
  column, wherever that sits.
- New columns take the formatting of the column to their left, and of the column to
  their right at the front, as `add_columns` does.
- `add_columns` gains `after=`, exclusive with `before=`, for a caller placing one
  group itself.
- `_restructure` calls `place_columns(planned.columns)` where it called
  `add_columns(added)`. A tab that is missing or empty still gets its whole header from
  `add_columns`.

| Sheet header | Projection | Result |
|---|---|---|
| `id, name, notes` | `id, email, name, notes` | `id, email, name, notes` |
| `id, name` | `status, id, name, city` | `status, id, name, city` |
| `id, legacy, name` | `id, name, city` | `id, legacy, name, city`, then `legacy` dropped with `drop_extra` |
| `name, id` | `id, email, name` | `name, id, email` |

## Formatting of inserted rows [D4]

`insertDimension` is sent with `inheritFromBefore: False` (`apply.py:228`), so new rows
take the formatting of the row they sit above. With `insert_above` that row is the first
of the block the new rows are being kept out of. When that block is formatted directly
(a grey fill on closed rows), a new open row arrives grey.

- `inheritFromBefore` is True, except when the rows go directly below the header
  (0-based `at == 1`), where the row above is the header and inheriting from below is
  right.
- No tab option (decision 3).
- Conditional format rules are unaffected: they apply by range, and an insert inside a
  rule's range extends it either way.

## Line endings [D6, M4]

`write_values_csv` builds its writer with the `csv` module's default line terminator
(`files.py:60`), so every `.csv` and `.tsv` file a sync or a pull writes ends its lines
with CRLF. Two callers, independently, found it the only difference when their LF files
were rewritten (24 files and 3). The cost is a whole-file diff the first time a run
changes a file, and a permanent one for a repository that normalizes line endings.

- `write_values_csv(..., newline="crlf")` gains the argument and keeps its default, so
  `sheets-get -o` still writes the CRLF rows that 0.10.0's changelog describes.
- `write_records(..., newline="lf")` passes it through, and defaults to LF (decision
  3). A JSON file is written with LF already.
- A tab field `newline`, `"lf"` (the default) or `"crlf"`, for every mode. Like `bom`
  it is refused on a `.json` tab.
- The base follows its tab's setting.
- `pull_all_tabs` writes LF.
- A file is rewritten only when its records change, so a CRLF file that 0.11.0 wrote
  keeps its line endings until the next run that changes it, and flips once then. The
  changelog says so.
- Reading is unaffected: the reader takes either.

## Tests

- `insert_point`: the case above; a column outside the projection; no match; several
  pushes to the column; a plan with no new rows. One test that the preview's row and
  the apply's row are equal.
- `place_columns`: each row of the table, a blank header gap, a grid too narrow for the
  new columns, and the refusals `add_columns` has.
- The inserted-row request, asserted from `FakeSheetGrid.calls`: above a row, directly
  below the header, and at the end of the tab, where nothing is inserted.
- Line endings, asserted on bytes: a sync's local file and base, a pull, `--all-tabs`,
  both settings, with and without `bom`, and `sheets-get -o` unchanged.
- Live: one sync that pushes a match and adds a row, then reads the new row's fill
  back with a `fields` mask; one `--add-missing` run that places a column mid-header.

## Config and docs in this step

- `newline` in `_TAB_FIELDS`, `TabConfig`, and the guide's tab field table.
- The guide's sections on `insert_above` and `--add-missing` say where rows and
  columns land.
- Changelog, under Changed: the four changes of this step.
