---
status: active
branch: feature/sheets-sync-adoption-d-checks-and-hooks
---

# 007d — Give the check hooks the columns and the plan

Part of [007](../plan.md). Step 4 of the umbrella's implementation order. It needs the
`MergePlan` of [`c-merge-additions.md`](c-merge-additions.md), and
[`e-stores.md`](e-stores.md) and [`f-push-rows-and-links.md`](f-push-rows-and-links.md)
pass its hooks through.

Notes applied: R11, D3.

## The gap

`validate` is the way in for a caller whose checks are code, not the config's inline
schema. It receives rows and nothing else, and two notes found that too narrow:

- It cannot check columns [D3]. Two checks need them: that every column is declared,
  and that every required column is present. The merged rows hold the local file's
  columns only (`sync.py:479`), so a column that exists only on the sheet is invisible
  to it, though the tab's read has the list.
- A warning scoped to what a run folded needs the plan [R11]. With rows alone, a check
  that warns when a folded edit leaves sibling rows divergent warns on every
  divergence, on every run.

## `CheckContext`

```python
@dataclass(frozen=True)
class CheckContext:
    tab: str  # the tab's title
    stage: str  # "local", "merged", or "sheet"
    rows: Sequence[Mapping[str, str]]
    columns: tuple[str, ...]  # the columns of rows
    projection: tuple[str, ...]
    sheet_columns: tuple[str, ...] | None
    adding: tuple[str, ...]
    dropping: tuple[str, ...]
    plan: MergePlan | None

    @property
    def extra_columns(self) -> tuple[str, ...]: ...


Check = Callable[[CheckContext], list[str]]
```

- `stage` is where the rows come from: `"local"` for the local rows of a sync or a
  push, `"merged"` for a sync's merged result, and `"sheet"` for the rows a pull read.
- `sheet_columns` is the sheet header's named columns. It is None when the sheet has
  not been read yet (the local stage, which runs before any request) and when the tab
  is missing or has no header.
- `adding` and `dropping` are the columns this run adds to the sheet and deletes from
  it. `extra_columns` is the sheet's columns outside the projection, less `dropping`: a
  column a run is about to delete is not one to check, since the usual reason to drop
  it is that the schema no longer declares it [D3].
- `plan` is the merge, at the merged stage, and None at the others.

## The hooks

`validate` keeps its signature and its meaning (decision 10). Two hooks are added, both
taking a `CheckContext`:

| Hook | Receives | Blocks a write | Runs |
|---|---|---|---|
| `validate` | rows | yes | at every stage, as today |
| `check` | `CheckContext` | yes | at every stage, after `validate` |
| `warn` | `CheckContext` | no | once, at the run's last stage, after every blocking check passed |

- All three are on `plan_tab`, `sync_tab`, `pull_tab`, `push_tab`, `push_rows`, and
  `run_target`.
- `check`'s messages join `TabReport.problems`, prefixed like `validate`'s.
- `warn`'s messages go to `TabReport.warnings` and are printed. They never block a
  write and never change the exit code.
- A run with problems runs no `warn`: its messages would describe rows that are not
  written.

## Warnings across a restructure [R11]

`_plan` resets the report's lists on entry (`sync.py:378`), and the merge after a
restructure runs with `check=False`, since it must equal the one the checks passed.

- `_plan` resets `warnings` only when `check` is True.
- With `check=False` no hook runs, so a warning is produced once and carried across.
- `TabPlan` keeps `check` and `warn` beside `validate`, for the second `_plan` call.

## What stays outside

- The `sheets-sync` command has no way to name a hook, so a caller with a schema in
  code keeps a command of its own. That is acceptable, and the guide says so
  ([`h-docs-and-release.md`](h-docs-and-release.md)).
- A schema setting that refuses an undeclared column would serve a config-only user.
  `check` covers the caller that asked, and the setting is out of scope.

## Tests

- A `check` that refuses an undeclared column on either side, and passes it when the
  run drops it.
- A `check` for a required column that neither side has.
- `sheet_columns` at each stage, and for a missing and an empty tab.
- A `warn` that reads `plan.fold_cells`, over a run that folds and one that does not.
- A sync with `add_missing` and a `warn`: one warning in the report after the
  restructure, and the hook called once.
- `warn` with a problem in the run: not called.
- The exit code with warnings only: 0.

## Config and docs in this step

- No config field: hooks are code.
- `format_report` prints warnings under their own heading, after problems.
- Changelog: `CheckContext`, `check`, and `warn` under Added.

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-d-checks-and-hooks`, cut from step c's branch,
with a draft PR onto it. One commit, `e2956c6`: `CheckContext`, `check`, `warn`,
`TabReport.warnings`, the report's heading, the guide's table of hooks, and the
changelog line. `push_rows` takes the hooks in step f, where it is added.

Decisions made during the work:

- **At the merged stage `columns` is the local file's columns**, since the merged rows
  are the local side as it will be. At the sheet stage of a pull, `columns` and
  `projection` are both the columns read, and `sheet_columns` is every named column
  of the header.
- **`projection` on a push is the columns written**, in the local file's order.
- **`warn`'s messages are stored as given**, with no `T (merged):` prefix. They come
  from one stage, and the report prints them under the tab.
- **A pull and a push reset `warnings` on entry**, as `_plan` does, so a report passed
  in for a second run does not keep the first run's.
- **`STAGES`** names the three stages, and is exported with `CheckContext`. `Check`,
  the hook's type, is in `sync.py` beside `Validate`.
- The last four fields of `CheckContext` have defaults, so a caller's test can build
  one from the rows and the columns.

**Live suite.** Not run for this step. The step adds no live case, and changes no
request a run makes: the hooks are called on rows already read. The counts stand at
68 writes and 84 reads.
