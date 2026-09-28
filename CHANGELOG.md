# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- An `exclude` tab field for `pull` tabs, and `TabConfig.exclude`: columns to leave out of a pull, by header name, where `columns` names the ones to keep. Their values never reach the local file, a hook, or a report, and a column added on the sheet later is still pulled. It is refused together with `columns`, on a `sync` or `push` tab, and when it names a `key`, `schema`, or `widths` column. A name in `exclude` that is not one of the tab's header columns refuses the pull and leaves the local file alone, listing every such name, since a sensitive column renamed on the sheet would otherwise be written to disk.
- `reorder_rows` in `gdrives.sheets` puts a keyed tab's rows in an order the caller computes, given as the rows' keys, by moving whole rows with `moveDimension`, so each row keeps its formatting and the cells of columns that were never read. It sends the fewest single-row moves in one request, keeps entirely blank rows in their positions, and previews unless given `apply=True`. An order that leaves out a row of the tab, names a key the tab lacks, repeats a key, or holds a key of the wrong length is refused, with every problem listed and nothing written. With `apply=True` the tab is read again before the moves (`SheetChangedError` when it changed) and read back after them (`ReadBackError` when a row lost a cell or is out of place). It returns a `ReorderResult`: `moves`, `moved`, `unchanged`, and `applied`. The guide has a section on keeping a tab in order.
- `JsonEntryStore` in `gdrives.sheets` reads and writes one named entry of a JSON file shaped `{"Tab A": [rows], "Tab B": [rows]}`, for a workbook kept in one file. The entry is read as `read_records` reads a `.json` file and written as `write_records` writes one, typed by `types=`. A write reads the file again, replaces the entry or adds it at the end, keeps every other entry's value and place, and replaces the file atomically, so a rewrite that changes nothing leaves a file in the library's format byte-for-byte the same. A file that is not a JSON object, or that names an entry twice, is refused and never overwritten.
- An `entry` tab field in `gdrives-sheets.json`, for every mode, and `TabConfig.entry`: the tab's local side is that entry of its `.json` `local` file. A `base_file` target field keeps every `sync` tab's base as the entry named by the tab's title in one `.json` file, typed by the tab's schema, instead of one CSV per tab; it is refused together with `base` and inside a `.gdrives` directory. The loader now keys the files tabs write by path and entry: two tabs may write two entries of one file, and the same entry twice, or a whole file beside an entry of it, is refused. The guide has a section on a workbook kept in one JSON file.
- A `render` tab field, for every mode, `unformatted` (the default) or `formatted`, and `RENDERS`, which names the two. With `formatted` a tab is read as the sheet displays it, so a cell showing `50%` reads as `50%` where it read `0.5`, and a local file holding the displayed text is in step with the tab on its first run. Every read of a run follows the setting: the preview, the re-read guard and the read-back of a sync or a push, and the header read of a run that adds, places, or deletes columns or sets widths. Writes are unchanged literal strings, which read back as written. A column declared `date` or `datetime` is still read as serial numbers and arrives as ISO 8601; a displayed number that does not parse as its declared `int` or `float` is a schema problem. `TabConfig.render` holds the setting, `read_tab`, `parse_tab`, and `push_rows` take `render=`, and `Table.render` records the setting a tab was read with, which `apply_plan`'s guard and read-back use. `add_columns`, `place_columns`, `delete_columns`, and `set_column_widths` take `render=` for their header read. A run that does not set it sends the requests it sent before.
- `CredentialInfo` from `gdrives.auth` gains `oauth_client`, `terminal`, `consent_skipped`, `service_account`, and `passed_over`, so a caller can say which credential `describe_credentials()` chose and why, without calling any private helper. `passed_over` lists each cached OAuth token file that was looked at and not used, as a `PassedToken(path, reason)`, `reason` one of `PASSED_REASONS`: `missing`, `scopes`, `unreadable`, or `invalid`. The new fields are excluded from `CredentialInfo` equality and hashing, so a `CredentialInfo` built the old way, and the string it prints, are unchanged.
- A `transform` hook on `pull_tab`, `plan_tab`, `sync_tab`, `run_target`, and `pull_all_tabs` cleans the rows read from the sheet before anything else sees them: the checks, `validate`, `check`, `warn`, the merge, the report, the local file, and the base get what it returns, never the text as read. It returns one row for each row given, in order, each with exactly the columns it was given and every value a string, and anything else is refused, saying which. The keys are indexed again after it with the tab's `blank_keys`, so a transform that makes two keys equal, or one blank, is refused and the local side is left alone. On a sync, a sheet cell that differs from the local side only by what the transform removes is in sync and is not pushed, so the sheet keeps its text; the re-read guard and the read-back compare the tab as read. A transform must be idempotent: a local edit it would change is folded back in cleaned, once, on the next run. `pull_all_tabs` gives the hook the tab's title as a second argument, and `run_target` refuses it on a push. `Transform` and `TitledTransform` in `gdrives.sheets.sync` are its two types, beside `Validate` and `Check`, and `TabPlan.transform` and `TabPlan.seen` hold the hook and the rows the merge compared. A run with no transform sends the requests it sent before.

### Changed

