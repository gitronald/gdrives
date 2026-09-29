# Review of plan 019 at the close

The review gate of 2026-09-29, on the branch at `d5656d2`: the whole diff against
`dev` (54 files, 8256 lines added), at the review's highest level. Four finders read
it (correctness, reuse and efficiency, test coverage and edge cases, and docs and rule
consistency), their candidates were verified one by one against the code, and the
session that ran them read the source diff itself.

Counts: 13 candidates, 2 dropped before verification, 5 rejected by it, and 6 findings,
all fixed.

## Findings, each fixed with a test

| # | Where | Finding | Fix | Commit |
|---|---|---|---|---|
| 1 | `gdrives/sheets/commands.py` | `sheets-links` swept a target's tabs by their config titles and ignored `sheet_id`, so a tab renamed on the sheet refused the whole sweep, where a sync finds it | `run_links` reads the listing, finds each tab as a sync does, and notes a rename on stderr. `sweep_url_links` takes `listing=`, so the request count is what it was. The colours are still checked before the first request | `0510b24` |
| 2 | `gdrives/export.py` | A UTF-8 byte-order mark before a quoted first CSV cell hid that cell from the scan, and the line break inside it was rewritten | The scan starts after a mark, which is kept | `1e233bb` |
| 3 | `gdrives/sheets/structure.py` | `clear_link_format(formulas=False)` with no `rows` ended each split block with an open range, which started past the grid when the cell left was in the tab's last row. The fake grid refuses that range as the API refuses one outside the grid, and the batch with it | The grid's size is read when a cell is left and no `rows` are given, and a range that would start past it is not sent | `5fc376c` |
| 4 | `docs/sheets-sync.md` | The example of the credential fallback line ended with a parenthesis from the earlier wording | Removed, and a guide test holds the line to `credential_line` | `b994b82` |
| 5 | `gdrives/resolve.py` | The types `sheets-create --from` converts were listed a second time, for the refusal's hint | The hint is decided by `SOURCE_MIMES` | `a476890` |
| 6 | `gdrives/sheets/structure.py` | `_URL_FORMAT_FIELDS` was a second name for `CELL_STYLE_FIELDS` | One name | `5fc376c` |

Finding 3 was reproduced against the fake grid and not against the live API.

## Rejected, with the evidence

| Candidate | Why it was rejected |
|---|---|
| `cell.row in rows` on a list, once per cell left | 0.23 s for 3,000 cells and 40,000 rows, and the same test was there before the plan |
| `_is_allowed` builds its set of allowed values per cell | 0.38 s for 50,000 `datetime` cells and 5 allowed values, and 3 s for 50. A list of allowed moments is short |
| `run_create` and `create_spreadsheet` both check the workbook and the tabs | The command checks before it builds a service and the library checks for its own callers, as they did for the title and the tabs |
| `_column_problems` restates `ColumnSchema.__post_init__` | The config checker had its own copy before, since it lists every problem at once. The plan made that copy one function |
| Blocks split around a cell left: gaps, overlaps, or an empty range | None found over a bounded grid, for skips at the first, second, and last rows, for unsorted and repeated `rows`, and for adjacent columns |

Dropped before verification: the comparison of rows made at plan time and again at
apply time, which is what holds `pending` to what an apply writes, and a `pattern` of
the empty string, which reads as no pattern in a schema export and matches no cell.

## Checked and found clean

- The CSV scan against Python's `csv` module, for every input of up to 7 characters
  over `a`, a comma, a quote, CR, and LF, to `lf` and to `crlf`: 195,312 cases, and
  none where the rows or the cells differ.
- A target's `defaults`: 120 valid configs (9 kinds of tab, each default and value,
  and both input options), and no default made one invalid.
- The added lines, for a local path, a real address or file ID, and any word of a
  private caller: none.

## The gate

`ruff check`, `ruff format --check`, and `pyrefly check` are clean. The full suite,
live tests included, gives 3741 passed and 1 skipped (the revision test that needs
`GDRIVES_TEST_FILE_ID`), at 100% line and branch coverage.
