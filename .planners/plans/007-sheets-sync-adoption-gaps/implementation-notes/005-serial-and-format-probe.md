# A live probe of serial-number reads and number formats

- Written: 2026-09-27T11:29:55-07:00
- Live API: one temporary tab of the test spreadsheet, through the service account,
  deleted at the end. 4 reads and 5 writes.
- Scope: two of the questions the plan had left open after note 004: what a
  `SERIAL_NUMBER` read returns (M5), and what an unformatted read returns for a cell
  with a number format (D5, M6). No package code was changed.

Findings are numbered `P9` to `P14`, continuing note 004's `P1` to `P8`.

## Method

Column A held eight cells: an ISO date, an ISO date-time, and a `9/27/2026` date, each
written with `USER_ENTERED`; an ISO date and an ISO date-time written with `RAW`; the
plain number 45000; and two date-time serials with milliseconds, written as
`numberValue` with a date-time format. Column B held `50%` and `$1,234.50` written with
`USER_ENTERED`, `3.00` written with `USER_ENTERED`, and the number 3 with the format
`0.00`.

Both columns were read three ways with `values.batchGet` over `Tab!A:A` and `Tab!B:B`,
the shape of the second read that step b plans: `FORMATTED_VALUE`; `UNFORMATTED_VALUE`
with `FORMATTED_STRING`, which is how `read_tab` reads today; and `UNFORMATTED_VALUE`
with `SERIAL_NUMBER`. A grid read gave each cell's stored value and number format.

## Findings

**P9. Under `SERIAL_NUMBER`, text comes back as text.** The two ISO strings written
with `RAW` came back as the same strings. A date came back as a whole number (an `int`
in the parsed JSON) and a date-time as a float. Step b's design assumed this, and it
holds.

| Cell | Stored | Read today | Read as serial |
|---|---|---|---|
| `USER_ENTERED` `2026-09-27` | 46292, format `yyyy-mm-dd` | `2026-09-27` | 46292 |
| `USER_ENTERED` `2026-09-27 10:30:15` | 46292.43767361111, format `yyyy-mm-dd h:mm:ss` | `2026-09-27 10:30:15` | 46292.43767361111 |
| `USER_ENTERED` `9/27/2026` | 46292, format `m/d/yyyy` | `9/27/2026` | 46292 |
| `RAW` `2026-09-27` | the string | `2026-09-27` | `2026-09-27` |
| `RAW` `2026-09-27T10:30:15` | the string | `2026-09-27T10:30:15` | `2026-09-27T10:30:15` |
| The number 45000 | 45000 | 45000 | 45000 |

**P10. The epoch is 1899-12-30, and a serial keeps the millisecond.** 46292 is
2026-09-27 by that epoch. The two serials with milliseconds came back digit for digit
as sent, and converted to `10:30:15.123` and `23:59:59.999` exactly. Rounding to the
millisecond is safe.

**P11. A plain number cannot be told from a date serial.** 45000 reads as 45000 under
both date-time options. **Confirms M5**: conversion has to be by declaration.

**P12. Display text can name the wrong day.** The cell holding `23:59:59.999` displays
as `9/28/2026 0:00:00`, the next day, because its format shows whole seconds and the
display rounds. That is what `read_tab` reads today. The serial read gives the right
moment. No note had this, and it is a second reason for M5 beside locale and format.

**P13. An unformatted read drops the number format.** `50%` reads `0.5`,
`$1,234.50` reads `1234.5`, and the number 3 with the format `0.00` displays `3.00`
and reads `3`. **Confirms D5 and M6.** One detail: `3.00` entered with `USER_ENTERED`
is stored as 3 with no format and displays `3`, so the `3.00` case arises from a
format set on the cell, not from how the value was typed.

**P14. `USER_ENTERED` turns an ISO date into a date, and takes its pattern from the
text.** The ISO date was stored as a number with the format `yyyy-mm-dd`, and the
date-time with `yyyy-mm-dd h:mm:ss`, where the hour is not padded. So a date written
that way reads back, as display text, equal to what was written; a date-time before
10:00 does not (`9:05:00` for `09:05:00`). This is for plan 010, which writes typed
columns as values. The test spreadsheet's locale was not varied.

## What this settles

- Step b's first task is done, and its design stands. It is made sturdier anyway: a
  cell is converted when the serial read gives a number, and otherwise keeps the value
  of the first read, so the conversion does not rest on P9.
- The guide's examples for moving a sync over are checked.
