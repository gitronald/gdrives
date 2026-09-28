---
id: 10
slug: sheets-typed-writes
status: done
branch: feature/sheets-typed-writes
created: 2026-09-27T11:29:26-07:00
concluded: 2026-09-27T21:45:23-07:00
pr: https://github.com/gitronald/gdrives/pull/61
---

# Write typed columns to the sheet as dates and numbers, not text

## Plan

### Goal

Let a tab ask that a column declared `int`, `float`, `bool`, `date`, or `datetime` be
written to the sheet as a value of that type, so that the people who use the sheet can
sort it, filter it, and compute over it.

A sync writes every value as a `RAW` literal string. That is what makes a write read
back identical, and it is why a pushed `2026-09-27` is text on the sheet: it
left-aligns, sorts as text, and `=A2+7` fails. Plan 006 left this open for numbers, and
plan [007](../007-sheets-sync-adoption-gaps/plan.md) found it again for dates and set
it aside (its decision 15). 007 also builds what this plan needs and could not have had
before: dates read from serial numbers, and comparison normalized by declared type.

**Depends on plan 007, steps b and c.** This plan does not start before they merge.

### Scope

In scope:

1. Typed values written for the pushed cells and the new rows of a sync.
2. The same for a whole-tab push.
3. A read-back that compares by declared type.
4. A helper that turns the text a column already holds into values.

Out of scope:

- Formulas. A cell starting with `=` stays a literal string.
- Any formatting but the number format a date needs to display as a date.
- Key columns, which are always written as text (see the design).
- A change of default. Text stays the default, and a tab opts in.

### Design

#### The choice of mechanism

Two ways to write a typed value, both checked against the live API in plan 007's
[note 005](../007-sheets-sync-adoption-gaps/implementation-notes/005-serial-and-format-probe.md)
(finding P14) or its note 004 (P4).

| | B. `USER_ENTERED` | C. Typed `userEnteredValue` |
|---|---|---|
| How | The ISO string is sent through the values API and Sheets parses it | `updateCells` sends a `numberValue` or `boolValue`; a date is sent as its serial |
| Parsing | By Sheets, under the spreadsheet's locale. Not varied in the probe | None. The package converts, by the declared type |
| Date display | Sheets sets a pattern from the text: `yyyy-mm-dd`, and `yyyy-mm-dd h:mm:ss` with an unpadded hour [P14] | The package has to set a number format, or the cell shows `46292` |
| New rows | Not covered. `apply_plan` writes new rows with `updateCells`, which has no input option | Covered, by the same request |
| Requests | Pushes split in two `values.batchUpdate` calls, one per input option | Pushes move into the run's one `spreadsheets.batchUpdate`, so every sheet write of a run is one request, and atomic |
| Promise kept | The sync sets no format itself | The sync sets one format, on date cells |

**Proposed: C.** B cannot write a new row, so a plan that chose B would need C's
mechanism for rows anyway, and then has two. C is exact and does not depend on locale.
Its cost is the one format it sets, which is the first open question below.

#### Writing

- A tab field `typed_writes`, true or false, for `sync` and `push` tabs. The schema
  already declares the types.
- `cell_data(text, type_)` in `cells.py`, pure, returns the `CellData` for one
  canonical string: `numberValue` for `int` and `float`, `boolValue` for `bool`, and the
  serial as `numberValue` for `date` and `datetime` (the inverse of 007's
  `serial_to_cell`). A blank is an empty cell, and `str` is a `stringValue`, as today.
- A cell that does not parse as its column's type never reaches a write: the schema
  check refuses the tab first.
- With `typed_writes`, `apply_plan` sends pushed cells as `updateCells` requests in the
  same `spreadsheets.batchUpdate` as the inserts, after them, with the rows as they are
  once the inserts have run. The order that protects row numbers today (pushes first)
  becomes an order inside one request.
- Without it, nothing changes: pushes go through `values.batchUpdate` as `RAW`.
- `push_rows` writes the whole tab with `updateCells` in place of `values.update`.
- 007's `clear_links` rides along: the link field joins the mask of the same request.

#### Key columns stay text

Keys are matched by their text, with whitespace normalized and nothing else, and
`from_cell` reads `007` as the `int` 7. A key written as a number would come back as
`7` and match no local row. So a key column is written as a literal string whatever
its declared type, and the guide says that a column whose leading zeros matter is a
`str` column.

#### Reading back

