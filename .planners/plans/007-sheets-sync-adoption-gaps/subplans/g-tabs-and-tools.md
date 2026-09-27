---
status: active
branch: feature/sheets-sync-adoption-g-tabs-and-tools
---

# 007g — Name a tab by sheetId, list tabs once per run, and add the small tools

Part of [007](../plan.md). Step 7 of the umbrella's implementation order. Four
additions that share no design, each small. The tab listing comes first, since naming a
tab by `sheetId` needs it.

Notes applied: M7, M8, M12, D8.

**Before starting:** plan 008 edits `gdrives/sheets/commands.py` and `gdrives/cli.py`
too. If it has merged into `dev`, merge `dev` into the umbrella branch first. See the
umbrella's [Log](../plan.md#log).

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
  grid read of row 1 with a `fields` mask over `data.columnMetadata.pixelSize`, through
  `pull_grid` ([`f-push-rows-and-links.md`](f-push-rows-and-links.md#grid-reads-valuespy-d14-p8)).
  A column with a blank header cell is left out.
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

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-g-tabs-and-tools`, cut from step f's branch, with
a draft PR onto it.

**Plan 008 had not merged into `dev`** when the step started: its PR was open. So the
step went ahead on the umbrella's stack as it was, and whichever plan merges second
resolves `commands.py` and `cli.py`. This step's edits there are `run_widths`, the
`_noticed` decorator on every `run_*`, the two options of `run_pull`, and the
`sheets-widths` command with `--bom` and `--slug` on `sheets-pull`.

| Commit | Part |
|---|---|
| `ae279c0` | `TabListing`, `tab_listing`, `listing=` on the orchestration, and the `sheet_id` tab field |
| `cd18d49` | `get_column_widths` and `sheets-widths`, `bom=` and `name=` on `pull_all_tabs` with `--bom` and `--slug`, `slug`, and `retry_notices` |
| `bd375a5` | The guide, the README, the changelog, and the live case |

Decisions made during the work:

- **`run_target` reads the listing lazily, inside each tab's `try`.** A listing that
  fails is reported for the tab that met it, and the next tab reads it again, so a run
  still reports per tab. It reads the listing again after a tab that was created.
- **`_restructure` reads a fresh listing only when it created the tab.** A run that
  only adds or drops columns plans again with the listing it had.
- **`ensure_tabs` takes `existing=`**, the titles a caller has just read, which is what
  takes the third listing out of a sync that creates its tab.
- **`push_rows` takes `sheet_id=` beside `title`.** Its checks run before any request,
  and finding a tab by its id needs the listing, so the id is resolved after them.
  `title` is then what the report calls the tab.
- **`TabPlan.title` is the tab's title on the sheet**, which the structure steps, the
  reads, and the widths use. `TabPlan.tab` stays the config's tab, so the base file
  keeps the config's title.
- **`get_column_widths` makes one read, not two.** The grid read of row 1 asks for
  the header cells (`effectiveValue`) with the widths, so no values read is needed
  for the header.
- **A retry is told to the callback as a `RetryNotice`**: the status, the delay, the
  attempt that follows, and the most attempts, which the printed line needs for
  `attempt 3 of 5`. The design gave the callback three values.
- **A title with no slug is an error for its tab**, reported in the run's report, and
  the other tabs are written. The collision check covers the titles that mapped.
- **`sheet_id` takes a whole number from 0**, since the first tab of a spreadsheet has
  the id 0.

**Seen and left.** The conditional format rules (`rules.py`) do not go through
`with_retry`, so `sheets-rules`, `sheets-add-rule`, and `sheets-delete-rule` run inside
`retry_notices` and have no retry to announce. It is as 0.11.0 has it.

**The request counts that dropped**, pinned in `TestRequestBudget`:

| Run | Listings before | After |
|---|---|---|
| A preview of N tabs of one mode | N | 1 |
| A sync that creates its tab | 3, with 2 reads of the grid size | 2, with the same 2 |
| A run of three push tabs that creates the second | 3, with 1 read of the grid size | 2, with the same 1 |

**Live suite.** One case: the temporary tab is renamed and synced by its `sheet_id`.
The tests after it reach the tab under its new title. One run of the whole suite, by
the orchestrating session, after the case had passed by itself:

| | Writes | Reads |
|---|---|---|
| Before this step | 74 | 93 |
| The new case | 5 | 6 |
| Reads the single listing took out of two sync cases | | -2 |
| After | 79 | 97 |

25 passed in 76 seconds. 3 reads were refused on the quota and sent again, which the
counts above leave out. The live suite runs one tab at a time, so the listing saves
it little: the saving is one read per tab after the first of a run.

### 2026-09-27 — plan 008 merged in

Plan [008](../../008-oauth-token-and-consent-safety/plan.md) merged into `dev` and
shipped as 0.12.0 after this step was pushed, so this plan resolved the overlap. `dev`
was merged into the umbrella branch and carried up the stack, and reached this branch
as `acc7289`. The umbrella's Log has the whole merge.

- **Nothing conflicted here.** `gdrives/cli.py`, `gdrives/sheets/commands.py`, and
  `tests/test_sheets_commands.py` merged by themselves: 008 took
  `_announce_credentials` out of `commands.py` and changed the two calls to it, and
  this step's edits are elsewhere in the file.
- **`sheets-widths` announces a wait with no edit.** 008 put the announcement in
  `_cli_errors`, which every command enters, this step's one included.
- **No expectation of this step's tests changed.** They compare the whole of stderr,
  and 008 prints the credential line on a preview only when the authentication is
  about to wait on a consent or a token refresh. The tests run on a service that is
  patched in, so neither is pending and stderr is what it was.
- **Two tests were added** to 008's `TestAWaitIsAnnounced`, for what neither plan had
  tested alone: `sheets-widths` with a token that is refreshed first, and the order
  of stderr when such a run also retries a call, which is the spreadsheet ID, the
  credential line, then the retry notice.

Ruff and pyrefly are clean, and 2189 unit tests pass at 100% coverage. The live suite
was not run for the merge: 008 has no live tests and changed no request. Step h runs
it.
