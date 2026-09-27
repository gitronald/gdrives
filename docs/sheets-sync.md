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
- [The first sync](#the-first-sync)
- [Pull and push](#pull-and-push)
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
| `input_option` | no | How pushed values are entered: `RAW` (the default) or `USER_ENTERED`. A target with a `sync` tab must use `RAW` |

### Tab fields

| Field | Modes | Meaning |
|---|---|---|
| `local` | all (required) | The local file. Its extension picks the format: `.csv`, `.tsv`, or `.json` |
| `mode` | all | `sync` (the default), `pull`, or `push` |
| `key` | all | The key columns that identify a row: a list of one or more names. Required for `sync`; optional for `pull` and `push`, where it makes the preview report rows by key |
| `columns` | all | The **projection**: the columns the sheet carries. Default: every column of the local file. The key, owned, `schema`, `insert_above`, and `widths` columns must be in it |
| `schema` | all | Per column: `type` (`str`, the default, `int`, `float`, `bool`, `date`, or `datetime`), `required` (true or false), and `allowed` (a list of permitted values). Checked before anything is written |
| `bom` | all | `true` writes a byte-order mark at the start of a `.csv` or `.tsv` file, for spreadsheet apps that need one. Not for `.json` |
| `widths` | `sync`, `push` | Column widths in pixels, by header name. Set only on a run that wrote to the sheet |
| `local_owned` | `sync` | Columns whose local value always wins. See [ownership](#ownership) |
| `sheet_owned` | `sync` | Columns whose sheet value always wins |
| `owns_rows` | `sync` | `true` makes the local file own the set of rows. Default `false` |
| `insert_above` | `sync` | One `{column: value}` or `{column: [values]}` pair: new rows go above the first sheet row whose column holds one of the values, instead of at the end |
| `bootstrap` | `sync` | How a tab with no base starts. `local` (the default) is the only value; `--adopt` is a flag, not a config value. See [the first sync](#the-first-sync) |

The loader checks the whole file before any request is made and reports every
problem at once: unknown fields, a missing or empty key on a `sync` tab, key or
owned columns outside `columns`, a column both `local_owned` and
`sheet_owned`, a key column that is owned, a `sync`-only field on a `pull` or
`push` tab, `widths` on a `pull` tab, a malformed `insert_above` or schema,
`USER_ENTERED` on a target with a `sync` tab, and two tabs that would write the
same file (a local file or a base file, compared case-insensitively, across
the whole config).

Local columns outside `columns` are **carried**: they stay in the local file,
pass through a sync untouched, and never reach the sheet or the base. Sheet
columns outside `columns` are never read or written, unless `--drop-extra`
deletes them.

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
the local file, the new rows for the sheet, the conflicts, the overrides, and
the row flags, each cell with its before and after values. A value that came
from the sheet or a file is shown with control characters escaped, so it cannot
drive the terminal.

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
"edited locally".

**Commit the base alongside the local file.** Everyone who syncs the same
target then shares one base, and a clone of the project syncs correctly on its
first run. A base that is lost or out of date makes a sync misread which side
changed, so do not keep it in an ignored or cache directory (the loader refuses
a base inside `.gdrives/`).

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

## Pull and push

**`pull`** replaces the local file with the tab. The tab is read over the
configured `columns` (every named header column by default) and checked
against the schema. A missing tab, a tab with no header row, and a tab with no
data rows are refused, and the local file is left alone. A missing local file
is created. The preview compares the tab with the current local file: row
counts, a drop in the row count, and, with a `key`, the rows added, removed,
and changed. An unchanged file is not rewritten. A pull writes only local
files; it never writes to the sheet.

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

**`sheets-pull --all-tabs`** dumps every tab of a spreadsheet with no config:

```bash
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/                 # Preview
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --apply         # One .csv per tab
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --format json --apply
gdrives sheets-pull <spreadsheet-id> --all-tabs -o out/ --skip Notes --apply
```

The values of every tab are read in one request. Each file is named from the tab title (path
separators and control characters replaced) plus the extension. `--skip TITLE`
(repeatable) leaves a tab out, which protects a local file that shares a name
with a tab but is produced elsewhere. A `--skip` title the spreadsheet lacks,
and two tabs whose file names collide, are refused before any values are
read. A tab with no values or no header row is skipped and reported, never
written as an empty file.

## How cells are read and written

Both sides are compared as **canonical strings**.

**Reading.** A tab is read with unformatted values, and dates as their
formatted text. A number reads as its value, whatever its display format: a
cell showing `50%` reads as `0.5`, and one showing `3.00` reads as `3`. A
checkbox reads as `TRUE` or `FALSE`, a blank cell as an empty string, and a
formula cell as its result. Columns are found by header name, never by
position, and a header that repeats a name stops the run.

**Local files.** In a `.csv` or `.tsv` file every cell is a string, so leading
zeros, booleans, and dates stay exactly as written. A `.json` file is an array
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
  formatting, formulas, hyperlink styling, data validation, and conditional
  format rules are neither read nor copied. A formula cell in a synced column
  reads as its result, and a push to that cell replaces the formula with a
  literal value, so keep formula columns out of the projection or make them
  `sheet_owned`.
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

Refused before any request, with exit code 1: an unknown target, a `--tab`
the target lacks or that belongs to another mode (all of them listed at once),
a target with no tabs of the command's mode, `--all-tabs` combined with
`--tab` or `--config`, `--all-tabs` without `-o`, and `-o`, `--skip`, or
`--format` without `--all-tabs`.

The report goes to stdout. The spreadsheet ID, the credential line, and error
messages go to stderr.

Tabs are independent: a tab that fails (a refusal, an API error, a failed
guard or read-back) is reported with its error, and the run goes on to the next
tab.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | In sync, or every change applied. For a preview: the run can go ahead, whether or not it found changes |
| 1 | An error: the config, a schema problem, an API error, a refusal, or a failed guard or read-back |
| 2 | Needs a person: conflicts or row flags remain |

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

`sheets-sync --apply` and `sheets-push --apply` request the `spreadsheets`
write scope, cached in its own token file as for the other Sheets write
commands. `sheets-pull --apply` writes only local files and stays on the
read-only scope.

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
Each takes an optional `validate` callable (rows in, a list of problem messages
out) for checks a schema cannot express. The pieces underneath are exported
too: `read_tab`, `merge`, `apply_plan`, `verify`, `read_records`, and
`write_records`.
