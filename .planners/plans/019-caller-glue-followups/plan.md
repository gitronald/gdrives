---
id: 19
slug: caller-glue-followups
status: done
branch: feature/caller-glue-followups
created: 2026-09-29T01:19:50-07:00
concluded: 2026-09-29T09:50:50-07:00
pr: https://github.com/gitronald/gdrives/pull/63
---

# Remove the glue callers still write around gdrives.sheets

## Plan

### Goal

Moving a hand-written sheet sync onto `gdrives.sheets` 0.14 is mostly a deletion:
the merge, the guards, the read-backs, and the retries are the library's. What is
left in a caller afterwards is a thin layer of glue, and much of it is the same
from one caller to the next: a wrapper that turns one tab's report into a run's, a
copy of the retry message, a schema written in Python and restated as JSON, a loop
over tabs to check links. This plan moves the general part of that glue into the
library, so a caller declares its targets and keeps only what is its own.

Every example below is synthetic (a club's `Roster` spreadsheet with `Members` and
`Dues` tabs, a module named `clubtools`). Fixtures, docs, and tests follow the same
rule.

### Steps

Ordered by how much caller code each removes, with the cheapest seams first.

| # | Step | Scope |
|---|---|---|
| 1 | Report and run seams | `format_report` and `exit_code` for one tab's report, a `pending` property, a default retry printer, and an optional `spreadsheet_id` |
| 2 | Read a tab into memory | `pull_records`: a pull's checks with no store and no `apply` |
| 3 | Announce a credential fallback | The credential line is printed when a consent was skipped for lack of a terminal |
| 4 | Schema by reference | A tab's `schema` names `module:attribute`, found when a run starts |
| 5 | `strict_schema` for the local side only | `strict_schema: "local"` leaves the sheet's own columns alone |
| 6 | A `pattern` for a column | `ColumnSchema.pattern`, a regular expression a `str` cell must match in full |
| 7 | `sheets-links` command | Check and fix the links of URL cells over a spreadsheet's tabs |
| 8 | A wider link audit | Cell text, formula links, and link styling without a link; clearing the styling too |
| 9 | A spreadsheet from a local workbook | `sheets-create --from book.xlsx` |
| 10 | `upload --no-replace` | Refuse a name that is taken, instead of replacing it |
| 11 | Refuse a target that is not a native spreadsheet | A path that resolves to an `.xlsx` of the same name is named as such |
| 12 | Line endings of a text export | `export --newline lf` and `export_file(newline=...)` |
| 13 | Lower value, each may stop at a write-up | Target-level tab defaults, a stock whitespace transform, and column descriptions with a schema export |
| 14 | Guide | Migration notes and store recipes in `docs/sheets-sync.md` |

#### 1. Report and run seams

Four small additions, each replacing a helper that callers write alike.

- `format_report` takes a `TabReport` as well as a `SyncReport`, and `TabReport`
  gets `exit_code`, with the rule `SyncReport.exit_code` applies to a run of one
  tab. A caller of `push_rows` or `pull_tab` no longer wraps the report to print
  it or to decide its exit code.
- `TabReport.pending` and `SyncReport.pending`: whether `--apply` would write
  anything. It counts cell writes, a tab to create, columns to add or drop, and a
  replacement that differs, so a preview whose only change is a column is still
  pending. The sync commands print `Preview only; rerun with --apply to write.`
  after a preview that is pending, on stderr, so stdout stays the report.
- `print_retry` is public (the message the CLI prints now), and
  `retry_notices()` with no callback uses it.
- `run_target`'s `spreadsheet_id` may be None, and is then taken from
  `target.spreadsheet` when that is a URL or a bare ID. A Drive path is refused
  with a message naming `resolve_file_id`, since resolving one needs a Drive
  service and the drive cache. `Target.spreadsheet_id` gives the same value to
  the other calls.

#### 2. Read a tab into memory

A caller that wants a tab's rows in memory, checked, builds a pull tab with a
`MemoryStore`, runs `pull_tab(..., apply=True)`, checks the report, and reads the
store back. `apply=True` on a call that writes nothing outside the process reads
wrongly, and the four steps are the same everywhere.

