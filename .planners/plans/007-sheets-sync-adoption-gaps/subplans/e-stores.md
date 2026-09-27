---
status: draft
branch:
---

# 007e — Put the local side and the base behind a store

Part of [007](../plan.md).

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

- `FileStore(path, types=None, bom=False)` wraps `read_records` and `write_records`, and
  is what a config file's `local` path and a target's base directory become. Behavior for
  config-driven runs is unchanged.
- `MemoryStore(columns, rows)` holds records in memory, for tests and for a caller that
  saves them itself after the run.
- `TabConfig` gains `store` and `Target` gains `base_store(tab)`. `TabConfig.local` stays,
  and is None for a tab built in code with a store that is not a file.
- `plan_tab`, `apply_tab`, `pull_tab`, and `push_tab` call the store where they read or
  wrote a path. The write order is unchanged and remains the safety property: checks,
  structure, sheet, local store, base store, widths.
- A store's `write` may raise `ValueError` or `OSError`; both are reported per tab, as a
  file error is today. `TabReport` gains `local_label`.
- The config's collision check (two tabs writing one file) covers file stores only. A
  caller passing its own stores owns that check.

What this allows, each as a small class on the caller's side: one tab of a file that
holds several tabs; typed rows, converted with the codec of item 1; rows written back in
an order of the caller's choosing; and a local side that is computed, where `write` saves
the folded values wherever the computation reads them from.

`docs/sheets-sync.md` gets a section on writing a store, with a worked example.
