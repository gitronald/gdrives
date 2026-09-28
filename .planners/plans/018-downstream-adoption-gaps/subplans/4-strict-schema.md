# Refuse projection columns the schema does not declare

Step 4 of [plan 018](../plan.md). Originally drafted as plan 015, which was retired when the steps were folded into one plan.

## Spec

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
  **Widened on 2026-09-27: see [the amendment](#amendment-every-column-of-either-side)
  below, which replaces this sentence.**
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

### Amendment: every column of either side

Added on 2026-09-27, before the step was started, at the owner's word. A second list
of gaps asked for a wider check than the one above, and one setting is better than
two. Where this section and the design above differ, this section holds.

- **What is checked:** every column of **either side** must have a `schema` entry,
  less the columns the run is dropping. That is the projection, as above, and also:
  - the local side's columns outside the projection (the carried columns). A typed
    local file writes a carried column by its declared type, so an undeclared one is
    written as text, which is the silent failure this setting is for.
  - the sheet's named header columns outside the projection, less the columns a run
    with `drop_extra` deletes.
- **`strict_schema` stays a boolean.** There is no second setting and no `"all"`.
- **When:**
  - **sync:** the local side's columns, in and out of the projection, at the `local`
    stage, before any request, as above. The sheet's columns at the `sheet` stage,
    after the read and before anything is written. A column that both sides have is
    reported once, at the `local` stage.
  - **push:** the local side's columns at the `local` stage. A push replaces the tab,
    so the header it replaces is not checked.
  - **pull:** the sheet's named header columns at the `sheet` stage, as above. With
    `columns`, the header's columns outside it are checked too. With `exclude`
    (step 1), an excluded column is not read and is not checked.
- **`schema` may then name a column outside the projection.** Today the config
  checker refuses a `schema` column that `columns` leaves out. Under `strict_schema`
  that refusal is lifted, since a carried column has to be declared somewhere. With
  `strict_schema` off, the refusal stays as it is.
- **The message says which side:**
  `Members (sheet): column 'notes' has no schema entry, and the tab is strict_schema`.

Tests, beside the ones above:

- A carried local column with no schema entry is a problem at the `local` stage, and
  no request is made. This replaces "a carried local column is not" in the last line
  of the tests above.
- A sheet column outside the projection is a problem at the `sheet` stage, and
  nothing is written.
- A column a run drops with `drop_extra` is not reported.
- A column on both sides is reported once.
- A pull with `exclude` does not report the excluded column.
- A `schema` entry for a column outside `columns` is accepted with `strict_schema`,
  and refused without it, as today.

### Out of scope

- Inferring a type for the undeclared column.
