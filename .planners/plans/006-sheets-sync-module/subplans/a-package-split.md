---
status: draft
branch:
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
