---
status: active
branch: feature/sheets-sync-adoption-c-merge-additions
---

# 007c — Add normalized comparison, held cells, partial keys, and plan predicates

Part of [007](../plan.md). Step 3 of the umbrella's implementation order. Everything
here is pure. The sections are built in the order below: the draft had partial keys and
the merge additions as separate steps, and both edit `merge.py`, so they are one step
and one diff [R14].

Notes applied: R1, R6, R7, R10, D9.

## Type-normalized comparison (`merge.py`)

Two cell strings can differ and mean the same value: `true` and `TRUE` in a `bool`
column, `3.0` and `3` in a `float` column. Compared as text, each reads as an edit.

The draft said such a sheet never settles. It does, in one or two runs [R1]: with a
file-backed local side the sheet's spelling folds in and the two sides agree, and with a
typed local side the fold is decoded, read back in canonical form, and pushed on the
second run. The costs of comparing as text are these:

- a fold and a push for each respelling, each a write nobody asked for; and
- a false conflict when the other side changed the same cell for real, since text
  comparison sees both sides as changed. With base `3.0`, the sheet retyped to `3`, and
  the local file edited to `4`, the run reports a conflict where there is one edit.

The second is the stronger reason. A respelling is common in practice: a sync writes
`RAW` strings, so the sheet holds the text `3.0`, and a person who retypes the cell
leaves the number 3, which reads as `3`.

- `normalize_cell(text, type_)` returns `to_cell(from_cell(text, type_))` when `text`
  parses as `type_`, and `text` unchanged when it does not.
- `merge(..., types=None)` takes a `{column: type}` mapping and compares base, local, and
  sheet cells in normalized form. Only comparison changes: a push still sends the local
  file's stored text, and a fold still takes the sheet's stored text, as with row keys.
- A cell is pushed only when the two sides differ in normalized form. Today the test
  is on text (`merge.py:349`), which would push `TRUE` over `true` on every run. Two
  sides that agree in normalized form are in sync, and the base takes the local text.
- Key columns are never normalized by type [R10]. `from_cell` reads `007` as an `int`
  (`cells.py:26`), so a typed key column would make `007` and `7` one row. Keys are
  matched by `normalize_key`, on whitespace only, and that stays. `merge` normalizes
  the non-key cells only, and ignores a key column named in `types`.
- Parsing stays strict. `3.0` in an `int` column does not parse, so it is compared as text
  and reported by the schema check. No value is coerced.
- `plan_tab` passes the types the tab's schema declares. A tab with no schema behaves
  exactly as before.
- It is on for every tab with a schema, with no option to turn it off (decision 3).
- A whole-tab pull or push compares text, as today. It replaces one side with the
  other, so there is no edit to misread.

## Holding invalid sheet cells

Today a sheet value that fails the tab's schema is folded into the merged rows, the merged
check reports it, and nothing is written for the tab: one mistyped cell on a shared sheet
blocks every other push and fold until someone fixes it.

