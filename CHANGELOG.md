# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
