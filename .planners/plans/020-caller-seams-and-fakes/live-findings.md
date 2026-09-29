# Live findings for plan 020

What the Sheets API v4 returned on 2026-09-29, read by the orchestrating session.
Every write was to the live suite's temporary tab of the test spreadsheet, which is
reset between tests and deleted after the last. Every name below is synthetic.

## Step 3: a `repeatCell` whose range starts past the grid

Each request was a `repeatCell` with an empty cell under the mask
`userEnteredFormat.textFormat.link`, the request `clear_link_format` sends, sent alone
in its own `batchUpdate`. The tab had the grid `addSheet` gives: 1000 rows, 26
columns. Indexes are 0-based and end-exclusive, as the API takes them.

| Range | Answer |
|---|---|
| Rows from 999, open (the last row) | Taken |
| Rows from 1000, open (the row count) | **400** `Invalid requests[0].repeatCell: Range (T!A1001:B) exceeds grid limits. Max rows: 1000, max columns: 26` |
| Rows from 1005, open | **400**, the same message, naming `T!A1006:B` |
| Rows 1000 to 1001 | **400**, naming `T!A1001:B1001` |
| Columns 26 to 27 | **400**, naming `T!AA1:AA2` |
| Columns from 26, open | **400**, naming `T!AA1:2` |
| Rows 999 to 1003 (starts inside, ends past) | Taken; the grid stays 1000 rows |
| Rows 999 to 1003, setting `bold` | Taken; the grid stays 1000 rows |
| Columns 25 to 28 (starts inside, ends past) | Taken |
| Rows 3 to 3 (no cell) | Taken |
| Columns 3 to 3 (no cell) | Taken |

So:

- **A range that starts at or past the grid's last row or column is refused**,
  open-ended or not, with a 400 whose message names the range in A1 notation and the
  grid's size. The refusal is of the whole batch, as for any request.
- **A range that starts inside the grid is taken**, whether it ends past the grid or
  holds no cell. The grid is not grown.
- The message names the request by its place in the batch (`requests[2]`).

## What this says of plan 019's fix

`clear_link_format(formulas=False)` splits the rows it clears around the cells it
leaves, and the last stretch is open-ended. Plan 019 leaves that stretch out when it
would start at or past the grid's row count, reasoning from the fake. The API agrees:

- With the fix, on a tab of 3 rows whose last row holds a `HYPERLINK` formula, the
  format link in row 2 is cleared and the formula keeps its link.
- With the check taken out for one run (and put back), the same call was refused:
  `400 Invalid requests[2].repeatCell: Range (T!B4:B) exceeds grid limits. Max rows:
  3, max columns: 26`, and nothing was cleared.

## What changed in the fake

`FakeSheetGrid` refused every `repeatCell` whose range was not wholly inside the
grid, in words of its own. It now refuses what the API refuses, in the API's words,
and takes what the API takes: a range that ends past the grid is applied to the cells
the grid has, and a range of no cells does nothing.

## Pinned by

- `test_a_clear_range_is_refused_when_it_starts_past_the_grid` (live): the table
  above, less the `bold` row.
- `test_clear_link_format_leaves_a_formula_in_the_last_row` (live).
- `TestLinks` in `tests/test_sheets_grid.py`: the same answers from the fake.
