---
id: 11
slug: datetime-cell-form
status: draft
branch:
created: 2026-09-27T16:46:14-07:00
concluded:
pr:
---

# Read datetime serials in one fixed-width form

## Plan

### Goal

Every datetime cell that a read turns from a serial number into text arrives as
`YYYY-MM-DD HH:MM:SS.mmm`. Today `serial_to_cell` rounds a serial to the millisecond and
then writes it with `to_cell`, which is `str(datetime)`. `str` drops the fraction when it
is zero, so one column holds both `2026-02-03 13:11:57.926000` and `2026-02-03 12:00:00`.
A local file written from a pull tab then has a column with two widths, which is awkward
to parse downstream and noisy to diff. Serials are rounded to the millisecond already,
so three digits of fraction lose nothing.

### Decision: change the serial path only, not `to_cell`

`to_cell` stays `str(value)` for a datetime. The reasons:

1. **`to_cell` is lossless and the fixed form is not.** `to_cell` also writes a
   caller's own typed values (`encode_rows`, a JSON file's typed values), and a
   datetime built in code can hold microseconds. A millisecond form would drop them,
   and `from_cell(to_cell(v)) == v` would stop holding. A serial can't hold anything
   finer than a millisecond, so on the serial path the fixed form is exact.
2. **Changing `to_cell` would change much more.** It is used for every cell the
   library writes or compares: pushed text, the typed JSON path, `insert_above`
   values, and `normalize_cell`. Moving all of them to the fixed form would rewrite
   cells that callers pushed as text and records they built in code. The serial path
   is only used to read a declared `date` or `datetime` column.
3. **A `date` is already fixed-width** (`date.isoformat`), so only `datetime`
   changes.

What does not change: a text cell in a `datetime` column (a value pushed as a literal
string, or typed as text) is read as its text, as before. Rewriting text would be a
guess. `datetime.fromisoformat` accepts a date alone, an offset, and microseconds, and
rewriting any of those to the millisecond form either loses data or invents it. It
would also break `verify`, below. A column of serials, such as a form-responses
timestamp column, is uniform after this change. A column mixing serials and text is
uniform in its serial cells.

### Compatibility (a changed output)

Base files and local files that people have committed hold the old form. What each
path does with them:

- **Merge.** A typed column compares through `normalize_cell`, which is
  `to_cell(from_cell(text))`. `datetime.fromisoformat` reads both forms, so an old-form
  base or local cell and a new-form sheet cell normalize to one value and compare
  equal. When two sides agree, the base takes the local text, so the first run after
  upgrading writes nothing to the sheet, the local file, or the base. A test pins this
  by merging an old-form base and local file against a new-form sheet read.
- **An untyped column** gets no serial read, so nothing changes there.
- **Pull.** A pull compares raw strings and rewrites the file when they differ. The
  first pull after upgrading rewrites each datetime cell whose fraction was zero, once.
  The changelog says so.
- **`verify` (`apply.py`)** compares pushed text to the read-back as raw strings. A
  push is a `RAW` literal string, which the sheet stores as text, and the serial read
  returns text as text, so the read-back of a pushed cell is the text sent. The serial
  path never sees it. A test pins that a pushed old-form datetime reads back unchanged
  through the declared-type read. `verify` needs no change.
- **`_check_push` (whole-tab push)** reads the grid without types, so it has no serial
  path and is unaffected.

### Changes

- `cells.py`: `serial_to_cell` writes a datetime as
  `moment.isoformat(sep=" ", timespec="milliseconds")`. Update the docstrings of
  `serial_to_cell`, `parse_tab`, and `pull_serials`.
- Tests (`tests/test_sheets_cells.py`, `tests/test_sheets_table.py`,
  `tests/test_sheets_sync.py`, `tests/test_sheets_apply.py`):
  - a whole-second serial and a fractional serial give the same width
  - midnight and the end of the day round-trip through `from_cell`
  - a merge of an old-form base and local file against a new-form sheet read writes
    nothing
  - a pushed old-form datetime passes `verify` on the declared-type read-back
  - existing assertions of the old form are updated
- The live test that reads a datetime serial, if one asserts the form, is updated.
- Docs: `docs/sheets-sync.md` (the section on date columns), the README if it shows the
  form, and a `### Changed` entry under `[Unreleased]` in `CHANGELOG.md` that describes
  the one-time respelling on a pull.

### Out of scope

- Rewriting text cells in a datetime column (see the decision above).
- Any change to `to_cell`, `normalize_cell`, or `encode_rows`.
