# Replacement review: a caller with its own three-way sync moving onto 0.11.0

- Written: 2026-09-27T10:46:44-07:00
- Code reviewed: `gdrives/sheets/` at `v0.11.0` (unchanged at `e056980`)
- Scope: what a downstream caller that already ran its own keyed three-way sync
  (config, merge, guarded apply, read-back, structure steps) can replace with
  `gdrives.sheets`, and what is missing or different when it tries. No code was
  changed.

Notes are numbered `D1` to `D14`, to sit beside `R1` to `R15` in
`001-accuracy-review.md`. Line references are to the tag above.

The caller in view has these traits, each of which is common enough to plan for:

- several targets, each with a few sync tabs backed by one `.csv` file per tab,
  all committed, all written with LF line endings by another tool;
- a schema declared in code, shared with a second command that checks the same
  tabs outside a sync;
- local-owned columns that the caller derives, one of which is also the
  `insert_above` column;
- a formatting pass of its own that runs after a sync wrote to the sheet; and
- a config and base snapshots that already exist, under its own file names.

## What checks out

- **The merge is a full replacement.** A differential run of 3,000 random
  scenarios (composite key, one local-owned and one sheet-owned column,
  `owns_rows` on and off, with and without a carried column) produced the same
  pushes, appends, folds, conflicts, row flags, overrides, next local rows, and
  next base from both engines. The one difference is D9.
- **An existing layout maps onto the config without moving a file.** `--config
  PATH` and the target's `base` field are enough: a config kept in a data
  directory, with base snapshots at `<dir>/<target>/<tab>.csv`, loads once its
  ownership fields are renamed and its paths are made relative to the config
  file. Every local file and every base file resolved in place.
- **The readers agree.** `read_records` returned the same columns and rows as a
  DataFrame-based all-string reader on 24 files.
- **Most of the caller's code goes.** Of about 1,750 lines, about 1,200 have an
  equivalent: the merge, the tab reader, the guarded apply and read-back, the
  structure steps, the retry helper, the config loader, the orchestration, and
  the report. What stays is a formatting pass (D11), a one-off file conversion,
  and a thinner command line.
- **The preview of `--add-missing` is better than what it replaces.** The merge
  is previewed with the missing columns as blanks, where the caller's own
  engine could not preview a tab until the columns were on the sheet.

## Blocking differences

### D1. `insert_above` is placed from the sheet as read, not as it will be

`apply_plan` finds the insert row by testing each fresh row's value in the
`insert_above` column (`apply.py:311-322`). The pushes of the same plan are
sent before the rows are inserted, so by the time the rows land the column may
hold different values than the ones tested.

The case that breaks: the `insert_above` column is local-owned, and one run
both pushes a row's value to a matching one and appends a new row. Rows 2 to 9
are open and rows 10 on are closed; the run closes row 5 and adds a row. The
new row belongs above row 5, the first closed row once the run is done. It is
placed above row 10, below a closed row.

Needed: evaluate the match on the fresh rows with the plan's pushes to that
column applied. When the column is outside the projection there are no pushes
to it and nothing changes. The preview should name the row (`above row N`),
computed the same way, so the placement can be checked before `--apply`.

### D2. Added columns always go after the last header column

`_restructure` calls `add_columns` with no position (`sync.py:620-623`), so
every missing column lands at the right edge. A caller that treats the local
file's header as the sheet's header expects each new column at its place in the
projection.

Needed: place each missing column directly after the nearest earlier
projection column the sheet has, or at the front when there is none, counting
the columns added before it in the same run. The index has to be taken on the
sheet's header, not the local one: when the sheet still carries a column the
projection lacks (one `--drop-extra` removes in the same run), an index taken
from the local header lands the new column short of its place.

`add_columns` has `before=` and no `after=`, and it adds one adjacent group per
call. Columns that land in different places need either several calls or a
form that takes a position per column in one request.

### D3. A schema held in code cannot check what the sheet's header holds

`validate` is the way in for a caller whose schema is not the config's inline
JSON. It has two limits:

- It is reachable from the library only (`run_target`, `plan_tab`). The
  `sheets-sync` command has no way to name one, so such a caller keeps its own
  command. That is acceptable, and worth saying in the guide.
- It receives rows and nothing else, so it cannot check columns. Two checks
  need them: that every column is declared (an undeclared column in the local
  file or on the sheet is a problem), and that every required column is
  present. The merged rows passed to it hold the local file's columns only
  (`sync.py:479`), so a column that exists only on the sheet is invisible to it
  even though `Table.extra_columns` has the list.

Needed: a way for the check to see the columns of both sides. Either the hook
receives a context (the local columns, the sheet's extra columns, and whether
this run drops them), or the schema gains a strict setting that refuses an
undeclared column. A column a run is about to delete should not be checked,
since the usual reason to drop one is that the schema no longer declares it.
This is the same shape of gap as R11: the hook's signature is too narrow for
the checks callers write.

## Differences to decide

### D4. Inserted rows take the formatting of the row below

`insertDimension` is sent with `inheritFromBefore: False` (`apply.py:228`), by
design: the comment says the new rows take the formatting of the row they sit
above. With `insert_above` that row is the first of the block the new rows are
being kept out of. When that block is formatted directly (a grey fill on
closed rows, say), a new open row arrives grey.

Desired: inherit from the row above, except when the insert point is directly
below the header, where the row above is the header and inheriting from below
is right. If the current behavior has users, make it a tab option.

### D5. Unformatted reads change what an existing base is compared with

Tabs are read with `UNFORMATTED_VALUE` (`table.py:79-85`). A caller whose own
engine read displayed values saved a base of displayed strings. On its first
run over 0.11.0, any cell with a number format reads differently (`50%` as
`0.5`, `3.00` as `3`) and is reported as a sheet edit, folded into the local
file.

