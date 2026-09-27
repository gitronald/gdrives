# Accuracy review of the plan against the current code

- Written: 2026-09-27T10:43:52-07:00
- Code reviewed: `gdrives/sheets/` at `f177518` (unchanged at `e056980`)
- Scope: the plan's claims about current behavior, the fit of each item to the
  needs of a downstream caller, and gaps in the design. No code was changed.

Notes are numbered `R1` to `R15` so later notes can cite, combine, or overrule
them. Line references are to the commit above.

## What checks out

- **Need.** Each of the nine items answers a need a downstream caller has:
  columns declared by Python class, a composite key with a blank component, a
  local file and a base that hold several tabs, a local side computed by code,
  link clearing after a whole-tab push, write predicates on the plan, and a
  consistency warning that does not block.
- **Current behavior.** These descriptions are accurate: `index_rows` refuses any
  blank key cell (`cells.py:138`); an invalid sheet cell is folded, reported by
  the merged check, and blocks every write for the tab (`sync.py:479`,
  `sync.py:559`); `_in_sync` tests the four write lists (`sync.py:1239`);
  coverage is gated at 100%; and row deletions and per-column `USER_ENTERED`
  writes are open items of plan 006.
- **Already covered by plan 006.** Column ownership (`local_owned`,
  `sheet_owned`), `owns_rows`, the bootstrap, and explicit-row appends need
  nothing new.

## Inaccuracies

### R1. Item 3: "never settles" is overstated

A cell respelled on one side settles in one or two runs. With a file-backed
local side the sheet's spelling folds in and the two sides agree. With a typed
local side the fold is decoded, read back in canonical form, and pushed, so the
sheet takes the canonical spelling on the second run.

The real costs of comparing as text are:

- a fold and a push per respelling, each a write nobody asked for; and
- a false conflict when the other side changed the same cell for real, since
  text comparison sees both sides as changed.

The second is the stronger argument for the item and is not in the plan.

### R2. The Compatibility section misses two visible changes

Item 3 is named as the one visible change. There are two more:

- `TabConfig.local` is `Path` today (`config.py:104`), required and positional.
  Item 6 makes it `Path | None`, which is a type-level break for a caller that
  reads `tab.local` under a strict type checker.
- `write_records` raises on the first cell that does not parse, prefixed with
  the path (`files.py:219-230`). Rewritten over `decode_rows`, it lists every
  such cell by row position. The message text changes.

### R3. Item 7 states the write order backwards

The plan lists "the single write over the old extent, the grid growth". The
grid is grown first (`sync.py:885`), then written (`sync.py:890-896`).

### R4. Item 1: only the write side has a private helper

The writer has `_json_value` (`files.py:166`). The reader calls `to_cell`
inline (`files.py:143`), and before that it refuses nested values
(`files.py:132`), strips column names, and refuses a name repeated after
stripping. `encode_rows` needs to say what it does with a `list` or `dict`
value; `to_cell` alone would write its `str()`.

## Design gaps

### R5. Item 6: no way to supply a custom base store

`Target.base` is a `Path` and `base_store(tab)` is described as a method, so it
can only return a file store under that directory. A caller whose base is not
one CSV per tab has nowhere to pass its own. The motivating case needs this.
Error messages that name `base_path` (`sync.py:410-413`, `sync.py:436-440`)
need the store's label instead.

### R6. Item 4: `on_invalid="refuse"` has no meaning inside `merge`

`merge` checks no schema today. The refusal is in `_plan`, after the merge
(`sync.py:479`). On `merge`, `"refuse"` is therefore either a no-op under a
misleading name or a new raise. Also unspecified:

- where a held row's reason is stored, since `RowFlag` holds a key and a flag
  only (`merge.py:86-91`);
- whether the option applies to pull tabs, which refuse on sheet problems too
  (`sync.py:779`), or is sync-only like the fields in `_SYNC_ONLY`; and
- that `validate` problems still refuse under `"hold"`.

### R7. Item 5: the list of `blank_keys` call sites is incomplete

`push_tab` calls `index_rows` directly (`sync.py:851`), so `push_tab` and
`push_rows` need the option as well.

### R8. Item 7: the `push_rows` signature is missing arguments

- `report`, which `push_tab` takes so `run_target` keeps a partial report on
  error (`sync.py:818`, `sync.py:1102`);
- `warn`, which item 9 says `push_rows` accepts;
- `blank_keys`, per R7; and
- a label for messages that name the local file today (`sync.py:843`,
  `sync.py:851`).

The refusal of an empty row list (`sync.py:842`) belongs in `push_rows` too.

### R9. Item 2: class types widen the `ColumnSchema.type` annotation

`ColumnSchema` is a frozen dataclass with `type: str` (`cells.py:167`). A
dataclass field has one annotation for both the constructor and the attribute,
so accepting classes makes the attribute `str | type`, and that flows into
`TabConfig.types -> dict[str, str]` (`config.py:118`) under pyrefly's strict
preset. Options: a public normalizer (`column_type(name_or_class) -> str`) that
callers apply first, or a separate constructor.

### R10. Item 3: key columns should be excluded explicitly

`from_cell` accepts `007` as an `int` (`cells.py:26`), so normalizing a typed
key column would treat `007` and `7` as one row. Keys are compared by
`normalize_key` today, on whitespace only. The plan should say that stays.

### R11. Item 9: a rows-only `warn` hook cannot see what was folded

With the signature of `validate`, the hook receives rows and nothing else. A
check scoped to the cells a run folded (warn when a folded edit leaves sibling
rows divergent) needs the plan, or it warns on every divergence on every run.
Also, `_plan` resets the report's lists on entry (`sync.py:378`) and the
re-merge after a restructure runs with `check=False`, so warnings must be
carried across it and not produced twice.

### R12. Item 6: two store requirements are unstated

- `read()` is called again after a restructure and the result must equal the
  first read (`sync.py:636-651`), so a computed store must be deterministic.
- A store that decodes on `write` fails after the sheet write when a cell does
  not parse. That is safe under the write order, since the base has not
  advanced, but it is avoided entirely when the tab's schema declares the same
  types, because the merged check then refuses before any write.

### R13. Item 1: the round trip has two exceptions

`decode_rows(encode_rows(rows), types) == rows` does not hold for a value of
`""`, which decodes to `None`, or for a row that lacks a key, which comes back
holding `None` under it. `from_cell`'s docstring makes the same claim
(`cells.py:55`).

## Process

### R14. Six branches and PRs do not fit one plan's frontmatter

The frontmatter has one `branch` and one `pr`. "Each step is its own branch and
PR" is the umbrella shape: `007` with subplans `007a` to `007f`. Steps b and c
both edit `merge.py`, so they are independent in design but not in the diff.

### R15. Live test cost

The live suite makes 55 writes against a limit of 60 per minute and already
waits out 429s. Three more cases lengthen those waits. The sync through a
custom store reaches no API surface that a file store does not, so it can run
against `FakeSheetGrid` only.
