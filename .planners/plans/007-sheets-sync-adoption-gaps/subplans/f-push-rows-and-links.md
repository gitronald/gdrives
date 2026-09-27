---
status: done
branch: feature/sheets-sync-adoption-f-push-rows-and-links
---

# 007f — Add push_rows, written cells, and link formatting

Part of [007](../plan.md). Step 6 of the umbrella's implementation order. `push_rows`
takes the hooks of [`d-checks-and-hooks.md`](d-checks-and-hooks.md), and `push_tab`
reads through the store of [`e-stores.md`](e-stores.md).

Notes applied: R3, R7, R8, D11, D12, D13, D14, and the probe findings P1 to P8 of
[note 004](../implementation-notes/004-notes-review-and-link-probe.md).

## `push_rows`

```python
def push_rows(
    service,
    spreadsheet_id,
    title,
    columns,
    rows,
    *,
    key=(),
    blank_keys="refuse",
    input_option=RAW,
    apply=False,
    schema=None,
    validate=None,
    check=None,
    warn=None,
    widths=None,
    clear_links=False,
    label="rows",
    report=None,
) -> TabReport: ...
```

The body of `push_tab` from the point where the local file has been read: the preview of
what the tab loses, the re-read guard, the grid growth, the single write over the old
extent, and the read-back. The grid is grown first (`sync.py:885`) and then written
(`sync.py:890-896`); the draft had the two the other way round [R3]. `push_tab` becomes:
read the store, call `push_rows`.

The draft's signature lacked four arguments [R8]:

- `report`, which `push_tab` takes so that `run_target` keeps a partial report on an
  error (`sync.py:818`, `sync.py:1102`);
- `check` and `warn`, which every orchestration function takes;
- `blank_keys`, since the push indexes rows by key (`sync.py:851`) [R7]; and
- `label`, for the messages that name the local file today (`sync.py:843`,
  `sync.py:851`). `push_tab` passes its store's label.

The refusal of an empty row list (`sync.py:842`) moves into `push_rows` too.

## The cells a run wrote [D11]

A caller's formatting pass runs after an apply that wrote to the sheet, over what it
wrote. `TabReport.wrote_sheet` gates it, and `ApplyResult` gives `pushed_rows` and
`appended_rows`, without the columns, so a pass scoped to the written cells has to read
the whole tab again.

- `ApplyResult.pushed_cells`: each pushed cell as `(row, column)`, the row as it is
  after the apply.
- `ApplyResult.appended_columns`: the columns written in each new row, which with
  `appended_rows` gives their cells.
- A whole-tab push writes rows 2 to `after_rows + 1` of the columns pushed. The report
  has both, and the guide says how to read them.

## Link formatting (`structure.py`)

Sheets formats text that looks like a URL or a domain as a link when it is written, under
`RAW` input too. For a tab meant to hold plain text that is a change nobody asked for.

Note D12 says the opposite, that a value pushed with `RAW` lands as bare text. The live
probe settled it, and found more than either text had. What it found:

| # | Finding |
|---|---|
| P1 | A cell whose whole value is a URL or a bare domain gains `userEnteredFormat.textFormat.link` when the value is written: under `RAW` and `USER_ENTERED`, through `values.update`, `values.batchUpdate`, and `updateCells`. A bare `example.com` gets the target `http://example.com`. A URL inside a sentence and an email address are not linked |
| P2 | A `repeatCell` with `fields: userEnteredFormat.textFormat.link` and an empty cell removes that link and leaves the rest of the text format |
| P3 | Writing the same value again puts the link back |
| P4 | `updateCells` with `userEnteredFormat.textFormat.link` in its `fields` mask beside `userEnteredValue`, and no link given, writes the value with no link, in one request, other formats kept |
| P5 | A link on part of a cell's text is in `textFormatRuns`. It survives P2's clear, and neither `hyperlink` nor `effectiveFormat` reports it. A run given over the whole text is not kept as a run: `hyperlink` and `effectiveFormat` report it, `userEnteredFormat` and `textFormatRuns` do not, and P2's clear removes it |
| P6 | A `repeatCell` can clear `textFormatRuns` in the same request as a cell format or a cell link. The format is kept, which D13 had reported lost |
| P7 | A `link` set through `userEnteredFormat` on a plain string did not take. A run over the whole text did |
| P8 | A grid read of a 1000 by 26 tab formatted throughout, with no `fields` mask, returned 18.6 MB and 26,000 cell objects, and raised nothing. With a mask naming the link fields it returned 382 bytes. D14's failure was not reproduced; its size was |

The design, from those:

- `clear_link_format(service, spreadsheet_id, tab, *, columns=None, rows=None,
  runs=True)` clears `userEnteredFormat.textFormat.link` over the named columns (every
  named column by default), in one `repeatCell` request per run of adjacent columns.
  Every other format is left alone [P2].
