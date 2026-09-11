"""Read and write Google Sheets cell values via the Sheets API v4.

Distinct from ``gdrives export``, which downloads a *whole* spreadsheet to a
local ``.xlsx``/``.csv`` file through the Drive API. Here we operate on live
cell ranges with ``spreadsheets.values.*`` (get/update/append/clear), so callers
can read a range into rows and write rows back without a round-trip through a
file.

The core helpers take a Sheets ``service``, a ``spreadsheet_id``, and an A1
``range_`` (e.g. ``"Sheet1!A1:C10"``). Values are plain ``list[list[str]]`` —
what the API returns with the default ``FORMATTED_VALUE`` render and what it
accepts on write. The Sheets API returns rows truncated at the last non-empty
cell, so display padding lives in ``format_values``; writes send rows as-is
(Sheets pads short rows with blanks).

Conditional format rules are the exception: they live on the spreadsheet
resource, not in ``spreadsheets.values``, so they are read with
``spreadsheets.get`` and written with ``spreadsheets.batchUpdate``, and address
cells by ``GridRange`` (numeric ``sheetId`` + 0-based, half-open indices) rather
than A1 strings.
"""

import csv
import json
import re
import string
import sys
from pathlib import Path
from typing import Any

from gdrives.files import Service

# The two valueInputOption modes. USER_ENTERED parses "=SUM(...)", dates, and
# numbers like the Sheets UI; RAW stores the literal string in each cell.
USER_ENTERED = "USER_ENTERED"
RAW = "RAW"


# -- core value operations --


def pull_values(service: Service, spreadsheet_id: str, range_: str) -> list[list[str]]:
    """Read an A1 range, returning its rows (empty list for an empty range)."""
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_)
        .execute()
    )
    # Sheets omits "values" entirely for an empty range.
    return result.get("values", [])


def update_values(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    values: list[list[str]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Overwrite an A1 range with ``values``; return the API update summary."""
    return (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=input_option,
            body={"values": values},
        )
        .execute()
    )


def append_values(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    values: list[list[str]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Append rows after the table in ``range_``; return the API append summary."""
    return (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=input_option,
            body={"values": values},
        )
        .execute()
    )


def clear_values(service: Service, spreadsheet_id: str, range_: str) -> dict[str, Any]:
    """Clear the values in an A1 range (keeps formatting); return the summary."""
    return (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_, body={})
        .execute()
    )