```python
records = pull_records(
    service,
    spreadsheet_id,
    "Members",
    schema={"joined": ColumnSchema("date"), "paid": ColumnSchema("bool")},
    exclude=["phone"],
)
```

- Takes what a pull tab takes: `columns`, `key`, `blank_keys`, `schema`,
  `strict_schema`, `exclude`, `render`, `sheet_id`, the hooks, and `transform`.
- Returns `Records`. Raises `PullError` (a `ValueError`) holding the `TabReport`
  when the pull is refused or finds problems, with the rendered report as its
  message.
- It is `pull_tab` underneath, so the two never differ in what they refuse.
- The guide shows the way to typed rows and to a dataframe:
  `decode_rows(records.rows, types)` gives Python values, which any dataframe
  library takes with its own schema. The library takes on no dataframe
  dependency.

#### 3. Announce a credential fallback

`announce_credentials` prints when a consent or a refresh is coming. It says
nothing when OAuth is configured, no cached token serves, and there is no terminal
for a consent, so the run goes on as the service account or ADC. A write then
lands under an identity nobody chose, and the only way to see it coming is to read
`describe_credentials` by hand, as callers do.

- Print the credential line when `CredentialInfo.consent_skipped` is True, with
  the reason: `Credential: service account <email> (OAuth is configured, but no
  cached token serves these scopes and there is no terminal for a consent; run
  gdrives login)`.
- Quiet, as now, when OAuth is not configured at all, where a service account is
  the intended credential.
- Once per scope set inside `announcing_credentials()`, as the other lines are.

#### 4. Schema by reference

