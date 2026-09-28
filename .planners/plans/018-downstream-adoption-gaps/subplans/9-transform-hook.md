# Transform the rows a tab is read as

Step 9 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second list
of gaps by the same downstream caller.

## Spec

### Goal

A hook that cleans the rows read from the sheet before anything looks at them.
`pull_tab` writes the tab as read. A downstream caller cleans cells on the way in: it
collapses runs of spaces and tabs, trims each line, keeps line breaks, and rewrites
profile URLs to one canonical form. Today it pulls the grid itself to do so.

### Design

- **The hook:** `Transform`, a function given the rows (a sequence of mappings from
  column to cell string) and returning rows. It is exported, beside `Validate` and
  `Check`.
- **Where it is taken:** `transform=` on `pull_tab`, `run_target`, and
  `pull_all_tabs`, and, as the next section decides, on `plan_tab` and `sync_tab`.
- **When it runs:** after the tab is parsed and before everything else. The schema
  check, `validate`, `check`, `warn`, the comparison with the local side, the report,
  and the file all see the transformed rows and never the rows as read.
- **What it may return:** one row for each row given, in the same order, each with
  exactly the columns it was given, every value a string. Anything else is refused,
  and the message says which of the three failed. A transform does not add, drop, or
  reorder rows or columns.
- **Keys.** A transform may change a key cell. The keys are checked again after it
  (`index_rows`, with the tab's `blank_keys`), so a transform that makes two keys
  equal, or one blank, is refused as the tab itself would be.
- **`pull_all_tabs`** has no columns or keys to name, and gives the hook the tab's
  title as a second argument, since one function serves every tab. `run_target`
  gives it the rows alone, and a caller that needs the tab uses a function per tab
  or closes over it.

### A sync tab takes the same hook

Yes. Without it, a tab pulled through a transform and synced raw reports each
cleaned cell as a sheet edit, and folds the uncleaned text back into the local file.

- The transform is applied to the sheet's rows as read, before the merge. The merge
  compares the cleaned sheet with the local side and the base, so a cell that
  differs from the local side only by what the transform removes is in sync.
- **The sheet keeps its text.** A cell the transform changed and nobody edited is
  not pushed. The local file and the base hold the cleaned text. The guide says so.
- The re-read guard compares raw reads, as now: it asks whether the sheet moved, not
  what it means. The read-back compares the cells a run pushed with what it read,
  raw, since a pushed cell holds the local text exactly.
- **A transform must be idempotent**: applied to its own result, it changes nothing.
  A local edit is pushed as written. If the transform would change it, the next run
  reads the cleaned form as a sheet edit and folds it in, once, and the two sides
  agree from then on. The guide states the requirement and this consequence.
- The local side is not transformed. It is the caller's own file.

If the merge, the guard, or held cells turn out to need more than this once the code
is open, the pull part lands alone and the sync part is written up as options in
this plan's Log.

### Tests

- pull: the file, the report, and each hook's rows hold the transformed cells. A
  test with a synthetic sentinel asserts that the text as read is in none of them.
- The three refusals: a row more or fewer, a column more or fewer, and a value that
  is not a string.
- A transform that makes two keys equal is refused, and the local side is left
  alone.
- `pull_all_tabs` gives the hook each tab's title.
- `run_target` passes the hook to each pull tab, and to each sync tab.
- sync: a sheet cell that differs from the local side and the base only by
  whitespace is in sync, makes no write, and keeps its text on the sheet. A real
  sheet edit is folded in cleaned. A local edit is pushed as written.
- The default, no transform, makes the requests and the report it made before.

### Docs

- `docs/sheets-sync.md`: `transform` in the hooks section, with a runnable example
  that collapses whitespace, the idempotence requirement, and a part of "Moving an
  existing sync over".
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- A transform of the local side, and one for a push.
- Naming the hook in the config file, which is step 11.
- Shipping a whitespace or URL cleaner. The caller writes its own.
