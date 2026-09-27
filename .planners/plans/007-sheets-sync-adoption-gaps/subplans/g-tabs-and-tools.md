---
status: draft
branch:
---

# 007g — Name a tab by sheetId, list tabs once per run, and add the small tools

Part of [007](../plan.md). Step 7 of the umbrella's implementation order. Four
additions that share no design, each small. The tab listing comes first, since naming a
tab by `sheetId` needs it.

Notes applied: M7, M8, M12, D8.

## One tab listing per run

Plan 006's close review measured it and left it for a follow-up: `run_target` lists the
spreadsheet's tabs once per tab, so a preview of a 5-tab target makes 5
`spreadsheets.get` and 5 `values.get` requests where 1 and 5 would do, and creating a
missing tab lists the tabs three times in a sync.

- `TabListing` holds each tab's title, `sheetId`, and grid size, from one
  `spreadsheets.get` with a `fields` mask over `sheets.properties`.
- `plan_tab`, `pull_tab`, `push_tab`, and `push_rows` take `listing=None` and read
  their own when it is None, so a caller of one function sees no change.
- `apply_plan` and `_grow` keep their own `tab_grid` read. They need the grid's size
  as it is just before the write, and a listing from the start of the run may be stale.
- `run_target` reads the listing once and passes it to every tab.
- A step that creates a tab returns the listing with the new tab in it. No function
  uses a listing from before a tab was created.
- Reading every tab's values in one `values.batchGet` is the larger saving and changes
  how the per-tab functions read. It stays out.

The request budget, as tests over `FakeSheetGrid.calls`:

| Run | Requests |
|---|---|
| Preview of N sync tabs, all present | 1 listing and N value reads |
| The same, with typed date columns in every tab | 1 and 2N |
| Preview of N push tabs | 1 and N |
| Preview of N pull tabs | 1 and N |

The apply counts are recorded in the step's Log before and after, and pinned where they
drop.

## A tab named by `sheetId` [M7]

A config's tabs are keyed by title, so a tab renamed on the sheet stops a run. For a
form-responses sheet the title is incidental and the tab's identity is what is stable.

- A tab field `sheet_id`, a whole number, for every mode.
- The config stays keyed by title. The config's title names the tab in reports, picks
  the base file's name, and is what `--tab` selects.
- With `sheet_id`, the tab is found by it, and the title it has on the sheet is used in
  every range. When the two titles differ the report notes it
  (`renamed on the sheet: 'Responses' is now 'Form responses 1'`). The config is not
  rewritten.
- A `sheet_id` the spreadsheet lacks is an error for the tab. The run never falls back
  to the title, since another tab may have taken it, and never creates the tab.
- Two tabs of one target with the same `sheet_id` are a config error.
- Naming a tab by position is the weaker alternative, since a tab added in front
  changes it. It is not offered.
- `sheets-pull --all-tabs` names no tabs and is unchanged.

## Column widths [M12]

The config sets widths, and they have to come from somewhere. A caller tunes them by
hand on the sheet and copies the numbers out.

- `get_column_widths(service, spreadsheet_id, tab) -> dict[str, int]` in
  `structure.py`: each named header column's width in pixels, in header order, from one
  `spreadsheets.get` with a `fields` mask over `data.columnMetadata.pixelSize`. A
  column with a blank header cell is left out.
- `gdrives sheets-widths SHEET [--tab TITLE]` prints the result as a JSON object ready
  to paste under a tab's `widths`. It reads with the read-only scope, and takes the
  first tab by default, as `sheets-get` and `sheets-set` do.

## `--all-tabs` options [M8]

`_dump_tab` calls `write_records` without `bom` (`sync.py:1049`), and the file name is
`safe_filename` of the title (`sync.py:1015`). A config with one `pull` tab per file
covers both today, at the cost of listing every tab.

- `pull_all_tabs(..., bom=False, name=None)`. `name` maps a title to a file stem and
  defaults to `safe_filename`. The collision check runs on the mapped names.
- `slug(title)` in `gdrives.local`: lower case, each run of characters other than
  letters and digits replaced with one hyphen, and hyphens trimmed from the ends. A
  title that leaves nothing is an error for that tab.
- `sheets-pull --all-tabs` gains `--bom`, refused with `--format json`, and `--slug`.
  Both are refused without `--all-tabs`, like `--skip`.

## A notice when a call is retried [D8]

`with_retry` waits without reporting (`retry.py:28-60`). A run that backs off four times
looks hung for 15 seconds or more.

- `with_retry(..., on_retry=None)`: a callback given the status, the delay, and the
  attempt, called before each wait.
- The value wrappers call `with_retry` themselves, so a caller cannot pass the
  argument through them. `retry_notices(callback)` is a context manager that sets the
  callback for every `with_retry` inside it that was given none, through a context
  variable, so it does not leak across threads or outlive the block.
- The `sheets-*` commands run inside one and print a line to stderr:
  `Sheets API returned 429; retrying in 4s (attempt 3 of 5)`.
- The library prints nothing unless asked.

## Tests

- The budget table, and a listing refreshed after a tab is created.
- `sheet_id`: a renamed tab through a sync, a pull, and a push; an id the spreadsheet
  lacks; a repeated id in the config; the base file named by the config's title.
- `get_column_widths` with a blank header gap, and the command's output parsed back as
  JSON.
- `slug` for mixed case, punctuation, non-ASCII letters, and an empty result;
  `--all-tabs` with `--bom` and `--slug`, a slug collision, and each refusal.
- `on_retry` called once per wait and not after the last attempt; `retry_notices`
  nested, and unset after the block.
- Live: one test that renames the temporary tab and syncs it by `sheet_id`.

## Config and docs in this step

- `sheet_id` in `_TAB_FIELDS`, `TabConfig`, and the guide's tab field table.
- `sheets-widths`, `--bom`, and `--slug` in the guide's command section, the README's
  command list, and the project `CLAUDE.md`.
- Changelog: all of the above under Added; the request counts under Changed.
