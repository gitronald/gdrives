# A live probe of moveDimension

- Written: 2026-09-27T17:35:35-07:00
- Live API: one temporary tab of the test spreadsheet, through the service account,
  deleted at the end.
- Scope: step 2 (`reorder_rows`) rests on how `moveDimension` counts its
  `destinationIndex`, which the spec asked to be checked against the live API before
  relying on it. These are the findings the planner, the fake's `moveDimension`, and
  the live test were written to. No package code was changed by the probe.

## Method

Rows `id`, then `A` to `E`, were written to grid rows 0 to 5 (0-based) of a tab whose
grid is 8 rows. Each case below is one `moveDimension` of one row
(`startIndex = s`, `endIndex = s + 1`), sent to that starting state unless it says
otherwise, and the rows were read back after it.

## Findings

**M1. `destinationIndex` is counted before the row is taken out, as the reference
says.** `s=1, dest=4` gave `B C A D E`: the row landed directly before the row that
was at index 4. `s=5, dest=1` gave `E A B C D`, and `s=1, dest=6` gave `B C D E A`. So
a move down to list position `p`, counted after the row is removed, sends `p + 1`, and
a move up to position `p` sends `p`.

**M2. A destination equal to the row's own index is refused.** 400:
`destinationIndex[1] must be outside the requested range[1-2]`. A destination of
`s + 1`, just past the row, is accepted and moves nothing. The planner therefore never
sends a move whose destination is inside `[startIndex, endIndex)`, and a row already
in its place gets no request at all.

**M3. The destination may equal the grid's row count, and not one more.**
`dest = rowCount` moves the row to the grid's last row. One past it is refused: 400,
`destinationIndex[9] is after last row[8]`.

**M4. The requests of one `batchUpdate` apply in order,** each to the state the one
before it left. `[s=1 dest=4, s=1 dest=6]` gave `C A D E B`.

**M5. The batch is atomic.** A valid first move and an invalid second returned 400,
and the tab was unchanged.

**M6. Formatting moves with its row.** A bold row stayed bold at its new position,
and its cells in a column outside the ones written moved too. A move does not change
the grid's row count.

## What this settles

- The spec's index rule holds (M1), and the planner converts each list move to it.
- The planner must not emit a no-op move (M2): a `destinationIndex` equal to its
  `startIndex` fails the whole batch (M5).
- One `batchUpdate` of every move is all or nothing (M5), and sequential application
  (M4) is what lets the planner compute each move against the list as the moves
  before it left it.
- `FakeSheetGrid` models M1 to M6: the destination counted before removal, requests in
  order, the two refusals as 400s, all or nothing, and formats moving with their rows.
  The live test of step 2 checks values, a fill, a blank row, and an unread column
  after a reorder that moves one row up and one down.
