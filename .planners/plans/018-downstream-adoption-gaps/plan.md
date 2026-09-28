---
id: 18
slug: downstream-adoption-gaps
status: draft
branch:
created: 2026-09-27T17:04:20-07:00
concluded:
pr:
---

# Close the remaining gaps that keep a downstream sync off gdrives.sheets

## Plan

### Goal

A downstream project compared its own sheet sync code against `gdrives.sheets` 0.13.0.
It found that most of that code can be replaced by the library, except where a few gaps
get in the way. This plan closes the remaining six. The first gap, one fixed form for
datetime cells, shipped as [plan 011](../011-datetime-cell-form/plan.md). The six steps
below were drafted as plans 012 to 017. They were folded into this one plan and those
plans were retired.

### Steps

Each step is independent, lands as its own branch and PR into `dev`, and keeps line
and branch coverage at 100% with ruff and pyrefly clean. Each also updates the README,
`docs/sheets-sync.md` where it touches sync, and the `[Unreleased]` changelog. Every
step is an addition or an opt-in, and existing behavior stays the default. The full
spec for each step is in `subplans/`.

| # | Step | Scope | Status |
|---|---|---|---|
| 1 | [Exclude named columns from a pull](subplans/1-pull-exclude-columns.md) | `exclude` tab field for pull tabs. It is refused with `columns`, and a name missing from the header refuses the pull | not started |
| 2 | [Reorder a keyed tab's rows](subplans/2-reorder-rows.md) | `reorder_rows`: whole-row `moveDimension` moves in one batch, with a preview, the re-read guard, and a read-back. Rows the order does not name are refused | not started |
| 3 | [A store for one entry of a multi-tab JSON file](subplans/3-json-entry-store.md) | `JsonEntryStore`, `entry` and `base_file` in the config, and collisions keyed by path and entry | not started |
| 4 | [Refuse undeclared columns](subplans/4-strict-schema.md) | `strict_schema` tab field: a projection column with no schema entry is a problem | not started |
| 5 | [Optional `Target.base`](subplans/5-optional-target-base.md) | `base` may be None, with a clear error when a tab without a base store needs it | not started |
| 6 | [Drive revisions, read-only](subplans/6-drive-revisions.md) | `gdrives/revisions.py` and a `revisions` command: list, and download by media or export link, checked against the live API | not started |

### Execution order

In the order above, which ranks the steps by how much downstream code each one
unblocks. One step at a time: each merges before the next branches, so the changelog
and `sync.py` never conflict. Step 5 comes after step 3 because a config's `base_file`
feeds `base_stores`, which is what makes a target with no `base` useful.

This plan stays `active` until the last step merges. Each step's status is updated
here, and each step's work is logged in this plan's `## Log`.

### Out of scope

- The draft plans 009, 010, and 004, which are separate efforts.
- Anything the downstream project does beyond these gaps. Its private details stay out
  of this public repo. Consumers are described generically and fixtures are synthetic.