- `verify` and the push read-back compare in normalized form (007's `normalize_cell`)
  for a typed column: a number written as 3 reads back `3` where the local text was
  `3.0`.
- Dates are read back as serials (007 step b), so the comparison is on ISO 8601 and
  not on display text.
- The merge is unchanged. It compares normalized already, and the base keeps canonical
  strings.

#### Cells that are already text

A column pushed before `typed_writes` was turned on holds text, and a cell is rewritten
only when it changes. `retype_columns(service, spreadsheet_id, tab, schema)` reads the
typed columns, and rewrites as values the cells that hold text which parses as the
column's type. It previews by default, reports what it would change and what does not
parse, and leaves everything else alone.

### Compatibility

Nothing changes for a tab that does not set `typed_writes`. For one that does, the
first run after turning it on changes no cell by itself; cells become values as they
are pushed, or all at once with `retype_columns`.

### Testing

Coverage is gated at 100%.

- `cell_data` for each type, a blank, and the round trip with `serial_to_cell`.
- `apply_plan` with `typed_writes`: pushes only, new rows only, both with an insert
  above the pushed rows, a key column of type `int`, and the request count, which is
  one.
- The read-back: a spelling that differs and a value that differs.
- `FakeSheetGrid` stores a typed `userEnteredValue`, and returns it under an
  unformatted read and as a serial.
- `retype_columns`: a mixed column, a cell that does not parse, and a preview that
  writes nothing.
- Live: one sync that pushes a date, a date-time before 10:00, a float, and a
  boolean, and reads each back; one formula on the sheet over the pushed date.

### Open questions

- **B or C.** C is proposed above. The owner decides when the plan is activated.
- **Which format a date cell gets, and when.** Setting `yyyy-mm-dd` on every date cell
  written is simple and overrides a format a person chose. Setting it only where the
  cell has no date format keeps theirs and costs a grid read per run. A third way is
  to set it on new rows only and leave pushed cells' formats alone.
- **Whether `retype_columns` gets a command.** A flag on `sheets-sync` would run it
  once; a helper alone leaves it to a caller's script.
- **`USER_ENTERED` targets.** A target can already push with `USER_ENTERED`
  (`input_option`). Whether `typed_writes` replaces that option, or the two are
  refused together, is settled with the first question.

## Log

### 2026-09-27

- The owner settled the open questions at activation: **C** (typed
  `userEnteredValue` by `updateCells`); a date format only on a written date
  cell that has **no date or time format** (one grid read); `retype_columns`
  as a **library helper only**; `typed_writes` and a `USER_ENTERED` target
  **refused together**.
- `1f0f317` — `cell_data`, `to_serial`, and `DATE_FORMATS` in `cells.py`;
  new `typed.py` (`typed_columns`, `dated_cells`, `format_requests`); new
  `retype.py` (`retype_columns`, `RetypeReport`, `RetypeCell`);
  `apply_plan`/`verify`/`push_rows` take `typed_writes`; config field
  `typed_writes` with its refusals. Beyond the spec, `cell_data` refuses a
  value the sheet would not hold exactly (an int past 2**53, a non-finite
  float, an aware or sub-millisecond datetime), before any request, and
  `typed_writes` is refused with `render: formatted`, whose read returns the
  displayed text, not the value.
- `retype_columns` takes a `key=` the plan's signature lacked, so the key
  columns stay text as they do in a typed write.
- `c5a3ae8` — `FakeSheetGrid` holds number formats (masked writes, grid read,
  inherited by inserted rows); `tests/test_sheets_typed.py` (77 tests);
  the guide's "Typed writes" section, with a runnable `retype_columns`
  example. `DATE_FORMATS` became a `MappingProxyType`, and the package
  surface test counts such constants.
- `421f35f` — live test: a sync pushes a date, a 09:05 date-time, a float, and
  a boolean as values; `yyyy-mm-dd` and `yyyy-mm-dd hh:mm:ss` display as
  expected (a zero-padded 24-hour hour); `=B2+7` over the pushed date computes;
  a pushed edit reads back by value; the next run writes nothing.
- `5acb19c` — changelog.

#### Review follow-up

- `/code-review` at medium (correctness and reuse finders): the correctness
  pass found nothing. Two cleanups were confirmed and fixed in `afe7a5e`:
  `_write_typed` hoisted `out.index(column)` out of its per-row loop, and
  `apply.py` uses `typed._VALUE_FIELD` for its value mask instead of two
  literals. Both are behavior-preserving; the existing tests cover them.

## Retrospective

- The four open questions were the real gate: each changed code shape
  (C's single batch, a grid read for formats, no CLI surface, a config
  refusal), so asking them at activation, with recommendations, kept the
  implementation to one pass.
- "Only where the cell lacks a date format" was more work than it sounds,
  because a new row's format is decided by where it lands: an inserted row
  inherits (from above, or below under the header), an in-place row keeps the
  grid's, and a row past the grid has none. Modelling inheritance in the fake
  made that testable.
- Plan 007's typed comparison paid off: the merge and base needed no change,
  and the read-backs reused `normalize_cell` and the serial read.
- The live suite showed the formats and the formula behave as designed; it did
  not exercise format inheritance on an inserted row against the real API,
  which rests on the fake's model.