- A tab option `on_invalid`, `"refuse"` (the default, today's behavior) or `"hold"`.
- The option belongs to the orchestration, not to `merge` [R6]. `merge` checks no
  schema today: the refusal is in `_plan`, after the merge (`sync.py:479`). On `merge`,
  `"refuse"` would be a no-op under a misleading name, or a new raise. So (decision 7):
  `merge(..., schema=None)`. Given a schema, a sheet cell that would be folded but
  fails its column's schema (type, `allowed`, or `required`) is listed in
  `MergePlan.held` instead. With no schema, `merge` folds as today. `plan_tab` passes
  the tab's schema only when the tab's `on_invalid` is `"hold"`.
- The local file keeps its value and the base keeps its value, so the cell is reported
  again on every run until the sheet is corrected.
- `HeldCell` has the fields of `Cell` and a `reason`, the text `problems` gives
  (`'yes' is not a valid bool`). `RowFlag` holds a key and a flag only
  (`merge.py:86-91`) and is not changed.
- A new sheet row with any invalid cell is flagged `remote_invalid` and not folded
  (decision 5). Its invalid cells are listed in `held` like any other, which is where
  the row's reasons are kept. `remote_invalid` joins `ROW_FLAGS`.
- A sheet cell blanked in a `required` column is held too (decision 6). It is an edit,
  not a malformed value, and folding it would make the local file fail its own check on
  the next run.
- A held cell is whatever the merge would have folded: a plain sheet edit, a
  `sheet_owned` column, or a conflict that `prefer="sheet"` resolves. A conflict that
  nothing resolves stays a conflict.
- `needs_attention` counts held cells and rows, so the run exits 2, and the report prints
  them with the reason.
- The local side is never held. An invalid local value refuses the tab under both
  settings, since the local file is the caller's own to fix.
- `validate` and `check` problems refuse the tab under both settings. They run on the
  merged rows, which under `"hold"` do not contain the held values.
- `on_invalid` is sync-only, and joins `_SYNC_ONLY` in `config.py`. A pull tab refuses
  on a sheet problem too (`sync.py:779`), and keeps refusing: holding a cell means
  keeping the local value of that one cell, which needs rows matched by key, and a pull
  replaces the whole file and may have no key.

## Composite keys with a blank component

`index_rows` refuses a row with any blank key cell. For a one-column key that is right.
For a composite key it rules out tables where one component is legitimately absent on some
rows and the remaining components still identify the row.

- `blank_keys`, `"refuse"` (the default) or `"partial"`, on `index_rows`, `parse_tab`,
  `read_tab`, `merge`, and as a tab config field.
- `push_tab` calls `index_rows` directly (`sync.py:851`), so it passes the tab's
  setting too [R7], and `push_rows` takes the argument
  ([`f-push-rows-and-links.md`](f-push-rows-and-links.md)).
- Under `"partial"` a row is refused only when every key cell is blank. Duplicate keys
  are refused as before, blank components included in the comparison.
- With a one-column key the two settings are the same.
- `apply_plan` and `verify` find rows through `row_key` and the table's `row_numbers`, so
  they need the option only where they read the tab again; the `Table` records the setting
  it was read with.
- The tab field is for every mode, since a pull or a push with a key indexes rows too.

This section is built first. The refusal is stricter than an engine that only refused
duplicates, base included, so a caller moving over can stop on rows it has synced for a
long time [D10]. For a one-column key the refusal is right, and the guide says so
([`h-docs-and-release.md`](h-docs-and-release.md)).

## Carried columns [D9]

`merge` takes the carried columns from the keys of the local rows (`merge.py:214`).
With a local file that has a header and no rows there are none to infer, so a row
folded from the sheet lacks the carried columns in `new_local`. A file-backed run is
unaffected, since `write_records` fills the gap from the file's column list. A caller of
`merge` alone, or a store that writes `new_local` as given, sees rows of two shapes.

- `merge(..., carry=None)` names the carried columns. None infers them from the rows,
  as today.
- `plan_tab` passes the local columns outside the projection, so every row of
  `new_local` holds every local column.
- A column named in both `carry` and `columns` is refused.

## Plan predicates

- `MergePlan.sheet_writes` (pushes or appends), `MergePlan.local_writes` (folded cells or
  rows), and `MergePlan.has_writes` (either). `_in_sync` in `sync.py` uses them.
- Held cells are not writes. `_in_sync` is false for a plan that holds any, as it is
  for one with conflicts or row flags, so the report never prints `in sync` above a
  list of held cells.

## Tests

- Partial keys: a blank component on each side, every component blank, and two rows
  that differ only in a blank component.
- Normalized comparison: each of the four cell cases for each type, a cell that does
  not parse, a typed key column (`007` and `7` stay two rows), and the false conflict
  above, which fails on 0.11.0. The exhaustive merge test of plan 006 is run with and
  without `types`.
- Held cells: each way a fold arises, a new row with one invalid cell, a `required`
  blank, and the same inputs with no schema, which fold as today. A second merge from
  the result holds the same cells.
- `carry=` with a local side of no rows, and the refusal.
- The predicates, for a plan of each kind.
- Live: one sync of a partial-key tab.

## Config and docs in this step

- `blank_keys` and `on_invalid` in `_TAB_FIELDS`, `TabConfig`, and the guide's tab
  field table. `on_invalid` in `_SYNC_ONLY`.
- The report prints held cells and `remote_invalid` rows under their own headings.
- Changelog: the options, `normalize_cell`, `carry=`, and the predicates under Added;
  normalized comparison under Changed.

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-c-merge-additions`, cut from step b's branch, with
a draft PR onto it. Partial keys were built first, as the design says.

| Commit | Part |
|---|---|
| `db38041` | `blank_keys` on `index_rows`, `parse_tab`, `read_tab`, `merge`, the re-reads of `apply_plan`, `push_tab`, and as a tab field; `Table.blank_keys` |
| `42ff400` | `normalize_cell`, `merge(types=, schema=, carry=)`, `HeldCell`, `MergePlan.held`, `remote_invalid`, and the three predicates |
| `d472982` | The `on_invalid` tab field, `plan_tab` passing the types, the schema, and the carried columns, the report's headings, and `_in_sync` |
| `27c2e54` | The guide, the changelog, and the live case |

Decisions made during the work:

- **`_in_sync` did not test for conflicts or row flags.** The design says it is false
  for a plan with either, and on 0.11.0 it was not: a tab with nothing to write and a
  conflict printed `in sync: nothing to write` below the conflict. It now tests
  `needs_attention`, which covers conflicts, row flags, and held cells. The line is a
  change to the report's text, and is in the changelog under Changed.
- **A held cell reports no override.** A `sheet_owned` column or `prefer="sheet"` that
  would have discarded a local value discards nothing when the sheet value is held.
- **A new sheet row is checked in every schema column of the projection**, its key
  columns included. A row both sides hold is checked in its non-key cells, which are
  the ones a merge folds.
- **On a row-owning tab a new sheet row is flagged `remote_added`**, as before, and
  none of its cells is held: it would not have been folded.
- **With `carry=` given, every row of `new_local` holds each carried column**, the
  existing local rows too, blank where a row lacked it. With `carry=None` the rows are
  left as 0.11.0 left them. `carry` also refuses a column named twice.
- **`adopt` merges with the same `types`, `schema`, and `carry`** as a merge against a
  base.
- **`cell_problem` is public.** It is `_cell_problem` of `cells.py` under a public
  name, and gives `HeldCell.reason` the text `problems` gives.
- **`check_blank_keys` is public** for the same reason `column_type` is: the functions
  that take the setting refuse a bad one before any request.
- **The report words a held cell with what stays**:
  `a / 'amt': 'x' is not a valid int; the local value stays '1'`. A cell of a held row
  has no local value, and prints the reason only. `remote_invalid` rows are listed
  under their own heading and left out of the row flags.
- **`ON_INVALID` and `BLANK_KEYS`** are the two settings' constants, and are exported.

The exhaustive merge test now also runs over two spellings of one value in typed
columns, and over a schema that one of the three values fails, with every ownership
setting. Its invariants hold in the form cells are compared in, and a held value is
checked to reach neither the local side nor the base.

**Live suite.** One case: a sync of a tab whose two key columns each have a blank
component on one row, with a push and a new row. One run of the whole suite, by the
orchestrating session, after the case had passed by itself:

| | Writes | Reads |
|---|---|---|
| Before this step | 64 | 78 |
| The new case | 4 | 6 |
| After | 68 | 84 |

23 passed in 99 seconds. 6 reads were refused on the quota and sent again, which the
counts above leave out.
