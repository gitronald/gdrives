# Live findings for plan 019

What the Google APIs returned on 2026-09-29, read by the orchestrating session. Every
write was to a temporary tab of the test spreadsheet, deleted afterwards, or to a
scratch file in the owner's test folder. Every name and URL below is synthetic.

## Step 8: links (Sheets API v4)

Cells were written with `updateCells` / `repeatCell` / `values.update`, then read
with `spreadsheets.get` under a field mask over `userEnteredValue`,
`formattedValue`, `hyperlink`, `textFormatRuns`, `userEnteredFormat.textFormat`,
and `effectiveFormat.textFormat`. Colours below are the rgb read, as #rrggbb.

## Where a link is

| Cell | `hyperlink` | `userEnteredFormat.textFormat.link` | `effectiveFormat.textFormat` | `userEnteredValue` |
|---|---|---|---|---|
| Format link on text `label` | the target | the target | link, `#1155cc`, underline true | stringValue |
| Format link on a URL's own text | the target | the target | link, `#1155cc`, underline true | stringValue |
| URL written `USER_ENTERED` by `values.update` | the URL | the URL | link, `#1155cc`, underline true | stringValue |
| `=HYPERLINK("https://example.com/b","label")`, nothing else written | the target | **absent** | link, `#1155cc`, underline true | formulaValue |
| Same formula, after a text colour was written to the cell | the target | **present** (the API copied it in) | link, the colour written, underline true | formulaValue |
| Empty cell given a format link | the target | (not returned) | link, `#1155cc`, underline true | absent, and no `formattedValue` |
| Link on part of the text (a run) | absent | absent | no link, black, underline false | stringValue; `textFormatRuns[i].format` holds `link`, underline true, `#1155cc` |
| Link as `set_url_links` leaves it (link, colour, underline false) | the target | the target, with the colour and `underline: false` | link, the colour, underline false | stringValue |

So:

- **A formula's link** is on `hyperlink` and `effectiveFormat.textFormat.link`, as a
  format link's is. `userEnteredFormat.textFormat.link` does NOT tell the two apart:
  it is absent on a formula cell nothing else was written to, and present once any
  other text format property was written. The formula is told by
  `userEnteredValue.formulaValue`.
- **`formattedValue`** is the displayed text: the label of a `HYPERLINK` formula, the
  URL of a one-argument one, absent on an empty cell.
- **A link colours and underlines its text in the effective format alone**: `#1155cc`
  and underline true, with no user-entered property. A user-entered colour or
  underline overrides it.

## Styling with no link

| Cell | `userEnteredFormat.textFormat` | `effectiveFormat.textFormat` |
|---|---|---|
| Underline true and rgb `#1155cc`, no link | underline true, `foregroundColorStyle.rgbColor`, and the older `foregroundColor` twin | underline true, `foregroundColorStyle.rgbColor` `#1155cc` |
| Underline true and theme colour `LINK` | underline true, `foregroundColorStyle.themeColor: LINK`, `foregroundColor` the resolved rgb | underline true, `foregroundColorStyle: {themeColor: LINK}` (no rgb there), `foregroundColor` the resolved rgb `#1155cc` |
| Underline only | underline true | underline true, colour `rgbColor: {}` (black) |
| Colour only | the colour | underline false, the colour |
| Plain | absent | underline false, `foregroundColorStyle: {rgbColor: {}}`, `foregroundColor: {}` |

- Black is `rgbColor: {}`: a channel at 0 is left out.
- A theme colour has no rgb under `foregroundColorStyle`; the resolved rgb is on
  `effectiveFormat.textFormat.foregroundColor`.

## What a reset leaves (a `repeatCell` with an empty cell, under a mask)

Mask `userEnteredFormat.textFormat.link` alone (what `clear_link_format` sends now):

- A format link: `hyperlink` gone, effective colour black, underline false.
- A link with a user-entered colour and underline (`set_url_links`' cells, or a
  cell someone styled): the link is gone and **the colour and the underline stay**,
  user-entered and effective. This is the "link styling with no link" left behind.
