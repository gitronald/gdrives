---
status: done
branch: feature/sheets-sync-e-config-and-orchestration
---

# 006e — Add the sync config and orchestration

Part of [006](../plan.md). Step 5 of the umbrella's implementation order. It ties
the read layer, the merge, and the apply step together behind a config file.

## Base snapshots and the first sync

The base is one CSV of canonical strings per tab, holding the merged columns
only. It is meant to be committed alongside the local file, so everyone syncing
the same target shares one base.

A tab with no base yet is bootstrapped by one of two explicit choices:

- `"bootstrap": "local"` (the default): the local file is taken as the base.
  Sheet-only cell edits fold in, and nothing is written to the sheet on that
  first run.
- `--adopt`: the tab is rewritten from the local file and that becomes the base.
  It requires `--apply`, and the preview lists every cell the rewrite would
  change on the sheet.

## Whole-tab pull and push

`pull` and `push` are for tabs with one source of truth, where a merge is more
than is needed.

- `pull_tab` reads the tab and replaces the local file. An empty tab is refused
  and the local file is left alone. When a `key` is configured, the preview
  reports rows added, removed, and changed against the current local file, and
  a drop in row count is called out.
- `push_tab` clears the tab's values and rewrites them from the local file, in
  header order. The preview reports what the sheet holds that the local file
  does not, because that is what the push discards. It runs the same re-read
  guard and read-back check as `sync`. `push` may use `USER_ENTERED`.

`sheets-pull` also works without a config, for a one-off dump:
`gdrives sheets-pull <sheet> --all-tabs -o out/` writes one file per tab, named
from the tab title by `safe_filename` (moved to `gdrives/local.py`, and still
importable from `gdrives.download`). `--skip <tab>` leaves a tab out, which
protects a local file that shares a name with a tab but is produced elsewhere.
Two tabs whose file names collide are refused.

## Config

`gdrives-sheets.json` is found from the working directory upward, the same way
`.env` is, or named with `--config`. Paths inside it are relative to the file.

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

