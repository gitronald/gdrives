# Sync a Google Sheet with a local file

`gdrives sheets-sync`, `sheets-pull`, and `sheets-push` keep a tab of a Google
Sheet and a local `.csv`, `.tsv`, or `.json` file in step. A small config file
says which tabs go with which files; the commands do the reading, the
comparing, the writing, and the checking that the writes landed.

Every command **previews by default**: it reads both sides, prints what it
would change, and writes nothing. Add `--apply` to write.

## Contents

- [The three modes](#the-three-modes)
- [Quick start](#quick-start)
- [The config file](#the-config-file)
- [How a sync merges](#how-a-sync-merges)
- [The base snapshot](#the-base-snapshot)
- [A workbook in one JSON file](#a-workbook-in-one-json-file)
- [The first sync](#the-first-sync)
- [Moving an existing sync over](#moving-an-existing-sync-over)
- [Pull and push](#pull-and-push)
- [Links](#links)
- [Keeping a tab in order](#keeping-a-tab-in-order)
- [How cells are read and written](#how-cells-are-read-and-written)
- [What is never done](#what-is-never-done)
- [A usage rule: no defaults in sheet-owned columns](#a-usage-rule-no-defaults-in-sheet-owned-columns)
- [Commands and options](#commands-and-options)
- [Exit codes](#exit-codes)
- [Credentials and scopes](#credentials-and-scopes)
- [Using it as a library](#using-it-as-a-library)

## The three modes

Each tab in the config has a mode:

| Mode | Source of truth | What `--apply` does |
|---|---|---|
| `sync` (default) | both | Merges the tab and the local file by row key against a saved base snapshot, writing each side's changes to the other |
| `pull` | the sheet | Replaces the local file with the tab's contents |
| `push` | the local file | Replaces the tab's values with the local file |

`sheets-sync` runs a target's `sync` tabs, `sheets-pull` its `pull` tabs, and
`sheets-push` its `push` tabs.

## Quick start

`gdrives-sheets.json`, at the root of a project:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "tabs": {
      "Members": {"local": "data/members.csv", "key": ["member_id"]}
    }
  }
}
```

```bash
gdrives sheets-sync roster                 # Preview: what would change, and where
gdrives sheets-sync roster --apply         # Write the sheet, the local file, and the base
git add sheets-base/roster data/members.csv  # Commit the base with the local file
```

## The config file

The config is `gdrives-sheets.json`, found in the working directory or the
nearest parent that has one (the same way `.env` is found), or named with
`--config PATH`. Relative paths inside it are relative to the config file's own
directory, wherever the command is run from.

The file is an object of **targets**. A target is one spreadsheet and the tabs
to keep in step with local files:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "base": "sheets-base/roster",
    "tabs": {
      "Members": {
        "mode": "sync",
        "local": "data/members.csv",
        "key": ["member_id"],
        "columns": ["member_id", "name", "status", "paid", "notes"],
        "local_owned": ["status"],
        "sheet_owned": ["notes"],
        "owns_rows": false,
        "schema": {
          "member_id": {"required": true},
          "paid": {"type": "bool"},
          "status": {"allowed": ["active", "closed"]}
        },
        "insert_above": {"status": ["closed"]},
        "widths": {"notes": 320}
      },
      "Summary": {"mode": "push", "local": "output/summary.csv"}
    }
  }
}
```

### Target fields

| Field | Required | Meaning |
|---|---|---|
| `spreadsheet` | yes | A Sheet URL, a bare file ID, or a Drive path (`My Drive/...`), resolved as the other `sheets-*` commands resolve theirs |
| `tabs` | yes | An object of one or more tabs, by tab title |
| `base` | no | The directory for the base snapshots. Default: `sheets-base/<target>`. It may not be inside a `.gdrives/` directory, which is a cache |
| `base_file` | no | A `.json` file that holds every `sync` tab's base, as the entry named by the tab's title, instead of one CSV per tab under `base`. Contradicts `base`, and may not be inside a `.gdrives/` directory. See [a workbook in one JSON file](#a-workbook-in-one-json-file) |
| `input_option` | no | How pushed values are entered: `RAW` (the default) or `USER_ENTERED`. A target with a `sync` tab must use `RAW` |

### Tab fields

| Field | Modes | Meaning |
|---|---|---|
| `local` | all (required) | The local file. Its extension picks the format: `.csv`, `.tsv`, or `.json` |
| `entry` | all | An entry of a `.json` `local` file that holds several, as `{"Members": [...], "Dues": [...]}`: the tab's local side is then that entry. Needs a `.json` `local`. See [a workbook in one JSON file](#a-workbook-in-one-json-file) |
| `mode` | all | `sync` (the default), `pull`, or `push` |
| `sheet_id` | all | The tab's `sheetId`, a whole number. The tab is then found by it, under whatever title it has on the sheet. See [a tab named by its sheetId](#a-tab-named-by-its-sheetid) |
| `key` | all | The key columns that identify a row: a list of one or more names. Required for `sync`; optional for `pull` and `push`, where it makes the preview report rows by key |
| `columns` | all | The **projection**: the columns the sheet carries. Default: every column of the local file. The key, owned, `schema`, `insert_above`, and `widths` columns must be in it |
| `exclude` | `pull` | Columns to leave out of a pull, by header name; the other way round from `columns`. Contradicts `columns`. See [excluding columns from a pull](#excluding-columns-from-a-pull) |
| `schema` | all | Per column: `type` (`str`, the default, `int`, `float`, `bool`, `date`, or `datetime`), `required` (true or false), and `allowed` (a list of permitted values). Checked before anything is written. A `date` or `datetime` column is read from the sheet as ISO 8601. See [how cells are read and written](#how-cells-are-read-and-written) |
| `bom` | all | `true` writes a byte-order mark at the start of a `.csv` or `.tsv` file, for spreadsheet apps that need one. Not for `.json` |
| `blank_keys` | all | `refuse` (the default) refuses a row with any blank key cell. `partial` refuses only a row whose every key cell is blank, for a composite key of which a component is absent on some rows. See [keys with a blank component](#keys-with-a-blank-component) |
| `newline` | all | The line ending a `.csv` or `.tsv` file is written with: `lf` (the default) or `crlf`. A `sync` tab's base follows it. `crlf` is not for `.json`, which is written with LF |
| `widths` | `sync`, `push` | Column widths in pixels, by header name. Set only on a run that wrote to the sheet |
| `clear_links` | `sync`, `push` | `true` leaves the cells a run writes with no link, where the sheet links a URL or a domain as it is written. Default `false`. See [links](#links) |
| `local_owned` | `sync` | Columns whose local value always wins. See [ownership](#ownership) |
| `sheet_owned` | `sync` | Columns whose sheet value always wins |
| `owns_rows` | `sync` | `true` makes the local file own the set of rows. Default `false` |
| `insert_above` | `sync` | One `{column: value}` or `{column: [values]}` pair: new rows go above the first sheet row whose column holds one of the values, instead of at the end. See [where new rows go](#where-new-rows-go) |
| `on_invalid` | `sync` | What a sync does with a sheet value that fails the `schema`: `refuse` (the default) writes nothing for the tab, and `hold` keeps that value out and writes the rest. See [holding invalid sheet values](#holding-invalid-sheet-values) |
| `bootstrap` | `sync` | How a tab with no base starts. `local` (the default) is the only value; `--adopt` is a flag, not a config value. See [the first sync](#the-first-sync) |

The loader checks the whole file before any request is made and reports every
problem at once: unknown fields, a missing or empty key on a `sync` tab, key or
owned columns outside `columns`, a column both `local_owned` and
`sheet_owned`, a key column that is owned, a `sync`-only field on a `pull` or
`push` tab, `widths` on a `pull` tab, a malformed `insert_above` or schema,
`USER_ENTERED` on a target with a `sync` tab, and two tabs that would write the
same file (a local file or a base file, compared case-insensitively, across
the whole config), the same entry of a file, or a whole file and an entry of
it.

Local columns outside `columns` are **carried**: they stay in the local file,
pass through a sync untouched, and never reach the sheet or the base. Sheet
columns outside `columns` are never read or written, unless `--drop-extra`
deletes them.

### A tab named by its sheetId

A config's tabs are keyed by title, so a tab renamed on the sheet stops a run.
For some sheets the title is incidental, such as the tab a form writes its
responses to, and the tab's `sheetId` is what stays the same. It is the
number after `gid=` in the tab's URL.

```json
"Responses": {"mode": "pull", "local": "data/responses.csv", "sheet_id": 1234567890}
```

- The tab is found by its `sheet_id`, and the title it has on the sheet is
  used in every request. When that title differs from the config's, the
  report says so: `renamed on the sheet: 'Responses' is now 'Form responses 1'`.
- The config's title still names the tab in reports, names the base file, and
  is what `--tab` selects. The config is not rewritten.
- A `sheet_id` the spreadsheet lacks is an error for the tab. The run never
  falls back to the title, which another tab may have taken, and never
  creates the tab.
- Two tabs of one target with the same `sheet_id` are a config error.

## How a sync merges

A sync compares three versions of every cell: the **base** (what both sides
held after the last applied sync), the **local** file, and the **sheet**. Rows
are matched by key, not by position, so sorting or reordering either side is
not a change to sync.

For a row on both sides, each cell in the projection:

| Case | Condition | Result |
|---|---|---|
| In sync | local = sheet | Nothing to do |
| Local edit | sheet = base, local differs | Push the local value to the sheet |
| Sheet edit | local = base, sheet differs | Fold the sheet value into the local file |
| Conflict | all three differ | Reported; neither side is written, and the base stays as it was |

A conflict leaves the base alone on purpose, so the same cell is reported on
every run until a person makes the two sides agree, or a run with
`--prefer local` or `--prefer sheet` settles it toward one side (each such cell
is reported as an override). `--prefer` affects cell conflicts only, not row
flags.

For a row on one side only, read against the base:

| Row is | In the base? | Result |
|---|---|---|
| Local only | no | New local row: appended to the sheet |
| Local only | yes | Deleted on the sheet: flagged `remote_deleted` |
| Sheet only | no | New sheet row: folded into the local file |
| Sheet only | yes | Deleted locally: flagged `local_deleted` |

A flagged row is left as it is on both sides, and is flagged again on every
run until a person resolves it (delete it on the other side too, or restore
it). A row on both sides that the base lacks is merged cell by cell against a
blank base.

Key cells are compared with surrounding whitespace stripped and inner runs of
whitespace collapsed, so a stray trailing space typed on the sheet does not
split one row into two. The stored text is never rewritten. A blank or repeated
key on either side stops the run with every such row listed.

### Typed columns compare by value

Two cell strings can differ and mean one value: `true` and `TRUE` in a `bool`
column, `3.0` and `3` in a `float` column. It is common in practice. A sync
writes literal strings, so the sheet holds the text `3.0`, and a person who
retypes the cell leaves the number 3, which reads as `3`.

On a tab with a `schema`, the cells of a column with a declared type are
compared as values of that type, so a respelling is not an edit. Without
this, a respelled cell is folded or pushed for nothing, and a respelling on
one side turns a real edit on the other into a conflict.

- Only comparison changes. A push sends the local file's text and a fold
  takes the sheet's, and neither side is rewritten to match the other's
  spelling.
- A cell that does not parse as its type is compared as text, and the schema
  check reports it. Nothing is coerced.
- Key columns are never compared by type: `007` and `7` are one `int` and two
  keys.
- A tab with no `schema` compares text, as does a pull or a push, which
  replaces one side with the other.

### Keys with a blank component

A row with a blank key cell is refused, on the sheet, in the local file, and
in the base. For a one-column key that is right: the row has no identity, and
it has to be fixed.

With a composite key, a component can be absent on some rows where the others
still identify the row. `blank_keys: "partial"` allows that: a row is refused
only when its every key cell is blank. Two rows share a key when their
components agree, blank ones included, and a repeated key is refused as
before.

### Holding invalid sheet values

A sheet value that fails the tab's `schema` reaches the merged rows, the check
reports it, and nothing is written for the tab. One mistyped cell on a shared
sheet then blocks every other push and fold until someone fixes it.

`on_invalid: "hold"` keeps such a value out instead. A sheet value that would
be folded but fails its column's type, `allowed`, or `required` is **held**:
the local file and the base keep their values, the rest of the tab is
written, and the run exits 2. The cell is held again on every run until the
sheet is corrected.

- A blanked cell in a `required` column is held too.
- A new sheet row with any invalid cell is held whole: it is not folded, and
  its invalid cells are listed.
- Only the sheet side is held. An invalid value in the local file stops the
  tab under both settings, since the local file is yours to fix.
- A held value discards nothing, so it reports no override, in a
  `sheet_owned` column and under `--prefer sheet` too.
- On a tab with `owns_rows`, a row added on the sheet is flagged
  `remote_added` as before, and none of its cells is held: it would not
  have been folded.
- A pull has no such option. It replaces the whole file, and refuses a tab
  with any problem.

### Ownership

Ownership overrides the cell rule for whole columns:

- A `local_owned` column always pushes the local value. When the sheet value
  had changed too, the discarded sheet value is reported as an override. The
  one exception is a row added on the sheet: it is folded in with the sheet's
  values in every column, local-owned ones included, since the local file has
  no value for it yet. From the next run on, the local value wins there too.
- A `sheet_owned` column always folds the sheet value. A new local row is
  appended with its sheet-owned cells blank, since those values are assigned
  on the sheet; a non-blank local value discarded that way is reported as an
  override.
- `owns_rows: true` makes the row set local-owned: a row added on the sheet is
  flagged `remote_added` instead of folded into the local file.

Key columns are identity and cannot be owned.

### The report

The report lists, per tab, the cells to push, the cells and rows to fold into
the local file, the new rows for the sheet, the conflicts, the overrides, the
sheet values and rows held, and the row flags, each cell with its before and
after values. A tab is reported `in sync` only when nothing is written and
nothing is left for a person. A value that came from the sheet or a file is
shown with control characters escaped, so it cannot drive the terminal.

### Where new rows go

New rows for the sheet go directly after the last row holding a value in any
column. With `insert_above` they are inserted directly above the first row
whose column holds one of the values, and go after the last row when no row
does. A tab that keeps its closed rows at the bottom sets
`"insert_above": {"status": ["closed"]}`, and new rows land above the closed
block.

The column is read **as it will be once the run's pushes are in**. When one
run closes row 5 and adds a row, the new row goes above row 5, the first
closed row after the run, and not above the closed block as it was read. A
column outside the projection gets no pushes, so it counts as read.

The report says where the rows go: `new rows for the sheet (2), above row 5`
in a preview, or `after row 40` when no row matches, and the rows they were
written to after an apply (`in rows 5 to 6`). The preview and the apply find
the row the same way. Both refuse an `insert_above` column the tab lacks,
as they do any projection column, unless `--add-missing` adds it. Its cells
then count as blank, but for the ones the run pushes.

Inserted rows take the **formatting of the row above them**, so a new open
row placed above a block of grey closed rows is not grey. Directly below the
header they take the formatting of the row below. Conditional format rules
apply by range and cover the new rows either way.

### The order of writes

`--apply` writes in a fixed order and stops at the first failure:

1. The schema checks, on the local rows and on the merged result. Any problem
   means nothing is written.
2. The structure steps asked for: create a missing tab with its header row,
   add missing columns (`--add-missing`), delete extra ones (`--drop-extra`).
   The tab is then read and merged again, and the run stops if that merge
   differs from the checked one.
3. The sheet writes: the tab is read again, and the run stops if its header,
   its rows, or their positions changed since the merge was computed; then the
   pushed cells, the new rows, and a read-back that checks every written cell.
4. The local file, then the base, then the column widths.

So a failed guard or read-back leaves the local file and the base as they
were, and the next run sees any sheet write that did land as already in sync.
The local file and the base are rewritten only when they change.

## The base snapshot

The base is one CSV per sync tab, `<base>/<tab title>.csv`, holding the
projection columns only. It records what both sides held after the last
applied sync, which is what lets a sync tell "edited on the sheet" from
"edited locally". A target's `base_file` keeps every tab's base in one JSON
file instead: see [a workbook in one JSON file](#a-workbook-in-one-json-file).

**Commit the base alongside the local file.** Everyone who syncs the same
target then shares one base, and a clone of the project syncs correctly on its
first run. A base that is lost or out of date makes a sync misread which side
changed, so do not keep it in an ignored or cache directory (the loader refuses
a base inside `.gdrives/`).

## A workbook in one JSON file

A project may keep a whole workbook in one JSON file, an object of entries
each holding a tab's rows, with typed values:

```
{
  "Members": [{"member_id": "m1", "name": "Ada", "paid": true}],
  "Dues": [{"member_id": "m1", "amount": 25.5, "due": "2026-10-01"}]
}
```

A tab's `entry` names its entry of a `.json` `local` file, and a target's
`base_file` keeps every `sync` tab's base in a second file of the same shape,
the entry named by the tab's title:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "base_file": "sheets-base/roster.json",
    "tabs": {
      "Members": {
        "local": "data/workbook.json",
        "entry": "Members",
        "key": ["member_id"],
        "schema": {"paid": {"type": "bool"}}
      },
      "Dues": {
        "local": "data/workbook.json",
        "entry": "Dues",
        "key": ["member_id"],
        "schema": {"amount": {"type": "float"}, "due": {"type": "date"}}
      },
      "Summary": {"mode": "pull", "local": "data/workbook.json", "entry": "Summary"}
    }
  }
}
```

- An entry is read and written as a `.json` local file is: an array of flat
  objects, each value typed by the tab's `schema`, a blank cell as `null`,
  and a date as its ISO 8601 string. The base in `base_file` is typed by the
  same schema. `bom` and `crlf` do not apply.
- A write reads the file again, replaces the tab's entry, or adds it at the
  end when it is new, and replaces the whole file through a temporary file
  and a rename. Every other entry keeps its value and its place.
- The file is written with a two-space indent, non-ASCII text as it is, and
  a final newline, so a rewrite that changes nothing leaves a file in that
  form byte-for-byte the same. A file formatted another way by hand is
  reformatted on its first write, with every value kept. A run that changes
  nothing does not write the file at all.
- A file that is not a JSON object, or whose object names an entry twice, is
  an error for the tab, and is never taken for a missing entry and
  overwritten.
- Two tabs may write two entries of one file. The loader refuses two tabs
  that would write the same entry, and a tab that writes the whole file
  beside one that writes an entry of it. A `push` tab only reads its entry.
- Tabs run one after another, and each write reads the file as the tab
  before left it. If a later tab fails, the file holds the earlier tab's new
  entry and the failed tab's old one, as two separate files would: the
  earlier tab's sheet, local entry, and base entry all landed, and the
  failed tab's did not.
- **Another process writing the file during a run is not guarded against**,
  as it is not for any local file: its write can be lost to the run's, or the
  run's to it. Do not edit the file, or run a second sync over it, while a
  run is going.

In code, `JsonEntryStore(path, entry, types=None)` is the store these become,
for a `TabConfig(store=...)` or a `Target(base_stores=...)` of your own.

## The first sync

A sync tab with no base file yet has nothing to compare against, so the first
run takes one of two explicit starts.

**Bootstrap (the default).** The local file is taken as the base. Cells edited
on the sheet fold into the local file, rows only on the sheet fold in, and
**nothing is written to the sheet on that run**: a push that ownership would
make (a `local_owned` column, say) is held back and listed, and goes out on the
next run. A local row the sheet lacks is flagged `remote_deleted`, since the
bootstrap cannot tell a new local row from one deleted on the sheet.

**`--adopt`.** The local file wins every difference: it is merged against an
empty base with every non-key column local-owned, `sheet_owned` columns
included, and local-only rows are appended to the sheet. A row only on the
sheet is flagged `remote_added` and left in place, never removed, and sheet
columns outside the projection are untouched. `--adopt` without `--apply`
previews the adoption and writes nothing.

The two do not combine after the fact. A bootstrap run **with `--apply` saves a
base**, and from then on local rows the sheet lacks are flagged
`remote_deleted` on every run, and `--adopt` is refused because a base exists.
To adopt after all, delete the tab's base file and run with `--adopt --apply`.
So when the local file holds rows the sheet should gain, preview the first
sync and choose `--adopt` before applying a bootstrap.

A tab that does not exist, or has no header row, and has no base is merged
against an empty base: `--apply` creates the tab, writes the header row, and
appends every local row. A tab with a header row and no data rows bootstraps.
A missing or emptied tab that **does** have a base is refused, since the tab
was emptied after a sync and a person should look first.

A projection column the sheet lacks is refused unless `--add-missing`, which
adds it as a blank column. A key column the sheet lacks is always refused: an
added key column would hold blank keys.

An added column lands **at its place in the projection**: directly after the
nearest projection column before it that the sheet has, or at the front when
there is none. Columns the sheet already has are never moved, so when the
sheet's order differs from the projection's, a new column still follows its
nearest earlier one, wherever that sits:

| Sheet header | Projection | Header after `--add-missing` |
|---|---|---|
| `id, name, notes` | `id, email, name, notes` | `id, email, name, notes` |
| `id, name` | `status, id, name, city` | `status, id, name, city` |
| `name, id` | `id, email, name` | `name, id, email` |

A new column takes the formatting of the column to its left, or of the one
to its right at the front. Columns that `--drop-extra` deletes in the same run
are deleted after the new ones are placed.

## Moving an existing sync over

For a project that already keeps a tab and a file in step with code of its
own, and has local files, a base, or both. The first preview can list changes
that nobody made. Each has one of the causes below, and **a preview shows all
of them before anything is written**, so look first.

**Numbers read without their format.** A tab is read with unformatted values.
A file or a base saved from what the sheet displays differs wherever a cell
has a number format: `50%` reads `0.5`, `$1,234.50` reads `1234.5`, and a 3
shown as `3.00` reads `3`. With the displayed text in both the local file and
the base, each such cell is reported as a sheet edit and folded into the local
file, once:

```
  fold into the local file (1):
    m1 / 'rate': '50%' -> '0.5'
```

Folding is the way through: after it the file, the base, and the sheet hold
one value. Check the preview for pushes of such cells. A push means the local
file holds the displayed text and the base does not, and applying it writes
the text `50%` over the number.

**Starting over.** Deleting the base makes the next run a
[first sync](#the-first-sync). A bootstrap takes the local file as the base,
so the same cells fold in. `--adopt` makes the local file win, which writes
its text over the sheet's cells: a cell holding the number 0.5 then holds the
text `50%`. Adopt when the local file holds what the sheet should hold.

**Typed dates.** A column declared `date` or `datetime` is read as ISO 8601
whatever the sheet displays, and the local file has to hold ISO 8601 too. A
local file with display text in a declared column fails the check of its own
rows, and nothing is written:

```
  problems (1), so nothing is written:
    Members (local): key ('m1',), column 'joined': '9/27/2026' is not a valid date
```

Rewrite the column in the file before declaring it. A column left undeclared
reads as the text the sheet displays, as it did before, and changes nothing.

**Blank keys.** A blank key cell is refused on every side, the base included
(`base: blank key ['member_id'] in rows [2]`), where code that refused only
repeated keys let such rows through. For a one-column key the refusal is
right, and the rows have to be fixed or removed, in the base too. For a
composite key of which a component can be absent, set
[`blank_keys: "partial"`](#keys-with-a-blank-component).

**Line endings.** A `.csv` or `.tsv` file is written with LF unless the tab
sets `newline: "crlf"`. A file is rewritten only when its records change, so
one with CRLF keeps it until a run changes the file, and changes once then.

**An existing layout.** `--config PATH` names a config kept anywhere, and a
target's `base` field names the directory of its base snapshots. A base is one
CSV per tab, `<base>/<tab title>.csv`, holding the projection columns, so a
base kept in another shape is converted to that, or deleted for a first sync.

## Pull and push

**`pull`** replaces the local file with the tab. The tab is read over the
configured `columns` (every named header column by default) and checked
against the schema. A missing tab, a tab with no header row, and a tab with no
data rows are refused, and the local file is left alone. A missing local file
is created. The preview compares the tab with the current local file: row
counts, a drop in the row count, and, with a `key`, the rows added, removed,
and changed. An unchanged file is not rewritten. A pull writes only local
files; it never writes to the sheet.

### Excluding columns from a pull

A pull tab's `columns` is an allowlist: a column added on the sheet later is
silently left out until `columns` names it too. `exclude` is the other way
round, for a tab that holds a few sensitive columns (personal data, say) and
grows new columns over time:

```json
"Members": {"mode": "pull", "local": "data/members.csv", "exclude": ["birthdate", "ssn"]}
```

Every column but the ones named is pulled, including one added on the sheet
after `exclude` was written. `exclude` and `columns` contradict each other and
cannot both be given on one tab.

The header is checked before anything is read into a row: every name in
`exclude` must be one of the header's named columns, or the pull is refused
and the local file is left alone, naming every name it could not find. A
denylist that quietly matched nothing after a column was renamed on the sheet
would start writing that column's data to the local file, which is the
failure this refusal exists to prevent. A renamed sensitive column is a
likely cause. A tab whose named columns are all excluded is refused too, since
there would be nothing left to pull.

An excluded column's values are never read into a row: they cannot reach the
local file, the preview report, or a `validate`, `check`, or `warn` hook. A
report may still show the excluded column's *name*: `sheet_columns` lists
every header column, since it describes the sheet's structure, and a local
file written before `exclude` was added has that column counted, and dropped,
like any other column the sheet no longer carries.

**`push`** replaces the tab's values with the local file: the header row and
every row, in the local file's column order (only the configured `columns`,
when there are some). The preview says what the sheet holds that the local
file does not, since that is what the push discards: row and cell counts, the
sheet columns the local file lacks, and, with a `key`, the rows removed,
added, and changed. With `--apply`:

- The tab is read again and the push is refused if it changed since the
  preview read.
- One write covers both the old and the new extent of the tab, padded with
  blank cells, so the old cells are cleared by the same write that fills the
  new ones and a failure cannot leave the tab empty. The grid is grown first
  when the data does not fit. A missing tab is created.
- The tab is read back. Under `RAW` every cell is compared; under
  `USER_ENTERED`, which rewrites values on entry, only the header and the row
  count are, and the report says so.
- A tab already holding exactly the local file is not written.

An empty local file is refused, as is a local file with a blank or repeated
key when a `key` is configured.

As a library, `push_rows` is the push of rows held in memory, with no config
and no file: the tab's title, the columns, and the rows, as records of cell
strings (`encode_rows` makes them from typed rows).

```python
from gdrives.sheets import encode_rows, push_rows

rows = encode_rows([{"id": 1, "total": 2.5, "paid": True}])
report = push_rows(
    service, "<spreadsheet-id>", "Summary", ["id", "total", "paid"], rows, apply=True
)
```

**`sheets-pull --all-tabs`** dumps every tab of a spreadsheet with no config:

```bash
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/                 # Preview
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --apply         # One .csv per tab
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --format json --apply
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --skip Notes --apply
```

The values of every tab are read in one request. Each file is named from the
tab title (path separators and control characters replaced) plus the
extension. `--skip TITLE`
(repeatable) leaves a tab out, which protects a local file that shares a name
with a tab but is produced elsewhere. A `--skip` title the spreadsheet lacks,
and two tabs whose file names collide, are refused before any values are
read. A tab with no values or no header row is skipped and reported, never
written as an empty file. `--bom` starts each `.csv` or `.tsv` file with a
byte-order mark, and `--slug` names each file by a slug of its title. A title
with no letter or digit has no slug, which is an error for that tab.

### Pulling and pushing the same files

Some projects treat the sheet as the source between edits: pull the tabs over
the local files, review the diff, and push back only when the local side was
edited. A row deleted on the sheet then leaves the file on the next pull.
`sync` mode does not fit that. It flags a deleted row on every run, and exits
2, until a person deletes it on the other side.

A tab has one mode, so the way through is **two targets** that name the same
spreadsheet and the same files, one with `pull` tabs and one with `push`
tabs:

```json
{
  "roster-in": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "tabs": {
      "Members": {"mode": "pull", "local": "data/members.csv", "key": ["member_id"]},
      "Summary": {"mode": "pull", "local": "data/summary.csv"}
    }
  },
  "roster-out": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "tabs": {
      "Members": {"mode": "push", "local": "data/members.csv", "key": ["member_id"]},
      "Summary": {"mode": "push", "local": "data/summary.csv"}
    }
  }
}
```

```bash
gdrives sheets-pull roster-in --apply    # The sheet replaces the local files
gdrives sheets-push roster-out           # Preview what a push would discard
gdrives sheets-push roster-out --apply   # The local files replace the tabs
```

The loader refuses two tabs that would write one file, and allows this: a
`push` tab reads its local file and does not write it, so the `pull` tab is
the file's only writer.

Neither mode keeps a base, so nothing tells an edit made on the sheet since
the pull from one made locally. A push replaces the tab with the file,
sheet edits included, and its preview lists what the sheet holds that the
file does not. Pull first, and read the push's preview before applying it.

## Links

The sheet formats text as a link when it is written, and a sync or a push
cannot write a URL without it: literal strings (`RAW`) are linked too.

- A cell whose **whole text** is a URL or a bare domain is linked. A bare
  domain's link points somewhere other than its text: `example.com` is given
  the target `http://example.com`.
- A URL inside a sentence, an email address, and plain text are not linked.
- Writing the same value again puts a cleared link back.

For a tab meant to hold plain text, `clear_links: true` leaves the cells a
run writes with no link, and no other cell is touched:

- A **push** clears the links of the columns it pushed, after the write. It
  costs one read when the push left no link, and a write and a second read
  when it left some.
- A **sync** clears the cells it pushed and the rows it added. New rows are
  written with no link at no cost. Pushed cells are cleared in the request
  that adds the new rows, so a run with pushes and no new rows makes one
  write more, and one read more for the tab's `sheetId`. The links of the
  written cells are then read back, which is one read more.
- The header row is a row like any other: a push writes it, and clears its
  links with the rest.
- A link that remains stops the run with a read-back error.
- Every other format is left alone: a cell keeps its bold and its fill.
- A tab's older cells keep their links. `clear_link_format` clears them, once.

As a library, `linked_cells` returns each cell of a tab that holds a link,
with the target of each and whether it is on part of the cell's text, and
`clear_link_format` clears the links of the columns and rows named:

```python
from gdrives.sheets import clear_link_format, linked_cells

for cell in linked_cells(service, "<spreadsheet-id>", "Members"):
    print(cell.row, cell.column, cell.targets, cell.in_runs)

clear_link_format(service, "<spreadsheet-id>", "Members", columns=["website"])
```

A link on part of a cell's text lives in the cell's text format runs. The
API cannot take a link out of a run without rewriting the run, so
`clear_link_format` clears the runs of such a cell whole; a cell whose runs
hold no link keeps them. `runs=False` leaves runs alone and saves the read
that finds them.

A caller that wants links, not plain text, looks for a target that differs
from the cell's text. Setting a link is the caller's to do. A link sent as a
text format run over the whole text takes; a link set as the cell's own
format on plain text did not, when tried.

## Keeping a tab in order

A sync keeps the sheet's row order: a row typed on the sheet stays where it
was typed, and a new local row goes after the last row, or above an
`insert_above` row. A tab meant to stay in one order drifts as people add
rows. `reorder_rows` puts it back in an order the caller computes, as the
keys of its rows, first to last. An order no sort on the sheet can express,
such as a status ranked by a custom order rather than alphabetically, is then
a few lines of Python. It is a library call; no command runs it.

```python
from gdrives.sheets import read_tab, reorder_rows

RANK = {"active": 0, "paused": 1, "closed": 2}


def place(row):
    """Active rows first, then paused, then closed, and by name within each."""
    return (RANK.get(row["status"], len(RANK)), row["name"])


table = read_tab(service, "<spreadsheet-id>", "Members", None, ["member_id"])
order = [row["member_id"] for row in sorted(table.rows, key=place)]
preview = reorder_rows(service, "<spreadsheet-id>", "Members", ["member_id"], order)
print(f"{preview.moves} move(s): {preview.moved}")
if not preview.unchanged:
    reorder_rows(
        service, "<spreadsheet-id>", "Members", ["member_id"], order, apply=True
    )
```

Each key in `order` is a sequence of cell strings, one per key column, or a
plain string for a one-column key. Keys are compared as a sync compares them,
with surrounding and doubled spaces ignored. Like the commands, it previews by
default and writes only with `apply=True`, which needs the `spreadsheets`
scope. It returns a `ReorderResult`: `moves`, the number of rows it moves;
`moved`, their keys; `unchanged`, True when the tab is already in order; and
`applied`, True when the moves were written.

- **The order names every row once.** The tab is read whole with the key,
  so a blank or repeated key on the tab is refused, as a sync refuses it
  (`blank_keys` works as for a sync). A row the order leaves out, a key the
  tab lacks, a key the order repeats, and a key of the wrong length are
  refused too, all listed in one error, and nothing is written. To keep rows
  the order does not care about, append them to it.
- **Blank rows stay put.** The rows reordered are rows 2 to the last row
  holding anything. An entirely blank row among them keeps its position,
  and the keyed rows fill the other positions in the order given.
- **Rows move whole.** Each row is moved with the API's `moveDimension`, not
  rewritten, so it takes its formatting, notes, validation, and the cells of
  columns that were never read with it.
- **Few moves.** The rows already in order relative to each other stay where
  they are, and every other row is moved once, so a tab with one row added
  out of place costs one move. A row that has to cross a blank row is always
  among those moved. All the moves go in one request, which the API applies
  all or nothing.
- **Guarded and read back.** With `apply=True` the tab is read again first,
  and `SheetChangedError` is raised, with nothing written, when its header,
  rows, or row numbers changed since the preview read. After the moves the
  tab is read back, and `ReadBackError` is raised when a row no longer holds
  the cells it held or is not in its place. A tab already in order gets no
  write.

Moving a row has the effects of dragging it on the sheet: formulas,
conditional format ranges, and named ranges are adjusted, so a formula that
refers to another row by position follows that row to its new place. A
filter view or a sort someone applied on the sheet is not applied again: the
tab is left in the order given.

## How cells are read and written

Both sides are compared as **canonical strings**.

**Reading.** A tab is read with unformatted values, and dates as their
formatted text. A number reads as its value, whatever its display format: a
cell showing `50%` reads as `0.5`, and one showing `3.00` reads as `3`. A
checkbox reads as `TRUE` or `FALSE`, a blank cell as an empty string, and a
formula cell as its result. Columns are found by header name, never by
position, and a header that repeats a name stops the run.

**Dates.** A date cell reads as the text its number format shows, which
depends on the format and the spreadsheet's locale: `9/27/2026` on one sheet
and `27.09.2026` on another. A column the `schema` declares `date` or
`datetime` is read a second time, as the serial numbers the sheet holds, and
each date cell arrives as ISO 8601 whatever the sheet displays: `2026-09-27`,
or `2026-09-27 10:30:15.000` for a date-time. A date-time read from a serial
always carries three digits of milliseconds, a whole second included, so every
such cell of a column has one width.

- Conversion is by declaration, never by guess. A number in an undeclared
  column cannot be told from a date's serial, and is left alone. A plain
  number in a declared column is read as a serial.
- A cell holding text stays as it is. A sync writes literal strings, so a date
  it pushed is text on the sheet, and reads back as written. A column can
  hold date cells and ISO text, and both arrive as ISO 8601. The text is
  not respelled: a date-time pushed as `2026-09-27 10:30:15` reads back as
  that, not with `.000`.
- Date-times read by earlier versions were written without a fraction when it was
  zero (`2026-09-27 10:30:15`). A sync compares a typed column by value, so a
  base or local file in that form is in sync with the new read, and nothing
  is rewritten. A pull compares text, so the first pull after upgrading
  rewrites those cells once, adding `.000`.
- A date-time in a `date` column is not cut to its day. It keeps its display
  text, and the schema check reports it.
- The serial is also the more exact read. Display text rounds to what its
  format shows, so a cell holding `23:59:59.999` under a format of whole
  seconds displays as midnight of the next day.
- A serial carries no time zone. The values are in the spreadsheet's own.
- The second read covers the declared columns only, and a tab with no
  declared date column is read once, as before.
- The two reads are not one moment. A row inserted on the sheet between
  them puts a date against the wrong row. A sync reads the tab again before
  it writes and stops if the rows moved; a pull has no such second look, so
  pull a tab with typed dates when nobody is inserting rows.

**Local files.** In a `.csv` or `.tsv` file every cell is a string, so leading
zeros, booleans, and dates stay exactly as written. Its lines end with LF, or
with CRLF when the tab sets `newline: "crlf"`, and the base follows the tab. A
file is rewritten only when its records change, so one written with other
line endings keeps them until a run changes it, and changes once then. A line
break inside a cell is written as the cell holds it. A `.json` file is an array
of objects with typed values, written with a fixed key order, a two-space
indent, and a final newline, so a run that changes nothing leaves it
byte-for-byte the same. Column names are read with surrounding whitespace
stripped, as the tab's header cells are, so `id ` in a file's header is the
column `id`; two names that are equal once stripped stop the run. Every local
write goes through a temporary file and a rename, so an interrupted run never
leaves a partial file.

**Writing.** A sync writes every value as a **literal string** (`RAW`), and a
push does too unless the target sets `input_option: USER_ENTERED`. Literal
strings read back exactly as written, which is what the read-back check and
the next merge depend on: `007` stays `007`, and `=1+2` is stored as text, not
run as a formula. The consequence is that **sheet formulas over a synced
column see text**: a number pushed as `"250"` is the string `250` to the
sheet, so a `=SUM()` over that column does not count it.

A date or number that a sync or a push writes is **text on the sheet**, as
every written value is. The sheet does not sort or format it as a date.

**Schema.** A `schema` type is declared, never guessed. A cell that does not
parse as its declared type (`int` takes digits with an optional minus sign;
`bool` takes `TRUE` or `FALSE` in any case; `date` and `datetime` take ISO
8601), a blank `required` cell, or a value outside `allowed` is a problem, and
a run with any problem writes nothing.

## What is never done

- **Rows are never deleted.** A row deleted on one side is flagged
  (`remote_deleted` or `local_deleted`) on every run, never removed from the
  other side. Delete it on both sides by hand.
- **Formulas and formatting are not synced.** A sync moves values only: cell
  formatting, formulas, data validation, and conditional format rules are
  neither read nor copied. A formula cell in a synced column reads as its
  result, and a push to that cell replaces the formula with a literal value,
  so keep formula columns out of the projection or make them `sheet_owned`.
  Links are the one format a tab can ask a run to touch: `clear_links` takes
  off the link the sheet gives a URL when it is written. See [links](#links).
- **A changed key is not followed.** Editing a key cell reads as one row
  removed and another added: the old key is flagged as deleted, and the new
  one arrives as a new row. To change a key, edit it on both sides (on the
  sheet, `gdrives sheets-set` does it by lookup).
- **Structure is changed only when asked.** Columns are added only with
  `--add-missing` and deleted only with `--drop-extra`; a missing tab is created
  only by a run with `--apply`. A tab is never deleted.
- **Formula-like text is not escaped in local files.** A cell holding text
  such as `=1+2` is written to the local file exactly as it reads, since an
  added prefix would come back as a change on the next sync or push. Anyone who
  can edit the sheet can put such text in it, so open a pulled or synced
  `.csv` or `.tsv` file in a text editor, or import it as text, rather than
  opening it directly in a spreadsheet app, which may run it as a formula.
  For a copy meant for a spreadsheet app, use
  `gdrives sheets-get --escape-formulas`.

## A usage rule: no defaults in sheet-owned columns

If the local file is generated by a script, that script **must not give a
`sheet_owned` column a default value**. A default cannot tell "never filled
in" from "cleared on purpose on the sheet": when someone blanks the cell on
the sheet, the next generated file restores the default, and the default then
looks like a local edit that is pushed back over the deliberate blank. Leave
sheet-owned cells blank in generated output, or leave the column out of the
generated file and let the sync fold the sheet's values in.

## Commands and options

```bash
gdrives sheets-sync roster                         # Preview every sync tab of the target
gdrives sheets-sync roster --tab Members           # Preview one tab
gdrives sheets-sync roster --apply                 # Write sheet, local file, and base
gdrives sheets-sync roster --apply --adopt         # First sync: the local file wins
gdrives sheets-sync roster --apply --add-missing   # Add local columns the sheet lacks
gdrives sheets-sync roster --apply --drop-extra    # Delete sheet columns outside the projection
gdrives sheets-sync roster --apply --prefer local  # Resolve conflicts toward local
gdrives sheets-pull roster --apply                 # Replace local files for the pull tabs
gdrives sheets-push roster --apply                 # Replace the push tabs from local files
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/  # One-off dump, no config
gdrives sheets-widths <spreadsheet-id> --tab Members     # Column widths, as JSON for the config
```

| Option | Commands | Meaning |
|---|---|---|
| `TARGET` | all | A target name in the config. For `sheets-pull --all-tabs`, a Sheet URL, file ID, or Drive path instead |
| `--config PATH` | all | The config file. Default: `gdrives-sheets.json` in the working directory or a parent |
| `--tab TITLE` | all | Run only this tab; repeat for several. Default: every tab of the command's mode |
| `--apply` | all | Write. Without it the command only previews |
| `--adopt` | `sheets-sync` | First sync only: the local file wins every difference. Allowed without `--apply`, as a preview |
| `--add-missing` | `sheets-sync` | Add projection columns the sheet lacks |
| `--drop-extra` | `sheets-sync` | Delete sheet columns outside the projection, with their data (the preview counts their non-blank cells) |
| `--prefer local\|sheet` | `sheets-sync` | Resolve cell conflicts toward one side |
| `--all-tabs` | `sheets-pull` | Dump every tab, with no config. Needs `-o` |
| `-o`, `--output DIR` | `sheets-pull` | The directory for `--all-tabs` files |
| `--skip TITLE` | `sheets-pull` | With `--all-tabs`, leave this tab out; repeat for several |
| `--format csv\|tsv\|json` | `sheets-pull` | With `--all-tabs`, the file format. Default `csv` |
| `--bom` | `sheets-pull` | With `--all-tabs`, start each `.csv` or `.tsv` file with a byte-order mark. Not with `--format json` |
| `--slug` | `sheets-pull` | With `--all-tabs`, name each file by its title in lower case, with each run of other characters than letters and digits as one hyphen: `Form responses 1` is `form-responses-1` |

Refused before any request, with exit code 1: an unknown target, a `--tab`
the target lacks or that belongs to another mode (all of them listed at once),
a target with no tabs of the command's mode, `--all-tabs` combined with
`--tab` or `--config`, `--all-tabs` without `-o`, `--bom` with
`--format json`, and `-o`, `--skip`, `--format`, `--bom`, or `--slug` without
`--all-tabs`.

The report goes to stdout. The spreadsheet ID, the credential line, retry
notices, and error messages go to stderr.

**`sheets-widths`** prints a tab's column widths in pixels as a JSON object
by header name, ready to paste under the tab's `widths`. It takes a Sheet
URL, file ID, or Drive path, reads the first tab unless `--tab` names one,
and uses the read-only scope. A column with a blank header cell is left out.

```bash
gdrives sheets-widths <spreadsheet-id> --tab Members
```

```json
{
  "member_id": 90,
  "name": 220,
  "notes": 320
}
```

**Retries.** A request the API refuses for its rate limit, or fails with a
5xx where repeating it is safe, is sent again after a wait that doubles each
time, up to five attempts. Each wait is announced on stderr, so a run that
backs off does not look hung:

```
Sheets API returned 429; retrying in 4s (attempt 3 of 5)
```

**Requests.** A run lists the spreadsheet's tabs once, and reads each tab's
values once: a preview of five tabs makes one listing and five reads. A tab
with a declared `date` or `datetime` column is read a second time. A run
that creates a tab lists the tabs again after it, and a listing that fails
is an error for the tab that met it, after which the next tab asks again.

Tabs are independent: a tab that fails (a refusal, an API error, a failed
guard or read-back) is reported with its error, and the run goes on to the next
tab.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | In sync, or every change applied. For a preview: the run can go ahead, whether or not it found changes |
| 1 | An error: the config, a schema problem, an API error, a refusal, or a failed guard or read-back |
| 2 | Needs a person: conflicts, row flags, or held sheet values remain |

`--apply` with conflicts present still applies every change that does not
conflict, then exits 2. When one tab exits 1 and another 2, the run exits 1.

Exit code 2 has a second meaning: Typer, which parses the command line, exits
2 on a usage error of its own, such as a missing `TARGET`, an unknown option,
or a `--prefer` or `--format` value outside its choices. Such a run prints a
`Usage:` message and makes no request. Automation that acts on exit code 2
should check that the report was printed, or run the command with arguments it
knows are valid.

## Credentials and scopes

A preview reads with the read-only scope, so it never triggers a consent for
write access. It prints a credential line only when its authentication is
about to wait on an interactive consent or a token refresh, as every `gdrives`
command does.

With `--apply`, each command first prints one line to stderr naming the
credential its requests will use (an OAuth token, a service account and its
email, or Application Default Credentials) and whether an interactive consent
or a token refresh comes first, so a run waiting on a browser consent does not
look hung and a write is not made as an unexpected identity:

```
Credential: service account sync-bot@<project>.iam.gserviceaccount.com (key <config-dir>/service_account.json)
Spreadsheet ID: <spreadsheet-id>
```

On a preview that waits, the line follows the spreadsheet ID, since the
wait starts when the first request is made.

`sheets-sync --apply` and `sheets-push --apply` request the `spreadsheets`
write scope, cached in its own token file as for the other Sheets write
commands. A cached token with a broader grant, such as `drive`, serves it
with no new consent. `sheets-pull --apply` writes only local files and stays
on the read-only scope.

A consent needs a terminal. For a run started by a tool that captures
stdin, grant the access first with `gdrives login --scope sheets`, which
prints the consent URL and waits. See [OAuth setup](setup-oauth.md).

## Using it as a library

The commands are thin wrappers over `gdrives.sheets`, whose functions take a
Sheets service and plain values, so a caller with its own config or its own
record source uses the same engine:

```python
from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service
from gdrives.sheets import format_report, load_config, run_target

target = load_config().target("roster")
service = build_sheets_service(SHEETS_WRITE_SCOPES)
report = run_target(service, "<spreadsheet-id>", target, "sync", apply=True)
print(format_report(report))
raise SystemExit(report.exit_code)
```

`plan_tab` and `apply_tab` split a sync of one tab into its read-and-merge and
its writes; `pull_tab`, `push_tab`, and `pull_all_tabs` are the other modes.
The pieces underneath are exported too: `read_tab`, `merge`, `apply_plan`,
`verify`, `read_records`, and `write_records`.

### Stores

A run reads and writes the local side of a tab, and its base, through a
**store**. A config names files, and each becomes a `FileStore`, or a
`JsonEntryStore` for a tab's `entry` and a target's `base_file`. A caller
whose local side is none of these gives the tab a store of its own: typed
rows kept some other way, rows written back in an order of its choosing, or
a local side that is computed.

A store has a `label`, which reports and errors show where a path was shown,
and three methods:

| Method | Does |
|---|---|
| `exists()` | Says whether there is anything to read. False on the local side is refused for a sync or a push, and a pull creates it. False on the base means a first sync |
| `read()` | Returns `Records(columns, rows)`: the columns in order, and the rows as dicts of canonical cell strings |
| `write(columns, rows)` | Replaces what the store holds. Every row holds every column of `columns` |

`TabConfig(store=...)` gives a tab its local store, and
`Target(base_stores={title: store})` gives a tab's base one. A tab needs
`local` or `store`; `TabConfig.local` is None for a tab with a store and no
file. `MemoryStore` holds records in memory, for tests and for a caller that
saves them itself after the run.

This store is one tab of a JSON file that holds several, as
`{"Members": [...], "Summary": [...]}`. `JsonEntryStore` does this, typed and
atomically; the example shows the shape of a store of your own:

```python
import json
from pathlib import Path

from gdrives.sheets import Records, TabConfig, Target, encode_rows, run_target


class WorkbookTab:
    """One tab of a JSON workbook, as a store."""

    def __init__(self, path, name, columns):
        self.path, self.name, self.columns = Path(path), name, list(columns)
        self.label = f"{path}#{name}"

    def _tabs(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def exists(self):
        return self.name in self._tabs()

    def read(self):
        return Records(self.columns, encode_rows(self._tabs()[self.name], self.columns))

    def write(self, columns, rows):
        tabs = self._tabs() | {self.name: [dict(row) for row in rows]}
        self.path.write_text(json.dumps(tabs, indent=2) + "\n")


columns = ["member_id", "name", "status"]
tab = TabConfig(
    title="Members",
    key=("member_id",),
    store=WorkbookTab("data/workbook.json", "Members", columns),
)
target = Target(
    name="roster",
    spreadsheet="<spreadsheet-id>",
    base=Path("sheets-base/roster"),
    tabs=(tab,),
)
report = run_target(service, "<spreadsheet-id>", target, "sync", apply=True)
```

What a store has to keep to:

- **`read()` returns the same records each time within a run.** After a run
  changes the tab's structure, the local side is read again, and the run
  stops unless the second read equals the first. A store that computes its
  rows has to be deterministic between the two.
- **A store that parses cells when it writes declares its types in the tab's
  `schema`.** The merged rows are then checked before anything is written. A
  cell that fails such a store's `write` fails after the sheet was written.
  That is safe, since the local store is written before the base, the base
  has not advanced, and the next run sees the sheet as already in sync; the
  schema avoids it.
- **`write` may raise `ValueError` or `OSError`.** Both are reported for the
  tab, as a file error is.
- The config loader refuses two tabs that would write one file, or one entry
  of one. It checks the stores a config builds (`FileStore` and
  `JsonEntryStore`) only, so a caller that gives tabs stores of its own owns
  that check.

### A retry of your own

The functions of `gdrives.sheets` that call the API retry inside themselves:
up to five attempts, waiting 1, 2, 4, and 8 seconds between them, each with
up to a second of jitter. Reads and writes that overwrite a fixed range
(`pull_values`, `pull_many`, `update_values`, `clear_values`,
`batch_update_values`) retry a 429 and a 500, 502, 503, or 504. Calls that add
something (`append_values`, `batch_update_spreadsheet`) retry a 429 only,
since a 5xx may mean the change already landed.

**Remove a retry of your own around them.** Code written before 0.11.0 had to
wrap these calls. Left in place, a wrapper of five attempts now makes up to
25 calls, and a server that keeps failing holds the run for more than a
minute before the error reaches it. When the attempts run out, the
last `HttpError` is raised as it was, so error handling around the call needs
no change.

The waits are silent in the library. `retry_notices` reports them to a
callback, for every call inside its block:

```python
from gdrives.sheets import pull_values, retry_notices


def say(notice):
    print(
        f"got {notice.status}, waiting {notice.delay:.0f}s "
        f"before attempt {notice.attempt} of {notice.attempts}"
    )


with retry_notices(say):
    values = pull_values(service, "<spreadsheet-id>", "'Members'!A1:C")
```

`with_retry` is the same retry for a call of your own, such as a request the
library has no wrapper for.

### The cells a run wrote

A formatting pass of the caller's own runs after a run that wrote to the
sheet, over what it wrote. `TabReport.wrote_sheet` says whether to run it,
and `TabReport.applied`, an `ApplyResult`, says where:

| Field | Holds |
|---|---|
| `pushed_cells` | Each pushed cell as `(row, column)`, the row as it is after the run |
| `appended_rows` | The spreadsheet rows of the new rows |
| `appended_columns` | The columns written in each new row, so the cells of the new rows are every such row by every such column |

A whole-tab push writes every cell of the columns pushed, in rows 2 to
`replacement.after_rows + 1`, under the header in row 1.

To read grid data for such a pass, `pull_grid` reads one range under a
`fields` mask, which it requires: an unmasked read returns every property of
every formatted cell, and a tab formatted throughout returned 18.6 MB where
the read of its links returned 382 bytes.

### Hooks

`plan_tab`, `sync_tab`, `pull_tab`, `push_tab`, `push_rows`, and `run_target`
take three optional callables, for checks a schema cannot express. Each
returns a list of messages:

| Hook | Receives | Blocks a write | Runs |
|---|---|---|---|
| `validate` | the rows | yes | at every stage |
| `check` | a `CheckContext` | yes | at every stage, after `validate` |
| `warn` | a `CheckContext` | no | once, at the run's last stage, when no check found a problem |

The stages are `local` (the local rows of a sync or a push, before any
request), `merged` (a sync's merged result), and `sheet` (the rows a pull
read). A `CheckContext` holds:

| Field | Holds |
|---|---|
| `tab`, `stage` | The tab's title, and the stage |
| `rows`, `columns` | The rows, and the columns they hold |
| `projection` | The columns the sheet carries |
| `sheet_columns` | The sheet header's named columns. None at the `local` stage, and when the tab is missing or has no header |
| `adding`, `dropping` | The columns this run adds to the sheet and deletes from it |
| `extra_columns` | The sheet's columns outside the projection, less `dropping` |
| `plan` | The merge, at the `merged` stage |

At the `merged` stage the rows are the local side as it will be, so
`columns` is the local file's columns. At the `sheet` stage of a pull,
`columns` and `projection` are both the columns read. On a push,
`projection` is the columns written, in the local file's order.

```python
DECLARED = {"member_id", "name", "status"}


def declared(context):
    """Refuse a column on either side that nothing declares."""
    found = [*context.columns, *context.extra_columns]
    return [f"undeclared column {c!r}" for c in found if c not in DECLARED]


def folded(context):
    """Say which rows the run takes an edit from the sheet for."""
    return [f"{cell.key} changed on the sheet" for cell in context.plan.fold_cells]


report = run_target(
    service, "<spreadsheet-id>", target, "sync", check=declared, warn=folded
)
```

A message from `validate` or `check` is a problem: the tab is written
nowhere, and the run exits 1. A message from `warn` is printed under
`warnings` and changes neither what is written nor the exit code. The
commands take no hooks, so a caller with checks in code runs the library from
a command of its own.
