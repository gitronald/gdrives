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

Steps 7 to 12 were added on 2026-09-27, while step 2 was in progress. They come from
a second list of gaps by the same downstream caller, in `gdrives.sheets` and in
`gdrives.auth`. One item of that list overlaps a step already here: step 4 refused an
undeclared column of the projection, and the list asks for every column of either
side. Step 4 was widened to that before it was started.

### Steps

Each step is independent, lands as its own branch and PR into `dev`, and keeps line
and branch coverage at 100% with ruff and pyrefly clean. Each also updates the README,
`docs/sheets-sync.md` where it touches sync, and the `[Unreleased]` changelog. Every
step is an addition or an opt-in, and existing behavior stays the default. The full
spec for each step is in `subplans/`.

| # | Step | Scope | Status |
|---|---|---|---|
| 1 | [Exclude named columns from a pull](subplans/1-pull-exclude-columns.md) | `exclude` tab field for pull tabs. It is refused with `columns`, and a name missing from the header refuses the pull | done, [#57](https://github.com/gitronald/gdrives/pull/57) |
| 2 | [Reorder a keyed tab's rows](subplans/2-reorder-rows.md) | `reorder_rows`: whole-row `moveDimension` moves in one batch, with a preview, the re-read guard, and a read-back. Rows the order does not name are refused | done, [#58](https://github.com/gitronald/gdrives/pull/58) |
| 3 | [A store for one entry of a multi-tab JSON file](subplans/3-json-entry-store.md) | `JsonEntryStore`, `entry` and `base_file` in the config, and collisions keyed by path and entry | not started |
| 4 | [Refuse undeclared columns](subplans/4-strict-schema.md) | `strict_schema` tab field: a column of either side with no schema entry is a problem, less the columns a run drops (widened on 2026-09-27) | not started |
| 5 | [Optional `Target.base`](subplans/5-optional-target-base.md) | `base` may be None, with a clear error when a tab without a base store needs it | not started |
| 6 | [Drive revisions, read-only](subplans/6-drive-revisions.md) | `gdrives/revisions.py` and a `revisions` command: list, and download by media or export link, checked against the live API | not started |
| 7 | [Set and check the links of URL cells](subplans/7-url-links.md) | `url_link_problems`, `set_url_links`, and a `link_urls` tab field for sync and push tabs, refused with `clear_links`. How a link is set is checked live first | not started |
| 8 | [Read a tab as displayed](subplans/8-render-option.md) | `render` tab field (`unformatted`, `formatted`), recorded on `Table` so the guard and the read-back read the same way | not started |
| 9 | [Transform the rows a tab is read as](subplans/9-transform-hook.md) | `transform` hook on a pull, run before the checks and the comparison, and on a sync for comparing cells | not started |
| 10 | [Say more in `describe_credentials`](subplans/10-credential-details.md) | `CredentialInfo` says whether OAuth is configured, whether a consent was skipped for lack of a terminal, and why each cached token was passed over | not started |
| 11 | [Name a run's hooks in the config file](subplans/11-config-hooks.md) | `hooks` tab field naming `module:function`. Starts as a design note, and may stop there | not started |
| 12 | [Stricter schema checks](subplans/12-stricter-schema-checks.md) | The schema fields `present` and `strict`. May stop at a write-up | not started |

### Execution order

In the order above, which ranks the steps by how much downstream code each one
unblocks. One step at a time: each merges before the next branches, so the changelog
and `sync.py` never conflict. Step 5 comes after step 3 because a config's `base_file`
feeds `base_stores`, which is what makes a target with no `base` useful.

Steps 7 to 12 follow step 6, in the order of their value to the caller. Step 11 comes
after step 9, whose hook it names, and step 12 after step 4, whose checks it sits beside.

### Rules for steps 7 to 12

- Every change is additive. A config or a call written for 0.13.0 behaves the same,
  with the same report.
- Unit tests use fakes and never the network.
- **A live check needs the owner's word first.** Where a step says to check live, ask
  for a scratch spreadsheet before any request, and touch nothing else.
- No release is cut. Each step updates `[Unreleased]`.
- Steps 11 and 12 are lower value. If the design of one looks doubtful once the code
  is open, its options are written up in the Log and the step stops there.
- Each step reports what was checked live and what was not.

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

### 2026-09-27 — six steps added

Written at 2026-09-27T17:32:12-07:00, while step 2 was being implemented. The owner
gave a second list of six gaps and asked for subplans for those the plan did not
cover. Five were not covered at all, and became steps 7 to 11. The sixth, stricter
schema checks, has three parts. Two were not covered (column presence, and strict
forms for `bool` and `date`). The third, undeclared columns, is step 4 with a wider
reach: step 4 checks the projection and says why it stops there, and the list asks
for every column of either side, less the columns a run drops. Step 4 is left as it
was written, and step 12 adds the wider check as `strict_schema: "all"`.

The list asked for one plan for each item. They are steps of this plan, as the owner
asked when handing the list over, each with its own branch and PR as the first six
have.

Decisions the subplans make, which the list left open:

- **Step 8:** a declared `date` or `datetime` column is still read from its serial
  number under `formatted`, and nothing is refused in the config check. A displayed
  number that does not parse as its declared type is a schema problem.
- **Step 9:** a sync tab takes the hook. It is applied to the sheet's rows before
  the merge, the sheet keeps its text, and a transform must be idempotent.
- **Step 10:** fields of `CredentialInfo` make the three private helpers
  unnecessary, and no public name is added for them.

### 2026-09-27 — step 2: reorder a keyed tab's rows

Written at 2026-09-27T17:37:35-07:00. Branch `feature/reorder-rows`, PR
[#58](https://github.com/gitronald/gdrives/pull/58).

**What landed.** `reorder_rows` and `ReorderResult` in the new `gdrives/sheets/order.py`.
The move planner is a pure function over the rows' identities. The apply reads the
tab, reads its `sheetId`, reads the tab again for the guard, sends every move in one
`batchUpdate`, and reads back. `FakeSheetGrid` models `moveDimension` by the rule the
live API showed, refusals included.

**The live probe**, run before the step, is in
[implementation-notes/001-move-dimension.md](implementation-notes/001-move-dimension.md).
It confirmed the reference on `destinationIndex`, and found one thing the spec did
not have: a destination equal to the row's own index is refused with a 400, so the
planner never sends a move that leaves its row in place.

**Where the work differs from the spec.**

- **The fewest moves, with blank rows, is not the rows less the longest increasing
  subsequence.** The spec's rule holds for a tab with no blank row. A blank row keeps
  its position, so a keyed row that has to cross one always moves: `x, blank, y` to
  `y, blank, x` takes two moves, where the rule gives one. The rows that may stay
  are those with as many blank rows above them in the target as now, and the longest
  increasing subsequence is taken over them.
- **The suite does not use hypothesis**, which the spec said it did. No dependency
  was added. The property is tested by every arrangement of up to six rows, with
  and without blank rows, and by 200 seeded random cases of up to 60 rows. Each case
  replays the moves on a list by the API's rule and compares the count with a
  separate search for the fewest.
- `moved` lists the keys of the rows moved, one for each request, not every row
  whose row number changes. `applied` is True only when moves were written.

**Tests.** 2554 unit tests pass at 100% line and branch coverage, with ruff and
pyrefly clean. The live test, `test_reorder_moves_whole_rows`, was run once by the
orchestrating session and passed: two moves, one up and one down across a blank row,
with a fill and an unnamed column moving with their rows. It makes 3 writes and 5
reads. The rest of the live suite was not run for this step.

### 2026-09-27 — step 4 widened, and step 12 narrowed

Written at 2026-09-27T17:40:11-07:00. The entry above left a choice with the owner:
two settings for undeclared columns (step 4's, and `strict_schema: "all"` in step
12), or one. The owner chose one. Step 4's subplan has an amendment, under which
`strict_schema` checks every column of either side, less the columns a run drops,
and stays a boolean. Step 12 lost its first part and keeps column presence and
strict forms. The entry above is left as it was written, and its last sentence on
step 12 no longer holds.

The amendment lifts one refusal of the config checker under `strict_schema`: a
`schema` entry for a column outside `columns`, which a carried column needs in order
to be declared at all.
