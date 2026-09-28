# Exclude named columns from a pull

Step 1 of [plan 018](../plan.md). Originally drafted as plan 012, which was retired when the steps were folded into one plan.

## Spec

### Goal

Let a pull tab name columns to leave out, so their values never reach the local file
and never appear in a report, while every other column of the tab, including columns
added later, is still pulled. A tab's `columns` is an allowlist. On a tab that holds a
few sensitive columns (personal data such as ID numbers and birthdates) and grows new
columns over time, an allowlist silently ignores every new column. Today a downstream
project works around this by pulling the grid itself and dropping the columns by hand.

### Design

- **Config:** a tab field `exclude`, a list of column names. It applies to `pull` tabs
  only. It is refused on a `sync` or `push` tab, since what those modes write to the
  sheet is the local side's business, not a read filter. The checker refuses:
  - `exclude` together with `columns`, since the two contradict each other
  - a blank or repeated name, as for `columns`
  - a `key`, `schema`, or `widths` column that `exclude` names, since that column
    would be read after all
- **Python API:** `TabConfig.exclude: tuple[str, ...] = ()`. `TabConfig.__post_init__`
  refuses `exclude` with `columns`, so a tab built in code is held to the same rule. The
  mode, key, and schema checks stay in the config checker and in `pull_tab`, as the
  other fields' checks do.
- **`pull_tab`:** with `exclude`, the header is parsed first and every name in
  `exclude` must be one of its named columns. **A name the header lacks refuses the
  pull, and the local side is left alone.** A denylist that quietly matches nothing
  after a rename on the sheet would start writing the renamed column's data to disk,
  which is the failure this field exists to prevent. The message lists every missing
  name and says that a renamed sensitive column is the likely cause.
  The columns read are then the header's named columns less `exclude`, in header
  order, passed to `parse_tab` as an explicit list. The excluded values never enter a
  `Table`, so no hook, check, report, or file can see them.
- **What reports may still show:** the excluded column *names*, which the config
  already holds. `CheckContext.sheet_columns` keeps listing every header column,
  because it describes the sheet's structure. `Replacement.dropped_columns` counts a
  column that the old local file had and the new one lacks. If a local file written
  before `exclude` was added holds an excluded column, the report counts that
  column's non-blank cells from the local file, and the pull removes the column from
  the file. That is what should happen, and the report shows it.
- **`key`** must be outside `exclude`. `parse_tab` needs it in the columns read.
- A tab that has only excluded columns reads no columns and is refused, with its own
  message.

### Tests

- config: accepted on a pull tab. Refused on sync and push tabs, with `columns`, with
  blank or repeated names, and when a `key`, `schema`, or `widths` column is excluded.
- `TabConfig(exclude=..., columns=...)` raises.
- `pull_tab`: excluded columns are absent from the written file, from `TabReport`
  (`format_report` text), from the `check`, `validate`, and `warn` rows, and from
  `pull_serials` requests. A later column added on the sheet is pulled. An excluded
  name missing from the header refuses the pull, writes nothing, and names the column.
  A local file that held an excluded column loses it, and the report counts its cells.
- A test asserts that no excluded value appears anywhere in `format_report`'s output
  or in the file bytes. It uses a synthetic sentinel value.

### Docs

- `docs/sheets-sync.md`: a subsection under pull tabs on `exclude`, with a config
  example (loaded by `tests/test_sheets_guide.py`) and the rename refusal.
- README: the `exclude` field in the config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- `exclude` for sync and push tabs, and for `sheets-pull --all-tabs`, which takes no
  config.
- Matching by pattern. The field takes exact header names only.
