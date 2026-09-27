---
status: done
branch: feature/sheets-sync-c-merge-engine
---

# 006c — Add the three-way merge engine

Part of [006](../plan.md). Step 3 of the umbrella's implementation order. It is pure
functions with no I/O: it takes the records from
[`b-read-layer.md`](b-read-layer.md) and produces the `MergePlan` that
[`d-apply-and-structure.md`](d-apply-and-structure.md) applies.

## Three-way merge

`merge.merge(base, local, remote, key, columns, ...)` is a pure function over
three lists of records and returns a `MergePlan`. `base` is what both sides held
after the last applied sync.

Per cell, for a row present on both sides, with base `b`, local `l`, and sheet
`r`:

| Case | Condition | Result |
|---|---|---|
| In sync | `l == r` | Nothing to do; base becomes `l` |
| Local edit | `r == b`, `l != b` | Push `l` to the sheet; base becomes `l` |
| Sheet edit | `l == b`, `r != b` | Fold `r` into the local file; base becomes `r` |
| Conflict | All three differ | Report it and write neither side; base stays `b` |

A conflict leaves the base where it was on purpose, so the same cell is reported
on every run until a person makes the two sides agree.

Per row, read against the base:

| Row is | In base? | Result |
|---|---|---|
| Local only | No | New local row: append it to the sheet |
| Local only | Yes | Deleted on the sheet: flag `remote_deleted` |
| Sheet only | No | New sheet row: fold it into the local file |
| Sheet only | Yes | Deleted locally: flag `local_deleted` |

Deletions are flags. Neither side's rows are ever removed by a sync.

**Column ownership** overrides the cell rule:

- `local_owned` columns always push the local value. When the sheet also
  changed, the discarded sheet value is reported as an override.
- `sheet_owned` columns always fold the sheet value. A new local row is appended
  with its sheet-owned cells blank, since those values are assigned on the
  sheet.
- `owns_rows: true` makes the row set local-owned: a new sheet row is flagged
  `remote_added` instead of being folded in.
- Columns outside the `columns` projection are **carried**: they exist only in
  the local file, pass through a sync untouched, and never reach the sheet or
  the base.

Key columns are identity and cannot be owned. Ownership sets must not overlap
and must sit inside the projection; the merge raises otherwise.

`MergePlan` holds `pushes`, `appends`, `fold_cells`, `fold_rows`, `conflicts`,
`overrides`, `row_flags`, `new_local`, and `new_base`, plus
`needs_attention` (true when there are conflicts or row flags). `new_local`
keeps the local file's row order, with rows added on the sheet after it.

`--prefer local|sheet` resolves cell conflicts for one run in favor of the named
side and reports each as an override. Without it, conflicts are only reported.

## Log

### 2026-09-27 — implementation

`merge.py` holds `merge()`, `MergePlan`, and the entry types `Cell`, `Override`, `NewRow`,
and `RowFlag`, all frozen dataclasses with named fields. Row identity reuses
`cells.index_rows` and `cells.row_key`.

Decisions on points the sections above leave open:

- **Signature.** `merge(base, local, remote, key, columns, *, local_owned=(),
  sheet_owned=(), owns_rows=False, prefer=None)`.
- **A row on both sides but missing from the base** merges cell by cell against a blank
  base. A cell only one side filled moves to the other, and a cell both sides filled
  differently is a conflict.
- **Key columns** are never pushed, folded, or reported, even when the two sides' stored
  text differs in whitespace. Each side keeps its own text, and the base takes the local
  text.
- **Flagged rows change nothing.** A `remote_deleted` row stays in `new_local` and keeps
  its base row. A `local_deleted` row is not folded in and keeps its base row. A
  `remote_added` row is not folded in and gets no base row. Each flag therefore repeats
  until a person resolves it. A base row that is on neither side is dropped with no flag.
- **Order of `new_base`.** Local rows in order, then rows folded from the sheet, then the
  kept base rows of `local_deleted` rows. Placing those last keeps the order the same on
  the next run; the exhaustive test caught the unstable order that interleaving produced.
- **New local rows and `sheet_owned`.** The row is appended with its sheet-owned cells
  blank, and they are blank in `new_local` and `new_base` too. A non-blank local value
  discarded this way is reported as an override.
- **New sheet rows and `local_owned`.** A folded row keeps its sheet values in
  local-owned columns, with no override, since there is no local value to prefer.
- **Overrides are reported in both directions:** `local_owned` discarding a changed sheet
  value, and `sheet_owned` discarding a changed local value.
- **`prefer`** resolves each cell conflict toward the named side, reports it as an
  override, and moves the base to the winning value. It does not affect row flags.
- **Plan entries name rows by normalized key**, which matches `Table.row_numbers`.

One consequence of the library API in
[`e-config-and-orchestration.md`](e-config-and-orchestration.md#library-api): exporting
the function `merge` from `gdrives.sheets` shadows the submodule of the same name as a
package attribute. `from gdrives.sheets.merge import MergePlan` works, and
`gdrives.sheets.merge.MergePlan` does not, so a test cannot patch a name by the dotted
path `gdrives.sheets.merge.<name>`.

Tests: case tests for each rule, and an exhaustive test over every base, local, and sheet
state of a small domain. It simulates the plan and asserts that settled cells agree on
all three sides, flagged rows are unchanged, no row is removed, and a second merge from
the result is a no-op with the same conflicts and flags.

Review: three reviewers (conformance, correctness, and test quality) reported no
findings. Two of them wrote an independent oracle from the tables above and compared it
with `merge()` over 117,912 enumerated cases, with no disagreement. The orchestrating
session also read `merge.py`. Checks rerun after the workflow: ruff and pyrefly clean,
1095 tests pass, and coverage is 100%.

Commits: `9662324`, `196277d`, `af260fc`.
