---
id: 18
slug: downstream-adoption-gaps
status: active
branch: feature/downstream-adoption-gaps
created: 2026-09-27T17:04:20-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/59
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

**Changed on 2026-09-27, after step 2:** steps 1 and 2 each landed as a branch and a
PR of its own. Every step from 3 on lands on one branch,
`feature/downstream-adoption-gaps`, which has one PR into `dev`.

| # | Step | Scope | Status |
|---|---|---|---|
| 1 | [Exclude named columns from a pull](subplans/1-pull-exclude-columns.md) | `exclude` tab field for pull tabs. It is refused with `columns`, and a name missing from the header refuses the pull | done, [#57](https://github.com/gitronald/gdrives/pull/57) |
| 2 | [Reorder a keyed tab's rows](subplans/2-reorder-rows.md) | `reorder_rows`: whole-row `moveDimension` moves in one batch, with a preview, the re-read guard, and a read-back. Rows the order does not name are refused | done, [#58](https://github.com/gitronald/gdrives/pull/58) |
| 3 | [A store for one entry of a multi-tab JSON file](subplans/3-json-entry-store.md) | `JsonEntryStore`, `entry` and `base_file` in the config, and collisions keyed by path and entry | done, on the branch |
| 4 | [Refuse undeclared columns](subplans/4-strict-schema.md) | `strict_schema` tab field: a column of either side with no schema entry is a problem, less the columns a run drops (widened on 2026-09-27) | done, on the branch |
| 5 | [Optional `Target.base`](subplans/5-optional-target-base.md) | `base` may be None, with a clear error when a tab without a base store needs it | done, on the branch |
| 6 | [Drive revisions, read-only](subplans/6-drive-revisions.md) | `gdrives/revisions.py` and a `revisions` command: list, and download by media or export link, checked against the live API | done, on the branch |
| 7 | [Set and check the links of URL cells](subplans/7-url-links.md) | `url_link_problems`, `set_url_links`, and a `link_urls` tab field for sync and push tabs, refused with `clear_links`. How a link is set is checked live first | done, on the branch |
| 8 | [Read a tab as displayed](subplans/8-render-option.md) | `render` tab field (`unformatted`, `formatted`), recorded on `Table` so the guard and the read-back read the same way | done, on the branch |
| 9 | [Transform the rows a tab is read as](subplans/9-transform-hook.md) | `transform` hook on a pull, run before the checks and the comparison, and on a sync for comparing cells | done, on the branch |
| 10 | [Say more in `describe_credentials`](subplans/10-credential-details.md) | `CredentialInfo` says whether OAuth is configured, whether a consent was skipped for lack of a terminal, and why each cached token was passed over | done, on the branch |
| 11 | [Name a run's hooks in the config file](subplans/11-config-hooks.md) | `hooks` tab field naming `module:function`. Starts as a design note, and may stop there | done, on the branch |
| 12 | [Stricter schema checks](subplans/12-stricter-schema-checks.md) | The schema fields `present` and `strict`. May stop at a write-up | done, on the branch |

### Execution order

In the order above, which ranks the steps by how much downstream code each one
unblocks. One step at a time: each merges before the next branches, so the changelog
and `sync.py` never conflict. Step 5 comes after step 3 because a config's `base_file`
feeds `base_stores`, which is what makes a target with no `base` useful.

From step 3 on, the steps are still done one at a time and in order, as commits on the
one branch. Nothing merges into `dev` between them. The branch's PR is merged when the
plan is closed.

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

### 2026-09-27 — one branch for the remaining steps

Written at 2026-09-27T17:45:47-07:00, at the owner's word, while step 3 was being
implemented. Steps 1 and 2 are merged, as PRs #57 and #58. No step after them has a
PR of its own: steps 3 to 12 land on `feature/downstream-adoption-gaps`, the branch
the plan was activated with, and that branch has one PR into `dev`.

Step 3 had been started on `feature/json-entry-store`, which had no PR. Its commits
become the first commits of the one branch, and the step branch is deleted.

What follows from it:

- The steps stay in order, one at a time, each by a subagent in the branch's
  worktree, and each is reviewed and corrected before the next starts.
- Each step's Log entry is a commit on the branch, as before.
- CI runs on the branch's PR after each step is pushed.
- Nothing is merged into `dev` until the plan is closed, so the review of the whole
  diff that plan 007's retrospective asked for comes before the merge.

### 2026-09-27 — the steps run in parallel

Written at 2026-09-27T17:55:21-07:00, at the owner's word: the steps were going too
slowly one at a time. The rule of one step at a time, in the execution order and in
the entry above, no longer holds. What replaces it:

- Each step is implemented in a worktree and on a working branch of its own
  (`feature/downstream-adoption-gaps-<step>`), which is never pushed and has no PR.
  The orchestrating session merges each into `feature/downstream-adoption-gaps` as it
  finishes, runs the checks on the result, and resolves what conflicts.
- A step starts as soon as the steps it depends on are merged: 5 after 3, 12 after 4,
  9 after 8, and 11 after 9. Steps 4, 6, 8, and 10 started together while step 3 was
  finishing, and steps 5 and 7 when it was merged.
- Each agent is told that others are at work, to keep its edits to the shared files
  small and local, to add to the end of the changelog's list, and to list the shared
  files it changed.
- The live requests are still the orchestrating session's alone.

The owner named the spreadsheet for step 7's live check: the test spreadsheet, on a
temporary tab made for the probe and deleted after it.

### 2026-09-27 — step 3: a store for one entry of a JSON file

Working branch `feature/json-entry-store`, merged into the plan's branch as `5c6065a`.

**What landed.** `JsonEntryStore` in `stores.py`, the `entry` tab field, the
`base_file` target field, and `_Checker.collisions` keyed by path and entry. The
JSON reading and writing of `files.py` is in three helpers that `read_records`,
`write_records`, and the store share: the two the spec named, and `_json_text`, which
holds the dump's settings, so the two ways of writing a JSON file cannot differ.

**Decisions where the spec was silent.**

- A tab's local entry and the `base_file` entry of the same file are two entries when
  their names differ, and are allowed. The same name is refused.
- A path written whole by one tab and by entry by another is refused with a message
  of its own.
- `base_file` on a target with no sync tabs is accepted and unused, as `base` is.
- `entry` with `bom` or with `newline: "crlf"` is refused by the checks a `.json`
  local file already had.

**Byte stability has one exception, which is not new.** A `float` cell that holds
`-0.0` is rewritten as `0.0` by a write that changes nothing else, since `to_cell`
gives `0` for it. The library writes `-0.0` itself when given the cell `-0`. A flat
`.json` file has the same behaviour today. It is left as it is, and no test covers
it. Every other value tried is byte-identical after a rewrite: floats such as `0.1`,
`1e22`, and `5e-324`, whole numbers past 64 bits, dates, blanks, and text outside
ASCII.

**Tests.** 2632 unit tests pass at 100% line and branch coverage on the plan's branch
after the merge, with ruff and pyrefly clean. The spec asks for no live test, and
none was run.

### 2026-09-27 — steps 5, 8, and 10

Written at 2026-09-27T18:01:35-07:00. The three were merged into the plan's branch in
the order they finished: 8 (`0c9736d`), 5, and 10 (`065164a`). After the last merge,
2689 unit tests pass at 100% line and branch coverage, with ruff and pyrefly clean.
None of the three has a live test, and no live request was made for them.

**Merging.** Step 8 conflicted with step 3 in four files, and step 10 in the
changelog. Every conflict was two additions at one place, and both were kept: an
import line in `config.py`, a sentence of the README, a line of the guide test's
promised names, and the end of the changelog's list.

**Step 8, read a tab as displayed.** `render` is a tab field and `Table.render`
carries it, so the guard and the read-back read as the preview did without a caller
passing the setting twice. One private helper, `_pull_rendered`, turns the setting
into a request. The requests of four default runs were recorded before any code
changed (`tests/default_requests.py`) and are what the default is tested against.
`pull_serials` does send its own render options, so a declared date column arrives
as ISO 8601 under `formatted`, and nothing is refused in the config check.
Beyond the spec: `add_columns`, `place_columns`, `delete_columns`, and
`set_column_widths` take `render=`, since each reads the header during a run.

**Step 5, optional `Target.base`.** As specified. The subagent left `collisions`
unguarded, on the ground that a config always sets `base`. The spec asks for the
guard anyway, and the review added it (a sync tab of a target with no base and no
store for it is skipped there), with a test.

**Step 10, more in `describe_credentials`.** The five fields and `PassedToken` are
as specified. Two things the spec did not say:

- **The new fields are left out of equality and hashing** (`compare=False`).
  `describe_credentials` now fills them on every branch, so a comparison with a
  `CredentialInfo` built from the old fields, which the suite and a caller both
  make, would otherwise stop matching.
- **`invalid` is decided in `describe_credentials`**, where the check of a loaded
  token already was. The other three reasons come from `_load_token_reason`, which
  `_load_token` now calls, so `authenticate_oauth` and `describe_credentials` pass
  over a token for the same reason by the same code.

### 2026-09-27 — step 6: Drive revisions, read-only

Merged into the plan's branch, with a fix from the review after it (`e9c4f4d`). 2722
unit tests pass at 100% line and branch coverage, with ruff and pyrefly clean.

**The live probe** was run before the step, by the orchestrating session, on the
`drive.readonly` scope, and is in
[implementation-notes/002-drive-revisions.md](implementation-notes/002-drive-revisions.md).
What it changed in the design:

- An export link answers with a body when it fails: a 429 after about ten fetches in
  a few seconds, and a 401 without credentials, each an HTML page. The download
  checks the status, retries a 429 or a 5xx, and writes nothing but the body of a
  200.
- `revisions.get_media` on a native file fails with a 404 that reads as a missing
  revision. The file's MIME type decides, before any such call.
- `revisions.list` returned a 500 once and succeeded on every repeat, so it is
  retried, and `revisions.get` with it.
- An old revision of a native file can be exported, not only the head.
- `acknowledgeAbuse` is refused on a metadata read and is left out.

**What landed.** `gdrives/revisions.py` (`Revision`, `list_revisions`,
`download_revision`, `EXPORT_EXTENSIONS`, `LIST_FIELDS`) and the `revisions` command.
A test runs each public function against a fake service that raises on any method
outside `files.get`, `revisions.list`, `revisions.get`, `revisions.get_media`, and
the GET of an export link.

**From the review.** The subagent's `download_revision` took an extension in the
argument the spec names `mime_type`. It now takes a MIME type or an extension, and
the command's `--format` passes an extension as before.

**Decisions where the spec was silent.**

- A directory output names the file `<name>-<revision id>.<extension>`.
- An output that is not an existing directory is taken as a file path.
- A format given for a file stored as-is is refused, not ignored.
- `--json` prints the fields of `Revision`, not the API's own objects.
- `with_retry` is imported from `gdrives.sheets.retry`. It is the only backoff in the
  package, and it was not moved.

**Live test.** `tests/test_revisions_integration.py` was run once by the
orchestrating session: the listing and the download of the newest revision of the
test spreadsheet as `.xlsx` passed. The test of a file stored as-is was skipped,
since the test setup has no such file: it reads `GDRIVES_TEST_FILE_ID`, which is not
set. The probe did fetch a stored file's revision by `get_media`, and got the bytes
its `size` named.

### 2026-09-27 — steps 7 and 9

Written at 2026-09-27T18:10:41-07:00. Both are merged into the plan's branch
(`5631dc5`). 2819 unit tests pass at 100% line and branch coverage, with ruff and
pyrefly clean.

**Merging found the first conflict of meaning.** Step 7 read a tab's values with
`pull_values` and two render constants, which step 8 had taken out of
`structure.py` for its own helper. Git merged the file without a word, and ruff and
pyrefly both named the three missing names. The read now goes through
`_pull_rendered`, unformatted, since a URL cell is text and reads the same either
way. The guide test's counts of examples conflicted as expected and were set from
the guide: 8 JSON and 9 Python.

**Step 7, the links of URL cells.** The live probe, run with the owner's word on a
temporary tab of the test spreadsheet, is in
[implementation-notes/003-url-links.md](implementation-notes/003-url-links.md):

- A link set as the cell's own format takes, on plain text and on a URL. The guide
  said it did not, and is corrected.
- A link sent in the request that clears the text format runs is dropped, with no
  error. The runs are cleared by an earlier request of the same batch.
- The link, the colour, and the underline go in one request, and bold survives.
- A link alone underlines and colours the text in the effective format only, so the
  check reads the effective format.
- The oversized grid read was not reproduced on a fresh tab of 1000 rows. The read
  is bounded all the same, to the rows and columns of the URL cells.

What landed: `url_link_problems`, `set_url_links`, `UrlLinkProblem`,
`URL_LINK_REASONS`, the `link_urls` tab field, `link_urls=` on `apply_plan` and
`push_rows`, `ApplyResult.linked`, and `TabReport.linked`. A tab with N cells to
fix, R of them with runs, costs one batch of N + R requests. Where the spec was
silent: `URL_LINK_REASONS` is a frozenset, as the package's other sets of names are;
the match of `http` ignores case; and a run fixes exactly the cells it wrote, not
their rows by their columns.

The live test, `test_set_url_links_keeps_the_bold_and_clears_the_runs`, was run once
by the orchestrating session and passed.

**Step 9, the transform hook.** The sync part landed with the pull part, and the
spec's way out was not needed. `TabPlan.table` stays the tab as read, and the new
`TabPlan.seen` is the table the transform returned, which the merge compares. So the
guard compares a raw read with a raw read, the read-back checks pushed cells
against a raw read, and a cell the transform alone changed is equal on all three
sides and is not pushed. `apply.py` is unchanged. One helper, `_on_sheet`, renames a
push from its transformed key to the key its row has on the sheet.

Where the spec was silent, or the work differs:

- **`Transform` and `TitledTransform` are in `gdrives.sheets.sync` and not in
  `gdrives.sheets`**, as `Validate` and `Check` are. The package's surface test
  takes only names a submodule defines, and a type alias is not one.
- `insert_above` matches the values as read, not as cleaned, since the insert point
  is worked out on the raw re-read.
- The transform runs a second time in the merge that follows a restructure, so it
  has to be deterministic as well as idempotent. The guide says both.
- **A local row whose key equals a sheet row's key as read, but not as transformed,
  is refused at apply and not flagged by a preview.** It takes a transform that
  changes key cells. It is left as it is.

No live test was asked for or run for step 9.

### 2026-09-27 — step 4: refuse undeclared columns

Written at 2026-09-27T18:12:47-07:00. Merged into the plan's branch (`002f638`), by
the amendment and not by the design above it. 2843 unit tests pass at 100% line and
branch coverage, with ruff and pyrefly clean. No live test was asked for or run.

**Merging.** The step had branched from `dev` before any other step landed, and
conflicted in eight files. Every conflict was two additions at one place. Two were
resolved by hand and not by keeping both: the config check of the tab, where the
call that builds the schema had to take `strict_schema` and follow the check of
`render`, and the guide test's counts, now 9 JSON examples and 9 Python.

**What landed.** `TabConfig.strict_schema`, the tab field, and `strict_schema=` on
`push_rows`. A sync checks the local side's columns at the `local` stage, before any
request, and the sheet's columns outside the projection at the `sheet` stage, less
the columns `drop_extra` deletes. A pull checks the header's named columns, less the
ones `exclude` names. A push checks every column of the local side.

**A column on both sides is reported once with no code to do it.** The check of the
local side runs first and stops the plan before the sheet is read, so a column that
both sides have and the schema lacks is only ever reported at the `local` stage.

**Lifting the refusal of a schema column outside `columns` needed no other
change.** The subagent traced each user of the schema and the types: the merge,
`parse_tab`, `pull_serials`, and the JSON writer each keep only the entries of the
columns they are about to read or write. So a declared carried column is written by
its type, which is the point, and no sheet column outside the projection is read or
written for it.

### 2026-09-27 — step 11: hooks named in the config

Written at 2026-09-27T18:22:41-07:00. Merged into the plan's branch (`5becb78`). 2894
unit tests pass at 100% line and branch coverage, with ruff and pyrefly clean. No
live test was asked for or run.

**The design note came first**, and is in
[implementation-notes/004-config-hooks.md](implementation-notes/004-config-hooks.md).
Its verdict was that the design as the subplan proposed it was doubtful, and that
two changes made it sound. The step was implemented with both:

- **Reading a config imports nothing.** `load_config` and `parse_config` check a
  name's form, `module:function`, and no more. The subplan had a name that does not
  import as a config problem listed by `load_config`, which would have run the
  import-time code of every named module for every reader of a config, a preview
  and a library caller included. `resolve_hooks` does the importing. The commands
  call it before the credentials are built and before any request, and
  `run_target` calls it before its first request.
- **A module is looked for on `sys.path` alone.** The config's directory is not
  added, so a file beside a config shadows nothing and is not run.

**What landed.** `gdrives/sheets/hooks.py` (`resolve_hooks`, `tab_hooks`), `HOOKS`,
the `hooks` field of a tab and of a target, and `TabConfig.hooks`, which holds the
names only. A target's hooks are the default for its tabs, hook by hook. A hook
found this way is wrapped: what it raises, and a return of the wrong kind, become the
tab's error and name the hook, and the run goes on to the next tab. A tab's config
hooks run before the hooks given in code.

**From the review.** The wrapper's message for a return of the wrong kind quoted the
start of the value returned, which for a hook given the rows can be a tab's cells,
printed in a report. It now names the types alone: `returned a list holding a dict,
not a list of messages`.

**Left open.** A tab cannot switch off a hook its target names, except by naming
one of its own. A hook's traceback is not printed: the error carries the exception's
type and message.

### 2026-09-27 — step 12, and the state of the branch

Written at 2026-09-27T18:56:53-07:00. Step 12 is merged into the plan's branch
(`085771b`), and with it every step of the plan is implemented. Both of its checks
landed, and neither stopped at a write-up.

**What landed.** `ColumnSchema.present` and `ColumnSchema.strict`, the two schema
fields of the config, and `STRICT_TYPES`. `strict` on a column that is not `bool` or
`date` is refused by the config check and by `ColumnSchema` itself. The strict forms
are in `cell_problem`, so a sheet value about to be folded that fails one is a
schema problem, and is held under `on_invalid: hold`.

**A respelling that compares equal needed a check of its own.** The subplan has
`true` on the sheet beside `TRUE` locally reporting a problem and no edit. The merge
checks only a sheet value it is about to fold, and two cells that compare equal are
never folded, so that case never reached `cell_problem`. `_respelling_problems` finds
it after the merge, and the merge is unchanged. Such a cell refuses the tab under
either `on_invalid` setting, since nothing would be written for `hold` to hold back.

**From the review.** The subagent's version gave that check a fallback message for a
case it called unreachable, to keep branch coverage at 100%. The fallback is gone:
the check reports what `cell_problem` says and nothing when it says nothing, and a
test calls it directly for each outcome.

**Where the work differs from the subplan.**

- `2026-9-27` is refused with or without `strict`, since `date.fromisoformat` does not
  read it on any Python the package supports. The tests of the strict form use
  `20260927` and `2026-W39-7`.
- A push checks `present` against the columns it writes. The header of the tab it
  replaces is not looked at.
- A `present` column that `exclude` names is refused by the check step 1 already
  made of any schema column.

**The branch.** 2949 unit tests pass at 100% line and branch coverage, with ruff and
pyrefly clean, and CI passed on Python 3.11 to 3.14. The whole live suite was run
once on the finished branch by the orchestrating session: 36 passed and 1 skipped
in 153 seconds. The one skipped is the download of a revision of a file stored
as-is, which needs `GDRIVES_TEST_FILE_ID`. The test spreadsheet holds the tabs it
held before the plan, and no tab of a probe or a test was left behind.

**Not yet done.** The review of the whole diff against `dev`, which plan 007's
retrospective asked for, and the close. Interactions between steps that were built
side by side (`transform` with `link_urls` and `render`, `strict_schema` with
`entry`) are covered only as far as each step's own tests reach.