- A cell of a column declared `datetime` that is read from its serial number now always arrives as `YYYY-MM-DD HH:MM:SS.mmm`, with three digits of milliseconds even for a whole second: `2026-02-03 12:00:00.000`, where it was `2026-02-03 12:00:00` beside `2026-02-03 13:11:57.926000` in the same column. Serials are rounded to the millisecond already, so nothing is lost. **This changes output people have committed.** A sync compares typed columns by value, so a base or local file in the old form is in sync with the new read and is not rewritten. A pull compares text, so the first pull of a tab with a `datetime` column after upgrading rewrites each cell whose fraction was zero, once. Text cells in the column (a value pushed as a literal string) read as their text, as before, and `to_cell` is unchanged, so a `datetime` built in code keeps its microseconds.
- `Target.base` is optional, `None` by default, for a target built in code whose every sync tab has an entry in `base_stores`; a pull-only or push-only target needs neither, since those modes never read a base. `Target.tabs` now defaults to `()` too, to keep `base` before it with a default. `Target.base_path(tab)` raises `ValueError` naming the target and the tab when `base` is `None` and the tab has no entry in `base_stores`; it is reached, and can raise, only through `Target.base_store(tab)` for such a tab, in a sync's per-tab error handling, before the tab's sheet values are read. A config always sets `base`, so a config-built target is unaffected.

## [0.13.0] - 2026-09-27

### Added

- `insert_point` in `gdrives.sheets` returns the spreadsheet row a merge plan's new rows go above, for a table and an `insert_above` pair. It is pure, and `apply_plan` and the sync preview both use it. `TabReport.insert_row` and `TabReport.last_row` hold the result for a sync tab with `insert_above` and new rows, and the text report says where the rows go: `above row 5` or `after row 40` in a preview, and the rows written after an apply.
- `place_columns` adds each column a header lacks at its place in a wanted order, in one request, and returns the columns added. `add_columns` takes `after=` to add columns directly after a named column.
- A `newline` tab field in `gdrives-sheets.json`, `lf` (the default) or `crlf`, sets the line ending of a tab's `.csv` or `.tsv` local file and of its base. `write_records` and `write_values_csv` take `newline=`, one of `NEWLINES`.
- `encode_rows` and `decode_rows` in `gdrives.sheets` turn rows of typed values into records of canonical cell strings and back, for rows that never touch a file. `encode_rows` refuses a nested value and a column outside `columns`, and `decode_rows` lists every cell that does not parse as its declared type, each by row position and column.
- A column type can be declared by its class as well as its name: `from_cell`, `decode_rows`, and `write_records(types=)` take `str`, `int`, `float`, `bool`, `date`, and `datetime`. `column_type` returns the name for a name or a class, and `ColumnSchema.of` builds a schema from either. `ColumnSchema.type` still holds the name, and the config file takes names only.
- `read_tab` takes `types=`, and `parse_tab` takes `types=` and `serials=`. `pull_serials` reads a tab's declared date columns as serial numbers in one request, and `serial_to_cell` converts a serial to the ISO 8601 cell string of a `date` or a `datetime`. `Table.types` records the declared types of the columns read, and `SERIAL_TYPES` names the two types read that way. `parse_tab` and `merge` list every unknown type in `types` in one error.
- `merge` takes `types=`, `schema=`, `carry=`, and `blank_keys=`. `types` compares the cells of a typed column by value (`normalize_cell`). `schema` holds a sheet value that fails it in `MergePlan.held`, a list of `HeldCell`, instead of folding it, and flags a new sheet row with an invalid cell `remote_invalid`. `carry` names the local columns outside the projection, so every row of `new_local` holds each of them even when the local side has no rows to infer them from. `cell_problem` says why one cell does not fit a `ColumnSchema`.
- A `blank_keys` tab field, `refuse` (the default) or `partial`, for every mode. `partial` allows a row of a composite key to leave a component blank, and refuses only a row whose every key cell is blank. `index_rows`, `parse_tab`, `read_tab`, and `merge` take `blank_keys=`, and `Table.blank_keys` records the setting a tab was read with. `check_blank_keys` refuses a setting that is not one of `BLANK_KEYS`.
- An `on_invalid` tab field for `sync` tabs, `refuse` (the default) or `hold`. With `hold`, a sheet value that fails the `schema` is kept out of the local file and the base, the rest of the tab is written, the report lists the held values and rows, and the run exits 2. `ON_INVALID` names the two settings.
- `MergePlan.sheet_writes`, `MergePlan.local_writes`, and `MergePlan.has_writes` say whether a plan writes to the sheet, to the local side, or to either.
- Two hooks beside `validate`, on `plan_tab`, `sync_tab`, `pull_tab`, `push_tab`, and `run_target`. `check` blocks a write as `validate` does, and is given a `CheckContext`: the rows, their stage (`local`, `merged`, or `sheet`, the three `STAGES`), the columns of the rows, the projection, the sheet's columns, the columns the run adds and drops, and the merge plan. `warn` takes a `CheckContext` too, runs once after every blocking check has passed, and blocks nothing: its messages go to `TabReport.warnings`, are printed under their own heading, and leave the exit code as it is.
- Stores, in `gdrives.sheets`: a run reads and writes the local side of a tab and its base through a `Store` (a `label`, `exists`, `read`, and `write`) where it used a path. `FileStore` is what a config's `local` path and a target's base directory become, and `MemoryStore` holds records in memory. `TabConfig(store=...)` gives a tab a local store of the caller's own, with `TabConfig.local_store` returning the store a run uses, and `Target(base_stores={title: store})` gives a tab's base one, with `Target.base_store(tab)`. `TabReport.local_label` is the label of the local store, and the text report prints `local store:` for a tab that has no local file. The refusals of a run and the rest of the report say `local store` for such a tab too. A config-driven run reads, writes, and prints what it did before.
- `push_rows` in `gdrives.sheets` pushes rows held in memory to a tab, with no config and no file: the preview of what the tab loses, the re-read guard, the grid growth, the single write over the old extent, and the read-back of `push_tab`, which now reads its store and calls it. It takes `key`, `blank_keys`, `schema`, the three hooks, `widths`, `clear_links`, a `label` for its messages, and a `report` to fill.
- `ApplyResult.pushed_cells` lists each cell a sync pushed as `(row, column)`, the row as it is after the run, and `ApplyResult.appended_columns` the columns written in each new row, so a caller's pass over the written cells need not read the tab again.
- `linked_cells` returns each cell of a tab that holds a link as a `LinkedCell`: its row, its column, the target of each link, and whether any is on part of the cell's text. `clear_link_format` clears the link format of the columns and rows named, in one request, and leaves every other format alone; with `runs` it also clears the text format runs of a cell that holds a link in them. `strip_links` clears the links a run just wrote and returns the ones that remain. `link_clear` builds the `repeatCell` request that clears one field over a block of cells, for a caller that batches its own, and `LINK_FIELDS`, `CELL_LINK_FIELD`, and `RUNS_FIELD` are the masks these use.
- A `clear_links` tab field for `sync` and `push` tabs, and `clear_links=` on `apply_plan` and `push_rows`: the cells a run writes are left with no link, where the Sheets API links a URL or a bare domain as it is written. A link that remains raises `ReadBackError`.
- `pull_grid` reads the grid data of one range under a `fields` mask, which it requires. A response too large for the HTTP client to decode raises `GridTooLargeError`, a `ValueError`, so a run reports it for its tab where the client's own error would have stopped the run. `decode_errors` returns the client's error classes it is raised for.
- A `sheet_id` tab field, for every mode, names a tab by its `sheetId`. The tab is found by it under whatever title it has on the sheet, the report notes a title that differs from the config's, and a `sheet_id` the spreadsheet lacks is an error for the tab, which is never looked for by title and never created. `push_rows` takes `sheet_id=`.
- `tab_listing` reads every tab's title, `sheetId`, and grid size in one request, as a `TabListing`. `plan_tab`, `sync_tab`, `pull_tab`, `push_tab`, and `push_rows` take `listing=`, and read their own when given none. `ensure_tabs` takes `existing=`.
- `get_column_widths` returns each named header column's width in pixels, from a read of the header and one grid read under the mask `WIDTH_FIELDS`, and `gdrives sheets-widths SHEET [--tab TITLE]` (`run_widths`) prints it as a JSON object ready to paste under a tab's `widths`.
- `sheets-pull --all-tabs` takes `--bom`, which starts each `.csv` or `.tsv` file with a byte-order mark, and `--slug`, which names each file by a slug of its title. `pull_all_tabs` takes `bom=` and `name=`, a function from a title to a file stem, which is made safe as a file name whatever the function returns, and `slug` is in `gdrives.local`. `slug` keeps a combining mark with its letter, so a title gives one stem whether its accents are composed or not.
- The `sheets-*` commands print a line to stderr before each wait of a retried request: `Sheets API returned 429; retrying in 4s (attempt 3 of 5)`. `with_retry` takes `on_retry=`, a callback given a `RetryNotice`, and `retry_notices` sets that callback for every retry inside a block. The library prints nothing unless asked.
- The guide, `docs/sheets-sync.md`, has sections on moving an existing sync over, on removing a caller's own retry, and on pulling and pushing the same files through two targets.

