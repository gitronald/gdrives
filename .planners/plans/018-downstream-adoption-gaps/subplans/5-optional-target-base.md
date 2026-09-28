# Make Target.base optional when every tab has a base store

Step 5 of [plan 018](../plan.md). Originally drafted as plan 016, which was retired when the steps were folded into one plan.

## Spec

### Goal

`Target.base` becomes optional, so a caller who builds a `Target` in code and gives
every tab a base store (`base_stores`) does not have to invent a directory that is
never used. When a tab with no entry in `base_stores` needs its base on a target with no
`base`, a clear error says so.

### Design

- `Target.base: Path | None = None`. Dataclass fields with defaults must come last, so
  `tabs` gets a default of `()` too, and the field order stays
  `name, spreadsheet, base, tabs, ...`. Positional callers keep working. A target with no
  tabs was already refused where it matters: `run_target` raises "has no sync tabs".
- `Target.base_path(tab)` raises `ValueError` when `base` is None:
  `target 'roster' has no base directory, and tab 'Members' has no entry in base_stores`.
  `base_store(tab)` goes through it only for a tab without a store, so a target whose
  every sync tab has a store never reaches it.
- It is reached in `_plan` (reading the base), under `run_target`'s per-tab error
  handling. So the tab is reported with that message and the run goes on to the next
  tab, as for any other refusal. It is raised before the tab's values are read,
  because `_plan` reads the base before it reads the sheet.
- Pull and push tabs never read a base, so a target of only pull and push tabs needs
  neither `base` nor `base_stores`.
- The config file is unchanged. `parse_config` still always sets `base`, to the
  default `sheets-base/<target>` when none is given. The collision check calls
  `base_path` only for config-built targets, which always have one. It is guarded
  anyway, so a `Target` with no `base` passed to it cannot raise.

### Tests

- `Target(name, spreadsheet, tabs=...)` with no base, and a `base_stores` entry for
  every sync tab: a sync runs, and nothing touches the filesystem.
- A tab with no store on a target with no base is reported with the message, and the
  next tab still runs.
- Positional construction with a base still works.
- A pull-only target with no base works.

### Docs

- The `Target` docstring, the guide section on stores (`docs/sheets-sync.md`), and the
  README if it builds a `Target` in code.
- CHANGELOG `[Unreleased]` / Changed: `Target.base` is optional.