| Field | Level | Meaning |
|---|---|---|
| `spreadsheet` | target | Sheet URL, file ID, or Drive path (resolved by `resolve_file_id`) |
| `base` | target | Directory for base snapshots; default `sheets-base/<target>` |
| `input_option` | target | `RAW` (default) or `USER_ENTERED`; `sync` tabs require `RAW` |
| `mode` | tab | `sync` (default), `pull`, or `push` |
| `local` | tab | Local file; the extension picks the format |
| `key` | tab | Key columns; required for `sync`, optional for `pull` and `push` |
| `columns` | tab | Projection: the columns the sheet carries; default is every local column |
| `local_owned`, `sheet_owned`, `owns_rows` | tab | Ownership, as described in [`c-merge-engine.md`](c-merge-engine.md#three-way-merge) |
| `schema` | tab | Per column: `type`, `required`, and `allowed` |
| `bootstrap` | tab | `local` (default); `--adopt` is a flag, not a config value |
| `insert_above` | tab | One `{column: value or [values]}` pair |
| `widths` | tab | Column widths in pixels, by header name |
| `bom` | tab | Write a byte-order mark to a CSV or TSV |

The loader validates everything before any network call and reports every
problem at once: unknown fields, a missing or empty key, key or owned columns
outside the projection, overlapping ownership, a malformed `insert_above`, and
`USER_ENTERED` on a `sync` tab.

## Library API

The CLI is a thin layer over functions that take a service and plain values, so
a caller with its own config or its own record source uses the same engine:

```python
from gdrives.sheets import merge, read_tab, apply_plan, verify

table = read_tab(service, spreadsheet_id, "Members", columns, key)
plan = merge(base, local, table.rows, key, columns, sheet_owned={"notes"})
```

`sync.plan_tab` and `sync.apply_tab` accept an optional `validate` callable
(`rows -> list[str]`) that runs on the local rows and again on the merged
result, for checks a declarative schema cannot express.

## Announcing the credential

`gdrives.auth.describe_credentials(scopes)` reports which credential a call will
use (OAuth token, service account, or ADC) and whether it will first need
interactive consent. The write commands print that to stderr before the first
API call, so a run waiting on a consent prompt does not look hung, and a write
is not attributed to an identity the user did not expect.

## Log

### 2026-09-27 — implementation

New modules `config.py` and `sync.py`. `auth.py` gained `describe_credentials` and
`CredentialInfo`. `safe_filename` moved to `gdrives/local.py` and is still importable from
`gdrives.download`. `table.py` gained `parse_tab` and `EmptyTabError`, so a grid read once
can be parsed more than once. No CLI command is added in this step.

`sync.py` provides `plan_tab`, `apply_tab`, and `sync_tab` (both in one call), `pull_tab`,
`push_tab`, `pull_all_tabs`, `run_target`, `TabReport` and `SyncReport` (with `exit_code`
0, 1, or 2), and a pure `format_report`.

Two readings that depart from the wording above:

- **`--adopt` is a merge, not a rewrite of the tab.** The local file is merged against an
  empty base with every non-key column local-owned and the row set local-owned. The local
  file wins every differing cell, including `sheet_owned` columns, and local-only rows are
  appended. Sheet columns outside the projection survive, and a sheet row the local file
  lacks is flagged `remote_added` and left in place.
- **`push_tab` writes once.** One `values.update` covers the old and new extents of the
  tab, padded with blanks, in place of a clear followed by a write, so a failure cannot
  leave the tab empty. The guard compares the grid read at apply time with the preview
  read.

Other decisions:

- **Order of `apply_tab`:** the schema and `validate` checks, the structure steps, a
  second read and merge, `apply_plan`, the local file, the base, and then the column
  widths. The local file and the base are written only when they change.
- **Bootstrap writes nothing to the sheet,** even for `local_owned` columns. Those pushes
  are held back (`TabReport.deferred`), and their base cells take the sheet value, so the
  next run pushes them as local edits.
- **Bootstrap and `--adopt` do not combine after the fact.** A bootstrap run with
  `--apply` saves a base, local rows the sheet lacks are then flagged `remote_deleted` on
  every run, and `--adopt` is refused once a base exists. The way out is to delete the
  base and run with `--adopt`; the refusal says so.
- **A missing or header-less tab with no base** is merged against an empty base, so on
  apply the tab is created, its header written, and the local rows appended. A header-only
  tab is bootstrapped instead. A missing or empty tab that has a base is refused.
- **A key column the sheet lacks** is refused even with `add_missing`, since an added key
  column would hold blank keys.
- **A missing local file** is refused for sync and push, and created by pull.
- **Column widths** are set only on a run that wrote to the sheet, so a no-op run makes
  no write.
- **Config.** Collisions between files that tabs write (local files and base files,
  compared case-folded) are checked across the whole config. Ownership, `owns_rows`,
  `bootstrap`, and `insert_above` on a pull or push tab are refused, as is a base
  directory inside `.gdrives/`. An `insert_above` column must be in the projection.
- **A preview's exit code** is 0 when the run can go ahead, whether or not it found
  changes to make, and 2 when conflicts or row flags remain.
- **`push_tab` under `USER_ENTERED`** checks only the header and the row count on
  read-back, and the report says so.
- **`describe_credentials`** follows the precedence of `authenticate()` through shared
  helpers (`_load_token`, `_needs_refresh`, and `_can_consent`), reads local files only,
  and holds no token, key, or secret. `authenticate_oauth` was split into those helpers
  with the same behavior.

Review: the conformance and test-quality reviewers reported no findings. The correctness
reviewer found one high-severity defect, confirmed by a verifier: `apply_tab` ran the
structure steps and only then re-ran the schema and `validate` checks on the second
merge, so a problem found there left a structure write on the sheet beside a report
saying nothing was written. The fix (`89c507a`): the checks on the first plan are the
only checks, and the second merge must equal the checked one, with the same local file.
A sheet or local edit made during the restructure raises `SheetChangedError`.

The orchestrating session also read `sync.py` and the `auth.py` changes, and extended the
adopt refusal to say how to start over.

Not verified against the real API: that a blank string in the padded `values.update` of
`push_tab` clears the old cell. The fake treats it that way, and step f's live tests
cover it.

Checks rerun after the workflow: ruff and pyrefly clean, 1510 tests pass, and coverage is
100%.

Commits: `62c3706`, `146a34c`, `37d6021`, `e1f0c41`, `f168b58`, `4c644cc`, `9c88727`,
`0f07d43`, `89c507a`, and the refusal message.
