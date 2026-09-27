---
id: 7
slug: sheets-sync-adoption-gaps
status: draft
branch:
created: 2026-09-27T10:24:14-07:00
concluded:
pr:
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
writes, saving the base, and reporting. This plan closes that gap with additions that are
each optional and each backward compatible.

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

Out of scope:

- A DataFrame dependency. Records stay plain dicts, as in plan 006.
- Syncing formatting. Item 8 is a standalone helper and an opt-in step of a push; the
  merge still moves values only.
- Applying row deletions, and per-column `USER_ENTERED` writes. Both remain open from
  plan 006.
- New CLI commands. The existing `sheets-sync`, `sheets-pull`, and `sheets-push` gain no
  flags; the new tab options are config fields.

### Design

#### 1. Typed codec (`cells.py`)

`read_records` and `write_records` already convert typed JSON values to canonical strings
and back, in private helpers. Make the conversion public, for rows that never touch a file:

```python
def encode_rows(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None
) -> list[dict[str, str]]: ...


def decode_rows(
    records: Sequence[Mapping[str, str]], types: Mapping[str, str]
) -> list[dict[str, CellValue]]: ...
```

- `encode_rows` applies `to_cell` to every value. `columns=None` takes every key in
  first-seen order, as `read_records` does for JSON; a column a row lacks is blank.
- `decode_rows` applies `from_cell` per column, `str` for a column `types` does not name.
  It raises one ValueError listing every cell that does not parse, each by row position
  and column.
- `decode_rows(encode_rows(rows), types) == rows` for rows whose values match `types`.
- The JSON reader and writer in `files.py` are rewritten over these two functions, so
  there is one conversion. A `date` or `datetime` column still writes its canonical string
  to JSON, which has no date type.

#### 2. Python types as column types

`from_cell`, `ColumnSchema`, `decode_rows`, and `write_records(types=)` accept the classes
`str`, `int`, `float`, `bool`, `date`, and `datetime` as well as their names.
`ColumnSchema.type` is stored as the name, so equality, `TabConfig.types`, and the config
file are unchanged. The config file accepts names only, since JSON has no classes.

#### 3. Type-normalized comparison (`merge.py`)

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

#### 4. Holding invalid sheet cells

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

#### 5. Composite keys with a blank component

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

#### 6. Stores (`stores.py`)

The orchestration reaches the local side and the base through a protocol instead of a
path:

```python
class Store(Protocol):
    label: str  # shown in reports and errors

    def exists(self) -> bool: ...
    def read(self) -> Records: ...
    def write(
        self, columns: Sequence[str], rows: Sequence[Mapping[str, str]]
    ) -> None: ...
```

- `FileStore(path, types=None, bom=False)` wraps `read_records` and `write_records`, and
  is what a config file's `local` path and a target's base directory become. Behavior for
  config-driven runs is unchanged.
- `MemoryStore(columns, rows)` holds records in memory, for tests and for a caller that
  saves them itself after the run.
- `TabConfig` gains `store` and `Target` gains `base_store(tab)`. `TabConfig.local` stays,
  and is None for a tab built in code with a store that is not a file.
- `plan_tab`, `apply_tab`, `pull_tab`, and `push_tab` call the store where they read or
  wrote a path. The write order is unchanged and remains the safety property: checks,
  structure, sheet, local store, base store, widths.
- A store's `write` may raise `ValueError` or `OSError`; both are reported per tab, as a
  file error is today. `TabReport` gains `local_label`.
- The config's collision check (two tabs writing one file) covers file stores only. A
  caller passing its own stores owns that check.

What this allows, each as a small class on the caller's side: one tab of a file that
holds several tabs; typed rows, converted with the codec of item 1; rows written back in
an order of the caller's choosing; and a local side that is computed, where `write` saves
the folded values wherever the computation reads them from.

`docs/sheets-sync.md` gets a section on writing a store, with a worked example.

#### 7. `push_rows`

```python
def push_rows(
    service,
    spreadsheet_id,
    title,
    columns,
    rows,
    *,
    key=(),
    input_option=RAW,
    apply=False,
    schema=None,
    validate=None,
    widths=None,
    clear_links=False,
) -> TabReport: ...
```

The body of `push_tab` from the point where the local file has been read: the preview of
what the tab loses, the re-read guard, the single write over the old extent, the grid
growth, and the read-back. `push_tab` becomes: read the store, call `push_rows`.

#### 8. Link formatting (`structure.py`)

Sheets formats text that looks like a URL or a domain as a link when it is written, under
`RAW` input too. For a tab meant to hold plain text that is a change nobody asked for.

- `clear_link_format(service, spreadsheet_id, tab, *, columns=None, rows=None)` clears
  only `userEnteredFormat.textFormat.link` over the named columns (every named column by
  default), in one `repeatCell` request per run of adjacent columns. Every other format
  is left alone.
- `linked_cells(service, spreadsheet_id, tab)` returns the cells that render as links, by
  row number and column name.
- `push_rows(clear_links=True)` runs the first after the write and the second as part of
  the read-back, raising `ReadBackError` when a link remains.

#### 9. Plan predicates and warnings

- `MergePlan.sheet_writes` (pushes or appends), `MergePlan.local_writes` (folded cells or
  rows), and `MergePlan.has_writes` (either). `_in_sync` in `sync.py` uses them.
- `warn`, a second hook with the signature of `validate`, on `plan_tab`, `sync_tab`,
  `pull_tab`, `push_tab`, `push_rows`, and `run_target`. Its messages go to
  `TabReport.warnings` and are printed. They never block a write and never change the
  exit code. It runs on the merged rows, after `validate` has passed.

### Compatibility

Every addition is a new function, a new optional argument, or a new config field with a
default that keeps today's behavior. The one visible change for an existing config is
item 3: a tab with a schema stops reporting differences of spelling as edits. That is
noted in the changelog. The release is a minor version.

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
- Item 8 needs `FakeSheetGrid` to keep a link flag per cell and to apply `repeatCell`.
- The live integration suite gains one case per item that reaches the API: a partial-key
  tab, a `push_rows` with `clear_links`, and a sync through a custom store.

### Implementation order

Each step is its own branch and PR, in this order:

| Step | Items | Depends on |
|---|---|---|
| a | 1, 2: the codec and Python types | — |
| b | 5: partial blank keys | — |
| c | 3, 4, 9 (predicates): the merge additions | a |
| d | 6: stores | a |
| e | 7, 8: `push_rows` and link formatting | d |
| f | 9 (`warn`), config fields, docs, changelog | c, d, e |

### Open questions

- **Normalized comparison by default.** Item 3 turns on for any tab with a schema. The
  alternative is an explicit tab option. Default-on is proposed because a declared type
  already states how the column should be read.
- **Held rows.** Item 4 holds a whole new row for one invalid cell. Folding the row with
  the invalid cells blank is the alternative, and it writes a row to the local file that
  does not match the sheet.
- **`required` under `hold`.** A sheet cell blanked in a `required` column is an edit, not
  a malformed value. Proposed: hold it too, since folding it would make the local file
  fail its own check on the next run.
- **Store and `pull_all_tabs`.** It writes one file per tab with no config. It could take
  a store factory; left as is unless a use appears.