Unformatted reads are the right default. Desired: a section in the guide on
moving an existing base over, saying which cells will differ and that a
preview shows them before anything is written. A per-tab render option is the
alternative, and it would make the canonical-string rule conditional. This was
reasoned from the code and not checked against live data.

### D6. `write_records` writes CRLF

`write_values_csv` builds its writer with the `csv` module's default line
terminator (`files.py:60`), so every `.csv` and `.tsv` file a sync or pull
writes has CRLF line endings. Rewriting 24 LF files, none came back
byte-identical, and the line endings were the only difference in each.

The cost is churn: a file shared with a tool that writes LF flips on every
write by either one, and a repository without an `eol` attribute commits the
flip as a whole-file diff. The JSON writer is byte-stable by design, and the
delimited writer should be too.

Desired: LF by default, or a `newline` option on the tab (and on `FileStore`,
plan item 6). `format_values` already passes `lineterminator="\n"` for stdout
(`commands.py:118`). Changing the default changes files 0.11.0 wrote, so it
needs a changelog line.

### D7. Wrapped calls now retry twice over

Since 0.11.0 the value wrappers retry inside themselves. A caller that wrapped
`pull_values` or `batch_update_values` in its own retry, as it had to before,
now makes up to its own attempts times five calls, and a persistent 5xx stalls
for minutes before it surfaces. The changelog lists the change under Changed.
Desired: say there what a caller with its own retry should do (remove it).

### D8. Retries are silent

`with_retry` waits without reporting (`retry.py:28-60`). A run that backs off
four times looks hung for about 15 seconds. Desired: an optional callback
(`on_retry(status, delay)`) that a command can use to print one line to
stderr.

### D9. Carried columns are inferred from the local rows

`merge` takes the carried columns from the keys of the local rows
(`merge.py:214`). With a local file that has a header and no rows, there are
none to infer, so a row folded from the sheet lacks the carried columns in
`new_local`. `apply_tab` writes with the file's own column list and
`write_records` fills the gap, so a file-backed run is unaffected. A caller of
`merge` alone, or a store (plan item 6) that writes `new_local` as given, sees
rows of two shapes. Desired: an optional `carry=` argument, which `plan_tab`
fills from `local.columns`.

### D10. Blank keys are refused where an older engine let them through

`index_rows` refuses a blank key cell on any side, base included
(`cells.py:138`). That is stricter than an engine that only refused
duplicates, so a first run can stop on rows the caller has synced for a long
time. Plan item 5 covers composite keys. For a one-column key the refusal is
right, and the guide's section on moving over (D5) should list it.

## Link formatting

### D11. A caller needs to know which cells a run wrote

The caller's formatting pass runs after an apply that wrote to the sheet, over
the tabs it wrote. `TabReport.wrote_sheet` gates it, and `ApplyResult` gives
`pushed_rows` and `appended_rows`. It does not give the columns, so a pass
scoped to the written cells has to reread the whole tab. Desired: the written
cells by row and column in `ApplyResult`, or a documented way to derive them
from the plan and the result.

### D12. Plan item 8 covers one direction only

Item 8 clears link formatting from cells meant to hold plain text. The caller
in view wants the opposite for a cell whose whole value is a URL: it is a
link, the link's target is exactly the cell's text, it has one colour, and it
is not underlined. A value pushed with `RAW` input lands as bare text, so
every push of a URL needs the pass.

`linked_cells` serves both directions if it returns the link's target with
each cell, since a link that points somewhere other than the text it shows is
the case worth catching. Setting a link can stay the caller's. Item 8 should
say which direction each helper serves.

### D13. A link can live in a cell's text format runs

A link pasted as rich text is stored in `textFormatRuns`, not in the cell's
`userEnteredFormat`. Two consequences for item 8:

- `clear_link_format` as designed clears
  `userEnteredFormat.textFormat.link` only, so a pasted link survives it, and
  `push_rows(clear_links=True)` then fails its own read-back.
- Clearing the runs has to be a separate, earlier request. Sent in the same
  `repeatCell` as a text format, the empty run list is applied after the
  format and resets it, so the request succeeds and the format is lost.

### D14. A grid read has to be bounded to the used range

`linked_cells` needs `spreadsheets.get` with `includeGridData`, which returns
every cell of the range as an object carrying its effective format, empty
cells included. A tab with a few dozen rows in a default 1,000-row grid
returned about 66,000 cell objects for about 550 that held a value: 16 MB of
JSON that compresses to 130 KB.

`httplib2` refuses a response that inflates more than 100 to 1, so that read
fails with `DecodeRatioError` before anything is parsed, and an emptier grid
fails harder because it compresses better. Needed: read the values first, take
the used range from them, and ask for grid data over that range only, one tab
per request, with a `fields` mask naming the format properties wanted.

## Order of need

| Note | Kind | Blocks a replacement |
|---|---|---|
| D1 | fix | yes, when the `insert_above` column is local-owned |
| D2 | fix | yes, when the header order matters to the caller |
| D3 | design | yes, for a schema held in code |
| D4 | option or fix | no |
| D5 | docs | no |
| D6 | fix | no, with an `eol` attribute; yes without one |
| D7 | docs | no |
| D8 | addition | no |
| D9 | addition | no |
| D10 | docs | no |
| D11 | addition | no |
| D12, D13, D14 | design of plan item 8 | no |

D1, D2, D4, and D6 are outside the plan's nine items and are small enough for
a patch release. D3 belongs with item 9's hooks. D12 to D14 belong with item
8.
