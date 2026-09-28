# Reorder a keyed tab's rows to a given order by moving whole rows

Step 2 of [plan 018](../plan.md). Originally drafted as plan 013, which was retired when the steps were folded into one plan.

## Spec

### Goal

An operation that takes a keyed tab and the order its rows should be in (a sequence of
keys the caller computed) and makes the sheet match, by moving whole rows. A sync keeps
the sheet's row order, and `insert_above` only places new rows, so a tab kept in a
canonical order drifts as collaborators append rows, and there is no way to restore
it. The order a downstream project needs cannot be expressed as a `sortRange`: it ranks
an enum column by a custom order rather than alphabetically, then sorts by a numeric id
with blanks last, then by further keys. So the caller computes the order, and the
library only applies it.

### Why move rows, not rewrite values

Rewriting values in place would leave each row's formatting, notes, validation, and
unprojected cells behind on the wrong row. `moveDimension` moves the whole row, with
its formatting and every column, including columns the caller never read.

### API

New module `gdrives/sheets/order.py`, re-exported from `gdrives.sheets`:

```python
reorder_rows(service, spreadsheet_id, tab, key, order, *,
             apply=False, blank_keys="refuse") -> ReorderResult
```

- `order` is a sequence of keys. Each key is a sequence of cell strings, one per key
  column, or a plain string for a one-column key. Keys are compared normalized
  (`normalize_key`), as everywhere else.
- `ReorderResult`: `moves` (the number of `moveDimension` requests), `moved` (the keys
  that change position), `unchanged` (True when the tab is already in order), and
  `applied`.
- It previews by default: read, plan, report, and write nothing.

### Rows and positions

- The tab is read whole (`read_tab`, `columns=None`, with `key` and `blank_keys`).
  That refuses a blank or repeated key, as a sync does.
- The block being reordered is spreadsheet rows 2 to `last_row`. Entirely blank rows
  inside it keep their positions. The keyed rows fill the non-blank slots in the
  caller's order.
- **Rows the order does not name are refused**, listed by key, and nothing is written.
  Refused too: keys the tab lacks, a key the order repeats, and a key of the wrong
  length. Every problem is listed in one `ValueError`.

### Moves

- Give every row of the block an identity (its key, or a distinct token for each blank
  row). Build the target sequence, and keep the longest increasing subsequence of the
  current rows (by target index) in place. Every other row is moved once, to directly
  after its target predecessor. That makes the fewest single-row moves, so a tab with
  one appended row out of place costs one move rather than a cascade.
- The moves are computed by simulating them on a list, and every index is converted to
  `moveDimension`'s coordinates. Per the API reference, `destinationIndex` is counted
  **before the source row is removed**, so a move down targets one past the list
  index. **Check this against the live API** before relying on it (see Testing).
- All moves go in one `spreadsheets.batchUpdate`, which is atomic: either every row
  moves or none does.

### Guard and read-back

- With `apply`, the tab is read again just before the write, and the write is refused
  with `SheetChangedError` if its header, rows, or row numbers differ from the preview
  read (`_changes` from `apply.py`, over every named column).
- After the write, the tab is read back. Every row must hold the cells it held before,
  found by key, and the keyed rows must be in the given order. Otherwise it raises
  `ReadBackError`, listing the mismatches.
- An already-ordered tab makes no write request.

### Other effects to document

`moveDimension` adjusts formulas, conditional-format ranges, and named ranges the way a
drag in the UI does. A formula that refers to other rows by position follows its row.
A filter view or a sort that someone applied to the sheet is not reapplied. The guide
says this.

### Testing

- Unit tests with the fake service cover:
  - move computation: already in order, one appended row, a reversal, blank rows in
    the block, and a composite key
  - that every move sequence, replayed on a list with the API's index rule, yields the
    target (a property test over random permutations with blank rows mixed in; the
    suite already uses hypothesis)
  - the refusals
  - the guard and the read-back
  - preview writes nothing
- A live test (`tests/test_sheets_integration.py`) on a temporary tab covers:
  - write rows with a format on one of them and a column outside the key
  - reorder them
  - check that values, the formatting, and the extra column moved with their rows

  This is where the `destinationIndex` rule is confirmed.

### Docs

- `docs/sheets-sync.md`: a section on keeping a tab in order, with a runnable Python
  example that the guide test runs against the fake service. The section covers
  computing an order with a custom rank and calling `reorder_rows`.
- README: mention the operation in the sheets overview.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- A CLI command. The order is computed in code, so this is a library call.
- A config-driven order, and running a reorder as part of `sheets-sync`.
- Rows that the order does not name keeping a place. The refusal is the defined fate.
  A caller who wants them kept appends them to its order.
