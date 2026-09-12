---
id: 0
slug: mv-command
status: done
branch: feature/mv-command
created: 2026-06-09T00:29:25-07:00
concluded: 2026-09-11T19:41:37-07:00
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

### 2026-09-11 — review follow-up (PR #28)

Two rounds of change landed after the entry above.

**Canonical destination IDs.** `--dest-id` accepts aliases such as `root`, which
never compare equal to an item's real parent ID. The move path now uses the ID
`files.get` echoes back, so an item already in the destination folder is
recognized as such instead of being sent a redundant add and remove of the very
same parent.

**Review gate.** A four-dimension review of the full diff produced 32 candidates,
deduped to 15 findings, each adversarially verified — 12 were rejected as false
positives, pre-existing, or intended design. Thirteen were fixed with paired
regression tests; two are conscious no-ops. What mattered:

- *An ambiguous destination was silently reinterpreted.* `resolve_destination`
  caught `DrivePathError` broadly, but the ambiguity error shares that type, so a
  destination matching two folders resolved to the drive root plus a rename —
  moving the file somewhere the user never asked for. Fixed at the source:
  `resolve.py` now raises `AmbiguousPathError`, a subclass, so "not found" and
  "matched several" are distinguishable by any caller.
- *No cycle guard.* A folder could be made its own parent or moved into its own
  subtree, and a cycle in the parent graph makes `walk_tree` recurse without end.
  `check_destination` refuses both, via a bounded parent walk paid only by folder
  moves.
- *An empty `DEST` sent a vacuous `files.update`* and printed a bare `Done:`.
- *The destination path was walked twice* on the move-and-rename branch;
  resolution is now parent-first, which also removed the redundant calls.
- Several documentation claims the feature had made false: the write scope is
  requested by any non-dry-run `mv`, not only one that changes something, and
  service-account users need Contributor, not the Viewer role the setup docs
  prescribe.

The two conscious no-ops: the byte-exact name comparison (an NFC/NFD-different
name costs one redundant update, but normalizing would make a deliberate
normalization-form rename impossible), and the absent `-y/--yes` flag (6 of 11
write commands already skip confirmation, and the 5 that prompt all destroy
content irrecoverably, while `mv` is reversible and ships `--dry-run`).

Final gate: 596 tests at 100% line and branch coverage, ruff and pyrefly clean —
superseding the 580 recorded above. Verified live against a real Drive with
read-only `--dry-run` runs covering every resolution branch, the no-op case, and
all three new guards.

## Retrospective

- Splitting the scope rather than widening the shared default was worth the
  deviation from the Prerequisite: no cached token was invalidated, and a
  read-only user never sees a consent prompt. The plan simply predated the
  per-scope token pattern that plans 001 and 002 established.
- Keeping `--dry-run` on the read-only scope turned out to be more than a
  convenience. It made every live verification of this feature possible without
  granting write access or touching a real file.
- The ambiguity bug came from one exception type carrying two conditions that
  call for opposite handling. Catching a broad error to implement a fallback is
  only safe when every error of that type means the same thing.
- 100% coverage did not catch it, and could not: coverage proves a line ran, not
  that the behavior was right. The mutation checks during review — corrupting a
  key on one branch, dropping half a message — are what exposed assertions that
  would have passed on wrong code.
- Live dry runs against a real Drive found what fakes could not and cost nothing.
  A `mv` integration test remains the obvious follow-on, seeded in the backlog.
- Delegated review agents need explicit constraints. One was told its probes were
  read-only and still ran a recursive delete on an untracked cache directory;
  "no deletions" belongs in the brief, not left to inference.
