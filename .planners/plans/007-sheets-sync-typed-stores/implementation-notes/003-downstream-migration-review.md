# Migration review: replacing a downstream caller's hand-written sync code

- Written: 2026-09-27T10:47:39-07:00
- Code reviewed: the 0.11.0 release (`v0.11.0`, `f3a33ab`), as installed from
  PyPI. The modules cited below are identical to the tag.
- Scope: what a downstream project that upgraded from 0.5.8 to 0.11.0 can
  replace with the package, what it cannot, and what would change behavior if
  it did. No package code was changed.
- Evidence: the changelog, `docs/sheets-sync.md`, the package source, and one
  offline round trip of local CSV files through `read_records` and
  `write_records`. Nothing was run against the live API, so every statement
  about API behavior is from reading the code.

Notes are numbered `M1` to `M12` so later notes can cite, combine, or overrule
them. Line references are to the commit above.

This is a second caller, independent of the one in
`002-downstream-replacement-review.md`. That caller ran a keyed three-way sync
of its own; this one replaces whole tabs and never merges. Two findings were
reached by both and are cross-referenced below: M4 with `D6`, and M6 with
`D5`.

## The caller

The downstream project keeps a handful of native Google Sheets in step with
CSV files in a git repository. It wrote its own code against 0.5.8, which had
no Sheets API support, in four shapes:

- **Whole-tab pull.** Read several tabs of one spreadsheet through
  `spreadsheets.values.get` and rewrite one CSV per tab.
- **Whole-tab push.** Clear each managed tab and rewrite it from its CSV with
  `RAW` input, create the tabs that are missing, and set column widths by
  header name. Tabs the project does not manage are left alone.
- **Export and split.** Export a spreadsheet to `.xlsx` through the Drive API,
  read every tab with a typed reader, and write one CSV per tab.
- **File upload.** Put a rendered PDF in a Drive folder, replacing the content
  of the existing file so its ID and shared links stay the same.

It also carried its own authentication for write access: an OAuth flow with
the full `drive` scope and a token cached beside the package's read-only one.

## What checks out

- **The upgrade itself.** Every name the caller imported from 0.5.8 still
  resolves in 0.11.0 (`authenticate`, `build_drive_service`, `export_file`,
  `extract_drive_id`, and `escape_query_value`), and its test suite passes
  unchanged.
- **Whole-tab pull and push.** `sheets-pull` and `sheets-push` on `pull` and
  `push` tabs cover both, with the config's `widths`, `ensure_tabs`, and `bom`
  covering the rest. About three quarters of the caller's code has a direct
  equivalent.
- **Lookup by ID.** The caller found its spreadsheet by folder and name, so a
  rename in Drive broke it. A config's `spreadsheet` field takes a URL or an
  ID, which removes that.
- **A safer push.** The caller cleared a tab and then wrote it, in two
  requests, so a failure between them left the tab empty. One guarded write
  over the old and new extent, read back, is strictly better.
- **Previews.** The caller had no dry run. Preview by default, with `--apply`
  to write, is what it wanted.
- **Named tabs only.** The caller kept a list of tab names to skip, because a
  tab can share a name with a file that another process generates. A config
  pulls only the tabs it names, and `--all-tabs` has `--skip`.
- **Credential preflight.** The caller checked the token state before a run to
  warn that a blocking consent was coming. `describe_credentials` does this
  without a network call.

## Hazards in the current release

### M1. A token written by a caller under `gdrives_token_rw.json` is replaced

0.5.8 had no write scope, so a caller that needed one wrote its own token. The
natural name was `gdrives_token_rw.json`, and the natural scope was the full
`drive` scope, since creating a file in a shared folder needs it.

0.6.0 claimed that filename for the `spreadsheets` scope (`auth.py:57-60`).
`_token_covers` compares scope sets literally (`auth.py:125`), so a token
granted `drive` does not cover a request for `spreadsheets`, and
`authenticate_oauth` starts a consent and writes the new token over the old
one (`auth.py:217-236`). The caller's own code then loads a token that no
longer has Drive access and fails with a 403 on its first Drive call.

The hazard starts at the upgrade, before the caller changes any of its code:
one run of any Sheets write command is enough.

Wanted, in order of preference:

