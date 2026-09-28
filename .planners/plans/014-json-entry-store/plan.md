---
id: 14
slug: json-entry-store
status: retired
branch: null
created: 2026-09-27T16:46:17-07:00
concluded: 2026-09-27T17:04:35-07:00
pr: null
---

# Add a store for one entry of a multi-tab JSON file

## Plan

### Goal

A store that reads and writes one named entry of a JSON file shaped
`{"Tab A": [rows], "Tab B": [rows]}`, usable in code and from the config file.
`FileStore` reads a `.json` file only as a flat array of objects. A downstream project
keeps a whole workbook in one such file, with typed values, and its base snapshot in a
second file of the same shape. Today it has to write its own `Store` for both.

### Store

`JsonEntryStore(path, entry, types=None)` in `stores.py`, frozen dataclass, exported:

- `label`: `f"{path} [{entry}]"`.
- `exists()`: the file exists and its top-level object has `entry`. A file that is not
  a JSON object raises `ValueError` from `exists()` too, so a malformed file is never
  taken for a missing entry and overwritten.
- `read()`: the entry's array, parsed exactly as `read_records` parses a JSON array of
  flat objects (same refusals, same canonical strings).
- `write(columns, rows)`: re-read the file (fresh, not a cached copy) and replace the
  entry's value, or add it at the end when it is new. Every other entry keeps its value
  and its place. Then write the whole file atomically (`write_text`). The entry's rows
  are encoded exactly as `write_records` encodes a JSON file (typed by `types`, blank as
  `null`, dates kept as strings, `allow_nan=False`). The file is dumped with the same
  `indent=2, ensure_ascii=False` and a trailing newline.
- A duplicate key in the top-level object is refused (`object_pairs_hook`), rather than
  silently keeping the last one.

**Byte stability.** A rewrite that changes nothing leaves the file identical, when the
file is in the library's own format. That holds because the other entries are
re-dumped from their parsed values with the same settings, and JSON round-trips
Python's `int`, `float`, `str`, `bool`, and `None` exactly. A file formatted some other
way by hand is reformatted on its first write, with every value unchanged. The docs
say this. `apply_tab` already skips the local and base writes when nothing changed, so
an unchanged run does not touch the file at all.

### Shared code in `files.py`

Factor out two helpers, used by both `read_records`/`write_records` and the new store,
so the two JSON paths cannot drift:

- `_records_from_array(data, where)`
- `_json_array(path, columns, rows, types)`

The error text stays the same, with the entry named in `where`.

### Config

- Tab field `entry` (a non-blank string). It needs a `.json` `local`, and the tab's
  local side is then that entry. It is valid for every mode.
- Target field `base_file` (a `.json` path). Every sync tab's base is then the entry
  named by the tab's title in that file, instead of `base/<title>.csv`. The base is
  typed by the tab's schema. It is refused together with `base`, and refused inside a
  `.gdrives` directory, as `base` is.
- The config builds `JsonEntryStore`s for these through `TabConfig.local_store` (a tab
  with `entry`) and `Target.base_stores` (from `base_file`). No new dispatch is needed
  in `sync.py`.

### Collisions

`_Checker.collisions` today keys writers by path, so two tabs writing one file are
refused. It changes to key by `(path, entry)`, where `entry` is None for a whole file:

- two tabs writing different entries of one file: allowed
- two tabs writing the same entry: refused
- a whole-file writer and an entry writer on one path: refused

It learns the stores it can see into (`FileStore`, `JsonEntryStore`), so a config's
`base_file` entries are checked too. A caller's own store stays the caller's own
check, as now.

### Failure between two tabs of one run

Each write is a complete, atomic read-modify-write of the file, and tabs run in
sequence, so a tab's write reads the file as the previous tab left it. If tab B fails
after tab A wrote its entry, the file holds A's new entry and B's old one. That is the
same outcome as two separate files, and it is consistent: A's sheet, local entry, and
base entry all landed, and B's did not. The order inside a tab (sheet, then local,
then base) is unchanged, so a failure between B's local write and its base write
leaves B as the existing contract describes (the next run sees the sheet as already in
sync). What this store does not guard against is another process writing the file
during a run. That is true of every file store, and the docs say so.

### Tests

- Store:
  - read, exists, and write
  - other entries are preserved, in value and order
  - a new entry is added at the end
  - a no-op rewrite is byte-identical
  - typed values round-trip
  - a missing file, a non-object file, a non-array entry, and a duplicate top-level key
  - atomic write: a failing encode leaves the file as it was
- Config:
  - `entry` needs a `.json` `local`
  - `base_file` is refused with `base`
  - collisions: two entries of one file pass, the same entry twice fails, and a
    whole-file writer plus an entry writer on one path fails
  - `base_file` gives each sync tab a `JsonEntryStore` base
- A sync run over two tabs sharing one local file and one base file, with the fake
  service, where the second tab fails. It checks what each file holds afterwards.

### Docs

- `docs/sheets-sync.md`: a section on a workbook kept in one JSON file, with a config
  example, and the note on another process writing the file.
- README config summary.
- CHANGELOG `[Unreleased]` / Added.

## Log

### 2026-09-27

- Retired unimplemented: folded into [plan 018](../018-downstream-adoption-gaps/plan.md) as step 3 ([spec](../018-downstream-adoption-gaps/subplans/3-json-entry-store.md)). The spec above is kept as drafted.