### Changed

- A sync tab with a `schema` compares the cells of a typed column by value, so a difference of spelling is no longer an edit: `true` and `TRUE` in a `bool` column, or `3.0` and `3` in a `float` column, are in sync. Such a cell is no longer folded or pushed, and a respelling on one side no longer turns an edit on the other into a conflict. A push still sends the local file's text and a fold still takes the sheet's. Key columns are compared as before, and a tab with no `schema` is unaffected.
- A column that a tab's `schema` declares `date` or `datetime` is read from the sheet as ISO 8601 whatever the sheet displays, by a second read of the declared columns as serial numbers. A sync, its re-read guard and read-back, and a pull all read that way. A cell holding text is left as it is. A base that holds a date's display text where the serial gives another value, such as a date-time whose format hides its milliseconds, reports that cell as a sheet edit on the first run and folds the ISO value in. A tab with no declared date column is read once, as before.
- Record files are written with LF line endings. `write_records` now defaults to LF, so the local file and the base of a sync, the file of a pull, and the files of `sheets-pull --all-tabs` end their lines with LF, not CRLF. A file is rewritten only when its records change, so a file that an earlier version wrote keeps its CRLF until the next run that changes it, and changes once then. Set `newline: "crlf"` on a tab to keep CRLF. `write_values_csv` and `sheets-get -o` still write CRLF.
- New rows of a sync are placed by the `insert_above` column as it will be after the run's pushes. A run that pushes a matching value to a row and adds a row now inserts the new row above the pushed row, where earlier versions looked at the column as read and could place it below. A tab whose `insert_above` column is outside the projection is unaffected.
- Rows inserted with `insert_above` take the formatting of the row above them, not of the row they sit above. Directly below the header they still take the formatting of the row below.
- Columns added by `--add-missing` (`add_missing`) land at their place in the projection, directly after the nearest earlier projection column the sheet has, not after the header's last column. Columns the sheet already has are not moved.
- A sync preview refuses an `insert_above` column the tab lacks, as the apply already did.
- The text report says `in sync: nothing to write` only when nothing is left for a person. A tab with nothing to write and a conflict or a row flag printed that line below them.
- A run of several tabs lists the spreadsheet's tabs once, where it listed them once per tab: a preview of N tabs makes one listing and N reads of values, not N and N. A sync that creates its tab lists the tabs twice, not three times.
- `TabConfig.local` is `Path | None`, and defaults to None. A tab loaded from a config always has it; a tab built in code with a `store` may not. A caller that reads `tab.local` under a strict type checker has to handle None. A tab with neither `local` nor `store` is refused when it is built.
- Two `ApplyResult` objects are equal only when their `pushed_cells` and `appended_columns` are too. A caller that compares a result with one built from four values gives the two lists as well.
- `write_records` lists every cell of a `.json` file that does not parse as its declared type, each by row position and column, where it raised on the first one. The message still starts with the path. An unknown type in `types` is refused whatever the rows hold.

