---
id: 20
slug: caller-seams-and-fakes
status: active
branch: feature/caller-seams-and-fakes
created: 2026-09-29T12:32:11-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/65
---

# Publish the preview hint and the test fakes, and tighten small schema, link, and export edges

## Plan

### Goal

A caller that wraps `gdrives.sheets` in a command line of its own, and tests that
wrapper, still reaches past the public surface in a few places: it copies the wording
of the preview hint, it cannot import the fakes the library's own tests run on, and it
reads messages and edge cases that were written with one mode in mind. This plan
publishes those seams and tightens the edges, each as an addition or a refusal of
something that never worked.

Every example below is synthetic (a club's `Roster` spreadsheet with `Members` and
`Dues` tabs). Fixtures, docs, and tests follow the same rule.

### Steps

| # | Step | Scope |
|---|---|---|
| 1 | `pending_hint` | The preview hint as a string, for a report or a sweep |
| 2 | `gdrives.testing` | `FakeSheetsService`, `FakeSheetGrid`, and `http_error` in the wheel |
| 3 | A clear range that starts past the grid, live | Read what the API answers, and pin it in a live test |
| 4 | The undeclared-column message | Name the `strict_schema` mode and the remedy |
| 5 | An empty `pattern` | A config problem, and a `ValueError` in code |
| 6 | `gdrives.__version__` | The installed version as an attribute |
| 7 | `allowed` built once per column | The cell strings of `allowed`, held by the schema |
| 8 | A set for `rows` | The row filters of the link functions |
| 9 | Line breaks inside quoted cells | `export --newline lf --newline-cells`, an opt-in |
| 10 | Docs | Changelog, README, the guide, and `CLAUDE.md` |

One branch and one PR for the whole plan.

#### 1. `pending_hint`

`commands._hint_pending` prints the hint and is private, so a caller that prints
`format_report` itself has to copy the sentence. Add

```python
def pending_hint(report: SyncReport | TabReport | LinkSweep) -> str | None
```

to `gdrives.sheets`: the line the commands print to stderr after a preview that
`--apply` would change (`Preview only; rerun with --apply to write.`, or `... to save
the base.` when `base_only`), and None when nothing is pending. It reads `pending`
and, where the report has one, `base_only`. The commands print what it returns, so
the wording lives in one place. It sits in `sheets/links.py`'s and `sheets/sync.py`'s
common importer; if neither can import the other's report type without a cycle, it
takes any object with a `pending` attribute (a `Protocol`).

#### 2. `gdrives.testing`

`FakeSheetsService` (preset responses) and `FakeSheetGrid` (a stateful grid) are in
`tests/helpers.py`, outside the wheel. A caller can test only what stops before a
request. Move them, with what they need (`http_error`, `LINK_BLUE`, and
`patch_sheets_service`), to a new module `gdrives/testing.py`.

- It imports nothing from `pytest`: `patch_sheets_service` takes any object with
  `setattr(target, value)`, which `pytest.MonkeyPatch` is.
- The library's tests import the fakes from `gdrives.testing`; `tests/helpers.py`
  keeps the helpers that are the test suite's own (Drive and Docs fakes, file
  builders).
- The module is inside the coverage source, and the floor is 100. Every branch of
  the fakes that no test runs is either tested or removed; nothing is excluded from
  the measurement by configuration.
- The module's docstring says what the fakes model and that they follow the
  library's needs: a caller's test passes against the fake when the library's call
  is one the fake models, and the API remains the authority.
- The Docs and Drive fakes stay in the tests. They are not asked for, and moving
  them later is an addition.

#### 3. A clear range that starts past the grid, live

Plan 019 left out the open-ended range of `clear_link_format(formulas=False)` when
it would start at or past the grid's last row, reasoning from the fake, which
refuses such a range with a 400. Send one to the API: on a temporary tab, a
`repeatCell` under the link mask whose `startRowIndex` is the grid's `rowCount`, with
no end, and one whose start is past it. Record the status and the message in a
sidecar `live-findings.md`, and pin the answer in a live test.