A caller that declares its columns in Python (one source for its own checks, its
docs, and the sync) has to restate them as JSON for each tab of each target, or
build its `Target`s in code and give up the config file and the CLI.

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "base": "sheets-base/roster",
    "tabs": {
      "Members": {
        "local": "data/members.csv",
        "key": ["id"],
        "schema": "clubtools.schema:MEMBERS",
        "strict_schema": true
      }
    }
  }
}
```

- `schema` is an object, as now, or a string naming `module:attribute`, the form
  `hooks` uses. The attribute is a mapping of column name to `ColumnSchema`, or
  a function that is given the tab's title and returns one.
- It is imported when a run starts and never when a config is read, as a hook
  is, and by the same code (`sheets/hooks.py`), so an import error reads the
  same way.
- The config checks that need the columns (a `key` column's type, `typed_writes`,
  `widths`, `exclude`, and the projection) cannot run at load for a referenced
  schema. They run when it is resolved, before any request, and report as config
  problems do. This is the design question of the step: `TabConfig` holds either
  `schema` or `schema_ref`, and resolution returns a tab with `schema` filled.
- `load_config` stays free of imports, so reading a config never runs a caller's
  code.

#### 5. `strict_schema` for the local side only

`strict_schema` makes an undeclared column of either side a problem. On a shared
sheet where collaborators keep columns of their own, outside the projection, that
refuses every run, and the caller's only choice is to turn it off.

- `strict_schema` takes `true` (both sides, as now) or `"local"`: every local
  column must be declared, and a sheet column outside the projection is left
  alone.
- `false` stays the default. A config written for 0.14 reads the same.

#### 6. A `pattern` for a column

- `ColumnSchema.pattern`: a regular expression that a non-blank cell's canonical
  string must match in full. For `str` columns only, and refused for any other
  type, as `strict` is for the types it does not serve.
- In a config it is a string, and one that does not compile is a config problem.
- A failure is a schema problem from `cell_problem`, so `on_invalid: "hold"`
  holds it.
- Example: `{"type": "str", "pattern": "https://example\\.com/members/[0-9]+"}`.

#### 7. `sheets-links` command

`url_link_problems` and `set_url_links` are in the library. `link_urls` formats
only the cells a run wrote, so the cells a person typed or pasted need a sweep,
and each caller writes the loop over tabs.

```bash
gdrives sheets-links <sheet-url>                       # Check every tab
gdrives sheets-links roster --tab Members --color "#1155cc" --apply
```

- The source is a spreadsheet or a config target. For a target, the tabs default
  to its tabs and the colour to their `link_urls`.
- Previews by default, on the read-only scope. `--apply` requests
  `spreadsheets` and prints the credential line first.
- Exit 0 when every URL cell follows the rule or was fixed, 2 when a preview
  found cells to fix, and 1 on an error.
- The library side is `sweep_url_links(service, spreadsheet_id, tabs=None, *,
  color, apply=False)`, which the command prints.

#### 8. A wider link audit

`linked_cells` reports where a link is and where it points. An audit that sorts
cells into kinds (a link to the cell's own text, a link to somewhere else, a link
on an empty cell, link styling with no link) needs more, so a caller keeps a grid
read of its own.

- `LinkedCell` gets `text` (the cell's displayed text) and `formula` (True when
  the link comes from a `HYPERLINK` formula). Both default, so a `LinkedCell`
  built as before is unchanged.
- `styled_cells`: the cells that are underlined or coloured as a link is and
  hold none.
- `clear_link_format(style=True)` also resets the underline and the text colour.
  By default it clears the link alone, as now.
- **Checked live first.** Which fields carry a formula's link, and what a reset
  of the text colour leaves behind, are read from the API before the code is
  written, on a scratch spreadsheet the owner names.

#### 9. A spreadsheet from a local workbook

`sheets-create` makes an empty spreadsheet. A caller that starts from a workbook
uploads it and converts it by hand.

```bash
gdrives sheets-create --from book.xlsx --folder "My Drive/clubs" --title "Roster"
```

- `files.create` with the spreadsheet MIME type and the workbook as the media
  body, which Drive converts. The upload is `upload.py`'s resumable one.
- `--from` takes `.xlsx` or `.csv`. `--title` defaults to the file's stem.
  Refused with `--tab`, since the workbook names its own tabs.
- `--dry-run` prints the operation on the read-only scope.
- Conversion is Drive's: the log records what a formula, a date, and a merged
  cell come out as, from one live run on a scratch folder.

#### 10. `upload --no-replace`

`upload` replaces a file of the same name in place. A caller that must never
overwrite checks for the name itself, in a second listing that can disagree with
the first.

- `--no-replace` (`replace=False` in `plan_upload`) refuses when `find_named`
  finds a file of that name, listing the IDs, and exits 1. Refused with
  `--file-id`, which names the file to replace.
- **The scope is a write-up only.** A caller may want the narrow `drive.file`
  scope for uploads. Under it a listing holds only the files the app created or
  opened, so neither the replace nor the refusal can see every file of a name.
  The options are written in the Log beside draft plan 004, which asks the same
  question of `mv`, and no scope option is added here.

#### 11. Refuse a target that is not a native spreadsheet

A Drive path can resolve to an uploaded `.xlsx` of the name a spreadsheet was
expected under, and the Sheets API then answers with an error that does not say
so.

- When a `sheets-*` command's source is a path, the resolved file's MIME type is
  checked: anything but a native spreadsheet is refused, naming the file, its
  type, and `sheets-create --from` as the way to convert it.
- A URL or an ID is not checked, since that would cost a Drive request the
  commands do not make now.

#### 12. Line endings of a text export

Drive exports a sheet's CSV with CRLF line endings, and a caller that commits the
file rewrites it after every export.

- `export_file(..., newline=None)`: `"lf"` or `"crlf"` rewrites the line endings
  of a text export (`.csv`, `.txt`, `.md`); None leaves the bytes as Drive sent
  them, as now. Refused for a binary format.
- `gdrives export <sheet-url> -o out.csv --newline lf`.
- A line break inside a quoted CSV cell is part of the value. The rewrite parses
  the CSV and changes the row endings only.

#### 13. Lower value

Each is small, and each stops at a write-up in the Log if its design looks
doubtful once the code is open.

- **Target-level tab defaults.** A target's `defaults` object gives `link_urls`,
  `strict_schema`, `newline`, `render`, and `blank_keys` to every tab that does
  not set its own, as a target's `hooks` already does for hooks.
- **A stock whitespace transform.** `gdrives.sheets.transforms:trim_cells`,
  named in a config's `hooks`: strips each cell and collapses runs of spaces and
  tabs inside a line, keeping line breaks, which is what `row_key` does to keys.
  The guide says what it costs: whitespace a collaborator typed stays on the
  sheet and is never pushed away.
- **Column descriptions and a schema export.** `ColumnSchema.description`, which
  no check reads, and `gdrives sheets-schema TARGET -o schema.csv`, one row per
  column with its type, its rules, and its description.

#### 14. Guide

`docs/sheets-sync.md` already has "Moving an existing sync over" and "Stores".
Both grow, and `tests/test_sheets_guide.py` runs what is added.

- **Moving an existing sync over**, new causes of a first preview's changes:
  a header's surrounding whitespace is stripped on read, an empty cell is written
  bare where another CSV writer quotes it, a typed column compares by value, and
  a blank key is refused where older code folded the row in.
- **A way to move over**, as steps: run the old merge's test cases against
  `merge`; read each committed file through its store and write it back,
  expecting the same bytes; preview every target with both engines; apply one
  small edit and revert it.
- **Look before tidying a shared sheet.** `reorder_rows` and `strict_schema` are
  previewed against the live sheet before either is adopted, since a sheet that
  people edit is rarely in the order, or of the columns, that the local side
  expects.
- **Store recipes**: a store that sorts its rows on write, and a store whose
  local side is computed and whose write lands some of the columns in another
  file.

### Left to the caller

Considered, and not moved into the library. The hooks and the `Store` protocol are
the interface for each.

- **A row order of the caller's own**, by ranks and enumerations. The store
  recipe of step 14 covers the local side, and `reorder_rows` the sheet.
- **Checks across rows**: a column that must hold one value within a group, or
  two rows that look like one. Each is a `check` or a `warn` hook. A hook named
  in a config takes no arguments, so a stock one would need a config of its own.
- **A local side that is computed.** It is a store, and step 14 shows one.
- **Dataframes.** Step 2 stops at typed rows.
- **One table split over several tabs**, with a check that every row landed in
  one of them: `ensure_tabs` and a `push_rows` per tab, in a loop of the
  caller's.
- **A caller's own file of targets.** A caller that keeps one builds `Target`s
  in code. Steps 1 and 4 make that shorter, and do not replace it.

### Rules

- Every change is an addition or an opt-in. A config or a call written for
  0.14.0 behaves the same and reports the same, except for step 3's credential
  line and step 1's preview hint, both on stderr.
- One branch, `feature/caller-glue-followups`, and one PR into `dev`. The steps
  are commits on it, in order.
- Line and branch coverage stay at 100%, with ruff and pyrefly clean. Unit tests
  use fakes and never the network.
- A live check needs the owner's word first, and a scratch spreadsheet or folder
  to run on. Steps 8 and 9 have one each.
- Each step updates the README, the guide where it touches sync, and the
  `[Unreleased]` changelog. No release is cut.
- This is a public repo. No caller is named or described, and every example,
  fixture, and message is synthetic.

### Out of scope

- A release, and any change to what `typed_writes` writes.
- Draft plan 004, beyond the note of step 10.
- Rebuilding a caller's commands as `gdrives` commands. The CLI grows where a
  command is general (`sheets-links`), and not to mirror one caller's.

## Log

### 2026-09-29: how the steps were run

Written at 2026-09-29T03:04:34-07:00. One branch and one draft PR,
[#63](https://github.com/gitronald/gdrives/pull/63). The steps ran in order, each
implemented by a subagent in the branch's worktree (step 4 on the larger model, for
its design question, the others on the smaller), then read, rerun, and pushed by the
orchestrating session before the next began. The subagents ran the unit tests only.
Every live call was made by the orchestrating session, with the owner's word for the
test spreadsheet and the test folder. What the APIs returned is in
[live-findings.md](live-findings.md).

At the branch's tip the full suite, live tests included, gives 3671 passed and 1
skipped (the revision test that needs `GDRIVES_TEST_FILE_ID`), at 100% line and
branch coverage, with ruff and pyrefly clean.

### 2026-09-29: what landed, step by step

| # | Commits | What landed, and where it departs from the plan |
|---|---|---|
| 1 | `4717471`, `ea99514` | `pending`, `format_report` for a `TabReport`, `print_retry`, `Target.spreadsheet_id`, and `run_target(None)`. `TabReport.exit_code` was there already, so a test now holds the two to one answer. `resolve.direct_file_id` is new, the one place that tells a URL, an ID, and a path apart. |
| 2 | `2f99ad7`, `44b6720` | `pull_records` and `PullError`. It takes `report=` and `listing=` beyond the plan's list: `report=` is how a caller reads what `warn` said. |
| 3 | `13e82d7`, `8d4b5e0`, `c5edbb5` | The fallback line, built by the public `credential_line`. `str(CredentialInfo)` is unchanged, so the reason follows the key path in a second pair of parentheses. |
| 4 | `4d100d2`, `f916bd5` | `TabConfig.schema_ref`, `resolve_tab`, and `resolve_target`. The load and the resolution call the same checks. A key column's type, `typed_writes`, and `widths` turned out not to read the schema at load, so nothing moved for them. `Target.base_file` is new: a referenced tab's base store is built when it is asked for. |
| 5 | `36da0e9`, `f879b6d`, `5cdaf0c` | `strict_schema: "local"`. `f879b6d` fails the guide test on its own: the count it needed is in `5cdaf0c`. |
| 6 | `a68e6a6`, `7a07a11`, `8cf815a` | `ColumnSchema.pattern`, checked last in `cell_problem`, and `PATTERN_TYPES`. |
| 7 | `ecd9efa`, `6c6243f` | `sheets-links`, and `sweep_url_links` in a new `sheets/links.py`. A tab with no named column in its first row is skipped, not an error. |
| 8 | `d59261f`, `157ede5`, `168763c` | `linked_cells(detail=True)` fills `text` and `formula`, so a 0.14 call reads what it read. `styled_cells`, and `clear_link_format(style=True)`. Built from the live findings, and pinned by one live test. |
| 9 | `9121f0d`, `d6182d8`, `62ee4e4` | `sheets-create --from`. `upload._send` became the public `send_upload`, beside `resumable_media`. The read-back checks the created file's type. |
| 10 | `4fb4547`, `2d21950` | `upload --no-replace`, which refuses on any file of the name, a Google-native one included. |
| 11 | `b192b4c`, `6173557` | The type check, at no request: the listing that found the file already carries its type. The message says to download the workbook and convert the local copy, since `--from` takes a local file. |
| 12 | `dc9d1b4`, `132cc84`, `f21cfe4` | `export --newline`. The CSV rewrite walks the bytes and never requotes. `NEWLINES` moved to `gdrives/local.py`. |
| 13 | `f00380d` to `36b37ee` | All three were built. `trim_cells` runs the key's normalization on each line of a cell, since on a whole cell it would fold the line breaks the plan says to keep. `sheets-schema` resolves the schemas alone (`resolve_schemas`), so a hook that does not import cannot stop an export. |
| 14 | `3fc50bc` | The guide's additions, each claim pinned by a test in `tests/test_sheets_guide.py`. |

### 2026-09-29: the review's own changes

- `8cf815a` put `PATTERN_TYPES` in its sorted place in `__all__`.
- `62ee4e4` wrote what a conversion keeps into the README and the changelog, from
  the live run.
- `f21cfe4` passes `newline` straight through `export`. The subagent had passed it
  only when set, so that test fakes written for two arguments kept working; four
  fakes now take it.
- `2179802` strips the colour from a usage error before a test reads it. CI renders
  Typer's usage errors in colour, which split `--title` and failed the test on all
  four Python versions, while the same test passed locally.

### 2026-09-29: step 10's scope, written up

Under the narrow `drive.file` scope a listing holds only the files the app created
or opened. A replace would then miss a file of the name that someone else made and
create a second one, and `--no-replace` would report the name as free exactly when
it should refuse. The options:

1. Keep the full `drive` scope for `upload`, the only one under which the listing
   is whole.
2. Offer a `drive.file` mode for a caller that only ever touches its own files, and
   refuse `--no-replace` there, or warn, since it cannot keep its promise.
3. Leave the narrow scope to the caller: a `drive.file` token of its own, and
   `--file-id` to replace, which needs no listing and gives up `--no-replace`.

Draft plan 004 asks the same of `mv` and `drive.metadata`, and its questions about
the token file's name apply here too. No scope option was added.

### 2026-09-29: left for the owner, at the close

- **Step 1.** A first sync's preview is `pending`, since an apply saves a base,
  though its report can end `in sync: nothing to write`. A run that would only save
  the base again is not counted, since the report's fields cannot show it.
- **Step 2.** `pull_records` raises `PullError` for an API error too, with the
  `HttpError` as its cause.
- **Step 3.** The two pairs of parentheses on a service account's fallback line, and
  a reason that also covers a cached token that is invalid or unreadable.
- **Step 4.** A referenced schema's `allowed` holds scalars, as JSON does, so a
  Python `date` there is refused. `tab.schema` read on an unresolved tab is empty,
  where `types` and `local_store` raise.
- **Step 5.** `strict_schema: "local"` on a push checks what `true` checks, and is
  accepted.
- **Step 8.** Clearing the link format of a `HYPERLINK` formula's cell removes its
  link and leaves the formula. It did so before this plan, and is now documented.
  An option to skip such cells was not built.
- **Step 9.** A service account cannot create from a workbook in a folder of My
  Drive. Two scratch spreadsheets from the live run are in the test folder, for the
  owner to remove.
- **Step 13.** `sheets-schema` does not escape a cell that starts with `=`, which a
  `pattern` may. `SCHEMA_COLUMNS` is not among the names `gdrives.sheets` exports.
- **Names made public beyond the plan's list:** `direct_file_id`,
  `credential_line`, `FALLBACK_REASON`, `PATTERN_TYPES`, `send_upload`,
  `resumable_media`, `resolve_spreadsheet_id`, `check_spreadsheet`,
  `resolve_schemas`, and `TARGET_DEFAULTS`.
- **The project's `.claude/CLAUDE.md`** is not tracked, so the branch does not carry
  its update: the package tree, the command list, and a paragraph for each step.

### 2026-09-29: the owner's questions, decided

Written at 2026-09-29T03:45:13-07:00. The owner read the list above and left the choices to the
orchestrating session, to be researched in the code. This entry takes the place of
that list. At the branch's tip the full suite, live tests included, gives 3732
passed and 1 skipped, at 100% coverage, with ruff and pyrefly clean.

| Question | Decision | Commit |
|---|---|---|
| Is a first sync's preview `pending`, and a run that only saves the base? | `pending` is exact: True when an apply of the same run would write the sheet, the local side, or the base. `TabReport.stale_base` and `stale_local` are set at plan time by the helpers `apply_tab` uses, and a test previews and applies eleven cases and holds the two to one answer. The report's text cannot change, so the hint carries it: `Preview only; rerun with --apply to save the base.` | `623d020` |
| Does `pull_records` wrap an API error? | No. `PullError` is for a refusal and for problems, as the plan words it. An `HttpError` or an `OSError` is raised as it was. | `00a1946` |
| Two pairs of parentheses on the fallback line | One sentence: `Credential: <credential>, since OAuth is configured, but ...`. The reason constant is private, and `credential_line` is the interface. | `96dc4bf` |
| `allowed` in a referenced schema | It takes `date` and `datetime` values, as a schema built in code does. | `65e047c` |
| `strict_schema: "local"` on a push | Accepted, as it was. A target's `defaults` gives the field to every tab, so a refusal on a push would make a default break a tab. | none |
| A `HYPERLINK` formula's cell under `clear_link_format` | `formulas=False` leaves such cells as they are. The default, and its requests, are unchanged. A second live test pins it. | `e7393ee` |
| The scope of `upload` | The full `drive` scope stays, option 1 of the write-up. Narrowing is draft plan 004's. | none |
| Formula escaping in `sheets-schema` | `--escape-formulas`, as `sheets-get` has it, off by default, and refused for a `.json` output. `SCHEMA_COLUMNS` is exported. | `9b11fb9` |
| The names made public | Kept, except the reason constant. Each stands beside a public name of its kind, or is used across modules. | none |

**One change goes past the plan's rule** that a 0.14 config reports the same. The
question about `allowed` showed a fault that was there before: a `datetime` cell
read from its serial number arrives as `2026-01-01 09:00:00.000`, so an allowed
`2026-01-01 09:00:00` never matched it. In a `date` or `datetime` column a cell and
the allowed values are now compared as the same moment. What matched still matches,
so the change only accepts more, and it is under `### Changed` in the changelog
(`373d1c5`).

