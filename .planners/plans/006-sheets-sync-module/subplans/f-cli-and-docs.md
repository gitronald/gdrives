---
status: draft
branch:
---

# 006f — Wire the CLI commands and write the docs

Part of [006](../plan.md). Step 6 of the umbrella's implementation order. The
commands are thin wrappers over the functions in
[`e-config-and-orchestration.md`](e-config-and-orchestration.md).

## CLI

```bash
gdrives sheets-sync roster                       # Preview every tab of the target
gdrives sheets-sync roster --tab Members         # Preview one tab
gdrives sheets-sync roster --apply               # Write sheet, local file, and base
gdrives sheets-sync roster --apply --adopt       # First sync: rewrite the tab from local
gdrives sheets-sync roster --apply --add-missing # Add local columns the sheet lacks
gdrives sheets-sync roster --apply --drop-extra  # Delete sheet columns outside the projection
gdrives sheets-sync roster --apply --prefer local  # Resolve conflicts toward local
gdrives sheets-pull roster --apply               # Replace local files for the pull tabs
gdrives sheets-push roster --apply               # Replace the push tabs from local files
gdrives sheets-pull <sheet> --all-tabs -o out/   # One-off dump, no config
```

Every command **previews by default** and writes only with `--apply`. The
report lists pushes, folds, new rows, overrides, conflicts, and row flags per
tab, with each cell's before and after values passed through
`gdrives.local.printable`.

Exit codes let automation tell the outcomes apart:

| Code | Meaning |
|---|---|
| 0 | In sync, or every change applied |
| 1 | Error: config, schema, API, or a failed guard |
| 2 | Needs a person: conflicts or row flags remain |

`--apply` with conflicts present still applies the changes that do not
conflict, then exits 2.

## Docs

- `README.md`: a "Sync a Sheet with a local file" section.
- `docs/sheets-sync.md`: the guide, covering the three modes, the merge rules,
  ownership, the base snapshot, and the first sync. It also records one usage
  rule: a column the sheet owns must not be given a default by whatever
  generates the local file, because a default cannot tell "never filled in"
  from "cleared on purpose", and would be pushed back over a deliberate blank.
- `.claude/CLAUDE.md`: package structure and the Commands block.
- `CHANGELOG.md`: entries under `[Unreleased]`.

## Log
