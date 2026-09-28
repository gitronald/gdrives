# Stricter schema checks: column presence and strict forms

Step 12 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second
list of gaps by the same downstream caller.

## Spec

### Goal

Two checks a downstream caller makes that `ColumnSchema` cannot, each opt-in. The
`check` hook can do both today. The gain is that `on_invalid: hold` and the
commands, which take no hook, can use them.

### How this relates to step 4

The caller's list had a third check: a column on either side that the schema does
not declare is a problem, less the columns the run is dropping. That is
[step 4](4-strict-schema.md), which was widened to it on 2026-09-27, before it was
started. This step first carried the wider check itself, as `strict_schema: "all"`,
and no longer does. It comes after step 4, and adds nothing to `strict_schema`.

### This step may stop at a write-up

It is one of the two lower-value steps. **If the design looks doubtful once the code
is open, the options are written up in this plan's Log and the step stops there.**
The two checks are independent, so one that is sound can land without the other.

### 1. Column presence

- A schema field `present`, true or false, false by default. A column declared
  `present` must be in the header: of the sheet for every mode, and of the local
  side for a sync and a push.
- It is not `required`. `required` says a cell may not be blank. A column can be
  present with blank cells, and a `required` column that is absent is today reported
  once per row, or not at all on a tab with no rows. `present` reports the column
  once: `Members (sheet): column 'email' is declared present and the header lacks it`.
- `--add-missing` adds a projection column the sheet lacks. A column the run is
  adding is not reported for the sheet.

### 2. Strict forms

- A schema field `strict`, true or false, false by default, for `bool` and `date`
  columns:
  - `bool`: `TRUE` or `FALSE`, in that case only. Today `true` passes.
  - `date`: `YYYY-MM-DD` only. Today any form `date.fromisoformat` reads passes,
    such as `20260927`.
- `strict` on a column of another type is refused in the config check, and by
  `ColumnSchema` itself, so that it never reads as a promise the code does not keep.
- The check lives in `cell_problem`, so a value that fails it is a schema problem
  like any other: it blocks the write, and with `on_invalid: hold` a sheet value that
  fails it is held.
- **Comparison is unchanged.** `normalize_cell` still compares `true` and `TRUE` as
  one value, so a strict column does not turn a respelling into an edit. It turns it
  into a problem, on the side that holds it.
- A `date` column read from its serial number arrives as `YYYY-MM-DD` and passes. A
  text cell in that column is checked as text.

### Tests

- Config: `present` and `strict` are accepted, and each is refused with a value of
  the wrong kind. `strict` on an `int` column is refused.
- Presence: a declared column the header lacks is reported once, on a tab with rows
  and on one with none. A column the run adds is not.
- Strict forms: `true`, `True`, and `20260927` are problems under `strict` and pass
  without it. A held cell under `on_invalid: hold`.
- A strict `bool` column with `true` on the sheet and `TRUE` locally reports a
  problem and no edit.
- Every default is unchanged: a test runs a 0.13.0 config and compares the report.

### Docs

- `docs/sheets-sync.md`: the two in the schema section, with the difference
  between `present` and `required` in a sentence of its own.
- README config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Strict forms for `int`, `float`, and `datetime`.
- Inferring a schema from a tab.
