---
id: 18
slug: downstream-adoption-gaps
status: active
branch: feature/downstream-adoption-gaps
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
| 1 | [Exclude named columns from a pull](subplans/1-pull-exclude-columns.md) | `exclude` tab field for pull tabs. It is refused with `columns`, and a name missing from the header refuses the pull | done, [#57](https://github.com/gitronald/gdrives/pull/57) |
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

## Log

### 2026-09-27 — how the steps are run

Written at 2026-09-27T17:24:00-07:00. The plan was activated on `dev` with the branch
name `feature/downstream-adoption-gaps`, which no branch carries: each step has its
own branch off `dev`, named for its subplan, and its own PR into `dev`. Each step is
implemented by a subagent in a worktree of its own, on a model picked by the step's
difficulty, and is then reviewed, corrected, and merged by the orchestrating session.
The subagents run the unit tests only. The live probes and the live tests are run by
the orchestrating session, to keep the requests under the API's quota.

Two live probes were run before the steps that depend on them, and their findings go
to those steps: `moveDimension`'s index rule for step 2, and the Drive revisions
calls for step 6.

### 2026-09-27 — step 1: exclude named columns from a pull

Branch `feature/pull-exclude-columns`, PR
[#57](https://github.com/gitronald/gdrives/pull/57).

**What landed.** `TabConfig.exclude` and the `exclude` tab field. The config checker
refuses it on a sync or push tab, with `columns`, with a blank or repeated name, and
when it names a `key`, `schema`, or `widths` column. `pull_tab` reads the header
first, refuses a name the header lacks (listing every one, and leaving the local
side alone), refuses a tab whose named columns are all excluded, and passes the
header's named columns less `exclude` to `parse_tab`.

**Beyond the spec.** The review of the subagent's work changed two things:

- `pull_tab` refuses an `exclude` that names a `key` or `schema` column, before any
  request. The first version dropped the excluded columns from the declared types and
  went on, which left the schema check to run over a column the rows did not hold.
  A config never gets there, since the checker refuses it, so this is for a tab built
  in code.
- `plan_tab` and `push_tab` refuse a tab with `exclude`. The spec kept the mode check
  in the config checker, and a sync tab built in code with `exclude` would have
  written the named columns to disk with no word of it. For a field whose purpose is
  to keep values off the disk, a refusal is the safer reading.

**Tests.** 2300 unit tests pass at 100% line and branch coverage, with ruff and
pyrefly clean. Two tests use a synthetic sentinel value in an excluded column: one
asserts it is in neither the report text nor the file bytes, and one that no
`validate`, `check`, or `warn` hook is given it. No live test was added or run for
this step, since a pull with `exclude` makes the same requests as one without.