- If the API refuses it, as the fake does, the fix stands and the fake's message is
  brought in line with the API's.
- If the API accepts it, the fix is still correct (nothing is sent that need not
  be), and the fake is changed to accept what the API accepts.

A second live test runs `clear_link_format(formulas=False)` on a tab whose last row
holds a `HYPERLINK` formula, the case the fix was for.

#### 4. The undeclared-column message

`column 'note' has no schema entry, and the tab is strict_schema` reads the same
under `strict_schema: true` and `"local"`, and names no way out. It becomes, by mode
and stage:

```text
Dues (sheet): column 'note' has no schema entry, and strict_schema is true: declare it in the tab's schema, or set strict_schema to "local" to leave the sheet's own columns alone
Dues (local): column 'note' has no schema entry, and strict_schema is "local": declare it in the tab's schema, or take it out of the local file
```

The remedy for the local stage is the same under both modes; `"local"` is offered
only at the sheet stage, where it is the one that helps. The guide's example and the
guide's test follow.

#### 5. An empty `pattern`

`pattern: ""` compiles, matches no non-blank cell, and reads as no pattern in a
schema export. Refuse it as a config problem (`'pattern' must not be empty`) in
`_column_problems`, so an inline schema and a referenced one refuse it in the same
words, and as a `ValueError` from `ColumnSchema`.

#### 6. `gdrives.__version__`

`gdrives.__version__` from `importlib.metadata.version("gdrives")`, the source
`gdrives --version` reads, which then prints the attribute. A source tree that is
not installed has no metadata, and the attribute is then `"0+unknown"`.

#### 7. `allowed` built once per column

`cell_problem` builds the list of allowed cell strings for every cell, and in a
`date` or `datetime` column a set of their normal forms as well. `ColumnSchema`
holds both after the first use (a `cached_property`, which a frozen dataclass
permits and which is no part of its equality or its `repr`). The message and the
order of the checks are unchanged. A schema whose `allowed` is a list changed after
the first check keeps the first answer; the docstring says so.

#### 8. A set for `rows`

`_skipped_rows`, `_link_clears`, and `strip_links` test `cell.row in rows` against a
sequence, once per cell. Each builds a set of `rows` once. `_row_spans` keeps the
sequence, since it reads the order.

#### 9. Line breaks inside quoted cells

`export --newline` keeps a line break inside a quoted CSV cell, by design: it is
part of the cell's value. A caller whose files held one ending throughout wants the
cells rewritten too. `--newline-cells` is the opt-in: with `--newline`, on a `.csv`
export, every line ending of the file becomes the one named, inside a quoted cell or
not. It is refused without `--newline` and with any other extension, before any
request. `set_line_endings` and `export_file` take `cells=False`. The default is
unchanged.

#### 10. Docs