## [0.12.0] - 2026-09-27

### Added

- `gdrives login` grants OAuth access with or without a terminal attached, for a run started by a tool that captures stdin while a person still watches its output. It prints the consent URL, waits for the browser to come back, caches the token, and prints the credential line. `--scope read|sheets|docs|drive` picks the access (`read` by default), and `--timeout SECONDS` (300 by default) exits 1, with every token file untouched, when nobody consents in time. It also exits 1 when the token of a consent could not be saved, since the next command would ask again. A cached token that already serves the scope is kept, and nothing is asked. It is also the way to grant again after a token's refresh has failed.
- `authenticate_oauth`, `authenticate`, `describe_credentials`, and the `build_*` service helpers take `force=True`: a consent that is needed runs whether or not stdin is a terminal, and OAuth that is not configured raises `gdrives.auth.ConsentError` instead of falling through to a service account or ADC. `authenticate_oauth` also takes `timeout=`, in seconds.
- `gdrives.auth.announce_credentials` prints the credential line for a scope set, and inside a `gdrives.auth.announcing_credentials()` block every service that is built announces a coming consent or refresh by itself. A library caller's stderr is left alone outside one.

### Changed

- A cached OAuth token whose grant is broader than the request is used as it is, instead of being replaced through a new consent. A token granted `drive` serves a request for `drive.readonly`, `drive.file`, `drive.metadata`, `spreadsheets`, or `documents`, and each write scope serves its own `.readonly`. A token that a caller wrote itself under `gdrives_token_rw.json` with the `drive` scope, before 0.6.0 took that name for the `spreadsheets` scope, is no longer replaced by the first Sheets write command. Such a token is loaded and refreshed with the scopes it records.
- A consent never overwrites a token file that holds a grant the new one does not include, no recorded scopes, or content that does not parse. The new token is written under the name derived from its scopes (`gdrives_token_spreadsheets.json` for the Sheets write scope, `gdrives_token_drive-readonly.json` for read-only), a warning names both files, and later runs look in both places. When that name is taken as well, the token serves the run and is not saved.
- The names `gdrives_token*.json` in `$GOOGLE_CONFIG_DIR` are reserved for the tokens `gdrives` writes. A caller that keeps a token of its own there should give it another name.
- Every command prints the credential line to stderr when its authentication is about to wait on an interactive consent or a token refresh, so a preview waiting on a browser consent no longer looks hung. `sheets-sync`, `sheets-pull`, and `sheets-push` still print it on every `--apply`. A caller that treats any stderr output as a failure will see this line on a preview.
- The consent URL is flushed as it is printed, so it is visible while the flow waits even when stdout is not a terminal.

## [0.11.0] - 2026-09-27

### Added

