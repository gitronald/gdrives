"""Read and write Google Sheets cell values via the Sheets API v4.

Distinct from ``gdrives export``, which downloads a *whole* spreadsheet to a
local ``.xlsx``/``.csv`` file through the Drive API. Here we operate on live
cell ranges with ``spreadsheets.values.*`` (get/update/append/clear), so callers
can read a range into rows and write rows back without a round-trip through a
file.

The package re-exports its public surface here, so ``from gdrives.sheets import
pull_values`` works regardless of which submodule defines a name:

- ``values``: ``spreadsheets.values.*`` wrappers, render options, tab
  lookups, and the structural ``spreadsheets.batchUpdate``
- ``retry``: ``with_retry`` and the retryable status sets
- ``cells``: canonical cell strings, column types, row keys, and schema checks
- ``a1``: A1 notation and ``GridRange`` conversion
- ``match``: keyed row updates (``find_rows``, ``set_by_match``)
- ``rules``: conditional format rules
- ``files``: local CSV/TSV grids, and CSV/TSV/JSON record files
- ``config``: the sync config file (``gdrives-sheets.json``), loaded and checked
- ``table``: ``read_tab`` and ``parse_tab``, a whole tab as header-named, keyed
  records
- ``merge``: ``merge``, the pure three-way merge of local records and a tab
- ``apply``: ``apply_plan``, which writes a merge plan's sheet side, guarded
  and read back
- ``structure``: add and delete columns by header name, create missing tabs,
  and set column widths
- ``commands``: the ``run_*`` CLI entry points
"""

from gdrives.sheets.a1 import (
    a1_quote,
    a1_to_grid_range,
    column_index,
    column_letter,
    grid_range_to_a1,
    split_a1,
)
from gdrives.sheets.apply import (
    ApplyError,
    ApplyResult,
    ReadBackError,
    SheetChangedError,
    apply_plan,
    verify,
)
from gdrives.sheets.cells import (
    COLUMN_TYPES,
    ColumnSchema,
    Problem,
    from_cell,
    index_rows,
    normalize_key,
    problems,
    row_key,
    to_cell,
)
from gdrives.sheets.commands import (
    format_values,
    run_add_rule,
    run_append,
    run_clear,
    run_delete_rule,
    run_get,
    run_rules,
    run_set,
    run_update,
)
from gdrives.sheets.config import (
    BOOTSTRAPS,
    CONFIG_NAME,
    INPUT_OPTIONS,
    LOCAL_EXTENSIONS,
    MODES,
    Config,
    ConfigError,
    TabConfig,
    Target,
    find_config,
    load_config,
    parse_config,
)
from gdrives.sheets.files import (
    Records,
    read_records,
    read_values_csv,
    write_records,
    write_values_csv,
)
from gdrives.sheets.match import find_rows, parse_pairs, set_by_match
from gdrives.sheets.merge import (
    OVERRIDE_REASONS,
    ROW_FLAGS,
    SIDES,
    Cell,
    MergePlan,
    NewRow,
    Override,
    RowFlag,
    merge,
)
from gdrives.sheets.retry import IDEMPOTENT_STATUSES, RATE_LIMIT_STATUSES, with_retry
from gdrives.sheets.rules import (
    add_conditional_rule,
    build_formula_rule,
    color_to_hex,
    delete_conditional_rule,
    describe_rule,
    format_rules,
    hex_to_color,
    list_conditional_rules,
    read_rule_json,
)
from gdrives.sheets.structure import (
    add_columns,
    delete_columns,
    ensure_tabs,
    set_column_widths,
)
from gdrives.sheets.table import EmptyTabError, Table, parse_tab, read_tab
from gdrives.sheets.values import (
    FORMATTED_STRING,
    FORMATTED_VALUE,
    FORMULA,
    RAW,
    SERIAL_NUMBER,
    UNFORMATTED_VALUE,
    USER_ENTERED,
    TabGrid,
    append_values,
    batch_update_spreadsheet,
    batch_update_values,
    clear_values,
    first_tab,
    list_tabs,
    pull_many,
    pull_values,
    tab_grid,
    tab_sheet_ids,
    update_values,
)

__all__ = [
    "ApplyError",
    "ApplyResult",
    "BOOTSTRAPS",
    "COLUMN_TYPES",
    "CONFIG_NAME",
    "Cell",
    "ColumnSchema",
    "Config",
    "ConfigError",
    "EmptyTabError",
    "FORMATTED_STRING",
    "FORMATTED_VALUE",
    "FORMULA",
    "IDEMPOTENT_STATUSES",
    "INPUT_OPTIONS",
    "LOCAL_EXTENSIONS",
    "MODES",
    "MergePlan",
    "NewRow",
    "OVERRIDE_REASONS",
    "Override",
    "Problem",
    "RATE_LIMIT_STATUSES",
    "RAW",
    "ROW_FLAGS",
    "ReadBackError",
    "Records",
    "RowFlag",
    "SERIAL_NUMBER",
    "SIDES",
    "SheetChangedError",
    "TabConfig",
    "TabGrid",
    "Table",
    "Target",
    "UNFORMATTED_VALUE",
    "USER_ENTERED",
    "a1_quote",
    "a1_to_grid_range",
    "add_columns",
    "add_conditional_rule",
    "append_values",
    "apply_plan",
    "batch_update_spreadsheet",
    "batch_update_values",
    "build_formula_rule",
    "clear_values",
    "color_to_hex",
    "column_index",
    "column_letter",
    "delete_columns",
    "delete_conditional_rule",
    "describe_rule",
    "ensure_tabs",
    "find_config",
    "find_rows",
    "first_tab",
    "format_rules",
    "format_values",
    "from_cell",
    "grid_range_to_a1",
    "hex_to_color",
    "index_rows",
    "list_conditional_rules",
    "list_tabs",
    "load_config",
    "merge",
    "normalize_key",
    "parse_config",
    "parse_pairs",
    "parse_tab",
    "problems",
    "pull_many",
    "pull_values",
    "read_records",
    "read_rule_json",
    "read_tab",
    "read_values_csv",
    "row_key",
    "run_add_rule",
    "run_append",
    "run_clear",
    "run_delete_rule",
    "run_get",
    "run_rules",
    "run_set",
    "run_update",
    "set_by_match",
    "set_column_widths",
    "split_a1",
    "tab_grid",
    "tab_sheet_ids",
    "to_cell",
    "update_values",
    "verify",
    "with_retry",
    "write_records",
    "write_values_csv",
]