### 2026-09-29: not closed in this session

The owner closes the plan in a later session. The PR stays a draft. What the close
has to do beyond the usual:

- **Copy the project's `.claude/CLAUDE.md`** from the worktree to the main checkout,
  before the worktree is removed. The file is not tracked, the worktree's copy holds
  the update for this plan, and the owner has said to copy it over.
- **Remove the two scratch spreadsheets** the live run of step 9 left in the test
  folder, `plan019-scratch-book` and `plan019-book`, or ask the owner to.
- **Still open, for the owner:** whether `tab.schema` read on an unresolved tab
  should raise, where it is empty now (`types` and `local_store` raise), and
  whether a tab with `clear_links` and nothing to push counts as `pending`, which
  it does not.

### 2026-09-29: the close

Written at 2026-09-29T09:51:12-07:00. The review gate ran on the branch at `d5656d2`,
and what it read, found, and rejected is in [review.md](review.md). It was posted to
the PR.

**Review follow-up.** Six findings, each fixed at the source with a test:

- `0510b24`: `sheets-links` finds a target's tab by its `sheet_id`, as a sync does.
- `1e233bb`: the CSV scan of `export --newline` starts after a byte-order mark.
- `5fc376c`: `clear_link_format(formulas=False)` sends no range past the tab's last
  row, and `_URL_FORMAT_FIELDS` is gone.