- A read layer in `gdrives.sheets` for keyed records: `read_tab` reads a whole tab in one request as header-named records of canonical cell strings, with row numbers by key; `read_records` and `write_records` move records to and from `.csv`, `.tsv`, and `.json` files (JSON with typed values, written byte-stably); `to_cell`, `from_cell`, and `problems` convert and check declared column types; `row_key` and `index_rows` compare row keys with whitespace normalized and refuse blank or duplicate keys.
- `pull_values` takes `render=` and `date_time_render=` (for example `UNFORMATTED_VALUE`), and `pull_many` reads several ranges in one `values.batchGet` request.
- `with_retry` retries a Sheets API call with exponential backoff and jitter.
- `gdrives.sheets.merge` merges local records and a tab's records by row key against a saved base, and returns a `MergePlan` of cells to push, cells and rows to fold into the local file, rows to append, conflicts, ownership overrides, and row flags, with the next local rows and base. It is pure: nothing is read or written. `local_owned` and `sheet_owned` columns always keep one side's value, `owns_rows` flags rows added on the sheet instead of folding them in, and `prefer` resolves conflicts toward one side. Deleted rows are flagged, never removed.
- `gdrives.sheets.apply_plan` writes a merge plan's cell pushes and new rows to the tab. It reads the tab again first and raises `SheetChangedError`, writing nothing, if the header, rows, or row numbers changed since the plan was computed. It then sends the pushes in one `RAW` `values.batchUpdate`, the new rows in one `spreadsheets.batchUpdate`, and reads the tab back (`verify`), raising `ReadBackError` with every mismatch listed if a write did not land. New rows go after the last row holding anything, growing the grid when needed, or with `insert_above` directly above the first row whose named column holds a given value. They never go through `values.append`, and only the synced columns of a new row are written.
- `add_columns`, `delete_columns`, `ensure_tabs`, and `set_column_widths` edit a spreadsheet's structure by header name and tab title, each in at most one request: add columns after the header or before a named column, delete columns by name, create missing tabs (never deleting one), and set column widths in pixels.
- `tab_grid` returns a tab's `sheetId` and grid size, and `Table.last_row` is the last row of a tab holding a value in any column.
- A sync config file, `gdrives-sheets.json`, names targets (a spreadsheet and its tabs), each tab kept in step with a local file in `sync`, `pull`, or `push` mode. `load_config` finds it from the working directory upward, resolves paths against the file's folder, and reports every problem at once in one `ConfigError` before anything touches the network.
- `gdrives.sheets.sync` runs a config's tabs as a library, previewing by default. `plan_tab` and `apply_tab` merge a sync tab against its base snapshot (one CSV per tab) and write in a fixed order: schema and `validate` checks, structure steps (`add_missing`, `drop_extra`, creating a missing tab), the guarded sheet writes, then the local file, the base, and column widths, so a failed step leaves every later file untouched. A first sync takes the local file as the base and writes nothing to the sheet; `adopt` instead makes the local file win every difference. `pull_tab` and `push_tab` replace one side with the other, with a preview of what the replaced side loses; a push is one write over the tab's old extent, guarded and read back. `pull_all_tabs` dumps every tab of a spreadsheet with no config. `run_target` collects a `SyncReport` whose `exit_code` tells success (0), an error (1), and conflicts or row flags left for a person (2) apart, and `format_report` renders it with every sheet- or file-supplied string made terminal-safe.
- `gdrives.auth.describe_credentials` says which credential a call with given scopes will use (OAuth token, service account, or ADC), whether an interactive consent comes first, and the service account's email, without a network call.
- `gdrives sheets-sync`, `sheets-pull`, and `sheets-push` run the `sync`, `pull`, and `push` tabs of a target in `gdrives-sheets.json` (or `--config PATH`), optionally narrowed with repeatable `--tab`. Each previews by default and writes only with `--apply`, printing its report to stdout and exiting 0 when in sync or applied, 1 for an error, and 2 when conflicts or row flags are left for a person. `sheets-sync` takes `--adopt` (for a first sync, allowed without `--apply` as a preview), `--add-missing`, `--drop-extra`, and `--prefer local|sheet`. `sheets-pull SHEET --all-tabs -o DIR` dumps every tab with no config, with `--skip TITLE` and `--format csv|tsv|json`. A config, target, or tab problem, and an option that belongs to the other form of `sheets-pull`, is refused with exit 1 before any request. A preview uses the read-only scope; with `--apply`, each command prints the credential it will use to stderr before its first request, and `sheets-sync` and `sheets-push` request the `spreadsheets` write scope. The guide is `docs/sheets-sync.md`.
- `run_sync`, `run_pull`, and `run_push` in `gdrives.sheets` are the entry points behind those commands; each returns the run's exit code.
- `parse_tab` parses a tab's grid already read (for example by `pull_many`) as `read_tab` does, and `read_tab` raises `EmptyTabError`, a `ValueError`, for a tab with no header row.
- `gdrives --version` prints the installed version and exits.

### Changed

- The `gdrives.sheets` value wrappers retry transient failures. Reads, `update_values`, `clear_values`, and `batch_update_values` retry on 429, 500, 502, 503, and 504; `append_values` and `batch_update_spreadsheet`, which add rows, columns, or rules, retry on 429 only, since a 5xx may mean the change already landed. A caller that wrapped these calls in a retry of its own should remove it: left in place, it makes up to its own attempts times five calls, and a failure that persists takes that much longer to reach it.
- `gdrives.sheets` is now a package, split into `values`, `a1`, `match`, `rules`, `files`, and `commands` submodules. Every name importable from `gdrives.sheets` before is still importable from it; code that patches a helper internally must now patch it on the submodule that looks it up.
- `safe_filename` moved from `gdrives.download` to `gdrives.local`; it is still importable from `gdrives.download`.

## [0.10.0] - 2026-09-26

### Added

- `download --skip-existing` resumes a folder download. Each entry maps to the local path the first run gave it (a second `a.pdf` is `a (1).pdf` both times), and entries already there are skipped instead of being saved again as ` (1)` copies.
- `download` accepts a bare file or folder ID, as `export`, `mv`, and the `sheets-*` and `docs-*` commands already did. A bare name that matches a cached drive still downloads that whole drive.
- `sheets-get --escape-formulas` prefixes `'` to every cell starting with `=`, `+`, `-`, `@`, a tab, or a carriage return, for output headed to Excel or LibreOffice. Values stay exact by default.
- The `docs-*` commands target the tab a Doc URL points at (`?tab=`), so a link copied while viewing a tab edits that tab. `--tab` still wins over the URL.
- Helpers behind these: `gdrives.local.write_text` (an atomic UTF-8 write), `escape_formula`, and `printable`; `gdrives.download.LocalNames` and `DownloadError`; `gdrives.files.IncompleteSearchError`; and `gdrives.docs.url_tab_id`.

### Changed