- With `runs`, it also clears `textFormatRuns` in the cells `linked_cells` reports a
  run link in, in the same `batchUpdate` [P5, P6]. The API cannot take a link out of a
  run without rewriting the run, and a rewritten run keeps the link's colour and
  underline, so the cell's runs are cleared whole. A cell with no link in its runs
  keeps them. `runs=False` skips the grid read this needs.
- `linked_cells(service, spreadsheet_id, tab, *, columns=None)` returns each cell
  holding a link as a `LinkedCell`: its row, its column, the target of each link, and
  whether they are in the cell's runs. The target is what makes it serve both
  directions [D12]: a caller that wants plain text looks for any, and a caller that
  wants links looks for a target that differs from the cell's text, as a bare domain's
  does [P1].
- It reads two fields, since no one field reports every link [P5]: `hyperlink` for a
  link on the whole cell, however it was set, and `textFormatRuns.format.link` for a
  link on part of the text. `userEnteredFormat.textFormat.link` misses a link that was
  set as a run over the whole text.
- The grid read is one `spreadsheets.get` for one tab, with a `fields` mask naming
  those two fields, over the header's columns [D14, P8]. The header comes from the
  caller when it has one, and from one read of row 1 when not.
- The read goes through `pull_grid`, below.
- `push_rows(clear_links=True)` runs the first after the write and the second as part of
  the read-back, raising `ReadBackError` when a link remains. The clear follows every
  write, since the write puts the link back [P3].
- `apply_plan(..., clear_links=False)` does the same for a sync, over the cells the run
  wrote and no others. New rows are written by `updateCells`, so their mask gains the
  link field and they cost no request [P4]. Pushed cells go through
  `values.batchUpdate`, which carries no format, so their clears are `repeatCell`
  requests added to the run's `spreadsheets.batchUpdate`, after the inserts and with
  the rows as they are after them. A run with pushes and no new rows sends one request
  more than today.
- A tab field `clear_links`, true or false, for `push` and `sync` tabs (decision 12).
  It clears what a run writes. A tab's older cells keep their links until
  `clear_link_format` is run over them once.
- Setting a link stays the caller's. The guide notes what the probe found: a run over
  the whole text takes, and a cell-level `link` on a plain string did not [P7].

## Grid reads (`values.py`) [D14, P8]

D14 reports `DecodeRatioError` from a grid read of 16 MB. The probe got 18.6 MB back
with no error, so the failure depends on how well a response compresses: `httplib2`
applies its 100 to 1 limit only past 10 MB of output. The question is closed without
reproducing it, in two parts (decision 14).

- `pull_grid(service, spreadsheet_id, range_, fields)` is the one way the package reads
  grid data. `fields` is required, so no read of the package is unmasked, and the
  range names one tab. With the link mask the probe's response was 382 bytes.
- It catches `httplib2`'s `DecodeRatioError` and `DecodeLimitError` and raises
  `GridTooLargeError`, a `ValueError` that names the range and says to narrow the range
  or the mask. The two are plain `Exception`s, not `HttpError`s, so today one would
  pass `TAB_ERRORS` and stop a whole run; as a `ValueError` it is reported for its tab.
- The two classes exist only in recent `httplib2` releases. They are looked up at
  import, and an older release that lacks them has nothing to catch.
- `pull_grid` is public, so a caller that reads grid data for its own formatting pass
  gets the mask and the message too.
- `linked_cells` reads through it, and so does `get_column_widths`
  ([`g-tabs-and-tools.md`](g-tabs-and-tools.md)).

## Tests

- `pull_grid`: the request it sends, a missing `fields`, each of the two errors raised
  by a fake transport and reported for one tab of a run, and an `httplib2` without the
  classes.
- `push_rows` with each refusal of `push_tab`, an empty row list, a `report` kept on
  an error, and `label` in each message. The `push_tab` tests pass unchanged.
- `pushed_cells` and `appended_columns`, with and without an `insert_above` shift.
- `FakeSheetGrid` models P1 to P6: a whole-cell URL or domain written as a value gains
  a cell link, `repeatCell` and `updateCells` honour a mask over
  `userEnteredFormat.textFormat.link` and `textFormatRuns`, and `spreadsheets.get` with
  `includeGridData` returns `hyperlink` and the runs under a `fields` mask.
- `clear_link_format` over adjacent and split columns, given rows, a cell with a run
  link and one with a bold run only, and `runs=False`, which sends no read.
- `apply_plan(clear_links=True)`: new rows only (the request count of today), pushes
  only (one more), both with an insert above the pushed rows, and a link that remains,
  which raises `ReadBackError`.
