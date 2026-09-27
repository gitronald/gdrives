"""Read and write Google Sheets cell values via the Sheets API v4.

Distinct from ``gdrives export``, which downloads a *whole* spreadsheet to a
local ``.xlsx``/``.csv`` file through the Drive API. Here we operate on live
cell ranges with ``spreadsheets.values.*`` (get/update/append/clear), so callers
can read a range into rows and write rows back without a round-trip through a
file.

The package re-exports its public surface here, so ``from gdrives.sheets import
pull_values`` works regardless of which submodule defines a name:

- ``values``: ``spreadsheets.values.*`` wrappers, tab lookups, and the
  structural ``spreadsheets.batchUpdate``
- ``a1``: A1 notation and ``GridRange`` conversion
- ``match``: keyed row updates (``find_rows``, ``set_by_match``)
- ``rules``: conditional format rules
- ``files``: local CSV/TSV interchange
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
from gdrives.sheets.files import read_values_csv, write_values_csv
from gdrives.sheets.match import find_rows, parse_pairs, set_by_match
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
from gdrives.sheets.values import (
    RAW,
    USER_ENTERED,
    append_values,
    batch_update_spreadsheet,
    batch_update_values,
    clear_values,
    first_tab,
    list_tabs,
    pull_values,
    tab_sheet_ids,
    update_values,
)

__all__ = [
    "RAW",
    "USER_ENTERED",
    "a1_quote",
    "a1_to_grid_range",
    "add_conditional_rule",
    "append_values",
    "batch_update_spreadsheet",
    "batch_update_values",
    "build_formula_rule",
    "clear_values",
    "color_to_hex",
    "column_index",
    "column_letter",
    "delete_conditional_rule",
    "describe_rule",
    "find_rows",
    "first_tab",
    "format_rules",
    "format_values",
    "grid_range_to_a1",
    "hex_to_color",
    "list_conditional_rules",
    "list_tabs",
    "parse_pairs",
    "pull_values",
    "read_rule_json",
    "read_values_csv",
    "run_add_rule",
    "run_append",
    "run_clear",
    "run_delete_rule",
    "run_get",
    "run_rules",
    "run_set",
    "run_update",
    "set_by_match",
    "split_a1",
    "tab_sheet_ids",
    "update_values",
    "write_values_csv",
]
