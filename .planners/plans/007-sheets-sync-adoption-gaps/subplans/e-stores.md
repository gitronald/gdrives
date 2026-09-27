---
status: done
branch: feature/sheets-sync-adoption-e-stores
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

## Log

### 2026-09-27 — implemented

Branch `feature/sheets-sync-adoption-e-stores`, cut from step d's branch, with a draft
PR onto it. One commit, `2c8d5b1`: `stores.py`, `TabConfig.store` and `local_store`,
`Target.base_stores` and `base_store`, the orchestration over stores,
`TabReport.local_label`, the guide's section, and the changelog.

**The check that the refactor kept behavior.** Every test of the suite before this
step passes over `FileStore`, its messages included, with two kinds of edit to the
tests and none to what they assert:

- The two tests that make a file write fail patched `write_records` where `sync.py`
  looked it up. `FileStore` looks it up in `stores.py`, so they patch it there.
- `TabConfig.local` is `Path | None` (decision 8), and the type checker refused the 22
  places where a test reads the file of `tab.local`. They go through `local_file` in
  `tests/helpers.py`, which asserts the path is there. This is what a caller under a
  strict type checker meets too, as the Compatibility table says.

Decisions made during the work:

- **`Store.label` is a read-only property of the protocol**, so a class attribute, an
  instance attribute, and a property all satisfy it. `FileStore`'s is a property over
  its path.
- **`FileStore` is a frozen dataclass**, so two stores of one file with the same
  settings are equal, and a `TabConfig` holding one still compares by value.
  `MemoryStore` is a plain class and compares by identity.
- **A `MemoryStore` made with no columns does not exist until it is written**, as a
  file does not. It counts its writes, which the tests of the write order read.
- **A store given with a file wins.** `TabConfig(local=..., store=...)` reads and
  writes the store, and the report still names the file.
- **Messages say `local file <path>` for a `FileStore` and `local store <label>` for
  any other**, so a config-driven run prints what it printed. The report's line is
  `local file:` when the tab has a file and `local store:` when it has only a label.
- **The base file follows its tab's `newline`**, which step a did through
  `write_records` and `Target.base_store` now does through the `FileStore` it builds.
- **The protocol's methods raise `NotImplementedError`.** A class that names `Store`
  as a base and leaves one out fails when it is called, and the coverage config
  already leaves such lines out.

The guide's example store, one tab of a JSON file that holds several, is a test
(`TestGuideExample`), so the example runs.

**Live suite.** No live case, as designed. One run of the whole suite, by the
orchestrating session, to check the refactor against the API: 23 passed in 111
seconds, with the counts where step c left them, at 68 writes and 84 reads (4 reads
were refused on the quota and sent again).
