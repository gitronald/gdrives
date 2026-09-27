---
status: draft
branch:
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
