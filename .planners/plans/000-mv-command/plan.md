---
id: 0
slug: mv-command
status: draft
branch:
created: 2026-06-09T00:29:25-07:00
concluded:
pr:
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
