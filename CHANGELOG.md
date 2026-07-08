# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
