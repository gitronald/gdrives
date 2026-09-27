"""CLI entry points for the ``sheets-*`` commands, and their display helpers.

Each ``run_*`` resolves its source to a spreadsheet ID, builds a Sheets service
with the scopes it needs (imported lazily, so the CLI starts fast), and calls
the helpers in the sibling modules.
"""

import csv
import json
import sys

from gdrives.local import escape_formula
from gdrives.sheets.a1 import a1_quote, a1_to_grid_range
from gdrives.sheets.files import read_values_csv, write_values_csv
from gdrives.sheets.match import set_by_match
from gdrives.sheets.rules import (
    _flatten_rules,
    _rule_tabs,
    add_conditional_rule,
    build_formula_rule,
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
    _lookup_tab,
    _tab_ids,
    append_values,
    clear_values,
    first_tab,
    pull_values,
    tab_sheet_ids,
    update_values,
)

# -- display --


def format_values(values: list[list[str]]) -> str:
    """Render rows as left-aligned columns, padding ragged rows to full width."""
    if not values:
        return ""
    width = max(len(row) for row in values)
    padded = [row + [""] * (width - len(row)) for row in values]
    col_widths = [max(len(row[i]) for row in padded) for i in range(width)]
    lines = [
        "  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in padded
    ]
    return "\n".join(lines)


# -- CLI entry points --


def _resolve_and_report(source: str) -> str:
    """Resolve ``source`` to a spreadsheet ID and echo it to stderr.

    Every command opens the same way, so the resolve-then-announce step lives
    here once instead of in each ``run_*`` entry point.
    """
    from gdrives.resolve import resolve_and_report

    return resolve_and_report(source, "Spreadsheet")


def run_get(
    source: str,
    range_: str | None = None,
    *,
    output: str | None = None,
    delimiter: str = ",",
    aligned: bool = True,
    escape_formulas: bool = False,
) -> None:
    """Read a range and print it, or write it to a delimited file with ``output``.

    With no ``range_``, defaults to the first tab. To stdout: aligned columns by
    default, or delimited rows when ``aligned`` is False. With ``output``: writes
    a delimited file (``delimiter``) and reports the row count to stderr.
    ``escape_formulas`` passes every cell through :func:`escape_formula`, for
    output a spreadsheet app will open; it is off by default because it changes
    values such as ``-5``.
    """
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()

    if range_ is None:
        # Quote the bare tab title so names with spaces or cell-like forms
        # ('Q3 Budget', '2026') stay valid A1 ranges, matching set_by_match.
        range_ = a1_quote(first_tab(service, spreadsheet_id))

    values = pull_values(service, spreadsheet_id, range_)
    if escape_formulas:
        values = [[escape_formula(cell) for cell in row] for row in values]

    if output:
        write_values_csv(output, values, delimiter=delimiter)
        print(f"Wrote {len(values)} row(s) to {output}", file=sys.stderr)
    elif not values:
        print("(empty range)", file=sys.stderr)
    elif aligned:
        print(format_values(values))
    else:
        # Force "\n" line endings: sys.stdout is a text stream, so csv's default
        # "\r\n" terminator would leave a stray CR (and "\r\r\n" on Windows).
        csv.writer(sys.stdout, delimiter=delimiter, lineterminator="\n").writerows(
            values
        )


def run_update(
    source: str, range_: str, values_file: str, *, raw: bool = False
) -> None:
    """Overwrite a range with rows read from a local CSV file."""
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    values = read_values_csv(values_file)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = update_values(
        service,
        spreadsheet_id,
        range_,
        values,
        input_option=RAW if raw else USER_ENTERED,
    )
    print(
        f"Updated {result.get('updatedCells', 0)} cell(s) in "
        f"{result.get('updatedRange', range_)}"
    )


def run_append(
    source: str, range_: str, values_file: str, *, raw: bool = False
) -> None:
    """Append rows read from a local CSV file after the table in ``range_``."""
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    values = read_values_csv(values_file)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = append_values(
        service,
        spreadsheet_id,
        range_,
        values,
        input_option=RAW if raw else USER_ENTERED,
    )
    updates = result.get("updates", {})
    print(
        f"Appended {updates.get('updatedRows', 0)} row(s) to "
        f"{updates.get('updatedRange', range_)}"
    )


def run_clear(source: str, range_: str, *, yes: bool = False) -> None:
    """Clear the values in a range, confirming first unless ``yes``."""
    import typer

    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    if not yes and not typer.confirm(f"Clear values in {range_}?", default=False):
        print("Aborted.", file=sys.stderr)
        return
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = clear_values(service, spreadsheet_id, range_)
    print(f"Cleared {result.get('clearedRange', range_)}")


