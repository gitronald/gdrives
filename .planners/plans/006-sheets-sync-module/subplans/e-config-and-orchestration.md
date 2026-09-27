---
status: draft
branch:
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
