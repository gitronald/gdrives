---
id: 15
slug: strict-schema
status: draft
branch:
created: 2026-09-27T16:46:18-07:00
concluded:
pr:
---

# Refuse projection columns the schema does not declare

## Plan

### Goal

An opt-in tab setting under which a column the tab carries, but the schema does not
declare, is a problem, found before any request. A column with no schema entry is
treated as `str`. For a typed local side that is a silent failure: a new boolean column
added locally syncs as text until someone notices. A downstream project checks for
this itself, and a setting on the tab is better than a `check` hook that every caller
has to write.

### Design

- **Config:** a tab field `strict_schema`, true or false, false by default. It is valid
  for every mode.
- **Python API:** `TabConfig.strict_schema: bool = False`. `push_rows` gets
  `strict_schema=False` too, since it takes `schema` directly.
- **What is checked:** every column of the projection must have a `schema` entry,
  key columns included. A key has a type as much as any column does, and declaring it
  `str` is one line. Columns outside the projection (carried local columns, sheet
  columns outside `columns`) are not checked, since the run does not type them.
- **When:**
  - **sync:** at the `local` stage, in `_plan` before the base, the tab listing, or the
    sheet is read. The projection there is the configured `columns`, or every local
    column.
  - **push** (`push_tab`, `push_rows`): at the `local` stage, before any request.
  - **pull:** there is no local stage. A pull's projection is the columns it reads
    from the sheet, which the sheet can grow, and that is where an undeclared column
    turns up. The check runs at the `sheet` stage, after the read and before anything
    is written. For a pull, "before any request" cannot hold: the columns are not known
    until the header is read.
- **How it is reported:** one problem per undeclared column, through the same path as
  schema problems, so it lands in `TabReport.problems` and blocks every write:
  `Members (local): column 'active' has no schema entry, and the tab is strict_schema`.
  The check lives in `_problems`, next to the schema check, with `strict_schema` passed
  through.
- It is independent of `on_invalid`. `hold` concerns sheet values, and this concerns
  the columns.

### Tests

- Config: accepted on each mode, and must be a boolean.
- sync: an undeclared projection column is reported at the local stage, and the fake
  service records no request. A fully declared tab runs as before. The default off
  changes nothing.
- push_tab and push_rows: the same, with no request.
- pull: an undeclared sheet column is a problem at the `sheet` stage, and the local
  file is not written.
- A key column is covered, and a carried local column is not.

### Docs

- `docs/sheets-sync.md`: `strict_schema` in the schema section, with an example.
- README config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Inferring a type for the undeclared column.
