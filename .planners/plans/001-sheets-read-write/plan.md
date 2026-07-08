---
id: 1
slug: sheets-read-write
status: draft
branch: feature/sheets-read-write
created: 2026-07-07T18:55:26-07:00
concluded:
pr:
---

# Read and update Google Sheets values via the Sheets API

## Plan

### Goal

Add first-class **cell-level** read and write for Google Sheets: pull values from a
range and push values back — both as importable functions and as `gdrives` CLI
commands. This is distinct from the existing `export` command, which downloads a
*whole* spreadsheet to `.xlsx`/`.csv` via a Drive export. Here we want to operate on
live cell ranges (get / update / append / clear) using the **Sheets API v4**
(`spreadsheets.values.*`), so callers can read a range into rows and write rows
back without a round-trip through a local file.

### Scope

In scope:
- `gdrives.sheets` module with pull/update/append/clear helpers over `spreadsheets.values`.
- A `build_sheets_service()` alongside the existing `build_drive_service()`.
- Write-capable auth (new scope) — see **Key decision: scopes** below.
- Spreadsheet targeting by URL, file ID, *or* Drive path (reuse existing resolution).
- CLI commands to read and write ranges.
- Tests with a fake Sheets service (mirror `tests/helpers.py` patterns).

Out of scope (possible follow-ups):
- Structural edits via `spreadsheets.batchUpdate` (add/delete tabs, formatting,
  freezing rows) — values only for now.
- DataFrame (pandas/polars) integration — keep the core returning plain
  `list[list[str]]`; a typed loader can come later.
- Creating new spreadsheets from scratch.

### Key decision: scopes (write access)

`auth.py` currently pins a single module-level constant:

```python
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
```

Reading a sheet's values also works read-only (via
`https://www.googleapis.com/auth/spreadsheets.readonly` or the existing
`drive.readonly`), but **updating** requires a write scope
(`https://www.googleapis.com/auth/spreadsheets`, or the broader `drive`). Adding a
write scope to the shared constant would force *every* existing read-only user to
re-consent (the cached OAuth token in `gdrives_token.json` is scope-bound and is
invalidated when scopes change), and hands write access to commands that only read.

**Recommended approach — parameterize scopes per operation:**
- Change `authenticate*()` / `build_drive_service()` / the new
  `build_sheets_service()` to accept a `scopes` argument (default = current
  read-only list), instead of reading a single global constant.
- Read commands keep the read-only scope (no re-consent for existing users).
- Write commands request `.../auth/spreadsheets`; persist that token under a
  distinct filename (e.g. `gdrives_token_rw.json`) so the read-only token stays
  intact and the two don't clobber each other on refresh.
- Service-account and ADC paths already take `scopes=` — just thread it through.

Confirm this split at implementation time; the alternative (bump the single global
scope to `spreadsheets`) is simpler but re-prompts all users and over-grants.

### API surface (functions)

New module `gdrives/sheets.py`. Core helpers take a Sheets `service`, a
`spreadsheet_id`, and an A1 `range` (e.g. `"Sheet1!A1:C10"`):

- `pull_values(service, spreadsheet_id, range_) -> list[list[str]]`
  wraps `spreadsheets().values().get(...).execute()` and returns
  `result.get("values", [])` (Sheets omits `values` entirely for an empty range).
- `update_values(service, spreadsheet_id, range_, values, *, input_option="USER_ENTERED") -> dict`
  wraps `values().update(..., valueInputOption=input_option, body={"values": values})`.
  `USER_ENTERED` parses `"=SUM(...)"`/dates/numbers like the UI; `RAW` writes literal
  strings — expose as an option, default `USER_ENTERED`.
- `append_values(service, spreadsheet_id, range_, values, *, input_option="USER_ENTERED") -> dict`
  wraps `values().append(...)` (adds rows after the table in `range_`).
- `clear_values(service, spreadsheet_id, range_) -> dict`
  wraps `values().clear(...)`.
- `list_tabs(service, spreadsheet_id) -> list[str]`
  wraps `spreadsheets().get(..., fields="sheets.properties.title")` so callers can
  discover tab names (Sheets errors on a bad range, so this is the friendly path).

Targeting a spreadsheet mirrors `export.run`: accept a URL / ID / path, resolve to a
file ID via `extract_drive_id` (URL/ID) or `resolve_path(path, allow_files=True)`
(Drive path). Factor the "source -> spreadsheet_id" step so both CLI commands and
importable `run_*` functions share it.

### CLI surface

Follow the existing Typer patterns in `cli.py` (each command wraps its logic in the
`_cli_errors()` context manager and does a lazy import of its module):

- `gdrives sheets-get <source> <range> [--csv/--tsv] [-o out.csv]`
  Pull a range and print it (default: aligned columns to stdout; `-o` writes CSV).
- `gdrives sheets-update <source> <range> --values-file data.csv [--raw]`
  Read a local CSV into rows and overwrite the range. `--raw` selects
  `valueInputOption=RAW`.
- `gdrives sheets-append <source> <range> --values-file data.csv [--raw]`
- `gdrives sheets-clear <source> <range> [-y]`  (confirm before clearing, like
  `download`'s prompt; `-y` skips it).

Decide at build time whether these are flat commands (`sheets-get`) or a Typer
sub-group (`gdrives sheets get ...`). Flat matches the current `show-drives` style;
a sub-group reads better as the surface grows. Recommend the flat style for
consistency, revisit if it gets crowded.

Range parsing: accept a bare `A1:C10` (defaults to the first tab) and a qualified
`TabName!A1:C10`. When no range is given, `sheets-get` could default to the used
range of the first tab (fetch `list_tabs`, read `TabName` with no range).

### CSV interchange

`sheets-get -o` and `sheets-update --values-file` bridge to local CSV. Use the stdlib
`csv` module — every cell is a string (`list[list[str]]`), which matches what the
Sheets API returns and accepts. Ragged rows: Sheets returns rows truncated at the
last non-empty cell, so pad short rows when aligning columns for display; on write,
send rows as-is (Sheets pads with blanks).

### Implementation order

1. **Auth**: parameterize `scopes` through `authenticate*`, add
   `build_sheets_service(scopes=...)`, and split the write token filename. Keep the
   read-only default so existing behavior is byte-for-byte unchanged.
2. **Core module** `gdrives/sheets.py`: `pull_values`, `update_values`,
   `append_values`, `clear_values`, `list_tabs`, plus the shared
   `source -> spreadsheet_id` resolver and thin `run_*` entry points.
3. **CLI**: wire `sheets-get` / `-update` / `-append` / `-clear` into `cli.py`.
4. **Tests**: fake Sheets service in `tests/helpers.py`, then
   `tests/test_sheets.py` covering each helper, range parsing, the source resolver,
   and CSV round-trip. Add CLI tests via the Typer runner like `test_cli.py`.
5. **Docs**: update `README.md` and `.claude/CLAUDE.md` (package-structure table +
   Commands block); note the new write scope in `docs/setup-oauth.md`.
6. **Checks**: `uv run ruff check .` and `uv run pyrefly check` clean before close.

### Open questions

- Write token: separate file (`gdrives_token_rw.json`) vs. re-consenting the single
  token to the superset scope. Recommend separate file (least disruption).
- `sheets-update` input: CSV file only, or also accept inline `--value` for a single
  cell / small range? Start with `--values-file`; add inline later if wanted.
- Should `pull_values` optionally coerce to typed values (`valueRenderOption`)?
  Default `FORMATTED_VALUE` (strings, matches the UI); expose the option later.
