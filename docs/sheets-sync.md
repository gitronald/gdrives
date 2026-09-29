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
- [Look before tidying a shared sheet](#look-before-tidying-a-shared-sheet)
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
| `spreadsheet` | yes | A Sheet URL, a bare file ID, or a Drive path (`My Drive/...`), resolved as the other `sheets-*` commands resolve theirs; a path must name a native spreadsheet, and a workbook uploaded as-is is refused |
| `tabs` | yes | An object of one or more tabs, by tab title |
| `base` | no | The directory for the base snapshots. Default: `sheets-base/<target>`. It may not be inside a `.gdrives/` directory, which is a cache |
| `base_file` | no | A `.json` file that holds every `sync` tab's base, as the entry named by the tab's title, instead of one CSV per tab under `base`. Contradicts `base`, and may not be inside a `.gdrives/` directory. See [a workbook in one JSON file](#a-workbook-in-one-json-file) |
| `input_option` | no | How pushed values are entered: `RAW` (the default) or `USER_ENTERED`. A target with a `sync` tab, or a tab that sets `typed_writes`, must use `RAW` |
| `hooks` | no | The default `hooks` of the target's tabs, hook by hook: a tab's own name for a hook wins. A push tab is not given the target's `transform`. See [hooks in the config](#hooks-in-the-config) |
| `defaults` | no | An object giving `link_urls`, `strict_schema`, `newline`, `render`, and `blank_keys` to every tab that does not set its own. See [defaults for every tab](#defaults-for-every-tab) |

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
| `schema` | all | Per column: `type` (`str`, the default, `int`, `float`, `bool`, `date`, or `datetime`), `required` (true or false), `allowed` (a list of permitted values), `present` (true or false), `strict` (true or false, `bool` and `date` only), `pattern` (a regular expression, `str` only), and `description` (text for a person, read by no check). Checked before anything is written. A `date` or `datetime` column is read from the sheet as ISO 8601. Or a string, `"module:attribute"`, naming a schema written in Python: **naming it runs it**, see [a schema in code](#a-schema-in-code). See [how cells are read and written](#how-cells-are-read-and-written) and [column presence and strict forms](#column-presence-and-strict-forms) and [a pattern for a column](#a-pattern-for-a-column), and [describing the columns](#describing-the-columns) |
| `bom` | all | `true` writes a byte-order mark at the start of a `.csv` or `.tsv` file, for spreadsheet apps that need one. Not for `.json` |
| `blank_keys` | all | `refuse` (the default) refuses a row with any blank key cell. `partial` refuses only a row whose every key cell is blank, for a composite key of which a component is absent on some rows. See [keys with a blank component](#keys-with-a-blank-component) |
| `newline` | all | The line ending a `.csv` or `.tsv` file is written with: `lf` (the default) or `crlf`. A `sync` tab's base follows it. `crlf` is not for `.json`, which is written with LF |
| `render` | all | How the tab's cells are read: `unformatted` (the default) reads a number as its value, and `formatted` reads every cell as the sheet displays it, so a cell showing `50%` reads as `50%`, not `0.5`. See [how cells are read and written](#how-cells-are-read-and-written) |
| `widths` | `sync`, `push` | Column widths in pixels, by header name. Set only on a run that wrote to the sheet |
| `clear_links` | `sync`, `push` | `true` leaves the cells a run writes with no link, where the sheet links a URL or a domain as it is written. Default `false`. See [links](#links) |
| `link_urls` | `sync`, `push` | An object with one field, `color`, a `#rrggbb` colour. After a write, each URL cell the run wrote is given a link to its own text, in that colour, not underlined. Contradicts `clear_links`. See [links](#links) |
| `typed_writes` | `sync`, `push` | `true` writes each column the `schema` declares `int`, `float`, `bool`, `date`, or `datetime`, less the key, as a value of that type instead of as text, so the sheet can sort and compute over it. Default `false`. Needs `render` `unformatted`. See [typed writes](#typed-writes) |
| `local_owned` | `sync` | Columns whose local value always wins. See [ownership](#ownership) |
| `sheet_owned` | `sync` | Columns whose sheet value always wins |
| `owns_rows` | `sync` | `true` makes the local file own the set of rows. Default `false` |
| `insert_above` | `sync` | One `{column: value}` or `{column: [values]}` pair: new rows go above the first sheet row whose column holds one of the values, instead of at the end. See [where new rows go](#where-new-rows-go) |
| `on_invalid` | `sync` | What a sync does with a sheet value that fails the `schema`: `refuse` (the default) writes nothing for the tab, and `hold` keeps that value out and writes the rest. See [holding invalid sheet values](#holding-invalid-sheet-values) |
| `bootstrap` | `sync` | How a tab with no base starts. `local` (the default) is the only value; `--adopt` is a flag, not a config value. See [the first sync](#the-first-sync) |
| `strict_schema` | all | `true` makes it a problem for a column of either side to have no `schema` entry, and `"local"` does so for the local side only. Default `false`. See [requiring every column to be declared](#requiring-every-column-to-be-declared) |
| `hooks` | all | Functions that run as the tab's `validate`, `check`, `warn`, and `transform`, each named as `"module:function"`. **Naming a function runs it**: see [hooks in the config](#hooks-in-the-config). No `transform` on a `push` tab |

The loader checks the whole file before any request is made and reports every
problem at once: unknown fields, a missing or empty key on a `sync` tab, key or
owned columns outside `columns`, a column both `local_owned` and
`sheet_owned`, a key column that is owned, a `sync`-only field on a `pull` or
`push` tab, `widths` on a `pull` tab, a malformed `insert_above` or schema,
`USER_ENTERED` on a target with a `sync` tab, and two tabs that would write the
same file (a local file or a base file, compared case-insensitively, across
the whole config), the same entry of a file, or a whole file and an entry of
it. A `schema` named as `module:attribute` is checked by its form alone when
the file is read; the checks that need its columns run when a command
resolves it, before its first request (see [a schema in code](#a-schema-in-code)).

Local columns outside `columns` are **carried**: they stay in the local file,
pass through a sync untouched, and never reach the sheet or the base. Sheet
columns outside `columns` are never read or written, unless `--drop-extra`
deletes them.

### Defaults for every tab

A target's `defaults` object gives five tab fields to every tab of the target
that does not set its own: `link_urls`, `strict_schema`, `newline`, `render`,
and `blank_keys`, as a target's `hooks` gives hooks. It is for a target whose
tabs share a rule, so the rule is written once.

```json
{
  "roster": {
    "spreadsheet": "My Drive/clubs/Roster",
    "defaults": {
      "strict_schema": "local",
      "link_urls": {"color": "#1155cc"},
      "newline": "crlf"
    },
    "tabs": {
      "Members": {"local": "data/members.csv", "key": ["member_id"]},
      "Dues": {"local": "data/dues.csv", "key": ["member_id", "year"], "newline": "lf"},
      "Summary": {"mode": "pull", "local": "data/summary.csv"}
    }
  }
}
```

- A tab that names a field has set its own, whatever the value: `"newline":
  "lf"` on `Dues` above keeps LF, though `lf` is also the built-in value.
- Each default is checked as the tab field is, in the same words, and a
  problem names the target's `defaults`: `target 'roster', 'defaults':
  'newline' must be one of ['crlf', 'lf'], not 'cr'`. Any other name is a
  problem.
- A default is given only to a tab it applies to, and skipped, without a note,
  for a tab it would contradict, so a default never makes a tab invalid that
  was valid without it. `link_urls` is not given to a `pull` tab or to a tab
  that sets `clear_links`, `newline` is not given to a tab whose `local` is a
  `.json` file, and `render` `formatted` is not given to a tab that sets
  `typed_writes`. Above, `Summary` reads with `newline` `crlf` and no
  `link_urls`, since a pull writes no links. A target's `hooks` follow the
  same rule: a push tab is not given the `transform`.
- Nothing else changes: a config with no `defaults` loads as it did.
- A `Target` built in code has no `defaults`. Its `TabConfig`s carry their own
  values, and the loader is the only place the defaults are applied.

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

### Requiring every column to be declared

A column with no `schema` entry is read and written as `str`. For a column
meant to hold a number or a date, that is a silent failure: a new column
added locally syncs as text until someone notices.

```json
"Members": {
  "mode": "sync", "local": "data/members.csv", "key": ["member_id"],
  "schema": {"member_id": {}, "paid": {"type": "bool"}},
  "strict_schema": true
}
```

`strict_schema: true` makes it a problem for a column of either side, less
one a run is dropping, to have no `schema` entry:

- The local file's columns, in and out of the projection (a carried column
  included), are checked at the `local` stage, before any request. A `sync`
  or a `push` with such a column is refused before the sheet is even read.
- The sheet's named header columns outside the projection are checked at the
  `sheet` stage, once the tab is read: for a `sync`, once the merge is done
  and before anything is written; for a `pull`, before the local file is
  written. A `sync`'s local-side check runs first, so a column both sides
  carry is reported once, at the `local` stage.
- A column a `sync` run drops with `--drop-extra` is not checked, since it
  will not be on the sheet after the run. A `pull`'s `exclude` names a column
  that is never read, and is not checked either.
- A key column needs an entry too, one line (`"member_id": {}`, `str` is the
  default): a key has a type as much as any other column does.

With `strict_schema`, `schema` may also name a column outside `columns`,
which is refused otherwise: a carried or excluded column has to be declared
somewhere. `"local"` gets the same allowance, since a carried local column is
one it checks.

#### Only the local side

On a shared sheet, collaborators may keep columns of their own outside the
projection, and `strict_schema: true` refuses every run because of them.
`strict_schema: "local"` checks the local side only: every local column must be
declared, and a sheet column outside the projection is left alone.

```json
"Members": {
  "mode": "sync", "local": "data/members.csv", "key": ["member_id"],
  "columns": ["member_id", "name", "paid"],
  "schema": {"member_id": {}, "name": {}, "paid": {"type": "bool"}},
  "strict_schema": "local"
}
```

What it checks depends on the mode:

- A `sync` runs the `local` stage as `true` does, and skips the `sheet` stage.
- A `pull` has no local file to check before the read. Under `true` it checks
  every named header column, read or not; under `"local"` it checks the
  columns it reads, which become the local file's columns, and leaves the other
  header columns alone. With no `columns`, that is every named header column
  less `exclude`, so the two agree.
- A `push` replaces the whole tab and has only a local side, so `"local"` and
  `true` check the same columns. It is accepted there, not refused, as a
  `blank_keys` is accepted in every mode.

Any other value is a config problem. `true` and `false` read and report as
before.

### Column presence and strict forms

Two more per-column checks, each opt-in and independent of the other and of
`strict_schema`.

`present: true` says a column must be in the header, not that its cells must
hold a value: a `present` column may still have blank cells, where `required`
governs blanks and says nothing about whether the column exists at all. A
`present` column the header lacks is reported once, whether or not the tab
has any rows:

```json
"Members": {
  "mode": "sync", "local": "data/members.csv", "key": ["member_id"],
  "schema": {"member_id": {}, "email": {"present": true}}
}
```

```
Members (sheet): column 'email' is declared present and the header lacks it
```

- Checked against the local file's columns at the `local` stage for a `sync`
  or a `push`, and against the sheet's header at the `sheet` stage for a
  `sync` or a `pull`. A `push` replaces the tab whole, so only the columns it
  writes are its "local side"; the sheet's own header, about to be
  overwritten, is not checked.
- A column `--add-missing` is about to add is not reported for the sheet.
- A `present` column always has a `schema` entry, so `exclude` naming one is
  already refused as naming any `schema` column is.

`strict: true` narrows a `bool` or `date` column to its one exact form:
`TRUE` or `FALSE` for `bool` (`true` and `TRUE ` fail it), and `YYYY-MM-DD`
for `date` (`20260927`, a valid ISO 8601 basic date, fails it). It is refused
on any other type, in the config and by `ColumnSchema` itself.

```json
"Members": {
  "mode": "sync", "local": "data/members.csv", "key": ["member_id"],
  "schema": {"member_id": {}, "paid": {"type": "bool", "strict": true}}
}
```

The check is part of `cell_problem`, so a value that fails it is a schema
problem like any other: it blocks the write, and `on_invalid: "hold"` holds a
sheet value that fails it, the same as any other invalid sheet value.
**Comparison is unchanged**: a `strict` column still compares `true` and
`TRUE` as one value, so a bare respelling is never folded or pushed. That
also means the merge's own check, which only runs on a value about to be
folded, never sees such a respelling; it is still reported, since nothing
would be written for it either way, and it always refuses the tab under
either `on_invalid` setting (there is nothing for `hold` to hold back).

### A pattern for a column

`pattern` is a regular expression that a `str` column's cells must match in
full, as `re.fullmatch` does: a cell that only contains a match fails, so
anchors are not needed. It is refused on any other type, and a string that is
not a regular expression is a config problem naming the column and giving the
compile error.

```json
"Members": {
  "mode": "sync", "local": "data/members.csv", "key": ["member_id"],
  "schema": {
    "member_id": {},
    "profile": {"type": "str", "pattern": "https://example\\.com/members/[0-9]+"}
  }
}
```

```
Members (local): key ('m1',), column 'profile': 'example.com/members/1' does not match the pattern 'https://example\\.com/members/[0-9]+'
```

A blank cell is never checked against the pattern: `required` is the rule for
blanks. The check is part of `cell_problem`, run after `required`, the type,
`strict`, and `allowed`, so a value that fails it is a schema problem like any
other: it blocks the write, and `on_invalid: "hold"` holds a sheet value that
fails it. In JSON the backslash is written twice, as above. The expression is
compiled once per distinct string, not once per cell.

### Describing the columns

A schema column takes a `description`, a string that says what the column
holds. No check of a cell reads it, and a config written without one loads as
before; it is documentation kept beside the rules it describes.
`gdrives sheets-schema` lists the columns a target's tabs declare, one row
each, with the type, the rules, and the description:

```bash
gdrives sheets-schema roster                      # CSV to stdout
gdrives sheets-schema roster --tab Members -o schema.csv
gdrives sheets-schema roster -o schema.json       # .csv, .tsv, or .json by the extension
```

```json
"Members": {
  "local": "data/members.csv", "key": ["member_id"],
  "schema": {
    "member_id": {"required": true, "description": "The member's number, kept for life."},
    "status": {"allowed": ["active", "closed"], "description": "Whether dues are being collected."}
  }
}
```

The columns are `tab`, `column`, `key` (`TRUE` for a column in the tab's
`key`), `type`, `required`, `present`, `strict`, `allowed`, `pattern`, and
`description`. A flag is `TRUE` or `FALSE`, as cells are written. `allowed` is
the permitted values as canonical cell strings in a JSON array, such as
`["active", "closed"]`, which reads back whatever a value holds, and is blank
when the column has no list. `pattern` and `description` are blank when unset.
The rows follow the config's order of tabs, and each tab's order of columns.

- Only the columns a tab's `schema` declares are listed. A column of `key` or
  `columns` that the `schema` leaves out is not, since a config does not know
  the columns of a tab that has no projection, and half a list would mislead.
  `strict_schema` is the way to require that every column is declared.
- The command reads the config alone: no request to Google, and no
  credential. **A `schema` given as `"module:attribute"` runs that module**,
  as a sync does (see [a schema in code](#a-schema-in-code)), so read a
  config from somewhere else before you run it. Hooks are not imported.
- With `-o` the file is written atomically, with LF line endings; without it
  the CSV goes to stdout. `--tab` limits the tabs, and `--config` names the
  config file. It exits 0, or 1 on a problem with the config, a tab, a
  schema, or the output path.
- In code, `schema_rows(target, tabs=None)` returns the rows as dicts, in the
  order of `gdrives.sheets.schema.SCHEMA_COLUMNS`, for a caller's own docs:
  give `write_records` the columns and the rows to write a file, or
  `format_schema(rows)` for the CSV text. A tab whose `schema` is a reference
  must be resolved first, and `resolve_schemas(target)` does that without
  the hooks that `resolve_target` also finds.

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

A preview that `--apply` would change ends, on stderr, with `Preview only;
rerun with --apply to write.` Stdout stays the report. The hint is left out
after an apply, after a preview with nothing to write, and after a preview
whose tabs are all left to a person or refused, since applying those writes
nothing. `TabReport.pending` and `SyncReport.pending` give the same answer
to a caller: True when a preview found a cell to write, a tab to create or
give a header, a column to add or delete, a first sync's base to save, or a
pull or push whose target differs. A preview whose only change is a column is
pending. A tab that stopped on an error or found problems is not, and neither
is the report of a run that applied.

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
   With `typed_writes` the pushed cells and the new rows go in one request,
   which lands whole or not at all.
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

**Files that hold what the sheet displays.** Where the local file and the
base are meant to hold the displayed text, set `render: "formatted"` on the
tab instead. Every read of the tab then returns what the sheet displays, so
the first preview reports no edit for such cells, and the sheet's numbers are
never folded into the file. The setting applies to a pull and a push too: a
pull writes the displayed text, and a push compares the file with it. A value
a run writes is still a literal string, so a cell the run changes holds the
text `75%` from then on, where it held the number 0.75. Declared date columns
still arrive as ISO 8601. A column declared `int` or `float` does not go with
`formatted` on a tab with number formats: see
[how cells are read and written](#how-cells-are-read-and-written).

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

**Blank keys.** The default is `blank_keys: "refuse"`: a row with data and a
blank key cell is refused on every side, the base included
(`base: blank key ['member_id'] in rows [2]`), where code that folded such a
row in, or let it through because it checked only repeated keys, gets a
refusal here. For a one-column key the refusal is right, and the rows have to
be fixed or removed, in the base too. For a composite key of which a component
can be absent, set [`blank_keys: "partial"`](#keys-with-a-blank-component).
Then a row is refused only when every key cell is blank, and a row that
leaves one component blank is matched by the components it has. A row whose
cells are all blank, key included, is skipped on either setting, as on a tab.
Whitespace alone counts as blank.

**Line endings.** A `.csv` or `.tsv` file is written with LF unless the tab
sets `newline: "crlf"`. A file is rewritten only when its records change, so
one with CRLF keeps it until a run changes the file, and changes once then.

**A header's surrounding whitespace.** A header cell is stripped on read,
leading and trailing whitespace only, on the sheet and in the local file
alike (and in a base, which is read as a local file is). Whitespace inside a
name stays: `First name` and `First  name` are two columns. So a sheet header
typed as `name ` matches a file column `name`, and no cell is reported for it.
What shows instead:

- A config that names the padded spelling (`"columns": ["name "]`) finds no
  such column, and the run stops with `has no column(s) ['name ']` and the
  header as read. Name the column without the padding.
- Two headers that differ only in padding read as one name, and the tab is
  refused with `header repeats ['name']`. Rename one on the sheet.
- A file whose header is padded is rewritten with a stripped header, the next
  time a run writes it (see the next cause for when that is).

**Quoting.** A file is written by the `csv` module's default rules: a field
is quoted only when it holds the delimiter, a quote, or a line break, and an
empty cell is written bare. A file from a writer that quotes every field, or
quotes the empty ones (`m2,""`), holds the same cells and different bytes.
Reading gives the same records, so a preview compares the cells and reports
nothing for it. The bytes change when a run writes the file: a
`sheets-sync --apply` that folds something into it, or a `sheets-pull --apply`
that finds the records changed (a pull leaves an unchanged file alone). It
then loses every quote it did not need, all at once, and a diff of the commit
shows every line changed. Rewrite the files once in a commit of their own,
before the first applied run, so that later diffs show only real edits. The
[steps below](#a-way-to-move-over) read each file through its store and write
it back, to show which files change.

**Typed columns.** A column with a declared type in the `schema` is
[compared by value](#typed-columns-compare-by-value): `3.0` and `3` in a
`float` column, `true` and `TRUE` in a `bool` column, are one cell. Code that
compared the text reports those cells as edits and folds or pushes them; this
one reports nothing, and rewrites neither side. The reverse holds for a column
the `schema` leaves undeclared: it is compared as text, so `3.0` against `3`
is an edit here, and code that compared numbers by value did not report it. If
a preview lists such cells, declare the column's type in the `schema`.

**Cleaning done in code.** Code that cleaned the cells it read (collapsing
spaces, rewriting links to one form) and kept the cleaned text in its files
moves the cleaning into a [`transform`](#cleaning-what-is-read). Without
it, each cleaned cell reads as a sheet edit and the text as read is folded
into the local file. With it, those cells are in sync, the sheet keeps its
text, and a pull writes the cleaned text.

**An existing layout.** `--config PATH` names a config kept anywhere, and a
target's `base` field names the directory of its base snapshots. A base is one
CSV per tab, `<base>/<tab title>.csv`, holding the projection columns, so a
base kept in another shape is converted to that, or deleted for a first sync.

### A way to move over

Four steps, each of which can be run before anything of the old code is
removed and before anything is written to the sheet.

**1. Run the old merge's test cases against `merge`.** The three-way merge is
a pure function: `merge(base, local, sheet, key, columns)` takes three lists of
records (dicts of cell strings) and returns a `MergePlan`, with no request
made and no file touched. A test table of the old code, with a base, a local
side, a sheet, and the expected result in each row, ports to calls of it:

```python
from gdrives.sheets import merge

case_key = ["id"]
case_columns = ["id", "name", "dues"]
case_base = [
    {"id": "m1", "name": "Ada", "dues": "10"},
    {"id": "m2", "name": "Bea", "dues": "10"},
    {"id": "m4", "name": "Dee", "dues": "10"},
]
case_local = [
    {"id": "m1", "name": "Ada", "dues": "15"},
    {"id": "m2", "name": "Bea", "dues": "12"},
    {"id": "m5", "name": "Eve", "dues": "10"},
]
case_sheet = [
    {"id": "m1", "name": "Ada L.", "dues": "10"},
    {"id": "m2", "name": "Bea", "dues": "20"},
    {"id": "m3", "name": "Cy", "dues": "10"},
    {"id": "m4", "name": "Dee", "dues": "10"},
]
case_plan = merge(case_base, case_local, case_sheet, case_key, case_columns)
print("push to the sheet:", [(c.key, c.column, c.local) for c in case_plan.pushes])
print("append to the sheet:", [row.key for row in case_plan.appends])
print("fold into the local side:", [(c.key, c.column) for c in case_plan.fold_cells])
print("new rows for the local side:", [row.key for row in case_plan.fold_rows])
print("conflicts:", [(c.key, c.column) for c in case_plan.conflicts])
print("row flags:", [(flag.key, flag.flag) for flag in case_plan.row_flags])
```

Read the plan as the [merge tables](#how-a-sync-merges) do. Here `m1` has a
local edit of `dues` (a push) and a sheet edit of `name` (a fold), `m2` has
both sides changing `dues` (a conflict, which neither side takes), `m5` is new
locally (an append), `m3` is new on the sheet (a new local row), and `m4` is
gone locally while the base has it (the flag `local_deleted`).
`case_plan.new_local` and `case_plan.new_base` are the two sides after the merge. The keyword arguments
are the tab's settings: `local_owned`, `sheet_owned`, `owns_rows`, `prefer`,
`blank_keys`, `types`, `schema`, and `carry`. A case that the old code merged
differently is a difference to look at before any file is touched.

**2. Read each committed file through its store, and write it back.** The
bytes should not change. A file that does changes when a run first writes it,
and it is better to see that now:

```python
from pathlib import Path

from gdrives.sheets import FileStore

scratch = Path("data/dues-check")
scratch.mkdir(parents=True, exist_ok=True)


def rewritten(store):
    """Whether reading a file through `store` and writing it back changes its bytes."""
    before = store.path.read_bytes()
    store.write(*store.read())
    changed = store.path.read_bytes() != before
    store.path.write_bytes(before)
    return changed


ours = FileStore(scratch / "ours.csv")
ours.write(["id", "name"], [{"id": "m1", "name": "Ada"}, {"id": "m2", "name": ""}])
other = FileStore(scratch / "other.csv")
other.path.write_bytes(b'"id","name"\r\n"m1","Ada"\r\n"m2",""\r\n')
rewrites = [
    rewritten(ours),
    rewritten(other),
    rewritten(FileStore(other.path, newline="crlf")),
]
print(rewrites)  # [False, True, True]
```

The first is `False`: a file the library wrote is stable. The second and third
are `True`: the other file is quoted throughout, and setting `newline="crlf"`
(the tab's `newline`) does not make the quotes unnecessary. What a difference
means:

- **Quotes** the writer did not need, or a bare empty cell where the file had
  `""`: see the quoting cause above. Rewrite the files in a commit of their own.
- **Line endings**: the file is CRLF and the store writes LF. Set the tab's
  `newline: "crlf"` (`FileStore(path, newline="crlf")` in code), or accept
  one rewrite.
- **A byte-order mark**: reading drops it, and the store writes one only with
  `bom=True` on the tab (`FileStore(path, bom=True)`). A file that starts with
  one and is written without it changes on its first write.
- **A padded header** is written stripped.

A `.json` file is rewritten in the library's own format, two-space indent and a
final newline, so a file formatted another way shows a difference there too,
with every value kept.

**3. Preview every target with both engines, and compare.** Run the old code
in the way it previews, and `gdrives sheets-sync TARGET` (no `--apply`) for
each target, against the same sheet and the same committed files. Every
difference between the two should have one of the causes above. The preview
writes nothing, to the sheet, the files, or the base.

```bash
gdrives sheets-sync roster              # every sync tab of the target, preview only
gdrives sheets-sync roster --tab Dues   # one tab
```

**4. Apply one small edit, and revert it.** Change one cell of one row on the
sheet, run `gdrives sheets-sync roster --tab Dues --apply`, and see it land
in the local file and the base and nowhere else. Then change the cell back on
the sheet and apply again: the file and the base return to what was committed
(`git diff` shows nothing). Do the same for a cell edited in the local file,
which pushes. A tab that survives both moves as the old code did is ready to
move over.

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

`clear_link_format` clears the link and nothing of the look. The API shows a
link in blue and underlined, and shows a cell that lost its link as plain text
again, but a cell whose colour and underline were written (as `set_url_links`
does, or a person did) keeps them: the text stays blue, or underlined, with no
link. `style=True` clears the underline and the text colour too, in the same
request, and leaves the bold and the rest of the format. It resets every cell
of the columns and rows it is given, linked or not, so pass the `rows` of the
cells you mean. It cannot change a look that comes from conditional
formatting or the theme, and it does not touch text format runs, which `runs`
clears.

A link that comes from a `HYPERLINK` formula is the cell's link like any
other, and `clear_link_format` removes it: the formula stays in the cell and
shows its label as plain text. To find such cells first, read with
`linked_cells(..., detail=True)`, whose `formula` is True for them (see
[auditing links](#auditing-the-links-of-a-tab)).

A link set as the cell's own format (`userEnteredFormat.textFormat.link`,
sent with `repeatCell`) takes, on plain text and on a cell whose link points
elsewhere. A link sent as a text format run over the whole text takes too,
and the API stores it as the cell's own link. A link sent in the request
that clears the cell's text format runs does not: the API drops it without
an error, so the runs are cleared by an earlier request, which may be in the
same batch.

### URL cells that keep a link

A tab meant to hold links wants the opposite: each cell whose whole text is
a URL holds a link to exactly that text, in a colour of its own, and not
underlined. A **URL cell** is one whose text, stripped, is `http://` or
`https://` followed by characters with no whitespace. A bare domain, a URL
inside a sentence, and an email address are not URL cells, and are never
read or written here.

`link_urls` on a sync or a push tab gives the URL cells a run writes their
link, and touches no other cell:

```json
"Members": {
  "local": "data/members.csv",
  "key": ["member_id"],
  "link_urls": {"color": "#1155cc"}
}
```

- A **push** checks the URL cells of the columns it pushed, after the
  write. A **sync** checks the cells it pushed and the rows it added.
- The check reads the tab's values, then one grid read bounded to the rows
  and columns of the URL cells. A run that wrote no URL costs the first read
  only.
- A cell is fixed when it holds no link, its link points somewhere other
  than its text, its text is not in the colour, its text is underlined, or
  it has text format runs. **The text is the authority**: a link that points
  elsewhere is pointed at the text, and the text is never changed.
- The fix sends one request, in which each cell gets its link, its colour,
  and `underline: false` under a mask of exactly those three properties, so
  the cell keeps its bold, its fill, and its font. A cell with text format
  runs has them cleared by a request of its own, earlier in the batch. The
  cells are then checked again, and one that is still wrong stops the run
  with a read-back error.
- A preview does not run the check. The report of an apply says how many
  cells were given a link: `URL cells given a link: 2`.
- It is refused on a pull tab, and together with `clear_links`.

As a library, `url_link_problems` returns each URL cell that breaks the rule
as a `UrlLinkProblem` (its row, its column, its text, and its `reasons`, from
`URL_LINK_REASONS`), and `set_url_links` fixes them and returns them. Both
take `columns` and `rows` as `clear_link_format` does, so a caller can pass
the cells an `ApplyResult` wrote:

```python
from gdrives.sheets import set_url_links, url_link_problems

for cell in url_link_problems(service, "<spreadsheet-id>", "Members", color="#1155cc"):
    print(cell.row, cell.column, cell.text, cell.reasons)

set_url_links(service, "<spreadsheet-id>", "Members", color="#1155cc")
```

The colour is compared with the one the API returns, a fraction per channel,
to the nearest of 255 steps. A tab with no problem gets no write.

### Checking a whole spreadsheet

`link_urls` formats the cells a run wrote. The cells a person typed or pasted
are never touched by it, and a sheet that people edit collects URL cells with
the API's blue and underline, or a link to somewhere else. `sheets-links`
sweeps them: it applies the rule above to every tab of a spreadsheet, or to
the tabs of a config target, and touches no other cell.

```bash
gdrives sheets-links <spreadsheet-id> --color "#1155cc"           # Check every tab
gdrives sheets-links roster                                        # A target's tabs, in their link_urls colour
gdrives sheets-links roster --tab Members --color "#1155cc" --apply  # Fix one tab
```

- **The source** is a spreadsheet (a URL, a file ID, or a Drive path) or a
  target name. With `--config`, it is a target. Without it, a URL or a path
  is a spreadsheet, and a bare word is a target when the config found from the
  working directory upward has a target of that name, and otherwise a
  spreadsheet ID. A directory with no config has only spreadsheets, and a
  config file that cannot be read is an error. The config's hooks and schemas
  are not imported.
- **The tabs** are every tab of a spreadsheet, or the `--tab`s. For a target
  they default to the tabs that set `link_urls`, or every tab of the target
  when `--color` is given. A tab without `link_urls` is left out with a note
  on stderr, and one named with `--tab` is refused unless `--color` is given.
  A pull tab has no `link_urls`, so it is swept when it is named or when
  `--color` is given.
- **The colour** is `--color` (`#rrggbb`), which a spreadsheet needs and which
  overrides a target's `link_urls` for every tab swept.
- **A preview** reads with the read-only scope, lists each URL cell that
  breaks the rule with its reasons, and exits 2 when it found any, with the
  usual hint on stderr. `--apply` prints the credential line, requests the
  `spreadsheets` scope, fixes each tab in one request and reads it back, and
  exits 0.
- **A tab that cannot hold a URL cell in a named column**, one with no header
  row or a header of blank cells (a notes tab, a chart tab, an empty tab), is
  reported as skipped and is no error. A tab that fails, because its header
  repeats a name, the API refuses a request, or the read-back finds a cell
  still wrong, is reported with its message and the sweep goes on to the next;
  the exit code is then 1. To leave such a tab out, name the tabs to check.

As a library, `sweep_url_links(service, spreadsheet_id, tabs=None, *, color,
apply=False)` returns a `LinkSweep`, whose `tabs` are `TabLinks` (the
`problems`, whether they were `applied`, and any `skipped` or `error`), with
`pending` and `exit_code`. `color` is one colour, or a mapping of tab title to
colour that has an entry for each tab swept. `format_sweep(sweep)` renders what
the command prints.

### Auditing the links of a tab

`linked_cells` tells where a link is and where it points. An audit that sorts
the cells into kinds needs the cell's text too, and to find the cells that
look linked and are not. Two additions read that:

- `linked_cells(..., detail=True)` reads under `LINK_DETAIL_FIELDS` and fills
  `LinkedCell.text`, the cell's displayed text (empty for an empty cell, which
  can hold a link), and `LinkedCell.formula`. Without `detail` the read and
  the result are those of `linked_cells` before, and `text` and `formula`
  keep their defaults, `""` and False.
- `styled_cells` returns each cell that looks like a link and holds none, as
  a `StyledCell`: its row, its column, its text, its `reasons`, from
  `LINK_STYLE_REASONS`, and `resettable`.

`formula` is True when the cell holds a link on the whole cell and its
formula calls `HYPERLINK`, in any case. A format link cannot tell this: the
API copies a formula's link into the cell's format once any other text
property is written to it. The rule reads the formula's text, so a
`HYPERLINK` inside a string, or inside a branch of another function, also
counts, and a formula that does not call it (`=A1`) does not.

A cell looks like a link when its text is underlined (`underline`) or shown
in the link colour (`color`), and it holds no link when it has no link on the
whole cell and none in its text format runs. The look is read from the
effective format, since a link's own underline and colour have no
user-entered property, and a theme colour from its resolved colour. The
colour is `#1155cc`, the one the API gives a link; a tab whose links use
another colour, such as its `link_urls`, names it with `colors=["#0b57d0"]`
(which replaces the default, so name both to find both), and `colors=[]`
finds underlines alone. Colours are compared to the nearest of 255 steps a
channel. A cell with no text is never returned, and styling inside text
format runs is not read. `resettable` is False when some of the look is not
set by the cell itself, and `clear_link_format(..., style=True)` will not
change it.

```python
from gdrives.sheets import linked_cells, styled_cells

kinds: dict[str, list[tuple[int, str]]] = {}
for cell in linked_cells(service, "<spreadsheet-id>", "Members", detail=True):
    if cell.formula:
        kind = "formula link"
    elif not cell.text:
        kind = "link on an empty cell"
    elif cell.targets == (cell.text,):
        kind = "link to its own text"
    else:
        kind = "link to somewhere else"
    kinds.setdefault(kind, []).append((cell.row, cell.column))
for cell in styled_cells(service, "<spreadsheet-id>", "Members"):
    kinds.setdefault("link styling with no link", []).append((cell.row, cell.column))
for kind, where in kinds.items():
    print(kind, where)
```

A bare domain (`example.com`) is a link to somewhere else by this rule: its
target is `http://example.com`. A link on part of a cell's text is sorted by
its targets, like any other. The cells of one kind can then be passed on:
`clear_link_format(..., rows=[...], style=True)` for the styling with no
link, and for the links a caller meant to be plain text.

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

## Look before tidying a shared sheet

A sheet that people edit is rarely in the order, or of the columns, that the
local side expects. Someone adds a row in the middle, and a collaborator keeps
a column of notes that no config knows. Both
[`reorder_rows`](#keeping-a-tab-in-order) and
[`strict_schema`](#requiring-every-column-to-be-declared) act on the sheet as
it is, so preview each against the live sheet before adopting either.

`reorder_rows` previews by default. Without `apply=True` it reads the tab,
writes nothing, and returns a `ReorderResult` that says what an apply would do:

```python
from gdrives.sheets import reorder_rows

dues_order = ["m1", "m2", "m3"]
dues_preview = reorder_rows(service, "<spreadsheet-id>", "Dues", ["id"], dues_order)
print(f"{dues_preview.moves} row(s) would move: {dues_preview.moved}")
print("already in order:", dues_preview.unchanged, "; written:", dues_preview.applied)
```

`moves` is the number of rows the order would move, `moved` their keys, and
`unchanged` is True when nothing would. A count near the number of rows means
the order is not the one the sheet is kept in, and moving that many rows on a
sheet others are editing is a decision to make with them. A count of one or two
is a row typed out of place.

`strict_schema` is previewed by the sync itself. With the setting in the
config, a `sheets-sync` without `--apply` reads the sheet and lists every
column the `schema` leaves undeclared, and writes nothing:

```bash
gdrives sheets-sync roster --tab Dues    # preview: the problems list the undeclared columns
```

```
  problems (1), so nothing is written:
    Dues (sheet): column 'note' has no schema entry, and the tab is strict_schema
```

The run exits 1 while there are problems. For a sheet where collaborators keep
columns of their own, `strict_schema: "local"` checks the local file's columns
only, and leaves the sheet's extra columns alone (see
[only the local side](#only-the-local-side)). Decide between `true` and
`"local"` by what that preview lists: columns the config should declare, or
columns that belong to other people.

## How cells are read and written

Both sides are compared as **canonical strings**.

**Reading.** A tab is read with unformatted values, and dates as their
formatted text. A number reads as its value, whatever its display format: a
cell showing `50%` reads as `0.5`, and one showing `3.00` reads as `3`. A
checkbox reads as `TRUE` or `FALSE`, a blank cell as an empty string, and a
formula cell as its result. Columns are found by header name, never by
position, and a header that repeats a name stops the run.

**Displayed values.** A tab with `render: "formatted"` is read as the sheet
displays it instead: a cell showing `50%` reads as `50%`, one showing
`$1,234.50` as `$1,234.50`, a date as its display text, and a checkbox as
`TRUE` or `FALSE`, as before. Every read a run makes of the tab follows the
setting: the read of the preview, the read before the writes, the read-back
after them, and the read of the header row a run makes to add, place, or
delete columns or to set widths. So the checks of a run compare one kind of
read with the same kind. Writes do not change: a value is written as a
literal string, which the sheet displays as written whatever the cell's
number format, so a cell a run wrote reads back as the text it wrote.

- A column declared `date` or `datetime` is still read a second time, as
  serial numbers, which does not depend on the setting, and arrives as ISO
  8601. Declaring a date column is the way to keep it out of the display's
  hands.
- A column declared `int` or `float` is read as displayed too, and a number
  displayed with a format, such as `1,234.50` or `50%`, does not parse as its
  type. It is a schema problem like any other, and a sync with
  `on_invalid: "hold"` holds it. A column of plain numbers displays its
  values and reads as before, so nothing refuses the combination, but a typed
  numeric column and `formatted` do not go together on a tab with number
  formats.
- A `bool` column reads `TRUE` or `FALSE` under either setting.
- `sheets-pull --all-tabs` takes no config and always reads unformatted.

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
every written value is. The sheet does not sort or format it as a date. A tab
that sets `typed_writes` writes its declared columns as values instead: see
[typed writes](#typed-writes).

**Schema.** A `schema` type is declared, never guessed. A cell that does not
parse as its declared type (`int` takes digits with an optional minus sign;
`bool` takes `TRUE` or `FALSE` in any case; `date` and `datetime` take ISO
8601), a blank `required` cell, or a value outside `allowed` is a problem, and
a run with any problem writes nothing.

### Typed writes

A tab with `typed_writes: true` writes each column its `schema` declares
`int`, `float`, `bool`, `date`, or `datetime` as a value of that type: a
number, a checkbox value, or a date. The people who use the sheet can then
sort it, filter it, and compute over it: `=A2+7` works on a pushed date, and
`=SUM()` counts a pushed number.

```json
"Dues": {
  "local": "data/dues.csv",
  "key": ["member_id"],
  "typed_writes": true,
  "schema": {
    "amount": {"type": "float"},
    "paid": {"type": "bool"},
    "due": {"type": "date"}
  }
}
```

- **Key columns stay text**, whatever their type. Keys are matched by their
  text, and `007` written as a number would come back `7` and match no local
  row. A column whose leading zeros matter is a `str` column.
- **Other columns stay text**: a `str` column, or one the `schema` does not
  declare, is written as a literal string, as without the field. So is a
  formula: a cell starting with `=` is text.
- **A date is written as the number the sheet holds for it**, which it shows
  as a date only under a date format. A date cell written that has no date or
  time format is given `yyyy-mm-dd`, or `yyyy-mm-dd hh:mm:ss` for a
  `datetime`, and one that has its own keeps it. Finding out costs a read of
  the date columns' formats on each run that writes a date. A row inserted by
  `insert_above` takes the format of the row above it, as any inserted row
  does, and gets the date format only when that row has none.
- **One request.** A sync sends its pushed cells, its new rows, and the date
  formats in one request, so the write lands whole or not at all. A push
  writes the whole tab in one request, as before.
- **The read-back compares by value.** A `float` written as `3.0` reads back
  `3`, and a `datetime` written as `2026-09-27 09:05` reads back
  `2026-09-27 09:05:00.000`; both match. The merge compared typed columns by
  value already (see [typed columns compare by value](#typed-columns-compare-by-value)),
  and the base keeps the text it had, so turning the field on changes nothing
  the next run compares.
- **Refused together with** `render: "formatted"`, which reads what a number
  format shows rather than the value written, and a target's
  `input_option: USER_ENTERED`, which is another way of entering values.
- **Values the sheet cannot hold exactly are refused** before anything is
  written: an `int` past 2^53, a `float` that is not finite, and a
  `datetime` with a time zone or finer than a millisecond. A value that does
  not parse as its type is a schema problem, as always.

**Cells that are already text.** Turning the field on rewrites no cell by
itself: a run writes only the cells that change, so a column pushed before
holds text until each cell is pushed again. `retype_columns` rewrites them
all at once. It previews by default, lists the text cells that parse as their
column's type and those that do not, and with `apply=True` rewrites the first
kind as values and leaves every other cell alone:

```python
from gdrives.sheets import ColumnSchema, retype_columns

schema = {
    "id": ColumnSchema("int"),
    "total": ColumnSchema("float"),
    "paid": ColumnSchema("bool"),
}
found = retype_columns(service, "<spreadsheet-id>", "Summary", schema)
print(f"{len(found.changes)} to rewrite, {len(found.unparsed)} left as text")
for cell in found.unparsed:
    print(cell.row, cell.column, cell.text, cell.problem)
retype_columns(service, "<spreadsheet-id>", "Summary", schema, apply=True)
```

Pass the tab's `key` as `key=[...]`, so the key columns stay text. Like a
sync, it reads the tab again before it writes and stops if anything changed,
and reads the rewritten cells back.

## What is never done

- **Rows are never deleted.** A row deleted on one side is flagged
  (`remote_deleted` or `local_deleted`) on every run, never removed from the
  other side. Delete it on both sides by hand.
- **Formulas and formatting are not synced.** A sync moves values only: cell
  formatting, formulas, data validation, and conditional format rules are
  neither read nor copied. A formula cell in a synced column reads as its
  result, and a push to that cell replaces the formula with a literal value,
  so keep formula columns out of the projection or make them `sheet_owned`.
  Links are one format a tab can ask a run to touch: `clear_links` takes
  off the link the sheet gives a URL when it is written, and `link_urls`
  gives a URL cell a link to its text, a colour, and no underline. See
  [links](#links). The other is the date format `typed_writes` gives a date
  cell it writes that has none: see [typed writes](#typed-writes).
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
gdrives sheets-links <spreadsheet-id> --color "#1155cc"  # Check the links of URL cells on every tab
gdrives sheets-schema roster -o schema.csv         # The target's schema columns, one row each
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
| `-o`, `--output FILE` | `sheets-schema` | Write the schema rows to this `.csv`, `.tsv`, or `.json` file. Default: CSV to stdout |
| `--skip TITLE` | `sheets-pull` | With `--all-tabs`, leave this tab out; repeat for several |
| `--format csv\|tsv\|json` | `sheets-pull` | With `--all-tabs`, the file format. Default `csv` |
| `--bom` | `sheets-pull` | With `--all-tabs`, start each `.csv` or `.tsv` file with a byte-order mark. Not with `--format json` |
| `--color #rrggbb` | `sheets-links` | The colour a URL cell's link text is to have. Needed for a spreadsheet; for a target, it overrides each tab's `link_urls` |
| `--slug` | `sheets-pull` | With `--all-tabs`, name each file by its title in lower case, with each run of other characters than letters and digits as one hyphen: `Form responses 1` is `form-responses-1` |

Refused before any request, with exit code 1: an unknown target, a `--tab`
the target lacks or that belongs to another mode (all of them listed at once),
a target with no tabs of the command's mode, `--all-tabs` combined with
`--tab` or `--config`, `--all-tabs` without `-o`, `--bom` with
`--format json`, and `-o`, `--skip`, `--format`, `--bom`, or `--slug` without
`--all-tabs`.

The report goes to stdout. The spreadsheet ID, the credential line, retry
notices, and error messages go to stderr.

**`sheets-links`** checks the URL cells of a spreadsheet's tabs, or of a
target's, and with `--apply` fixes them; see
[checking a whole spreadsheet](#checking-a-whole-spreadsheet). It exits 0 when
every URL cell follows the rule or was fixed, 2 when a preview found cells to
fix, and 1 on an error.

**`sheets-schema`** lists the schema columns of a target, from the config alone:
no request and no credential. See [describing the
columns](#describing-the-columns).

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
about to wait on an interactive consent or a token refresh, or when OAuth is
configured, no cached token serves, and there is no terminal for a consent (the
run then goes on as a service account or ADC, and the line ends with the reason),
as every `gdrives` command does.

With `--apply`, each command first prints one line to stderr naming the
credential its requests will use (an OAuth token, a service account and its
email, or Application Default Credentials) and whether an interactive consent
or a token refresh comes first, so a run waiting on a browser consent does not
look hung and a write is not made as an unexpected identity:

```
Credential: service account sync-bot@<project>.iam.gserviceaccount.com (key <config-dir>/service_account.json)
Spreadsheet ID: <spreadsheet-id>
```

When OAuth is configured but no consent could run for lack of a terminal, the
line ends with the reason, so a write is not made as an unexpected identity:

```
Credential: service account sync-bot@<project>.iam.gserviceaccount.com (key <config-dir>/service_account.json) (OAuth is configured, but no cached token serves these scopes and there is no terminal for a consent; run gdrives login)
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

`spreadsheet_id` may be None, and is then the target's own, when the config
names the spreadsheet by URL or ID: `run_target(service, None, target, "sync")`.
A Drive path is refused with a message that names `resolve_file_id`, since
resolving one needs a Drive service and the drive cache; a caller resolves it
with `gdrives.resolve.resolve_spreadsheet_id`, which also refuses a path that
names a file that is not a native spreadsheet, such as an uploaded `.xlsx`.
`target.spreadsheet_id` gives the same value to the other calls.

`format_report` takes the report of one tab as well, and `TabReport.exit_code`
is the code a run of only that tab exits with, so a caller of `pull_tab` or
`push_rows` prints and exits as the commands do:

```python
import sys

from gdrives.sheets import format_report, push_rows

one = push_rows(
    service,
    "<spreadsheet-id>",
    "Members",
    ["member_id", "name"],
    [{"member_id": "m1", "name": "Ada"}],
    key=["member_id"],
)
print(format_report(one))
if one.pending:
    print("Preview only; rerun with apply=True to write.", file=sys.stderr)
```

`plan_tab` and `apply_tab` split a sync of one tab into its read-and-merge and
its writes; `pull_tab`, `push_tab`, and `pull_all_tabs` are the other modes.
The pieces underneath are exported too: `read_tab`, `merge`, `apply_plan`,
`verify`, `read_records`, and `write_records`.

### Reading a tab into memory

`pull_records` reads a tab's rows into memory, checked as a pull checks them,
with no local file and nothing to apply. It takes what a `pull` tab takes:
`columns`, `key`, `blank_keys`, `schema`, `strict_schema`, `exclude`, `render`,
and `sheet_id`, and the hooks and `transform` that `pull_tab` takes. It is
`pull_tab` underneath, run on a `MemoryStore` with `apply=True`, so it refuses
what a `pull` tab refuses, and a combination a tab refuses (`exclude` with
`columns`, say) raises the same `ValueError` before any request.

```python
from gdrives.sheets import ColumnSchema, PullError, decode_rows, pull_records

types = {"id": "int", "total": "float", "paid": "bool"}
try:
    records = pull_records(
        service,
        "<spreadsheet-id>",
        "Summary",
        schema={name: ColumnSchema(kind) for name, kind in types.items()},
    )
except PullError as e:
    print(e)  # the report a `sheets-pull` of the tab would print
    print(e.report.exit_code)
else:
    typed = decode_rows(records.rows, types)
    print(typed[0])
```

The result is `Records`: `columns` in header order, and `rows` as dicts of
canonical cell strings, as a local file holds them. `decode_rows(records.rows,
types)` turns them into Python values (`int`, `float`, `bool`, `date`, and
`datetime` by the column's type, `None` for a blank cell), and any dataframe
library takes those rows with a schema of its own. The library depends on
none.

`PullError` is a `ValueError` whose `report` is the tab's `TabReport` and whose
message is `format_report(report)`. It is raised when `report.failed`: the pull
was refused (no such tab, no header row, no rows, an `exclude` name the header
lacks) or the API failed, which the report holds as its `error`, or the rows
have `problems` from the schema, `validate`, or `check`. What `warn` says fails
nothing; to read it, pass `report=TabReport(tab="Summary", mode="pull")` and
read its `warnings` afterwards.

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

`Target.base` is optional: when every sync tab has an entry in
`base_stores`, a target built in code needs no base directory at all (a
pull-only or push-only target needs neither, since those modes never read a
base). A sync tab with no `base` and no entry in `base_stores` is reported
with a `ValueError` naming the target and the tab, and the run goes on to the
next tab.

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

### Two store recipes

Each is a small class with `label`, `exists()`, `read()`, and `write(columns,
rows)`, used as a tab's `store`. A store may wrap another one, since it is
only asked for those four things.

**A store that sorts its rows on write.** It passes reads through, and sorts
the rows before handing them on. The sort key is the caller's, so an order
that no alphabetical sort gives (ranks, enumerations) is a function:

```python
from pathlib import Path

from gdrives.sheets import MemoryStore, TabConfig, Target, pull_tab, sync_tab


class SortedStore:
    """Keeps the rows of another store sorted by `sort_key`, whatever order they arrive in."""

    def __init__(self, inner, sort_key):
        self.inner, self.sort_key = inner, sort_key
        self.label = f"{inner.label} (sorted)"

    def exists(self):
        return self.inner.exists()

    def read(self):
        return self.inner.read()

    def write(self, columns, rows):
        self.inner.write(columns, sorted(rows, key=self.sort_key))


def by_id(row):
    return row["id"]


dues_held = MemoryStore(
    ["id", "dues", "paid"],
    [
        {"id": "m3", "dues": "30", "paid": "TRUE"},
        {"id": "m1", "dues": "10", "paid": "FALSE"},
    ],
)
dues_tab = TabConfig(title="Dues", key=("id",), store=SortedStore(dues_held, by_id))
dues_target = Target(
    name="roster",
    spreadsheet="<spreadsheet-id>",
    tabs=(dues_tab,),
    base_stores={"Dues": SortedStore(MemoryStore(), by_id)},
)
dues_first = sync_tab(service, "<spreadsheet-id>", dues_target, dues_tab, apply=True)
dues_second = sync_tab(service, "<spreadsheet-id>", dues_target, dues_tab, apply=True)
print([row["id"] for row in dues_held.read().rows])  # ['m1', 'm2', 'm3']
print(dues_first.wrote_local, dues_second.wrote_local)  # True False
dues_pulled = pull_tab(service, "<spreadsheet-id>", dues_tab, apply=True)
print(dues_pulled.wrote_local)  # True: the sheet's order is not the sorted one
```

The sheet's `m2` row folds in after the local rows, and the store puts it in
its place. What this costs, and what it does not:

- **A sync ignores row order.** Rows are matched by key, so a sorted local
  side stays in sync, and two rows in another order are not a change in the
  report. A sync writes the local side only when a run changes a cell or
  adds a row, so the sort happens on those runs. A file whose rows are out of
  order is left alone by a sync that has nothing to fold.
- **The base is compared in order.** A run saves the base again when its rows
  are in another order than the saved ones. Give the base the same wrapper, as
  above, or the run after a sort saves the base once for the new order.
- **A pull compares the lists of rows in order.** A tab in the sheet's order
  differs from a sorted local side, so an applied `pull_tab` writes the store
  on every run, and reports `the local store holds 3 rows (9 non-blank
  cells); it now holds 3`, with no row added, removed, or changed. The
  rewrite is harmless and leaves the same file. A pull that should be quiet
  wants a sort on the sheet (see [Keeping a tab in order](#keeping-a-tab-in-order)),
  or a store that does not sort.

**A store whose local side is computed, and written to two files.** Club
members are kept in a file of `id` and `name`, and their dues in a file of
`id`, `dues`, and `paid`. The tab shows one table of the four columns:
`read` joins the files on `id`, and `write` sends each column back to the file
it belongs to:

```python
from gdrives.sheets import FileStore, Records


class SplitStore:
    """A local side joined from two files on `id`, and written back to the two."""

    def __init__(self, people, dues):
        self.people, self.dues = people, dues
        self.label = f"{people.label} + {dues.label}"

    def exists(self):
        return self.people.exists() and self.dues.exists()

    def read(self):
        people, dues = self.people.read(), self.dues.read()
        owed = {row["id"]: row for row in dues.rows}
        extra = [name for name in dues.columns if name != "id"]
        rows = [
            {**row, **{name: owed.get(row["id"], {}).get(name, "") for name in extra}}
            for row in people.rows
        ]
        return Records([*people.columns, *extra], rows)

    def write(self, columns, rows):
        parts = ((self.people, ("id", "name")), (self.dues, ("id", "dues", "paid")))
        for store, belongs in parts:
            names = [name for name in columns if name in belongs]
            store.write(names, [{name: row[name] for name in names} for row in rows])


people = FileStore(Path("data/people.csv"))
owing = FileStore(Path("data/dues.csv"))
people.write(["id", "name"], [{"id": "m1", "name": "Ada"}, {"id": "m2", "name": "Bea"}])
owing.write(
    ["id", "dues", "paid"],
    [
        {"id": "m1", "dues": "10", "paid": "FALSE"},
        {"id": "m2", "dues": "20", "paid": "FALSE"},
    ],
)
standing = TabConfig(title="Standing", key=("id",), store=SplitStore(people, owing))
standing_target = Target(
    name="roster",
    spreadsheet="<spreadsheet-id>",
    tabs=(standing,),
    base_stores={"Standing": MemoryStore()},
)
joined = sync_tab(service, "<spreadsheet-id>", standing_target, standing, apply=True)
print(owing.path.read_text())  # id,dues,paid / m1,12,TRUE / m2,20,FALSE / m3,30,TRUE
```

The sheet's edit of `m1` and its new member `m3` fold into the local side:
`name` lands in `data/people.csv`, and `dues` and `paid` in `data/dues.csv`.
The store keeps to the rules above: `read` returns the same records each time,
and a file that cannot be written raises `OSError`, which the run reports for
the tab. A dues row with no member is not part of the local side, so a write
does not keep it.

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
callback, for every call inside its block, and with no callback it prints the
message the commands print (`print_retry`) to stderr:

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

`with retry_notices():` alone gives the commands' wording, `Sheets API returned
429; retrying in 1s (attempt 2 of 5)`, and `print_retry` is that function, for a
caller that wraps it or passes it as `on_retry`.

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
`warnings` and changes neither what is written nor the exit code. For the
commands to run a caller's hooks, the config names them: see
[hooks in the config](#hooks-in-the-config).

### Cleaning what is read

`pull_tab`, `plan_tab`, `sync_tab`, `run_target`, and `pull_all_tabs` take a
fourth hook, `transform`, which cleans the rows read from the sheet before
anything else looks at them. It is given the rows, as dicts of cell strings,
and returns them cleaned. Declared `date` and `datetime` columns already
hold ISO 8601. The checks, the other hooks, the merge, the report, the local
file, and the base see what it returns, never the text as read.

It returns one row for each row given, in the same order, each with exactly
the columns it was given, every value a string. It may change a key cell,
and the keys are checked again after it, so a transform that makes two keys
equal, or one blank, is refused as a tab holding them would be, and the local
side is left alone. `run_target` gives it the rows alone, to every pull and
sync tab, and refuses it on a push; a caller that needs the tab uses a
function per tab or closes over the title. `pull_all_tabs` gives it the
title as a second argument, since one function serves every tab.

This one collapses runs of spaces and tabs and trims each line of a cell,
keeping its line breaks:

```python
def collapse(rows):
    """Collapse runs of spaces and tabs, and trim each line; keep line breaks."""
    return [
        {
            column: "\n".join(" ".join(line.split()) for line in text.split("\n"))
            for column, text in row.items()
        }
        for row in rows
    ]


cleaned = run_target(service, "<spreadsheet-id>", target, "sync", transform=collapse)
```

On a sync the transform cleans the sheet's side of the merge, so a sheet cell
that differs from the local side and the base only by what the transform
removes is in sync. **The sheet keeps its text**: a cell the transform changed
and nobody edited is not pushed, and the local file and the base hold the
cleaned text. A real sheet edit is folded in cleaned. The re-read guard and
the read-back compare the tab as read, not as cleaned, and `insert_above`
matches the values as read. The local side is never transformed.

**A transform must be idempotent**: applied to its own result, it changes
nothing. A local edit is pushed as written. If the transform would change it,
the next run reads the cleaned form as a sheet edit and folds it into the
local file, once, and the two sides agree from then on. A run that adds or
deletes columns merges again after doing so, and calls the transform again,
so it must also return the same rows for the same input.

### A stock transform

`gdrives.sheets.transforms:trim_cells` does the cleaning above, so a caller
need not write it. It strips each cell and collapses each run of whitespace
inside a line to one space, keeping the line breaks of a cell that holds
several lines. It does to every cell what the merge does to a key, line by line
(`normalize_key`, the function `row_key` uses), and a key cell comes out with
the key it went in with. Name it in a config, for a tab or as a target's
default, as `"hooks": {"transform": "gdrives.sheets.transforms:trim_cells"}`
(see [hooks in the config](#hooks-in-the-config)), or pass it in code as
`transform=trim_cells`, from `gdrives.sheets`.

It differs from `row_key` in one respect: a key folds a line break into a
space, and a cell does not, since collapsing the lines of a note would change
what it says. A carriage return counts as whitespace, so `\r\n` becomes `\n`,
and a break at either end of a cell is stripped with the rest. It takes the
tab's title as an optional second argument and ignores it, so it also serves
`pull_all_tabs`.

**What it costs**: whitespace a collaborator typed stays on the sheet and is
never pushed away. The transform cleans the sheet's side of the merge only, so
a cell that differs from the local side and the base only by spacing is in
sync, and nothing is written to the sheet to tidy it. The local file and the
base hold the trimmed text. If the spacing matters on the sheet itself, tidy it with a script of
your own.

### Hooks in the config

**Running a command on a config runs the functions it names.** A `hooks`
field is code: `sheets-sync`, `sheets-pull`, and `sheets-push`, a preview
included, import each module it names and call its function with your
credentials in reach. Read a config from somewhere else before you run it, as
you would a script.

A tab's `hooks` names the functions that run as its `validate`, `check`,
`warn`, and `transform` (no `transform` on a `push` tab), so the commands run a
caller's checks. A target's `hooks` is the default for its tabs, hook by hook,
and a tab's own name for a hook wins:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "hooks": {"warn": "roster_checks:counted"},
    "tabs": {
      "Members": {
        "local": "data/members.csv",
        "key": ["member_id"],
        "hooks": {"validate": "roster_checks:known_status"}
      }
    }
  }
}
```

Each name is `module:function`. The module is imported as `import` finds it,
so it is installed where `gdrives` runs (a project's own package, under
`uv run gdrives ...`) or on `PYTHONPATH`. The config's directory is not
searched: a file beside the config is not found, and cannot shadow a module
of the same name. Here `roster_checks` holds:

```python
KNOWN_STATUSES = {"active", "closed"}


def known_status(rows):
    """Refuse a status the roster does not know."""
    return [
        f"{row['member_id']}: unknown status {row['status']!r}"
        for row in rows
        if row["status"] not in KNOWN_STATUSES
    ]


def counted(context):
    """Say how many rows a run saw."""
    return [f"{len(context.rows)} rows"]
```

Reading a config imports nothing: `load_config` checks only that each name
has the form `module:function`, and a config with no `hooks` imports nothing
at any point. A run finds the names before its first request, and every
module that does not import, name it lacks, and value that is not a function
is listed at once, the command exiting 1. `resolve_hooks(target)` does the
same from code, and returns the functions by tab and hook.

A hook named in the config takes and returns what the same hook given in code
does ([hooks](#hooks), [cleaning what is read](#cleaning-what-is-read)), with
one difference: whatever it raises, or a return that is not a list of
messages (for a `transform`, not a list of rows), is the tab's error, naming
the hook and the function, and the run goes on to the next tab. `run_target`
runs a tab's config hooks too, before the ones given in code: the messages
are the config hook's, then the code hook's, and a config `transform` runs
first, the code's cleaning what it returns.

### A schema in code

**Running a command on a config runs the schema module it names**, as it runs
the hooks. A `schema` given as `"module:attribute"` is code: `sheets-sync`,
`sheets-pull`, and `sheets-push`, a preview included, import the module, and
call its function when the attribute is one. `sheets-schema` runs it too.

A caller that declares its columns in Python, for its own checks and its own
docs, names that declaration in the config instead of restating it as JSON:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "base": "sheets-base/roster",
    "tabs": {
      "Members": {
        "local": "data/members.csv",
        "key": ["id"],
        "schema": "clubtools.schema:MEMBERS",
        "strict_schema": true
      }
    }
  }
}
```

The attribute is a mapping of column name to `ColumnSchema`, or a function
that is given the tab's title and returns one, for a module that serves
several tabs. Here `clubtools/schema.py` holds both:

```python
from gdrives.sheets import ColumnSchema

MEMBERS = {
    "id": ColumnSchema("str", required=True),
    "name": ColumnSchema("str", required=True),
    "joined": ColumnSchema("date", strict=True),
    "paid": ColumnSchema("bool"),
}

DUES = {
    "id": ColumnSchema("str", required=True),
    "amount": ColumnSchema("float", required=True),
}


def by_title(title):
    """The schema of the tab titled ``title``: ``"schema": "clubtools.schema:by_title"``."""
    return {"Members": MEMBERS, "Dues": DUES}[title]
```

The module is found as a hook's is: as `import` finds it, installed where
`gdrives` runs or on `PYTHONPATH`, and never in the config's directory.

Reading a config imports nothing: `load_config` checks only that the string
has the form `module:attribute`. The checks a `schema` object gets when the
file is read, and that need its columns, wait for the run: each column's
fields, a column outside `columns` (unless `strict_schema`), and a column
that `exclude` names. A command resolves the reference before its first
request, in the same pass that finds the hooks, and lists every problem at
once, each in the words the loader uses for a `schema` object, naming the tab
and the reference, and exits 1. A module that does not import, an attribute
it lacks, a value that is neither a mapping nor a function, a function that
raises or returns something else, a column name that is not a string, and a
value that is not a `ColumnSchema` are problems too.

From code, `resolve_target(target)` resolves every tab's reference and checks
every hook name the same way, and returns the target with each tab's
`schema` filled; `resolve_tab(tab)` does it for one tab. `run_target`,
`plan_tab`, `sync_tab`, `pull_tab`, and `push_tab` resolve a tab they are
given, so a tab built in code may name its schema too:
`TabConfig("Members", Path("data/members.csv"), key=("id",),
schema_ref="clubtools.schema:MEMBERS")`. It takes `schema` or `schema_ref`,
not both. Until it is resolved, a tab has no types: its `types` and
`local_store`, and a `base_file` entry for it, raise rather than read the
file untyped.