- `a476890`: the hint to convert a workbook is decided by `SOURCE_MIMES`.
- `b994b82`: the guide's fallback line lost a stray parenthesis, and a test holds it
  to `credential_line`.

Conscious no-ops, each with its measurement in the review: two per-cell costs that
stay under a second on a large tab, and two checks written twice on purpose.

At the branch's tip the full suite, live tests included, gives 3741 passed and 1
skipped, at 100% coverage, with ruff and pyrefly clean.

**Of the two questions left open:** a tab with `clear_links` and nothing to push is
not `pending`, and that is right, since an apply of it returns before it clears
anything. Whether `tab.schema` read on an unresolved tab should raise is still the
owner's.

## Retrospective

- **The plan's order held, and its one design question was the right one to name.**
  Step 4 was the only step that changed a type callers hold, and deciding at the
  start that a tab has either `schema` or `schema_ref` kept every later step free
  of it.
- **Reading the API before writing the code paid for itself in step 8.** The field
  that seemed to tell a formula's link from a format link does not, and the code
  would have been built on it.
- **Wording changed late is where the docs slipped.** The one docs finding was a line
  reworded after its step, in a file no test read that line of. A guide example that
  shows output needs a test as much as one that shows input.
- **A fake that refuses what the API refuses found a bug no test asked about.** The
  range past the grid was reproduced offline because the fake grid checks bounds.
  The unit tests of the split had all used a tab of 1000 rows.
- **A new command should be read against every field of the config it takes.**
  `sheets-links` took a target's tabs and missed `sheet_id`, which no step's scope
  named. A list of the tab fields a command honours would have shown the gap.
- **Next time:** leave the list of open questions shorter by deciding as the step is
  built, and size a plan of fourteen steps as an umbrella with subplans, since this
  one ends at the length where a plan should split.