- **A `HYPERLINK` formula: the link is gone too.** `hyperlink` and the effective
  link are absent afterwards, the text black and not underlined, while
  `userEnteredValue.formulaValue` still holds the formula and `formattedValue` the
  label. This is what `clear_link_format` does today to a formula cell.
- A link on a run: untouched (the runs are cleared by their own request).

Mask `link`, `underline`, and `foregroundColorStyle` together:

- Every cell: no `hyperlink`, effective colour black (`rgbColor: {}`), underline
  false. `userEnteredFormat.textFormat` is absent afterwards unless another
  property (bold) was set, which is kept. Clearing `foregroundColorStyle` clears
  the older `foregroundColor` twin with it.
- A cell styled with no link (rgb or theme colour, underline): black, not
  underlined, `userEnteredFormat` absent.
- A `HYPERLINK` formula: as under the link mask, the link is gone, the formula
  stays.
- A link on a run: untouched.

Mask `underline` and `foregroundColorStyle`, leaving the link:

- A linked cell goes back to the link's own look: effective `#1155cc`, underline
  true, the link kept. A user-entered `underline: false` is cleared, so the text
  is underlined again.

## Also seen

- A link sent in an `updateCells` whose mask also names `textFormatRuns` is
  dropped (the cell comes back with no link and no user-entered format). The code
  already knows this of `set_url_links`.
- A URL written as a `stringValue` by `updateCells` is not linked by the API; one
  written `USER_ENTERED` by `values.update` is.

The live test `test_link_audit_reads_a_formula_link_and_styling_with_no_link` pins the
three findings the code rests on, and passed.

## Step 9: a workbook converted by Drive

One `.xlsx` workbook of two tabs and one `.csv` file, each created by
`sheets-create --from` and read back with `spreadsheets.get`.

| In the `.xlsx` workbook | In the spreadsheet |
|---|---|
| Two tabs, `Members` and `Dues` | Both, by name and in order |
| `=D2*2`, `=SUM(D2:D3)` | The same formulas, `formulaValue` |
| `=HYPERLINK("https://example.com/members/7","link")` | The same formula, with `hyperlink` set |
| A date, format `yyyy-mm-dd` | `numberValue` 46056, number format `DATE` `yyyy-mm-dd`, shown `2026-02-03` |
| A date and time, format `yyyy-mm-dd hh:mm:ss` | `numberValue` 46056.54996527778, `DATE_TIME`, shown `2026-02-03 13:11:57` |
| `007` in a cell of text format | `stringValue` `007`, number format `TEXT` |
| `007` as a string in a cell of general format | `stringValue` `007`, no number format |
| `TRUE`, `FALSE` | `boolValue` |
| A merge over `A5:C5` | One entry in `merges`, rows 4 to 5, columns 0 to 3 |
| A bold cell | `textFormat.bold` |

| In the `.csv` file | In the spreadsheet |
|---|---|
| The file, with no title given | One tab, titled as the spreadsheet is: the file's stem |
| `007` | `numberValue` 7: the leading zeros are lost |
| `2026-02-03` | `numberValue` 46056, number format `DATE` `yyyy-mm-dd` |
| `12.5` | `numberValue` 12.5 |
| `"Grace, H"` | `stringValue` `Grace, H` |

A CSV is read as typed input is (`USER_ENTERED`), so text that looks like a number or
a date becomes one.

**A service account cannot make this upload into a folder of My Drive.** Drive
answered `403 storageQuotaExceeded`, and created nothing: the account's storage quota
is 0, and the upload is counted before it is converted. The same call with an OAuth
credential of the folder's owner succeeded. The resumable response carried
`mimeType` when `fields` asked for it, as the conversion check needs.

## Step 11: a path that is not a spreadsheet

Read-only, by path, in the test folder: a text file, a folder, and a Google Doc were
each refused with exit 1, naming the file and its type, and the test spreadsheet by
path was read. The check made no request of its own.

## Step 12: the line endings Drive sends

Read-only. The CSV export of the test spreadsheet (9 rows) had CRLF row endings, no
byte order mark, and no line ending after the last row. With `--newline lf` the file
had LF row endings alone and parsed to the same cells.
