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
        range_ = tabs[0]

    values = pull_values(service, spreadsheet_id, range_)

    if output:
        write_values_csv(output, values, delimiter=delimiter)
        print(f"Wrote {len(values)} row(s) to {output}", file=sys.stderr)
    elif not values:
        print("(empty range)", file=sys.stderr)
    elif aligned:
        print(format_values(values))
    else:
        csv.writer(sys.stdout, delimiter=delimiter).writerows(values)


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
