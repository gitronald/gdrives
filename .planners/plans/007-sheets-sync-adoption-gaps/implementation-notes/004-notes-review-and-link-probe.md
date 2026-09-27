# Review of notes 001 to 003, and a live probe of link formatting

- Written: 2026-09-27T11:14:40-07:00
- Code reviewed: `gdrives/` at `6b20b57`, which is identical to `v0.11.0`.
- Live API: two temporary tabs of the test spreadsheet, through the service account,
  both deleted at the end. 13 reads and 14 writes in all.
- Scope: whether each claim in `001-accuracy-review.md`,
  `002-downstream-replacement-review.md`, and `003-downstream-migration-review.md`
  holds, and what the Sheets API does with links. No package code was changed.

Probe findings are numbered `P1` to `P8`, to sit beside `R`, `D`, and `M`. Where a
finding overrules a note, it says so.

## Claims about the code

Every file and line reference below was opened and read.

| Notes | Verdict |
|---|---|
| R1 | Holds. Traced through `_merge_row`: the fold takes the sheet's text into the base, a typed local side reads back canonical, and the next run pushes it. The false conflict follows from `_decide` comparing text |
| R2 to R13 | Hold, each at the lines cited |
| R14 | Holds. Plan 006 met the same mismatch with an umbrella branch and a stack, which this plan reuses |
| R15 | The live suite's docstring agrees that its reads are over the limit and its writes under it. The counts were not measured again |
| D1, D2, D4 | Hold. `apply.py:311-322` tests the fresh read before the pushes go out; `sync.py:620-623` passes no position; `apply.py:228` sends `inheritFromBefore: False` |
| D3, D9, D11 | Hold |
| D6, M4 | Hold. `files.py:60` uses the `csv` default. One thing neither note has: `write_values_csv` also serves `sheets-get -o` (`commands.py:109`), whose CRLF rows 0.10.0's changelog describes, so the default changes on `write_records` and not below it |
| D7, D8, D10 | Hold. The waits of four retries come to between 15 and 19 seconds |
| M1, M2, M3 | Hold, with one condition on M1: the token is replaced only where a consent can run, which needs the client secrets file and a terminal. Without them the run falls through to a service account and the token is left alone. They belong to plan 008 |
| M5, M7, M8, M9 | Hold |
| M10, M11, M12 | Hold as absences: the package has no upload, no spreadsheet creation, and no read of column widths |

## Claims that could not be checked here

These rest on a downstream caller's own files and code, which this repository does not
have. Nothing in the plan depends on them being exact.

- The differential run of 3,000 merge scenarios, and the 24-file reader comparison
  (note 002, "What checks out").
- The line counts of the caller's code that has an equivalent (notes 002 and 003).
- The byte comparisons of rewritten files (D6, M4). The cause they report is in the
  code, whatever the counts.
- That every name imported from 0.5.8 still resolves (note 003). The five names listed
  exist at `HEAD`; the 0.5.8 side was not checked.

## Claims about the API, checked live

Plan item 8 said a `RAW` write formats URL-like text as a link. D12 said a value pushed
with `RAW` lands as bare text. D13 and D14 were reasoned from a caller's experience and
not checked against this package's write paths.

### Method

A temporary tab with the default 1000 by 26 grid. Five values were written three ways
(`values.update` with `RAW`, `updateCells` with a `stringValue`, and `values.update`
with `USER_ENTERED`): a URL, a bare domain, plain text, a URL inside a sentence, and an
email address. Cells were also seeded with a link over the whole text and over part of
it, through `textFormatRuns`, and with a bold or italic cell format. Each step was read
back with `spreadsheets.get`, `includeGridData`, and a `fields` mask over
`userEnteredValue`, `hyperlink`, `userEnteredFormat.textFormat`,
`effectiveFormat.textFormat`, and `textFormatRuns`.

### Findings

**P1. A whole-cell URL or domain is linked on write, under `RAW` too.** The URL and the
bare domain came back with `userEnteredFormat.textFormat.link` set, from all three
writes, and again from `values.batchUpdate` with `RAW`, which is the path a sync's
pushes take. The bare domain's target was `http://example.com`, not its text. The URL
inside a sentence, the email address, and the plain text were not linked. **Overrules
D12** on this point, and confirms plan item 8.

**P2. Clearing the cell link leaves the rest of the format.** A `repeatCell` with an
empty cell and `fields: userEnteredFormat.textFormat.link` removed the link from every
cell of P1. A cell that was bold stayed bold.

**P3. Writing the value again puts the link back.** The same `RAW` values written over
the cleared cells came back linked.

**P4. `updateCells` can write a URL with no link.** With
`fields: userEnteredValue,userEnteredFormat.textFormat.link` and no link given, the URL
was stored unlinked in the one request, and the cell's bold was kept. No note has this.
It makes clearing free for the rows a sync adds, which are written by `updateCells`.

**P5. A link on part of the text lives in `textFormatRuns`, and only there.** It
survived P2's clear. `hyperlink` and `effectiveFormat.textFormat.link` were empty for
that cell. **Confirms the first half of D13.** A run given over the whole text was not
kept as a run: the read showed no runs and no `userEnteredFormat` link, while
`hyperlink` and `effectiveFormat` reported the link, and P2's clear removed it. So no
one field reports every link: `hyperlink` and `textFormatRuns.format.link` together do.

**P6. Runs can be cleared in the same request as a format.** One `repeatCell` with
`textFormatRuns: []` and a bold text format, both named in `fields`, cleared the runs
and left the cell bold. One `repeatCell` naming the cell link and the runs cleared
both. A cell that was italic stayed italic when its runs were cleared. **The second
half of D13 was not reproduced**: it reports that the empty run list resets the format
sent with it. The case here was a bold format and a link run; a caller that saw
otherwise may have sent something else.

**P7. A cell-level link set on a plain string did not take.** `updateCells` with
`userEnteredFormat.textFormat.link` and a bold format, on a cell holding `cell link`,
stored the bold and no link. The same link sent as a run over the whole text took.
This matters to a caller that sets links, which the plan leaves to the caller.

**P8. An unbounded grid read is large, and a `fields` mask makes it small.**

| Grid | Read | Response |
|---|---|---|
| Fresh, 24 cells used | Whole tab, no mask | 36,708 bytes, 24 cell objects |
| Fresh, 8 cells used | Whole tab, link mask | 382 bytes, 4 cell objects |
| Filled throughout | Whole tab, no mask | 18,628,054 bytes, 26,000 cell objects |
| Filled throughout | Whole tab, link mask | 382 bytes, 4 cell objects |
| Filled throughout | Used range, link mask | 382 bytes, 4 cell objects |

The link mask named `hyperlink`, `userEnteredFormat.textFormat.link`, and
`textFormatRuns.format.link`. **D14 holds in part.** A fresh tab does not return every
cell of its grid; a tab formatted throughout does, as D14's tab must have been. The
18.6 MB response did not raise `DecodeRatioError` here. `httplib2` 0.32.0 applies its
100 to 1 limit only past 10 MB of output (`decode.py`, `LimitDecoder`), so whether a
response fails depends on how well it compresses, and D14's compressed better. The
mask alone brought the response down; bounding the range as well changed nothing in
this probe and costs nothing.

## Not probed

- What a `SERIAL_NUMBER` read returns for a text cell in a date column, and the
  precision of a date-time serial (M5). Step b opens with that check.
- The display of a `50%` or `3.00` cell under an unformatted read (D5, M6). Step h
  checks the guide's examples.
- Whether a value write resets a cell's existing runs.
