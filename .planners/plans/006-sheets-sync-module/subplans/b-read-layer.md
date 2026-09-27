---
status: active
branch: feature/sheets-sync-b-read-layer
---

# 006b — Build the read layer: cells, local files, tables, and retry

Part of [006](../plan.md). Step 2 of the umbrella's implementation order. It covers
everything needed to read a tab and a local file as keyed records. The merge in
[`c-merge-engine.md`](c-merge-engine.md) compares the canonical cell strings defined
here.

## Records and cells

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

## Local files

`files.py` reads and writes records by extension:

- `.csv` / `.tsv`: every cell is a string, and leading zeros, booleans, and
  dates stay exactly as written. A UTF-8 byte-order mark is accepted on read;
  `"bom": true` writes one for spreadsheet apps that need it.
- `.json`: an array of objects with typed values, written byte-stably (fixed
  key order, two-space indent, and a final newline) so a no-op run leaves the
  file unchanged and version control shows only real changes.

All writes go through `gdrives.local.atomic_output`.

## Reading a tab

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

## Row keys

A key is a tuple of one or more named columns. Key cells are compared with
surrounding whitespace stripped and internal runs collapsed, so a trailing space
typed on the sheet does not split one row into two; the stored text is never
rewritten. A duplicate key within one side raises, naming the key and the side.

## Retry

`retry.with_retry` retries with exponential backoff and jitter:

- Reads and `values.batchUpdate` (idempotent): on 429, 500, 502, 503, and 504.
- Row inserts, row appends, and column inserts and deletes (not idempotent): on
  429 only. A 5xx on one of these may have been applied, and a retry would
  apply it twice.

## Log

### 2026-09-27 — implementation

New modules `retry.py`, `cells.py`, and `table.py`; `files.py` and `values.py` extended.
Every new public name is exported from `gdrives.sheets`.

Decisions on points the sections above leave open:

- **How a tab is read.** `read_tab` reads with `UNFORMATTED_VALUE` and dates as
  `FORMATTED_STRING`, then passes every cell through `to_cell`. The canonical table's
  number and boolean rows only arise from unformatted values. The consequence: a number
  typed on the sheet reads as its value (`"3"`, `"0.5"`), whatever its display format
  (`3.00`, `50%`). A live test confirmed that a `RAW`-written `"3.0"` reads back as
  `"3.0"`, and a `USER_ENTERED` `"3.0"` reads back as `"3"`.
- **Blank cells** parse to `None` under every declared type.
- **Strict parsing.** `from_cell` refuses surrounding whitespace and digit-group
  underscores for every type but `str`. `int` takes digits with an optional minus, so
  `"3.0"` is not an `int`. `bool` takes `TRUE` or `FALSE` in any case.
- **Row keys** live in `cells.py` (`normalize_key`, `row_key`, and `index_rows`), so the
  merge imports them without importing the API wrappers. A composite key counts as blank
  when any of its parts is blank. `index_rows` lists every blank and duplicate key at
  once.
- **Optional arguments.** `read_tab` takes `columns=None` (every named header column) and
  `key=()` (no index), because pull and push treat both as optional.
- **Wide rows** are reported as `Table.wide_rows` (spreadsheet row numbers). They do not
  raise, and the cells past the header are not read.
- **A row with data only in columns that were not asked for** still needs a key, so it
  raises instead of being skipped.
- **Retry wiring.** Every wrapper in `values.py` goes through `with_retry`. Reads,
  `update_values`, `clear_values`, and `batch_update_values` retry on 429 and 5xx.
  `append_values` and `batch_update_spreadsheet` retry on 429 only. The
  `spreadsheets.get` call inside `rules.py` is not wrapped. With the defaults (5 attempts,
  1 second base, 32 second cap) a call that keeps failing gives up after about 15 to 19
  seconds.
- **Record files.** A delimited row wider than its header raises. `write_records` raises
  on a row holding a column outside `columns`. In JSON, dates and datetimes are stored as
  their canonical strings, blank cells as `null`, and `NaN` and `Infinity` are refused.
  A byte-order mark on a `.json` file raises.
- `pull_values` now returns `list[list[Any]]`, since an unformatted read returns numbers
  and booleans. A default read sends the same request as before.

`FakeSheetsService` in `tests/helpers.py` can now return an exception as a response and
models `values.batchGet`. Two live tests were added, for `read_tab` and `pull_many`.

Review: three reviewers (conformance, correctness, and test quality) reported no
findings; the orchestrating session also read the new modules. Checks rerun after the
workflow: ruff and pyrefly clean, 1016 tests pass, and coverage is 100%.

Commits: `3b33726`, `f05a286`, `1d405d2`, `0dac1e1`, `42af2bd`.
