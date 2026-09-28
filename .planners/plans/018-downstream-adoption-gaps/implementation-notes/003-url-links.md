# A live probe of setting a link on a cell

- Written: 2026-09-27T18:07:06-07:00
- Live API: one temporary tab of the test spreadsheet (1000 rows by 6 columns),
  through the service account, deleted at the end. 11 write requests and 12
  reads.
- Scope: step 7 (`url_link_problems`, `set_url_links`, the `link_urls` tab
  field) rests on how a link is set on a cell, which the spec asked to be
  checked against the live API before the API was final, and on the size of
  the grid read it needs. These are the findings the check, the fix, the
  fake's link formats, and the live test were written to. No package code was
  changed by the probe.

## Method

URLs and plain text were written to the tab as literal strings (`RAW`), and
each case below is one `batchUpdate` sent to one or two of those cells. The
cells were read back under the mask
`sheets(data(rowData(values(formattedValue,hyperlink,textFormatRuns,userEnteredFormat(textFormat),effectiveFormat(textFormat)))))`.

## Findings

**L1. A URL written as a literal string is linked, and the link alone
underlines and colours it.** The cell's `hyperlink` and
`userEnteredFormat.textFormat.link.uri` are its text, and it has no
`textFormatRuns`. The effective format has `underline: true` and the colour
`#1155cc` (red 0.06666667, green 0.33333334, blue 0.8), in `foregroundColor`
and `foregroundColorStyle.rgbColor` both. The user-entered format holds the
link alone, with no underline and no colour. So a check of the colour and the
underline reads the effective format.

**L2. Clearing the link clears what it showed.** A `repeatCell` with an empty
cell and the mask `userEnteredFormat.textFormat.link` takes the link off:
`hyperlink` is gone, and the effective format is back to `underline: false`
and the default colour (`{}`).

**L3. A link set as the cell's own format takes,** on plain text and on a URL
whose link was cleared: `repeatCell` with
`cell.userEnteredFormat.textFormat.link = {"uri": ...}` and the mask
`userEnteredFormat.textFormat.link`. The cell's `hyperlink`, and the
user-entered and effective `link.uri`, are then the target sent. The guide
said such a link did not take; that was wrong, and the guide is corrected.

**L4. The same request repoints a link.** A cell linked elsewhere was pointed
at its own text, and `hyperlink` followed.

**L5. Text format runs and the link do not go in one request.** A cell with
`textFormatRuns` (a link on part of its text), sent one `repeatCell` under the
mask `userEnteredFormat.textFormat.link,textFormatRuns`, came back with no
runs and no link: the link was dropped without an error. The same cell sent
two requests in one `batchUpdate`, first a `repeatCell` with an empty cell
under the mask `textFormatRuns`, then the one that sets the link, came back
with the link.

**L6. Link, underline, and colour go in one request, and the bold survives.**
A bold URL cell sent one `repeatCell` with
`textFormat = {link, underline: false, foregroundColorStyle: {rgbColor}}`
under the mask of those three properties came back with the link,
`underline: false`, and the colour (in `foregroundColor` and
`foregroundColorStyle`, user-entered and effective both), and still bold.

**L7. Colours come back rounded, with zero channels left out.** A channel
that is zero is omitted (`{}` is black, and the default), and the fractions
come back as float32 (`0.06666667`).

**L8. A run over the whole text is stored as the cell's own link.** An
`updateCells` of `textFormatRuns = [{"startIndex": 0, "format": {"link":
...}}]` came back with no runs and with `userEnteredFormat.textFormat.link`.
Runs remain only where they cover part of the text.

**L9. The oversized grid read was not reproduced.** On this fresh tab, a read
of `A1:F1000` under a mask with `effectiveFormat(textFormat)` returned the 8
rows that held values (4,938 bytes of JSON), the same as a read of `A1:B8`.
The response the downstream caller saw, an object for every cell of a
1000-row grid, presumably takes a tab whose empty rows carry formatting.

## What this settles

- The fix sets the link as the cell's own format with `repeatCell` (L3, L4),
  one request per cell, since each cell's link is its own text.
- A cell with runs has them cleared by a `repeatCell` of its own, placed
  before every link in the same batch, and the mask of the request that sets
  the link never names `textFormatRuns` (L5). A tab of N cells to fix, R of
  them with runs, costs one `batchUpdate` of N + R requests.
- The link, `underline: false`, and the colour go in one request under a mask
  of exactly those three properties, so bold, fill, and font are kept (L6).
- The check reads `hyperlink` for the target, `textFormatRuns` for the runs,
  and `effectiveFormat.textFormat` for the underline and the colour (L1), and
  compares colours per channel rounded to 0 to 255, a missing channel as 0
  (L7).
- The grid read stays bounded to the rows and columns of the URL cells, found
  by a read of the values first (L9): it costs nothing, and `pull_grid` still
  raises `GridTooLargeError` for a response too large to decode.
- `FakeSheetGrid` models L1, L2, L3, L5, and L6: a written URL is underlined
  and blue in its effective format only, an `underline: false` and a colour
  set as the cell's own format win, and a `repeatCell` whose mask names the
  link and the runs drops the link. The live test of step 7 writes a bold URL
  cell and a URL cell with a run, runs the fix, and reads the link, the
  colour, the underline, the runs, and the bold back.