The changelog's `[Unreleased]`, the README's command list and module tree, the
guide (`docs/sheets-sync.md`: the hint, the message, a section on testing a caller
with `gdrives.testing`, which the guide's test runs), and `.claude/CLAUDE.md`.

### Steps added by a second report

Added on 2026-09-29, after steps 1 to 10 were done, from a second reading of the
same kind of caller. They go on the same branch and the same PR. Each is an
addition or an opt-in; no default changes.

| # | Step | Scope |
|---|---|---|
| 11 | A schema reference that names an entry | `"clubtools.schema:SCHEMAS[members]"` |
| 12 | `check_spreadsheet` with the caller's remedy | `hint=`, and `NotSpreadsheetError` carrying the name and the type |
| 13 | A path walk that returns the entry | `walk_entry`, public |
| 14 | A hint for a pattern | `ColumnSchema.pattern_hint` and the `pattern_hint` column field |
| 15 | `trim_cell` | The one-cell function `trim_cells` applies |
| 16 | `newline` for a listing | `format_csv(rows, newline=None)` and `ls --save-as ... --newline` |
| 17 | Two options for the link audit | `rows=` on `linked_cells`, `own_colors=` on `styled_cells` |
| 18 | A seam for terminal detection | `gdrives.testing.terminal` |
| 19 | Announcing a refresh | `announcing_credentials(refresh=False)`, and a refused refresh is said |
| 20 | A note for plan 004 | The two-service option for a narrow upload scope |
| 21 | Docs | As step 10, for the steps above |

#### 11. A schema reference that names an entry

A `schema` reference names `module:attribute`, a mapping or a function given the
tab's title. A caller that keeps its schemas in one registry can use it only when
every tab's title is a key of the registry: tabs titled `Members 2026` and
`Members 2027` cannot share `members`. The reference takes a key in brackets:

```json
"schema": "clubtools.schema:SCHEMAS[members]"
```

- With a key, a mapping attribute is a registry, a mapping of key to schema, and the
  schema is its entry of that key. A function attribute is given the key in place of
  the title.
- The key is the text between the brackets as written, with no quoting: one or more
  characters, none of them a bracket. `load_config` checks the form, and still
  imports nothing.
- A registry with no such entry, and an entry that is not a mapping, are problems of
  the tab, listed with the rest when the run resolves its references.
- Without a key a reference means what it means today.

#### 12. `check_spreadsheet` with the caller's remedy

The refusal of an `.xlsx` or a CSV file ends by naming `gdrives sheets-create
--from`. A caller with a conversion command of its own cannot use the check, and
keeps a comparison of MIME types instead.

- `check_spreadsheet(file, hint=None)`: `hint` replaces the sentence after the
  semicolon, for the types the default sentence is given for.
- The refusal is a `NotSpreadsheetError`, a `ValueError`, with `name`, `mime_type`,
  and `convertible` (whether the type is one `sheets-create --from` converts), so a
  caller can write a message of its own. Its text without a `hint` is today's.
- `resolve_spreadsheet_id` and `resolve_and_report` pass `hint` on.

#### 13. A path walk that returns the entry

`walk_segments` returns the ID. The listing entry, with its `mimeType`, comes only
from the private `_walk`, so a caller that walks from a folder ID makes a second
`files.get` to learn the type. `walk_entry(service, folder_id, segments, *,
allow_files=False, corpora="allDrives")` returns the ID and the entry (None for no
segments), and `walk_segments` is its first result. With `check_spreadsheet` it is
`resolve_spreadsheet_id` from any folder.

#### 14. A hint for a pattern

A failure reads `'x' does not match the pattern 'https://example\\.com/members/[0-9]+'`,
to a person who edits a sheet and does not read regular expressions.
`ColumnSchema.pattern_hint`, and a `pattern_hint` field of a schema column, says
what the cell should be, as a noun phrase: with `"a member page link"` the failure
reads `'x' is not a member page link`. It is refused without `pattern`, and when it
is empty or not a string. It is the last field of `ColumnSchema`, and the last
column of a schema export.

#### 15. `trim_cell`

`trim_cells` takes rows, and the function it applies to a cell is private. A caller
that cleans a header or a cell read with `pull_values` wraps each in a row.
`trim_cell(text)` is that function, exported, and `trim_cells` calls it.

#### 16. `newline` for a listing

`listing.format_csv` ends its rows with CRLF, the `csv` module's default, so a
listing that is committed is rewritten after every run. `format_csv(rows,
newline=None)` takes `lf` or `crlf`, and None is today's output. `ls --save-as`
takes `--newline`, which applies to each file saved (a `.md` listing is written
with LF today) and is refused without `--save-as`. A listing's cells hold no line
break, since control characters are escaped, so the row endings are all there is.

#### 17. Two options for the link audit

- `linked_cells` takes `rows`, as `styled_cells` and `clear_link_format` do: it
  filters the result of the same grid read.
- `styled_cells(own_colors=True)` counts `color` for a cell that sets a text colour
  of its own, whatever the colour, as well as for one shown in one of `colors`.
  Such a cell's colour is `resettable`.

#### 18. A seam for terminal detection

Whether a consent can run is `gdrives.auth._is_interactive`, which a caller's test
of what it prints on a fallback patches by its private name.
`gdrives.testing.terminal(present)` is a context manager that makes the library see
a terminal, or none, inside its block.

#### 19. Announcing a refresh

Inside `announcing_credentials()` the credential line is printed whenever a token
refresh is coming, and an access token lasts about an hour, so a caller that runs
many commands prints it on most runs after an idle hour.

- `announcing_credentials(refresh=False)` leaves a coming refresh unannounced. A
  consent, a fallback for lack of a terminal, and an `always` line are printed as
  before.
- A refresh that Google refuses is said on stderr inside any announcing block,
  naming the token file, since what follows it (a consent, or another credential)
  is not what the run set out with. Today nothing says so.
- The default stays `True`, and the commands are unchanged. Whether the commands
  should stop announcing a refresh that succeeds is the owner's decision, left
  open here.

#### 20. A note for plan 004

Plan 019's write-up holds that under `drive.file` a listing is partial, so
`--no-replace` cannot keep its promise. Two services avoid that: the listing runs
on the read-only token and the write on `drive.file`. The option, and what it does
not solve, is appended to plan 004's Log. No scope option is added here.

### Out of scope

- A narrower `drive.file` scope for `upload`: plan 004, still a draft.
- The Docs and Drive fakes.
- `USER_ENTERED` parsing in `FakeSheetGrid`.

### Checks

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyrefly check`, and
`uv run pytest` (the live suite included, run once at the end by the orchestrating
session).

## Log

### 2026-09-29

All ten steps are on `feature/caller-seams-and-fakes`, one PR. The full suite,
live tests included, passed at the end: 3839 passed, 1 skipped (the revisions
test that needs `GDRIVES_TEST_FILE_ID`), coverage 100%.

Where the work differs from the plan:

- **Step 1.** `pending_hint` takes a `TabLinks` as well as the three types the plan
  names. `sheets/sync.py` imports the two link types from `sheets/links.py`, which
  imports nothing from it, so no `Protocol` was needed.
- **Step 2.** Four lines of the fakes had no test. The `effectiveValue` of a grid
  read, which no test and no library call asked for, got a test and stayed, since
  it answers as the API does. `patch_sheets_service` takes either fake.
- **Step 3.** The API refuses the range, so plan 019's fix stands. It also takes
  two ranges the fake refused, one that starts inside the grid and ends past it and
  one that holds no cell, so the fake changed in both directions. The refusal names
  the request's place in its batch, which the fake now does. The findings are in
  `live-findings.md`. The live test of the last-row case was run once with the
  check taken out of `_row_spans`, failed with the API's 400, and the check was
  put back unchanged.
- **Step 4.** The mode is spelled as the config's own messages spell it, `true` and
  `'local'`. The remedy at the local stage is the schema entry alone: taking a
  column out of a local file is not advice the library should give. `'local'` is
  offered for a sheet column the run does not read, which for a pull under `true`
  is a header column outside `columns`, and never for a column a pull reads, where
  it would change nothing.
- **Step 7.** `ColumnSchema.allowed_cells` is public, since a caller that renders a
  schema wants the same strings. Measured on 50,000 cells: 50 allowed `str`
  values, 0.16 s before and 0.01 s after; 28 allowed `datetime` values, 1.41 s
  and 0.07 s.
- **Step 8.** No new test: the row filters answer as they did, and the tests of
  `clear_link_format` and `strip_links` cover them.
- **Step 9.** The library parameter is `newline_cells` on `export_file` and `run`,
  and `cells` on `set_line_endings` and `check_newline`.