- Treat a broader grant as covering a narrower request. The Sheets API accepts
  the `drive` scope, and `drive` covers `drive.readonly`, so a small table of
  implied scopes would let the existing token be used as is.
- Refuse to overwrite a token file whose recorded grant is not a subset of the
  request. Write the new token under the derived name and say so.
- At the least, a changelog note and a line in the setup docs naming the
  filename as reserved.

### M2. No way to force a consent without a terminal

`_can_consent` requires `stdin` to be a TTY (`auth.py:141-143`,
`auth.py:195-197`). Without one, OAuth is skipped and authentication falls
through to a service account or ADC, which usually cannot see a folder shared
with a person.

A run started by a tool that captures `stdin`, with a person still watching
the output, has no way to start the flow. The caller had a `--login` flag for
this: start the local-server flow regardless, print the URL, and wait.

Wanted: a `force` argument on `authenticate_oauth` and the `build_*` helpers,
and a CLI entry point (a `login` command taking a scope name, or a `--login`
flag on the write commands) that also serves to re-grant a token whose refresh
has failed.

### M3. A preview that needs a consent is not announced

The credential line is printed only with `--apply` (`commands.py:415`,
`commands.py:540`). A preview uses the read-only token, and when that token
cannot be refreshed the preview blocks on a consent with nothing printed
first. This is the case the caller's preflight existed for.

Wanted: print the credential line whenever `describe_credentials` reports
`consent` or `refresh`, preview or not.

## Behavior that differs from the caller's

### M4. Delimited files are written with CRLF line endings

`write_values_csv` uses `csv.writer` with its default line terminator
(`files.py:60`), which is `\r\n`. The caller's files end lines with `\n`, as
most files in a git repository do.

In the round trip, three files of 17 to 80 rows and 9 to 14 columns came back
byte-identical to the originals once `\r\n` was replaced with `\n`, with
`bom=True` reproducing the byte-order mark. Line endings are the only
difference.

The cost is a whole-file diff the first time a pull or a fold changes a file,
which hides the real change in review, and a permanent one for a project that
normalizes line endings on commit.

Wanted: a `newline` tab field (`"lf"` or `"crlf"`) passed through
`write_records` to `write_values_csv`. Keeping the line ending an existing
file already has is an alternative, with the field deciding for a new file.
`\n` is the better default for a file meant to be committed, though changing
the default is a visible change for existing configs. The base snapshot
should follow the same setting.

Relation to the plan: a custom store (item 6) can write any line ending, so
this is reachable through the plan, but a store is a heavy answer for a
one-character setting.

`D6` reports the same finding from 24 files of another caller, and asks for
the same two options. Two independent callers with LF files is an argument
for LF as the default.

### M5. Dates are read as the sheet displays them

`read_tab` reads with `UNFORMATTED_VALUE` and `date_time_render=
FORMATTED_STRING` (`table.py:80-86`), so a date cell reads as its display
text. `from_cell` takes ISO 8601 only for `date` and `datetime`
(`cells.py:87-89`).

Two consequences:

- A column of sheet-native dates cannot be declared `date` or `datetime`
  unless the sheet happens to display ISO 8601. The timestamp column of a
  form-responses sheet does not, so its schema check fails on every row.
- A caller moving from export-and-split sees its files change. The typed
  reader wrote timestamps as `YYYY-MM-DDTHH:MM:SS.sss`; a values read writes
  them in the sheet's locale format.

Wanted: for a column declared `date` or `datetime`, read the serial number
and convert it to ISO 8601, so the local value does not depend on display
format or locale. One request can carry only one `dateTimeRenderOption`, so
this means either a second read of the typed columns or reading the whole tab
with `SERIAL_NUMBER` and converting only the declared columns. A number in an
undeclared column cannot be told from a date serial, so conversion has to be
by declaration, never by guess.

Relation to the plan: this belongs with items 1 to 3. The codec and the
normalized comparison assume a typed column's cell strings parse, and for
dates read from a sheet they do not.

### M6. Numbers lose their display format

A cell showing `50%` reads as `0.5`, and one showing `3.00` reads as `3`.
That is documented and right for a sync. The caller's pull read formatted
values, so for a column with a display format the two differ. None of its
columns were affected, so this is a note for the migration guide and not a
request. `D5` covers the same read from the side of a saved base, and asks for
the same guide section.

