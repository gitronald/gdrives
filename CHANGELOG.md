# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [0.5.8] - 2026-06-09

First release published to PyPI.

### Added

- PyPI publishing via GitHub Actions trusted publishing (OIDC), gated on the `PUBLISH_ENABLED` repository variable; `gdrives` is now installable from PyPI.

## [0.5.7] - 2026-06-09

### Changed

- Renamed the package from `gdrive` to `gdrives` (module, CLI entry point, token/credential filenames, and cache directory).
- Flattened the docs layout: guides moved from `docs/guides/` to `docs/`.