- Live, one test: a push with `clear_links` of a whole-cell URL, a bare domain, a URL
  in a sentence, and a cell seeded with a link on part of its text. It pins P1, P3,
  and P5, which the fake's link rule rests on.

## Config and docs in this step

- `clear_links` in `_TAB_FIELDS`, `TabConfig`, and the guide's tab field table; refused
  on a `pull` tab.
- The guide's list of what a sync never does keeps formatting on it, with links as the
  one exception a tab can ask for.
- Changelog: `push_rows`, `clear_link_format`, `linked_cells`, `clear_links`,
  `pull_grid`, `GridTooLargeError`, and the two `ApplyResult` fields under Added.

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-f-push-rows-and-links`, cut from step e's branch,
with a draft PR onto it.

| Commit | Part |
|---|---|
| `48bf48c` | `push_rows`, with `push_tab` reading its store and calling it |
| `8937fa5` | `ApplyResult.pushed_cells` and `appended_columns` |
| `526fb09` | `pull_grid`, `GridTooLargeError`, and the fake's links, runs, `repeatCell`, and masked grid reads |
| `2210f96` | `LinkedCell`, `linked_cells`, and `clear_link_format` |
| `d420f7d` | `strip_links`, `clear_links` on `push_rows` and `apply_plan`, and the tab field |
| `be233b5` | The guide, the changelog, and the live case |

Every `push_tab` test passes unchanged over `push_rows`.

Decisions made during the work:

- **The two new `ApplyResult` fields are compared.** A result that wrote other cells
  is another result. Nine tests compared a whole result built from four values, and
  now give the two lists. It is in the changelog under Changed, for a caller that does
  the same.
- **`push_rows` with the default `label` says `no rows to push`**, since `rows has no
  rows` does not read. With a label given it says what `push_tab` said:
  `local file <path> has no rows`.
- **`push_rows` takes the columns of the rows, for `CheckContext.columns`, from the
  rows' own keys** in first-seen order. It refuses `columns` that are empty, blank, or
  repeated, which a config's loader refuses for `push_tab`.
- **`pull_grid` returns the `GridData` of its range**, the first `data` block of the
  first sheet, and an empty dict when the range holds nothing the mask names.
  `decode_errors` looks the two `httplib2` classes up by module name, which is what
  the test of a release without them calls.
- **`linked_cells` and `clear_link_format` take `header=`, and `clear_link_format`
  takes `sheet_id=`**, each saving a read for a caller that has them. `linked_cells`
  reads from the first wanted column to the last, not from column A.
- **The header row is a row like any other** to both. A push writes the header too,
  and a header cell that is a domain is linked.
- **`clear_link_format(rows=)` takes 1-based spreadsheet rows**, in any order, and
  sends one request per run of adjacent rows and columns.
- **`strip_links` was added.** A run that clears what it wrote needs the links found,
  the clear, and the links left, and `clear_link_format` followed by `linked_cells`
  reads the grid three times for it. `strip_links` reads once when the write left no
  link, and twice with one write when it left some.
- **`link_clear` is public**, as the one place a clearing `repeatCell` is built.
  `apply_plan` builds its own clears with it.
- **A pushed cell has its runs cleared with its link**, in one `repeatCell` under the
  mask of both. The push replaced the cell's text, so runs over the old text mean
  nothing. `clear_link_format` keeps the runs of a cell that holds no link in them,
  since it has not changed the text.
- **`apply_plan(clear_links=True)` reads the grid size when it has pushes and no new
  rows**, for the `sheetId` its clears need. It is one read more beside the read-back
  of the links.

**What the fake assumes and nothing probed.** A value that is not a URL, written over
a linked cell, takes the link away. No library code rests on it: a run clears the
cells it wrote and reads their links back.

**Seen and left.** With `insert_above` on a column outside the projection, the
re-read includes that column, and the write of the new rows covers it with blank
cells. The rows are new, so nothing is lost, but the module's docstring says only the
projection's columns are written. It is as 0.11.0 does it, and outside this plan.

**Live suite.** One case, which passed at the first run against the API: it seeds a
URL, a bare domain, a URL in a sentence, an email address, and a link on part of a
cell's text with `RAW` input, reads the links (P1 and P5), pushes with `clear_links`,
writes one value again, and reads the links once more (P3). One run of the whole
suite, by the orchestrating session:

| | Writes | Reads |
|---|---|---|
| Before this step | 68 | 84 |
| The new case | 6 | 9 |
| After | 74 | 93 |

24 passed in 142 seconds. 5 reads were refused on the quota and sent again, which the
counts above leave out. The case is over the 6 reads that subplan h budgets for one: a
push costs 5, and the clear and its read-back 2. Both of the suite's counts are now
over the limit of 60 a minute, so every run waits on refusals.
