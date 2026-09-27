---
status: draft
branch:
---

# 007e — Put the local side and the base behind a store

Part of [007](../plan.md). Step 5 of the umbrella's implementation order. It is a
refactor of `sync.py` with behavior unchanged for a config-driven run, and the existing
orchestration tests passing unchanged are the check of that.

Notes applied: R2, R5, R12, D9.

## Stores (`stores.py`)

The orchestration reaches the local side and the base through a protocol instead of a
path:

```python
class Store(Protocol):
    label: str  # shown in reports and errors

    def exists(self) -> bool: ...
    def read(self) -> Records: ...
    def write(
        self, columns: Sequence[str], rows: Sequence[Mapping[str, str]]
    ) -> None: ...
```

- `FileStore(path, types=None, bom=False, newline="lf")` wraps `read_records` and
  `write_records`, and is what a config file's `local` path and a target's base
  directory become. Behavior for config-driven runs is unchanged.
- `MemoryStore(columns, rows)` holds records in memory, for tests and for a caller that
  saves them itself after the run.
- `TabConfig` gains `store`. `TabConfig.local` stays, and is None for a tab built in
  code with a store that is not a file.
- That changes the field's type [R2]. `local` is `Path` today, required and positional
  (`config.py:104`), and becomes `Path | None` with a default of None. A tab loaded from
  a config always has it. A caller that reads `tab.local` under a strict type checker
  has to handle None, which is a visible change (decision 8). A tab with neither
  `local` nor `store` is refused when it is built.
- `TabConfig.local_store` returns `store` when one is given, and else a `FileStore`
  over `local` with the tab's `types`, `bom`, and `newline`. The orchestration calls
  only this.
- `Target` gains `base_stores`, a mapping of tab title to store, and
  `base_store(tab)`, which returns the tab's entry when there is one and else a
  `FileStore` over `base_path(tab)`. The draft had `base_store` as a method only, which
  can return nothing but a file under `Target.base`, and the case that motivates stores
  (a base that is not one CSV per tab) needs a way in [R5]. `Target.base` stays a
  `Path`, and is unused for a tab that has an entry.
- `plan_tab`, `apply_tab`, `pull_tab`, and `push_tab` call the store where they read or
  wrote a path. The write order is unchanged and remains the safety property: checks,
  structure, sheet, local store, base store, widths.
- Messages that name a path name the store's `label`: the two refusals that name
  `base_path` (`sync.py:410-413`, `sync.py:436-440`) and the ones that name the local
  file (`sync.py:258`, `sync.py:270`, `sync.py:274`). A `FileStore`'s label is its path,
  so a config-driven run prints what it prints today.
- A store's `write` may raise `ValueError` or `OSError`; both are reported per tab, as a
  file error is today. `TabReport` gains `local_label`.
- The config's collision check (two tabs writing one file) covers file stores only. A
  caller passing its own stores owns that check.

## What a store must do [R12]

The protocol has three methods, and requirements the draft left unstated. They go in
the protocol's docstring and the guide.

- **`read()` returns the same records each time within a run.** After a restructure
  the local side is read again, and the run stops with `SheetChangedError` unless the
  second read equals the first (`sync.py:636-651`). A computed store has to be
  deterministic between the two.
- **A store that decodes on `write` should declare its types in the tab's schema.** A
  cell that does not parse fails such a store's `write` after the sheet write. That is
  safe under the write order, since the base has not advanced and the next run sees
  the sheet as already in sync. It is avoided entirely when the schema declares the
  same types, because the merged check then refuses before anything is written.
- **`write` receives rows of one shape.** `plan_tab` passes `carry=`
  ([`c-merge-additions.md`](c-merge-additions.md#carried-columns-d9)), so every row
  holds every column of `columns` [D9].
- `exists()` False on the local side is refused for a sync or a push and creates the
  file on a pull, as a missing file does today. On the base it means a first sync.

What this allows, each as a small class on the caller's side: one tab of a file that
holds several tabs; typed rows, converted with the codec of item 1; rows written back in
an order of the caller's choosing; and a local side that is computed, where `write` saves
the folded values wherever the computation reads them from.

`docs/sheets-sync.md` gets a section on writing a store, with a worked example.

## Tests

- The existing `plan_tab`, `apply_tab`, `pull_tab`, and `push_tab` tests pass unchanged
  over `FileStore`, their messages included.
- `MemoryStore` through a full sync, a pull, and a push.
- A store whose `write` raises, on the local side and on the base: the order of writes,
  and what the failed run leaves behind.
- A store whose second `read` differs, through a restructure: `SheetChangedError`.
- A custom base store through a bootstrap, a second sync, and the `adopt` refusal, whose
  message names the label.
- A tab built with neither `local` nor `store`.
- No live case: a store reaches no API surface that a file does not [R15].

## Config and docs in this step

- No config field: a config names files, and a store is code.
- The guide's library section gains the section on writing a store.
- Changelog: `Store`, `FileStore`, `MemoryStore`, `TabConfig.store`, and
  `Target.base_stores` under Added; the type of `TabConfig.local` under Changed.
