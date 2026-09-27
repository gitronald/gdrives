---
status: draft
branch:
---

# 007d — Give the check hooks the columns and the plan

Part of [007](../plan.md).

## Warnings

- `warn`, a second hook with the signature of `validate`, on `plan_tab`, `sync_tab`,
  `pull_tab`, `push_tab`, `push_rows`, and `run_target`. Its messages go to
  `TabReport.warnings` and are printed. They never block a write and never change the
  exit code. It runs on the merged rows, after `validate` has passed.