def run_set(
    source: str,
    match: dict[str, str],
    updates: dict[str, str],
    *,
    tab: str | None = None,
    raw: bool = False,
    allow_multiple: bool = False,
) -> None:
    """Set ``updates`` column(s) on the row(s) matching ``match``.

    Targets the first tab when ``tab`` is None.
    """
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)

    if tab is None:
        tab = first_tab(service, spreadsheet_id)

    summary = set_by_match(
        service,
        spreadsheet_id,
        tab,
        match,
        updates,
        input_option=RAW if raw else USER_ENTERED,
        allow_multiple=allow_multiple,
    )
    rows = summary["rows"]
    row_list = ", ".join(str(r) for r in rows)
    print(
        f"Set {summary['updated_cells']} cell(s) across {len(rows)} row(s) "
        f"(row {row_list}) in {tab}"
    )


def run_rules(source: str, *, as_json: bool = False) -> None:
    """List conditional format rules grouped by tab, or as raw JSON for replay."""
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()
    rules = list_conditional_rules(service, spreadsheet_id)
    if as_json:
        print(json.dumps(rules, indent=2))
    elif not rules:
        print("(no conditional format rules)", file=sys.stderr)
    else:
        print(format_rules(rules))


def run_add_rule(
    source: str,
    *,
    ranges: list[str] | None = None,
    formula: str | None = None,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    underline: bool = False,
    text_color: str | None = None,
    background: str | None = None,
    rule_json: str | None = None,
    index: int = 0,
) -> None:
    """Add a custom-formula rule over ``ranges``, or replay one from ``rule_json``.

    Options, colors, and the JSON file are validated before any API call, so a
    typo fails without a round-trip; only a range's tab needs the tab lookup to
    check.
    """
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    ranges = ranges or []
    flags = {
        "bold": bold,
        "italic": italic,
        "strikethrough": strikethrough,
        "underline": underline,
    }
    if rule_json is not None:
        if (
            ranges
            or formula is not None
            or any(flags.values())
            or text_color
            or background
        ):
            raise ValueError(
                "--rule-json cannot be combined with --range, --formula, "
                "or format options"
            )
        rule = read_rule_json(rule_json)
        spreadsheet_id = _resolve_and_report(source)
        service = build_sheets_service(SHEETS_WRITE_SCOPES)
    else:
        if not ranges or formula is None:
            raise ValueError("pass --range and --formula, or --rule-json")
        if not (any(flags.values()) or text_color or background):
            raise ValueError(
                "a rule needs at least one format option (--bold, --italic, "
                "--strikethrough, --underline, --text-color, or --background)"
            )
        fg = hex_to_color(text_color) if text_color else None
        bg = hex_to_color(background) if background else None
        spreadsheet_id = _resolve_and_report(source)
        service = build_sheets_service(SHEETS_WRITE_SCOPES)
        ids = tab_sheet_ids(service, spreadsheet_id)
        rule = build_formula_rule(
            [a1_to_grid_range(service, spreadsheet_id, r, tab_ids=ids) for r in ranges],
            formula,
            text_color=fg,
            background=bg,
            # Flags are on/off switches at the CLI: off means "leave unset".
            **{k: v or None for k, v in flags.items()},
        )
    add_conditional_rule(service, spreadsheet_id, rule, index=index)
    print(f"Added rule at index {index}: {describe_rule(rule)}")


def run_delete_rule(
    source: str, index: int, *, tab: str | None = None, yes: bool = False
) -> None:
    """Delete the rule at ``index`` on ``tab`` (default: first tab), confirming first.

    Reads the rule list fresh, so the prompt shows the rule actually at that
    position and an out-of-range index is refused before anything is sent.
    Sheets has no revision precondition and a prompt can sit open for a while,
    so after a confirmation the list is read again and the delete is refused
    unless the same rule is still at that index.
    """
    import typer

    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    # One read supplies both the tab lookup and the rule list.
    tabs = _rule_tabs(service, spreadsheet_id)
    ids = _tab_ids(tabs)
    tab = _lookup_tab(ids, tab)
    on_tab = [r for r in _flatten_rules(tabs) if r["tab"] == tab]
    if not 0 <= index < len(on_tab):
        raise ValueError(
            f"tab {tab!r} has {len(on_tab)} rule(s); no rule at index {index}"
        )
    summary = describe_rule(on_tab[index]["rule"])
    if not yes:
        if not typer.confirm(
            f"Delete rule [{index}] on {tab}: {summary}?", default=False
        ):
            print("Aborted.", file=sys.stderr)
            return
        now = [
            r
            for r in _flatten_rules(_rule_tabs(service, spreadsheet_id))
            if r["tab"] == tab
        ]
        if index >= len(now) or now[index] != on_tab[index]:
            raise ValueError(
                f"the rules on {tab!r} changed while waiting for confirmation; "
                "nothing was deleted, run sheets-rules and try again"
            )
    delete_conditional_rule(service, spreadsheet_id, ids[tab], index)
    print(f"Deleted rule [{index}] on {tab}: {summary}")