def batch_update_values(
    service: Service,
    spreadsheet_id: str,
    data: list[tuple[str, list[list[str]]]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Write several ``(range, values)`` pairs in one API round-trip.

    Wraps ``spreadsheets.values.batchUpdate`` — the efficient path for scattered,
    non-contiguous writes (e.g. one cell each across many rows/columns), so a
    conditional update touches the API once regardless of how many cells change.
    """
    body = {
        "valueInputOption": input_option,
        "data": [{"range": range_, "values": values} for range_, values in data],
    }
    return (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )


def list_tabs(service: Service, spreadsheet_id: str) -> list[str]:
    """Return the spreadsheet's tab (sheet) titles in order.

    The friendly path for discovering a valid range: Sheets errors on an unknown
    tab name, so a range-less ``sheets-get`` uses the first tab from here.
    """
    result = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
        .execute()
    )
    return [s["properties"]["title"] for s in result.get("sheets", [])]


def first_tab(service: Service, spreadsheet_id: str) -> str:
    """Return the spreadsheet's first tab title, raising if it has none.

    The shared default for range-less reads and keyed updates: both target the
    first tab when the caller names none.
    """
    tabs = list_tabs(service, spreadsheet_id)
    if not tabs:
        raise ValueError("spreadsheet has no tabs")
    return tabs[0]


# -- source resolution --


def resolve_spreadsheet_id(source: str, service: Service | None = None) -> str:
    """Resolve a spreadsheet URL, bare file ID, or Drive path to a spreadsheet ID.

    The Sheets-facing name for :func:`gdrives.resolve.resolve_file_id`: a URL or
    bare ID goes through ``extract_drive_id``; a Drive path (contains ``/``) is
    walked via ``resolve_path`` using the Drive API. ``service`` is the *Drive*
    service used for path resolution; when omitted, a read-only one is built.
    """
    from gdrives.resolve import resolve_file_id

    return resolve_file_id(source, service)


# -- conditional (find-and-set) updates --


def column_letter(index: int) -> str:
    """Convert a 0-based column index to its A1 letter (0 -> A, 26 -> AA)."""
    if index < 0:
        raise ValueError(f"column index must be non-negative, got {index}")
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def a1_quote(tab: str) -> str:
    """Quote a tab name for A1 notation, escaping embedded single quotes.

    ``'Q3 Budget'`` and names that look like cell refs need quoting; doubling any
    ``'`` (A1's escape) makes an arbitrary tab name safe to interpolate.
    """
    return "'" + tab.replace("'", "''") + "'"


def find_rows(grid: list[list[str]], match: dict[str, str]) -> list[int]:
    """Return the 1-based row numbers of data rows matching all ``match`` conditions.

    ``grid`` is the tab's values with row 0 as the header; ``match`` maps header
    names to required cell values and is ANDed (a composite key). Missing cells
    (ragged rows are truncated at the last non-empty cell) compare as ``""``. An
    empty ``match`` matches every data row.
    """
    if not grid:
        return []
    header = grid[0]
    unknown = [col for col in match if col not in header]
    if unknown:
        raise ValueError(f"match column(s) not in header {header}: {unknown}")
    idx = {col: header.index(col) for col in match}
    hits = []
    for r in range(1, len(grid)):
        row = grid[r]
        if all(
            (row[idx[col]] if idx[col] < len(row) else "") == value
            for col, value in match.items()
        ):
            hits.append(r + 1)  # grid row r is spreadsheet row r + 1 (row 1 = header)
    return hits


def set_by_match(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    match: dict[str, str],
    updates: dict[str, str],
    *,
    input_option: str = USER_ENTERED,
    allow_multiple: bool = False,
) -> dict[str, Any]:
    """Set ``updates`` column(s) on the row(s) whose cells satisfy ``match``.

    Reads ``tab``, locates rows with :func:`find_rows` (composite AND key over
    header-named columns), and writes every ``updates`` cell in one
    :func:`batch_update_values` call. Columns are addressed by header name.
    Refuses when nothing matches, or when more than one row matches unless
    ``allow_multiple`` is set — so a keyed update never silently rewrites the
    wrong row or a whole column. Returns ``{"rows": [...], "updated_cells": N}``.
    """
    if not updates:
        raise ValueError("no columns to set")
    quoted = a1_quote(tab)
    grid = pull_values(service, spreadsheet_id, quoted)
    if not grid:
        raise ValueError(f"tab {tab!r} is empty (no header row)")
    header = grid[0]
    unknown = [col for col in updates if col not in header]
    if unknown:
        raise ValueError(f"target column(s) not in header {header}: {unknown}")

    rows = find_rows(grid, match)
    condition = ", ".join(f"{col}={value!r}" for col, value in match.items())
    if not rows:
        raise ValueError(f"no row matching {condition}")
    if len(rows) > 1 and not allow_multiple:
        raise ValueError(
            f"{condition} matches rows {rows}; pass --all to update every match"
        )

    letters = {col: column_letter(header.index(col)) for col in updates}
    data = [
        (f"{quoted}!{letters[col]}{row}", [[value]])
        for row in rows
        for col, value in updates.items()
    ]
    result = batch_update_values(
        service, spreadsheet_id, data, input_option=input_option
    )
    return {"rows": rows, "updated_cells": result.get("totalUpdatedCells", len(data))}


def parse_pairs(pairs: list[str], flag: str) -> dict[str, str]:
    """Parse ``COLUMN=VALUE`` CLI arguments into a dict (last wins on repeats).

    Splits on the first ``=`` so values may contain ``=``; the column name is
    stripped, the value kept verbatim. ``flag`` names the option in error text.
    """
    out: dict[str, str] = {}
    for pair in pairs:
        col, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"{flag} must be COLUMN=VALUE, got {pair!r}")
        col = col.strip()
        if not col:
            raise ValueError(f"{flag} has an empty column name: {pair!r}")
        out[col] = value
    return out


# -- conditional format rules (spreadsheets.get / spreadsheets.batchUpdate) --
#
# Each tab holds an ordered list of rules addressed by position: the first rule
# that matches a cell wins, and both inserting at an index and deleting one shift
# every rule after it. So list returns each rule's index alongside it, and a
# delete is aimed by (tab, index) from a fresh list.

_CELL_RE = re.compile(r"([A-Za-z]*)([0-9]*)")


def column_index(letters: str) -> int:
    """Convert an A1 column letter to its 0-based index (A -> 0, AA -> 26)."""
    if not (letters.isascii() and letters.isalpha()):
        raise ValueError(f"not a column letter: {letters!r}")
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _unquote(tab: str) -> str:
    """Undo :func:`a1_quote`: strip surrounding quotes and un-double ``''``."""
    if len(tab) >= 2 and tab.startswith("'") and tab.endswith("'"):
        return tab[1:-1].replace("''", "'")
    return tab


def split_a1(range_: str) -> tuple[str | None, str]:
    """Split an A1 range into ``(tab title or None, cell span)``, unquoting the tab.

    Splits on the *last* ``!``, since a quoted tab name may contain one but the
    cell span never does.
    """
    if "!" not in range_:
        return None, range_
    tab, cells = range_.rsplit("!", 1)
    return _unquote(tab), cells


def _tab_ids(tabs: list[dict[str, Any]]) -> dict[str, int]:
    """Map each tab title to its ``sheetId`` from a ``spreadsheets.get`` sheets list."""
    # The API omits zero-valued fields, so the first tab's sheetId 0 may be absent.
    return {s["properties"]["title"]: s["properties"].get("sheetId", 0) for s in tabs}


def tab_sheet_ids(service: Service, spreadsheet_id: str) -> dict[str, int]:
    """Map each tab title to its numeric ``sheetId``, in tab order."""
    result = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)")
        .execute()
    )
    return _tab_ids(result.get("sheets", []))


def _lookup_tab(tab_ids: dict[str, int], tab: str | None) -> str:
    """Return ``tab`` (or the first tab when None), raising if it does not exist."""
    if not tab_ids:
        raise ValueError("spreadsheet has no tabs")
    if tab is None:
        return next(iter(tab_ids))
    if tab not in tab_ids:
        raise ValueError(f"no tab named {tab!r}; tabs: {list(tab_ids)}")
    return tab


def _parse_cell(ref: str, range_: str) -> tuple[int | None, int | None]:
    """Parse one A1 corner (``B3``, ``AA``, ``7``) to (column index, row number)."""
    ref = ref.replace("$", "")  # absolute refs ($A$2) address the same cells
    match = _CELL_RE.fullmatch(ref)
    if match is None or not ref:
        raise ValueError(f"bad A1 cell reference {ref!r} in {range_!r}")
    letters, digits = match.groups()
    row = int(digits) if digits else None
    if row == 0:
        raise ValueError(f"row numbers start at 1, got {ref!r} in {range_!r}")
    return (column_index(letters) if letters else None), row


def _cell_span(cells: str) -> dict[str, int]:
    """Convert an A1 cell span to GridRange indices (0-based, end-exclusive).

    An omitted row or column on a corner leaves that index unset, which is how
    the API expresses an open-ended range: ``A2:AA`` has no ``endRowIndex``.
    (A stored conditional format rule does not keep the open end: the API clamps
    it to the tab's current size, so ``A2:AA`` lists back as ``A2:AA1000``.)
    """
    start, sep, end = cells.partition(":")
    s_col, s_row = _parse_cell(start, cells)
    e_col, e_row = _parse_cell(end, cells) if sep else (s_col, s_row)
    span: dict[str, int] = {}
    if s_row is not None:
        span["startRowIndex"] = s_row - 1
    if e_row is not None:
        span["endRowIndex"] = e_row
    if s_col is not None:
        span["startColumnIndex"] = s_col
    if e_col is not None:
        span["endColumnIndex"] = e_col + 1
    for axis in ("Row", "Column"):
        lo, hi = span.get(f"start{axis}Index"), span.get(f"end{axis}Index")
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError(f"range {cells!r} ends before it starts")
    return span


def a1_to_grid_range(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    *,
    tab_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Convert an A1 range to a ``GridRange`` dict for ``spreadsheets.batchUpdate``.

    Resolves the tab title to its ``sheetId`` (a bare span like ``A2:C`` targets
    the first tab; a bare tab name, or ``Tab!``, covers the whole tab). Pass
    ``tab_ids`` from :func:`tab_sheet_ids` to convert several ranges with one
    lookup.
    """
    ids = tab_ids if tab_ids is not None else tab_sheet_ids(service, spreadsheet_id)
    tab, cells = split_a1(range_)
    if tab is None and _unquote(cells) in ids:
        tab, cells = _unquote(cells), ""
    tab = _lookup_tab(ids, tab)
    grid: dict[str, Any] = {"sheetId": ids[tab]}
    if cells:
        grid.update(_cell_span(cells))
    return grid


def grid_range_to_a1(grid: dict[str, Any]) -> str:
    """Render a GridRange's cell span in A1 notation, without the tab.

    An absent end index is an open end (``A2:AA``); an absent span is the whole
    tab, rendered as ``""``.
    """
    sc, ec = grid.get("startColumnIndex"), grid.get("endColumnIndex")
    sr, er = grid.get("startRowIndex"), grid.get("endRowIndex")
    cols = sc is not None or ec is not None
    rows = sr is not None or er is not None
    if not (cols or rows):
        return ""
    start = (column_letter(sc or 0) if cols else "") + (
        str((sr or 0) + 1) if rows else ""
    )
    end = (column_letter(ec - 1) if ec is not None else "") + (
        str(er) if er is not None else ""
    )
    return start if cols and rows and start == end else f"{start}:{end}"


def hex_to_color(value: str) -> dict[str, float]:
    """Convert ``#RRGGBB`` (or ``RRGGBB`` / ``#RGB``) to a Sheets Color (0-1 floats)."""
    digits = value.strip().removeprefix("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) != 6 or any(c not in string.hexdigits for c in digits):
        raise ValueError(f"color must be a hex string like '#999999', got {value!r}")
    red, green, blue = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": red, "green": green, "blue": blue}


def color_to_hex(color: dict[str, Any]) -> str:
    """Convert a Sheets Color to ``#rrggbb``; channels the API omits count as 0."""
    return "#" + "".join(
        f"{round(color.get(c, 0) * 255):02x}" for c in ("red", "green", "blue")
    )


def build_formula_rule(
    ranges: list[dict[str, Any]],
    formula: str,
    *,
    bold: bool | None = None,
    italic: bool | None = None,
    strikethrough: bool | None = None,
    underline: bool | None = None,
    text_color: dict[str, float] | None = None,
    background: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build a custom-formula ``ConditionalFormatRule`` over ``ranges`` (GridRanges).

    Only the format options given are sent, so an unset one keeps the cell's own
    formatting rather than being forced off. Colors are Sheets Color dicts (see
    :func:`hex_to_color`). Raises when there are no ranges, ranges on more than one
    tab (the API requires a rule's ranges to share a ``sheetId``), or no format
    options.
    """
    if not ranges:
        raise ValueError("a rule needs at least one range")
    if len({r.get("sheetId", 0) for r in ranges}) > 1:
        raise ValueError("a rule's ranges must all be on one tab")
    flags = {
        "bold": bold,
        "italic": italic,
        "strikethrough": strikethrough,
        "underline": underline,
    }
    text_format: dict[str, Any] = {k: v for k, v in flags.items() if v is not None}
    if text_color is not None:
        text_format["foregroundColor"] = text_color
    fmt: dict[str, Any] = {}
    if text_format:
        fmt["textFormat"] = text_format
    if background is not None:
        fmt["backgroundColor"] = background
    if not fmt:
        raise ValueError("a rule needs at least one format option")
    return {
        "ranges": list(ranges),
        "booleanRule": {
            "condition": {
                "type": "CUSTOM_FORMULA",
                "values": [{"userEnteredValue": formula}],
            },
            "format": fmt,
        },
    }


def batch_update_spreadsheet(
    service: Service, spreadsheet_id: str, requests: list[dict[str, Any]]
) -> dict[str, Any]:
    """Send structural ``requests`` via ``spreadsheets.batchUpdate``.

    Not :func:`batch_update_values` (``spreadsheets.values.batchUpdate``), which
    writes cell values; this one edits the spreadsheet resource itself.
    """
    return (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
        .execute()
    )


def _rule_tabs(service: Service, spreadsheet_id: str) -> list[dict[str, Any]]:
    """Read every tab's properties and conditional formats in one ``get``."""
    result = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            # A narrow mask: the default response carries the whole grid.
            fields="sheets(properties(sheetId,title),conditionalFormats)",
        )
        .execute()
    )
    return result.get("sheets", [])


def list_conditional_rules(
    service: Service, spreadsheet_id: str
) -> list[dict[str, Any]]:
    """Return every conditional format rule as ``{tab, sheet_id, index, rule}``.

    Rules come back in tab order, then rule order; ``index`` is the rule's
    position on its tab (what :func:`delete_conditional_rule` takes). Color-scale
    (``gradientRule``) rules are returned verbatim alongside boolean ones.
    """
    return _flatten_rules(_rule_tabs(service, spreadsheet_id))


def _flatten_rules(tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten :func:`_rule_tabs` output to ``{tab, sheet_id, index, rule}`` entries."""
    rules = []
    for sheet in tabs:
        props = sheet["properties"]
        # Like "values", the key is absent (not empty) on a tab with no rules.
        for index, rule in enumerate(sheet.get("conditionalFormats", [])):
            rules.append(
                {
                    "tab": props["title"],
                    "sheet_id": props.get("sheetId", 0),
                    "index": index,
                    "rule": rule,
                }
            )
    return rules


def add_conditional_rule(
    service: Service, spreadsheet_id: str, rule: dict[str, Any], *, index: int = 0
) -> dict[str, Any]:
    """Insert ``rule`` at ``index`` in its tab's rules (0 = first, highest priority).

    The tab is the one named by the rule's ranges' ``sheetId``; rules at or after
    ``index`` shift down by one.
    """
    if index < 0:
        raise ValueError(f"rule index must be non-negative, got {index}")
    return batch_update_spreadsheet(
        service,
        spreadsheet_id,
        [{"addConditionalFormatRule": {"rule": rule, "index": index}}],
    )


def delete_conditional_rule(
    service: Service, spreadsheet_id: str, sheet_id: int, index: int
) -> dict[str, Any]:
    """Delete the rule at ``index`` on tab ``sheet_id``; later rules shift up by one."""
    if index < 0:
        raise ValueError(f"rule index must be non-negative, got {index}")
    return batch_update_spreadsheet(
        service,
        spreadsheet_id,
        [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": index}}],
    )


def read_rule_json(path: str) -> dict[str, Any]:
    """Read one rule from a JSON file for replay with :func:`add_conditional_rule`.

    Accepts a bare ``ConditionalFormatRule`` or one entry of ``sheets-rules
    --json`` output (unwrapping its ``rule``). Its ranges keep their ``sheetId``,
    so they must name a tab that exists on the target spreadsheet.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "rule" in data:
        data = data["rule"]
    if not isinstance(data, dict) or not (
        "booleanRule" in data or "gradientRule" in data
    ):
        raise ValueError(
            f"{path}: expected one conditional format rule "
            "(an object with booleanRule or gradientRule, or one sheets-rules "
            "--json entry)"
        )
    return data


def _format_summary(fmt: dict[str, Any]) -> str:
    """Summarize a rule's CellFormat: flags plus text/fill colors as hex."""
    text = fmt.get("textFormat", {})
    parts = [
        flag
        for flag in ("bold", "italic", "strikethrough", "underline")
        if text.get(flag)
    ]
    fg = text.get(
        "foregroundColor", text.get("foregroundColorStyle", {}).get("rgbColor")
    )
    if fg is not None:
        parts.append(f"text {color_to_hex(fg)}")
    bg = fmt.get("backgroundColor", fmt.get("backgroundColorStyle", {}).get("rgbColor"))
    if bg is not None:
        parts.append(f"fill {color_to_hex(bg)}")
    return ", ".join(parts) or "no format"


def describe_rule(rule: dict[str, Any]) -> str:
    """Render a rule as one line: ranges, condition, and format summary."""
    ranges = ", ".join(
        grid_range_to_a1(r) or "(whole tab)" for r in rule.get("ranges", [])
    )
    if "gradientRule" in rule:
        return f"{ranges}  color scale"
    boolean = rule.get("booleanRule", {})
    condition = boolean.get("condition", {})
    kind = condition.get("type", "")
    # A custom formula speaks for itself; other types (TEXT_CONTAINS, ...) need it.
    parts = [] if kind == "CUSTOM_FORMULA" else [kind]
    parts += [
        v.get("userEnteredValue", v.get("relativeDate", ""))
        for v in condition.get("values", [])
    ]
    return (
        f"{ranges}  {' '.join(parts)}  [{_format_summary(boolean.get('format', {}))}]"
    )


def format_rules(rules: list[dict[str, Any]]) -> str:
    """Render :func:`list_conditional_rules` output by tab, one rule per line."""
    lines: list[str] = []
    tab = None
    for entry in rules:
        if entry["tab"] != tab:
            tab = entry["tab"]
            lines.append(f"{tab} (sheetId {entry['sheet_id']})")
        lines.append(f"  [{entry['index']}] {describe_rule(entry['rule'])}")
    return "\n".join(lines)


# -- CSV interchange --


def read_values_csv(path: str, *, delimiter: str = ",") -> list[list[str]]:
    """Read a local delimited file into rows of string cells."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f, delimiter=delimiter))


def write_values_csv(
    path: str, values: list[list[str]], *, delimiter: str = ","
) -> None:
    """Write rows of cells to a local delimited file, creating parent dirs."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=delimiter).writerows(values)


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
) -> None:
    """Read a range and print it, or write it to a delimited file with ``output``.

    With no ``range_``, defaults to the first tab. To stdout: aligned columns by
    default, or delimited rows when ``aligned`` is False. With ``output``: writes
    a delimited file (``delimiter``) and reports the row count to stderr.
    """
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()

    if range_ is None:
        # Quote the bare tab title so names with spaces or cell-like forms
        # ('Q3 Budget', '2026') stay valid A1 ranges, matching set_by_match.
        range_ = a1_quote(first_tab(service, spreadsheet_id))

    values = pull_values(service, spreadsheet_id, range_)

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
    if not yes and not typer.confirm(
        f"Delete rule [{index}] on {tab}: {summary}?", default=False
    ):
        print("Aborted.", file=sys.stderr)
        return
    delete_conditional_rule(service, spreadsheet_id, ids[tab], index)
    print(f"Deleted rule [{index}] on {tab}: {summary}")