- A folder download no longer stops at the first failure. An entry that fails with an API or filesystem error (a Doc over the export size limit, a download-restricted file, a name too long for the local filesystem) is reported, and the rest still download. The failures are listed at the end and the command exits 1. A subfolder that can't be created is reported once, and its contents are skipped.
- `sheets-append` inserts its rows (`insertDataOption=INSERT_ROWS`) instead of writing over whatever follows the table, so a second block of data one blank row below is pushed down rather than overwritten.
- `docs-update` and `docs-clear` leave plain `NORMAL_TEXT` paragraphs. The body's last paragraph no longer passes its heading, list bullet, indent, alignment, or text formatting on to the new text. Both now read the document before prompting, name the document and tab in the prompt, and refuse the write if the document changed while the prompt waited.
- `docs-append` sends the revision it read, so the write is refused if the body changed first instead of the text being glued onto someone else's line.
- `sheets-set` reads the tab again just before writing and refuses if the header or the matching rows moved since the first read. `sheets-delete-rule` reads the rules again after its prompt and refuses if a different rule now sits at that index.
- Listings request 1,000 items per `files.list` page instead of 100, so a large folder takes a tenth of the calls.
- Items that share a name are listed in a fixed order (by exact name, then file ID) instead of whatever order Drive returns, so `ls` output is stable and a `download --skip-existing` rerun maps duplicates to the same local names as the first run.
- The `.env` file is found by searching upward from the working directory, not from the installed package's directory. An installed `gdrives` now reads the project's `.env` instead of missing it or loading an unrelated one such as `~/.env`.
- A path-based `ls`, `sheets-get`, `sheets-rules`, or `docs-get` authenticates once instead of twice: every client that needs the same scopes reuses one set of credentials.
- `ls --save-as`, `sheets-get -o`, `docs-get -o`, and the drive cache are written atomically, like downloads and exports, so an interrupted `show-drives` no longer truncates `.gdrives/cache.json`. Their bytes are written untranslated, so CSV rows end in `\r\n` on Windows too, not `\r\r\n`.
- Replacing an existing file (`export -o`, `docs-get -o`, `sheets-get -o`, `ls --save-as`) keeps that file's permissions instead of resetting them from the umask, as the 0.9.1 entry already claimed.
- `ls --save-as` accepts the extension in any letter case (`.CSV`, `.Md`).
- Input files that start with a UTF-8 byte-order mark (Excel's "CSV UTF-8" format) no longer carry it into the first cell or the document: `--values-file`, `--text-file`, and `--rule-json` drop it.
- `docs.set_text` and `docs.clear_text` require the `doc` they write against (a `pull_document` result) instead of fetching one when it is omitted, and `docs.tab_content` no longer accepts a document fetched without its tabs.

### Fixed

- Network, DNS, and authentication failures end in a one-line error and exit 1 instead of a traceback. These include a service-account or ADC token refresh that fails, a denied OAuth consent, and the network dropping during an OAuth refresh. That last one is now reported as a network error instead of starting a new browser consent.
- A malformed drive cache is reported along with the fix (rerun `gdrives show-drives`) instead of as a `KeyError` or `TypeError`. A `--values-file` field too large for the csv module names the file and line.
- `mv` checks every parent of the destination, not just the first, before moving a folder, so a legacy multi-parent folder can no longer make a folder its own ancestor. `ls` and `download` stop at a folder that already contains itself instead of recursing until they crash, and `download` reports it as skipped because it contains itself.
- `docs-replace` no longer counts a match that spans an image, chip, or table cell, which the API never replaces. If the API replaces a different number of occurrences than were counted, the command says so and exits 1 instead of printing "Replaced 0 occurrence(s)" as a success.
- A listing that Drive marks `incompleteSearch` (results may be missing) is refused instead of being used as if complete.
- `--tab ""` on a `docs-*` command, or a Doc URL ending in an empty `?tab=`, as from an unset shell variable, is an error instead of silently targeting the first tab.
- A Drive path of nothing but slashes and spaces (`""`, `/`, ` / `) is refused with "Drive path must not be empty" instead of the misleading "Run 'gdrives show-drives' first"; `ls --shared-with-me` applies the same check.

### Removed

- `sheets.resolve_spreadsheet_id`, `docs.resolve_document_id`, `docs.raw_text`, and `drives.resolve_name`, which nothing called; use `resolve.resolve_file_id` and `drives.find_drive`.

### Security

- Terminal escape injection: `ls`, `download`, `mv`, `show-drives`, and CLI error messages escape control characters in Drive names, so a shared item whose name holds an ANSI or OSC sequence can't rewrite output, retitle the terminal, or set the clipboard. `ls --save-as` escapes them the same way in the CSV and markdown it writes, so viewing a saved listing with `cat` or `less` is safe too. Local file names replace those characters with `_`.
- CSV formula injection: `ls --save-as` CSVs prefix cells starting with `=`, `+`, `-`, `@`, a tab, or a carriage return with `'`, so a shared file named `=HYPERLINK(...)` doesn't run when the CSV is opened in a spreadsheet app.

## [0.9.2] - 2026-09-26

### Changed

- Dependabot opens its version-update PRs against `dev` instead of `main`, for both GitHub Actions and Python dependencies. Dependabot reads its config from the default branch only, so this takes effect once the release reaches `main`.
- Repo tooling synced with proj-template 0.10.0, recorded as `[tool.proj-template] version` in `pyproject.toml` (it never reaches the wheel or the PyPI metadata): the ruff pre-commit hook moves to v0.16.6, and `planners-validate` runs from its own `local` hook block.

### Security

- The CI and publish workflows pin every GitHub Action to a full commit SHA: `actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.0.1 (from 8.3.2), `actions/upload-artifact` v7.0.1, `actions/download-artifact` v8.0.1, and `pypa/gh-action-pypi-publish` v1.14.2. Only setup-uv was pinned before; the others followed a movable tag or branch, so a retag upstream could change what builds and publishes the package.

## [0.9.1] - 2026-09-12

### Changed

- Local writes are atomic. A new `gdrives.local.atomic_output` helper streams to a private temporary file beside the target and renames it into place only after a successful write, so `download`, `export`, and the OAuth token cache never leave a partial file behind or clobber an existing one on failure. Downloads and exports keep the permissions a direct write would have produced; the token cache stays owner-only (`0600`).
- `download` keeps colliding folder names distinct. Two sibling folders that share a name (or sanitize to the same name) now land in `name/` and `name (1)/` instead of merging into one local directory, and a folder whose only contents are subfolders no longer prints "Nothing to download".
- `file_type` no longer reports an ordinary `.doc`/`.sheet`-style extension with the label reserved for Google Workspace files; it is prefixed with a dot so the two stay distinct in `ls` output.
- `ls --save-as` creates the output file's parent directory, and `show-drives` creates a nested cache directory.

### Fixed

- Drive URLs are recognized by host, not substring: `strip_url_suffix` parses the URL and only strips `/edit`, `/view`, and the query string when the host is `drive.google.com` or `docs.google.com`, so a non-Drive URL that merely mentions one of them keeps its query.
- `docs-replace` counts matches per segment (body, headers, footers, footnotes), so a match that would have spanned a segment boundary in the concatenated text no longer inflates the count.
- `sheets-set` refuses a match or target column whose header appears more than once instead of silently using the first, and A1 ranges with absolute references (`$A$2:$C`) parse correctly. A range consisting only of a quoted tab name is treated as the whole tab, and an empty range is rejected.
- Empty and ambiguous arguments are rejected up front: `ls` refuses `PATH` together with `--drive-id`, `mv` refuses blank `SOURCE`, `--source-id`, `--dest-id`, or destination names and no longer treats a trailing-slash destination as a new file name, `resolve_shared_path` refuses an empty path, `walk_tree` refuses `depth < 1`, and `find_drive` raises when several cached drives share a name instead of returning the first.
- `mv` accepts a destination that is a cached drive name containing a slash.
- A cached OAuth token that is unreadable or malformed is logged and re-consented instead of crashing, and a `scopes` list with non-string entries is treated as not covering the request.
- Markdown listings (`ls --save-as map.md`) escape names and URLs so a file whose name contains `]`, `*`, or a newline no longer breaks the link, and CSV listings strip the trailing `/` only from folder paths.

## [0.9.0] - 2026-09-11

### Added

- `mv` — rename and move a Drive file or folder via `files.update`, mirroring Unix `mv`. The destination decides the operation: a bare name (no `/`) renames in place, a path that resolves to an existing folder moves the item into it under its current name, and a path whose parent folder exists but whose final segment does not does both in a single call. `--source-id`, `--dest-id`, and `--name` skip path resolution, and `--dry-run` prints the intended change without making it.
- Two moves are refused rather than guessed at: one that crosses drives (`files.update` cannot do it, detected as a `driveId` mismatch) and one whose item has several parent folders (the parents are listed instead of picking one to detach from). Duplicate names within a folder are allowed, as Drive permits them.
- `gdrives.mv` helpers behind the command: `resolve_destination` (a path to `(parent_id, new_name)`), `check_destination`, `sole_parent`, `check_arguments`, `describe`, and `apply_move`.

### Changed

- `get_file_metadata` moved from `gdrives.download` to `gdrives.files`, where the other Drive API wrappers live, and gained a `fields` parameter — `mv` needs `parents` and `driveId` beyond the default id/name/mimeType. `gdrives.download` imports it from its new home.

### Security

- `mv` is the first command that changes Drive itself, so it requests the full `drive` scope; `drive.readonly` cannot call `files.update`. The grant is cached in its own `gdrives_token_drive.json`, leaving the read-only, Sheets, and Docs tokens untouched, and `mv --dry-run` stays on the read-only default so previewing a move never triggers a write consent. ADC has no per-scope token to fall back on, so an ADC login needs the `drive` scope for `mv` — see `docs/setup-adc.md`.

## [0.8.0] - 2026-09-11

### Added

- Conditional format rules on Google Sheets. They are read with `spreadsheets.get` and written with `spreadsheets.batchUpdate`, separate from the `spreadsheets.values.*` cell commands:
  - `sheets-rules` — list every rule grouped by tab, each with its index on the tab; `--json` prints the raw rules for replay.
  - `sheets-add-rule` — add a custom-formula rule over one or more `--range`s with `--bold`, `--italic`, `--strikethrough`, `--underline`, `--text-color`, and `--background` (colors as hex), inserted at `--index` (default: first). `--rule-json` replays one captured rule instead.
  - `sheets-delete-rule` — delete the rule at `--index` on `--tab` (default: first tab); shows the rule and prompts unless `-y`, and refuses an out-of-range index.
- `gdrives.sheets` helpers behind them: `list_conditional_rules`, `add_conditional_rule`, `delete_conditional_rule`, `build_formula_rule`, `read_rule_json`, `describe_rule`, `a1_to_grid_range` / `grid_range_to_a1` (A1 strings to 0-based `GridRange` dicts and back), `hex_to_color` / `color_to_hex`, `column_index`, `tab_sheet_ids`, and `batch_update_spreadsheet` (the structural `spreadsheets.batchUpdate`, distinct from `batch_update_values`).
- A test run that skips the live integration tests because they aren't configured now ends with a short "live integration tests skipped" note naming what is missing. Before, the skips only showed up in the skipped count. The README has a new Development section covering how to set up the live tests.

## [0.7.1] - 2026-09-05

### Changed

- Test coverage now measures branches as well as lines and fails the run below 100%. The coverage settings (source, threshold, and standard exclusion patterns) live in `pyproject.toml`, so `uv run pytest` and the CI workflow share one configuration.

## [0.7.0] - 2026-09-05

### Added

- Live Google Docs read/edit via the Docs API v1 (`documents.get` / `batchUpdate` / `create`), distinct from `export`'s whole-file download:
  - `docs-get` — print a Doc's plain text (list items prefixed `- `, table rows tab-separated); `--json` prints the raw document, `-o` writes a file, and `--tab` picks a tab by title or ID.
  - `docs-update` — overwrite the body with a local text file; prompts unless `-y`.
  - `docs-append` — append `--text` or a `--text-file` as new paragraph(s).
  - `docs-replace` — find and replace; refuses when the phrase occurs more than once unless `--all`, errors when it occurs nowhere; `--ignore-case` relaxes matching.
  - `docs-clear` — empty the body; prompts unless `-y`.
  - `docs-create` — create a new Doc in My Drive root, optionally filled from a text file; prints its URL.
- Index-addressed Docs writes (`docs-update`, `docs-clear`) and `docs-replace` send the revision they read as `writeControl.requiredRevisionId`, so a write is refused if the document changed in between.
- Separate `documents` write scope for the Docs write commands, cached in its own `gdrives_token_documents.json`. Every scope set now gets its own token file, and a cached token whose granted scopes do not cover the request is discarded and re-authorized instead of failing with a 403.
- `export` accepts `.txt` (plain text) and `.md` (Markdown) outputs for Google Docs.

### Changed

- `sheets.resolve_spreadsheet_id` delegates to the new shared `resolve.resolve_file_id` (URL, bare ID, or Drive path to a file ID), which `docs.resolve_document_id` also wraps.

## [0.6.2] - 2026-09-04

### Changed

- Refreshed locked dependencies: google-api-python-client 2.199.0, google-auth-oauthlib 1.4.1, python-dotenv 1.2.3 (minimum raised to 1.2.3), and typer 0.27.1; dev tools pre-commit 4.6.2, pyrefly 1.2.0, and ruff 0.16.4. CI workflows bumped to setup-uv 8.3.2 and pinned to its commit SHA.

### Security

- Updated the transitive `cryptography` dependency from 49.0.0 to 50.0.1, resolving GHSA-g6cj-pr64-35w5 (CVE-2026-69247, a Bleichenbacher oracle in PKCS#7 EnvelopedData decryption).

## [0.6.1] - 2026-07-20

### Changed

- `listing.ls(save_as=...)` rejects unsupported file extensions with an error instead of silently writing CSV; `.md` and `.csv` remain the supported formats (the CLI already pre-validated, so only library callers are affected).
- Drive path resolution loads the drive cache once per lookup instead of once per prefix candidate.
- Refreshed locked dependencies, including protobuf 6 -> 7 and rich 14 -> 15; CI workflows bumped to setup-uv 8.3.1.

### Fixed

- Removed the unsupported `semver-major-days` cooldown option from the github-actions Dependabot config, which broke Dependabot runs.

## [0.6.0] - 2026-07-07

### Added

- Live Google Sheets read/write via the Sheets API v4 (`spreadsheets.values.*`), distinct from `export`'s whole-file download:
  - `sheets-get` — read an A1 range to aligned columns, `--csv`/`--tsv` stdout, or a delimited file (`-o`); a bare range or no range targets the first tab.
  - `sheets-update` — overwrite a range with rows from a local CSV (`--values-file`).
  - `sheets-append` — append CSV rows after the table in a range.
  - `sheets-clear` — clear a range's values (keeps formatting); prompts unless `-y`.
  - `sheets-set` — update row(s) located by header-named column value(s); repeat `--match` for a composite AND key and `--set` for multiple columns, refusing on 0 or >1 matches unless `--all`.
- Separate `spreadsheets` write scope for the write commands, cached in its own `gdrives_token_rw.json`, so read commands never request or re-consent write access. `--raw` stores literal strings instead of the default `USER_ENTERED` parsing.
- Dependabot cooldown windows for dependency update PRs.

### Changed

- `authenticate` and the `build_*` service helpers take an optional `scopes` argument; the read-only Drive scope remains the default.
- CI test workflow pins the Python version via a `UV_PYTHON` environment variable.

## [0.5.8] - 2026-06-09

First release published to PyPI.

### Added

- PyPI publishing via GitHub Actions trusted publishing (OIDC), gated on the `PUBLISH_ENABLED` repository variable; `gdrives` is now installable from PyPI.

## [0.5.7] - 2026-06-09

### Changed

- Renamed the package from `gdrive` to `gdrives` (module, CLI entry point, token/credential filenames, and cache directory).
- Flattened the docs layout: guides moved from `docs/guides/` to `docs/`.
