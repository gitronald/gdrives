---
status: done
branch: feature/sheets-sync-a-package-split
---

# 006a — Split `sheets.py` into a `gdrives/sheets/` package

Part of [006](../plan.md). Step 1 of the umbrella's implementation order. It is a
move with no behavior change, and the layout every other subplan builds in.

## Package layout

`gdrives/sheets/` replaces `gdrives/sheets.py`. `__init__.py` re-exports every
name that is importable from `gdrives.sheets` today through `__all__`, so
`from gdrives.sheets import pull_values` and the CLI's lazy imports keep
working.

```
gdrives/sheets/
├── __init__.py    # Re-exports (__all__); the public surface
├── values.py      # spreadsheets.values.* wrappers, list_tabs, first_tab, render options
├── a1.py          # A1 and GridRange helpers (column_letter, a1_quote, split_a1, ...)
├── match.py       # find_rows, set_by_match, parse_pairs
├── rules.py       # Conditional format rules
├── files.py       # Local record files: CSV, TSV, and JSON read/write
├── retry.py       # with_retry and the retryable status sets
├── cells.py       # Canonical cell strings, column types, schema checks
├── table.py       # read_tab -> Table (header-named, keyed rows + row numbers)
├── merge.py       # Pure three-way merge -> MergePlan (no I/O)
├── apply.py       # apply_plan, verify, row inserts
├── structure.py   # Columns, tabs, and column widths (spreadsheets.batchUpdate)
├── config.py      # Load and validate the sync config
├── sync.py        # Orchestration: plan_tab, apply_tab, pull_tab, push_tab, report
└── commands.py    # run_* CLI entry points (existing ones move here)
```

The first step is a move with no behavior change. Tests that patch an internal
name (for example `pull_values` as seen by `set_by_match`) must patch it on the
defining submodule after the move; patches of `gdrives.sheets.run_*` made by the
CLI tests keep working because the CLI resolves those names on the package at
call time.

## Log

### 2026-09-27 — implementation

`gdrives/sheets.py` became `gdrives/sheets/commands.py` by `git mv`, and the rest of the
code was copied into the new submodules by line range, so no function body was retyped.
A syntax-tree comparison of the old module against the new submodules shows the same 51
top-level definitions, none missing, added, or changed.

Only the submodules that receive existing code were created: `values.py`, `a1.py`,
`match.py`, `rules.py`, `files.py`, and `commands.py`. The rest arrive with their steps.

Placement the layout above did not settle:

- `batch_update_spreadsheet`, `tab_sheet_ids`, and the private tab lookups went to
  `values.py`, beside `list_tabs` and `first_tab`. `a1.py`, `rules.py`, and later
  `structure.py` all need them, and this keeps imports one-directional
  (`values` <- `a1` <- `match`, `rules` <- `commands`).
- `format_values` went to `commands.py`. It formats for the terminal and only `run_get`
  calls it, while `files.py` is for local record files.

`__init__.py` re-exports the 40 public names through `__all__`. No existing test imported
or patched a private `gdrives.sheets` name, so the existing tests passed with no edits.
A new `TestPackageSurface` checks that `__all__` matches what the submodules define.

Also updated: the README project tree, and a `[Unreleased]` entry in `CHANGELOG.md`.
`.claude/CLAUDE.md` still lists `sheets.py`; it is updated in step f with the other docs.

Review: three reviewers (conformance, correctness, and test quality) reported no
findings. Checks rerun after the workflow: ruff and pyrefly clean, 787 tests pass, and
coverage is 100%.

Commits: `abbbbd4`, `ecec764`, `936640d`.
