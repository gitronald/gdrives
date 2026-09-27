---
id: 7
slug: sheets-sync-adoption-gaps
status: draft
branch:
created: 2026-09-27T10:24:14-07:00
concluded:
pr:
---

# Close the gaps that keep a caller's own sync code from moving onto gdrives.sheets

## Plan

### Goal

Let a caller whose local side is not a flat file of strings use the whole sync stack, and
not only its lowest layer.

`gdrives.sheets` has two levels. The primitives (`read_tab`, `merge`, `apply_plan`) work
on records of canonical strings. The orchestration (`plan_tab`, `apply_tab`, `pull_tab`,
`push_tab`, `run_target`) adds the checks, the write order, the bootstrap, and the report,
and it reads and writes the local side and the base as files named in the config.

A caller can use the orchestration only when its data is one `.csv`, `.tsv`, or `.json`
file per tab. A caller holding typed rows in memory, a file that holds several tabs, or a
local side computed by code has to drop to the primitives, and then rebuilds what the
orchestration already does: converting typed values to and from cell strings, ordering the
writes, saving the base, and reporting. This plan closes that gap with additions that are
each optional and each backward compatible.

### Scope

In scope:

1. A public typed codec: rows of typed values to records and back.
2. Python types accepted wherever a column type is declared.
3. Comparison of cells normalized by their declared type, in `merge`.
4. Holding an invalid sheet cell for a person instead of refusing the whole tab.
5. Composite keys with a blank component.
6. A store protocol for the local side and the base, with the file store as the default.
7. `push_rows`: a whole-tab push of records held in memory.
8. Helpers to clear and to find link formatting on written cells.
9. `MergePlan` write predicates, and a non-blocking `warn` hook beside `validate`.

Out of scope:

- A DataFrame dependency. Records stay plain dicts, as in plan 006.
- Syncing formatting. Item 8 is a standalone helper and an opt-in step of a push; the
  merge still moves values only.
- Applying row deletions, and per-column `USER_ENTERED` writes. Both remain open from
  plan 006.
- New CLI commands. The existing `sheets-sync`, `sheets-pull`, and `sheets-push` gain no
  flags; the new tab options are config fields.

### Subplans

### Compatibility

Every addition is a new function, a new optional argument, or a new config field with a
default that keeps today's behavior. The one visible change for an existing config is
item 3: a tab with a schema stops reporting differences of spelling as edits. That is
noted in the changelog. The release is a minor version.

### Testing

Coverage is gated at 100%, so each step lands with its tests.

- Items 1 to 5 and 9 are pure and get unit tests: the codec round trip for each type, each
  refusal, normalized comparison for each of the four cell cases, held cells and rows
  under both settings, and partial keys including duplicates that differ only in a blank
  component.
- Items 6 and 7 run against `FakeSheetGrid`. The existing `plan_tab`, `apply_tab`,
  `pull_tab`, and `push_tab` tests must pass unchanged over `FileStore`, which is the
  check that the refactor kept behavior. New tests use `MemoryStore` and a store whose
  `write` raises, to confirm the order of writes and what a failed run leaves behind.
- Item 8 needs `FakeSheetGrid` to keep a link flag per cell and to apply `repeatCell`.
- The live integration suite gains one case per item that reaches the API: a partial-key
  tab, a `push_rows` with `clear_links`, and a sync through a custom store.

### Implementation order

Each step is its own branch and PR, in this order:

| Step | Items | Depends on |
|---|---|---|
| a | 1, 2: the codec and Python types | — |
| b | 5: partial blank keys | — |
| c | 3, 4, 9 (predicates): the merge additions | a |
| d | 6: stores | a |
| e | 7, 8: `push_rows` and link formatting | d |
| f | 9 (`warn`), config fields, docs, changelog | c, d, e |

### Open questions

- **Normalized comparison by default.** Item 3 turns on for any tab with a schema. The
  alternative is an explicit tab option. Default-on is proposed because a declared type
  already states how the column should be read.
- **Held rows.** Item 4 holds a whole new row for one invalid cell. Folding the row with
  the invalid cells blank is the alternative, and it writes a row to the local file that
  does not match the sheet.
- **`required` under `hold`.** A sheet cell blanked in a `required` column is an edit, not
  a malformed value. Proposed: hold it too, since folding it would make the local file
  fail its own check on the next run.
- **Store and `pull_all_tabs`.** It writes one file per tab with no config. It could take
  a store factory; left as is unless a use appears.
