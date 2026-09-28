# Set and check the links of URL cells

Step 7 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second list
of gaps by the same downstream caller.

## Spec

### Goal

A check and a fix for the links of a tab's URL cells, the opposite of `clear_links`.
`structure.py` can find links (`linked_cells`) and clear them (`clear_link_format`,
`strip_links`, the `clear_links` tab field), and nothing sets one. A downstream caller
wants every cell whose whole text is an `http://` or `https://` URL to hold a link to
exactly that text, in a colour the caller names, and not underlined. Today it does
this with its own requests after each run.

### Settle first

Both of these are settled before the API below is final, and the findings are
recorded in `implementation-notes/`.

- **How a link is set.** The guide says that a link set as the cell's own format on
  plain text did not take, and that one sent as a text format run did. The caller
  reports the opposite for the cell's own format: `userEnteredFormat.textFormat.link`
  sent with `repeatCell` works, provided `textFormatRuns` is cleared in a separate,
  earlier request. In the same request the link is dropped. Check live which holds,
  use what works, and correct the guide's paragraph.
- **The size of the grid read.** A mask that includes `effectiveFormat` returns an
  object for every cell of the grid, empty ones included. On a tab of a few dozen
  rows in a grid of 1000 the response compressed past 100 to 1, and `httplib2` refused
  it (`GridTooLargeError` is what `pull_grid` raises for that). The values are read
  first, and the grid read is bounded to the rows and columns that hold any.

**The live check needs the owner's word first.** Ask for a scratch spreadsheet before
any live request for this step, and touch nothing else.

### What a URL cell is

A cell whose whole text, stripped, matches `https?://` followed by characters with no
whitespace. A bare domain (`example.com`), a URL inside a sentence, and an email
address are not URL cells, and neither function reads or writes them.

### API

In `structure.py`, re-exported from `gdrives.sheets`:

```python
url_link_problems(service, spreadsheet_id, tab, *, color,
                  columns=None, rows=None) -> list[UrlLinkProblem]
set_url_links(service, spreadsheet_id, tab, *, color,
              columns=None, rows=None) -> list[UrlLinkProblem]
```

- `UrlLinkProblem`, a frozen dataclass: `row` (1-based), `column` (the header name),
  `text`, and `reasons`, a tuple of names from `URL_LINK_REASONS`:
  - `no_link`: the cell holds no link
  - `target`: the link points somewhere other than the cell's text
  - `color`: the text is not in the colour named
  - `underline`: the text is underlined
  - `runs`: the cell has text format runs
- `color` is `#rrggbb`. It is compared with the cell's colour as the API returns it
  (a fraction per channel), to the nearest of 255 steps.
- `columns` and `rows` are as for `clear_link_format`, so a caller can pass the cells
  of an `ApplyResult` (`pushed_cells`, `appended_rows`, `appended_columns`) and cover
  only what a run wrote. With neither, every column and every row that holds a value.
- `set_url_links` runs the check, writes only to the cells it returned, and returns
  them. It writes the link, the colour, and the underline, and clears the runs, under
  a `fields` mask of exactly those properties, so a cell's bold, fill, and font
  survive. **The cell's text is the authority**: a link that points elsewhere is
  pointed at the text, and the text is never changed.
- After the write the cells are checked again, and any that still break the rule
  raise `ReadBackError`, as a link that remains does for `clear_links`.
- A tab with no problem makes no write request.

### Tab field

`link_urls`, for `sync` and `push` tabs: an object with `color`. After a write, the
fix runs over the cells the run wrote, as `clear_links` does for its clear.

- It is refused on a pull tab, and refused together with `clear_links`. Both are
  config problems, reported before any request.
- `apply_plan` and `push_rows` take `link_urls=`, a colour or None.
- A preview does not run the check. The report of an apply says how many cells were
  given a link.

### Tests

- Unit tests with the fake service cover:
  - each reason alone, and a cell with several
  - a cell that is not a URL cell is never returned and never written
  - the write's mask: a test asserts that the request names no property but the
    link, the colour, the underline, and the runs
  - `columns` and `rows` limit both the read and the write
  - the grid read is bounded to the rows and columns that hold values
  - the read-back error
  - no problem, no write
- Config: `link_urls` is accepted on sync and push tabs, and refused on a pull tab,
  with `clear_links`, and with a colour that is not `#rrggbb`.
- A sync and a push with `link_urls` fix the cells they wrote and no others.
- A live test, once the owner has named the spreadsheet: write a URL cell that is
  bold, run the fix, and read the link, the colour, and the bold back.

### Docs

- `docs/sheets-sync.md`: the links section gains the check, the fix, and the tab
  field, and its last paragraph is corrected by what the live check found.
- README: the tab field in the config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Bare domains, and links on part of a cell's text.
- Any other format. The fix writes four properties.
- Running the check in a preview.
