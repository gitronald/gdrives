# Read a tab as displayed

Step 8 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second list
of gaps by the same downstream caller.

## Spec

### Goal

A tab setting that reads the tab as the sheet displays it. `read_tab`, `plan_tab`,
`pull_tab`, and `push_rows` read unformatted values: a cell showing `50%` reads as
`0.5`. A caller whose files hold what the sheet displays sees every cell with a
number format as a sheet edit on its first run, and cannot move over without
rewriting its files.

### Design

- **Config:** a tab field `render`, `unformatted` (the default) or `formatted`, for
  every mode. `RENDERS` names the two.
- **Python API:** `TabConfig.render`, and `render=` on `read_tab` and `push_rows`.
  The values are the config's words, not the API's constants: `formatted` sends
  `FORMATTED_VALUE`, and `unformatted` sends what is sent today.
- **`Table.render`** records the setting a tab was read with, as `Table.types` and
  `Table.blank_keys` do. The re-read guard and the read-back of `apply_plan` and
  `push_rows` read the tab again with the table's own setting, so both reads of a
  run are the same kind of read. No caller passes it twice.
- **Every read of a run follows the setting**: the preview read, the guard, the
  read-back, the header read of `structure.py` where a run makes one, and the read
  `push_rows` makes of what the tab holds.
- **Writes are unchanged.** They are `RAW` literal strings, and a literal string is
  displayed as it was written whatever the cell's number format, so a cell a run
  wrote reads back as the text written under either setting.

### Declared types under `formatted`

- **`date` and `datetime`.** A declared column is read by a second read of its own,
  as serial numbers, which does not depend on the render of the first. Under
  `formatted` that stays as it is: a declared date column arrives as ISO 8601, and
  every other column as displayed. This is sound, and it is the way to keep a date
  column out of the display's hands. Confirm in the code that `pull_serials` sends
  its own render options, and refuse the combination in the config check only if
  that turns out not to hold.
- **`int` and `float`.** A number is displayed by its format: `1,234.50`, `50%`.
  Such a cell does not parse as its type and is a schema problem, reported as any
  other, and with `on_invalid: hold` it is held. Nothing is refused in the config
  check, since a column of plain numbers is displayed as its value. The guide says
  that a typed numeric column and `formatted` do not go together on a tab with
  number formats.
- **`bool`.** Displayed as `TRUE` or `FALSE`, which is what is read today.

### Tests

- Config: the field is accepted on each mode, and any other word is refused.
- `read_tab` with `render="formatted"` sends `FORMATTED_VALUE`, and the table records
  it. The default sends what it sent before: a test compares the requests of a
  default run with the requests recorded before this step.
- A sync, a pull, and a push over a tab with a number format, with the fake service
  returning the displayed text for a formatted read: the first run reports no edit
  for a local file that holds the displayed text.
- The guard and the read-back of a `formatted` run send `FORMATTED_VALUE` too.
- A declared date column under `formatted` arrives as ISO 8601.
- A displayed number that does not parse as its declared type is a problem, and is
  held with `on_invalid: hold`.
- The fake service learns to return a displayed form for a formatted read, if it
  does not have one.

### Docs

- `docs/sheets-sync.md`: `render` in the tab fields table and in "How cells are read
  and written", and a part of "Moving an existing sync over" on files that hold what
  the sheet displays.
- README config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- `sheets-pull --all-tabs`, which takes no config.
- Writing with `USER_ENTERED`.
- A render per column.