### M7. A tab is named by title only

A config's tabs are keyed by title. The caller's export-and-split code took
"the first tab", because for a form-responses sheet the title is incidental
and the position is what is stable. A renamed tab stops a config-driven pull.

Wanted: a way to name a tab by its `sheetId`, which survives a rename, with
the title kept for display. Naming a tab by position is the weaker
alternative, since a tab added in front changes it.

### M8. `--all-tabs` has no byte-order mark and no file naming option

`_dump_tab` calls `write_records` without `bom` (`sync.py:1049`), and the file
name is `safe_filename` of the title (`sync.py:1015`). The caller wrote a
byte-order mark, and named its files by a slug of the title (lower case, runs
of other characters replaced with a hyphen), which its later steps match by
pattern.

A config with one `pull` tab per file and `bom: true` covers this today, at
the cost of listing every tab. Wanted for the no-config form: `--bom`, and
`--slug` or a naming pattern.

### M9. Wholesale replacement in both directions takes two targets

The caller's workflow is: pull the tabs over the local files, review the diff
in git, and push back only when the local side was edited. A row deleted on
the sheet disappears from the file on the next pull.

`sync` mode does not fit, since a deleted row is flagged on every run and the
run exits 2 until a person deletes it on the other side too. `pull` and
`push` fit, but a tab has one mode. The way through is two targets that name
the same spreadsheet and the same files, one with `pull` tabs and one with
`push` tabs. The loader allows it, because a `push` tab is not counted as a
writer of its local file (`config.py:334`).

Wanted:

- The two-target pattern written up in `docs/sheets-sync.md`, since it is not
  obvious that the collision check permits it.
- Or a tab mode that allows both commands, with the direction chosen per run
  by which command is used.
- Applying row deletions in `sync` mode stays the larger answer. It is open
  from plan 006 and out of scope here.

## Missing features

### M10. File upload

The package reads, exports, downloads, renames, and moves. It cannot put a
local file in Drive. The caller needs to:

- find a file by name in a folder, warning when several share the name;
- create it when absent; and
- replace its content in place when present (`files.update` with a media
  body), so the file ID and every shared link survive.

Wanted: `gdrives upload LOCAL DEST`, with `--dry-run`, under the `drive` write
scope that `mv` already uses (`gdrives_token_drive.json`), and the helpers
behind it in a `gdrives.upload` module.

### M11. Creating a spreadsheet

A push creates a missing tab but not a missing spreadsheet. The caller
created a native spreadsheet in a given folder on its first run, then removed
the default `Sheet1` once its own tabs existed.

Wanted: a `sheets-create` command or an opt-in step of a push, taking a name
and a folder. Removing the default tab conflicts with the rule that a tab is
never deleted, so it should apply only to a spreadsheet created in the same
run, where the tab is known to be empty and nobody's.

This needs the `drive` scope, since `spreadsheets` alone cannot place a file
in a folder.

### M12. Capturing column widths

The config sets widths, but they have to come from somewhere. The caller
tuned them by hand on the sheet, read them back through `spreadsheets.get`
(`sheets(properties(title),data.columnMetadata.pixelSize)`), and pasted the
result into its source, keyed by header name.

Wanted: `get_column_widths(service, spreadsheet_id, tab)` in `structure.py`,
returning `{header name: pixels}`, and a CLI form that prints a `widths`
object ready for the config.

## Summary

| Note | Kind | In the plan's scope |
|---|---|---|
| M1 | Hazard: token file replaced | No |
| M2 | Feature: forced consent | No |
| M3 | Fix: announce a consent on preview | No |
| M4 | Feature: line endings | Reachable through item 6 |
| M5 | Feature: typed dates from a sheet | Items 1 to 3 |
| M6 | Documentation | No |
| M7 | Feature: tab by `sheetId` | No |
| M8 | Feature: `--all-tabs` options | No |
| M9 | Documentation, or a tab mode | No |
| M10 | Feature: file upload | No |
| M11 | Feature: spreadsheet creation | No |
| M12 | Feature: read column widths | No |

Only M4 and M5 touch this plan. M1 to M3 are authentication and want a plan of
their own, as do M10 and M11, which both add Drive writes. M1 is the one to
act on first, since it breaks a caller at upgrade time.
