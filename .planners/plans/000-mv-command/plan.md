---
id: 0
slug: mv-command
status: active
branch: feature/mv-command
created: 2026-06-09T00:29:25-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/28
---

# Add mv command for Drive rename and move

## Plan

Add a `gdrives mv <source> <dest> [--dry-run]` command that uses the Drive API
(`files.update`) to rename and/or move files, mirroring Unix `mv`. Behavior is
determined by what `dest` resolves to:

- **New name** (same parent, or no path separators) → rename in place via
  `files.update` with `body={"name": new_name}`.
- **Existing folder** → move into it via `addParents`/`removeParents`.
- **Folder path + new name** (parent exists, final segment doesn't) → move and
  rename in a single `files.update` call.

By-ID flags (`--source-id`, `--dest-id`, `--name`) skip path resolution; `--dry-run`
prints the intended change without calling the API.

### Prerequisite

`auth.py` `SCOPES` must change from `drive.readonly` to `drive` (`files.update`
returns 403 otherwise). This invalidates cached OAuth tokens — document the re-auth
step.

### Edge cases

- **Cross-drive moves** — `files.update` can't move between drives; detect via a
  `driveId` mismatch and error clearly.
- **Multiple parents** — if `files.get` returns more than one parent, error with the
  list rather than guessing which to remove.
- **Duplicate names** — Drive allows them; no extra check.

### Follow-ons

Seeded as the backlog; each becomes its own plan when tackled:

- `--shared-with-me` support for `mv` (`resolve_shared_path` already exists).
- Batch rename (`mv --batch` reading a CSV mapping).
- A naming-convention lint/audit command.
- A broader write story unlocked by the `drive` scope: `upload`, `mkdir`, `rm`,
  `cp`, trash/restore.
- Internal deferreds: `StrEnum` for `corpora`, `match`/`case` in `walk_segments`,
  integration tests for `auth.py`/`cli.py`/`export.py`.
- Turn on PyPI publishing as `gdrives` via OIDC trusted publishing.

## Log

### 2026-09-11 — implemented on `feature/mv-command` (PR #28)

The command landed as specified: `mv` in `cli.py` delegating to a new `mv.py`,
with the three destination behaviors, the by-ID flags, and `--dry-run`.

**Scope handled differently than the Prerequisite said.** Rather than widening
the shared `SCOPES` default from `drive.readonly` to `drive` — which would
invalidate every cached token and force a re-auth on users who never move a file
— `mv` opts into a new `DRIVE_WRITE_SCOPES`, cached in its own
`gdrives_token_drive.json`. That is the per-scope token split plans 001 and 002
established *after* this plan was written, so read commands stay read-only and
existing tokens stay valid. ADC has no per-scope token to fall back on, so
`docs/setup-adc.md` now documents logging in with the full `drive` scope.

A consequence worth keeping: `--dry-run` builds the service with the read-only
default, so previewing a move never triggers a write-scope consent.

Other notes:

- `get_file_metadata` moved from `download.py` to `files.py` and gained a
  `fields` parameter, since `mv` needs `parents` and `driveId` beyond the default
  three fields. `download.py` imports it from its new home.
- Both edge cases landed as specified: a cross-drive move and a multi-parent
  item are refused with the offending names/IDs listed rather than guessed at.
  Duplicate names are allowed, per the plan.
- `docs-create`'s docstring and README/setup docs claimed the package never
  requests Drive write access; that is no longer true, so they were corrected
  alongside the feature.
- 580 tests pass at 100% line and branch coverage; ruff and pyrefly are clean.
