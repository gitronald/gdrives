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
