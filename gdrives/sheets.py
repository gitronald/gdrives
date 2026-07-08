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
"""

import csv
import sys
from pathlib import Path
from typing import Any

from gdrives.files import Service, extract_drive_id

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


# -- source resolution --


def resolve_spreadsheet_id(source: str, service: Service | None = None) -> str:
    """Resolve a spreadsheet URL, bare file ID, or Drive path to a spreadsheet ID.

    Mirrors how ``download`` picks a target: a URL or bare ID goes through
    ``extract_drive_id``; a Drive path (contains ``/``) is walked via
    ``resolve_path`` using the Drive API. ``service`` is the *Drive* service used
    for path resolution; when omitted, ``resolve_path`` builds a read-only one.
    """
    if source.startswith(("http://", "https://")):
        return extract_drive_id(source)
    if "/" in source:
        from gdrives.resolve import resolve_path

        return resolve_path(source, service, allow_files=True)
    return source


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

    spreadsheet_id = resolve_spreadsheet_id(source)
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
    service = build_sheets_service()

    if range_ is None:
        tabs = list_tabs(service, spreadsheet_id)
        if not tabs:
            raise ValueError("spreadsheet has no tabs to read")
        # Quote the bare tab title so names with spaces or cell-like forms
        # ('Q3 Budget', '2026') stay valid A1 ranges, matching set_by_match.
        range_ = a1_quote(tabs[0])

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

    spreadsheet_id = resolve_spreadsheet_id(source)
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
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

    spreadsheet_id = resolve_spreadsheet_id(source)
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
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

    spreadsheet_id = resolve_spreadsheet_id(source)
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
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

    spreadsheet_id = resolve_spreadsheet_id(source)
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)

    if tab is None:
        tabs = list_tabs(service, spreadsheet_id)
        if not tabs:
            raise ValueError("spreadsheet has no tabs")
        tab = tabs[0]

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
