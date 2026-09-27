"""Sheets API v4 wrappers: cell values (``spreadsheets.values.*``) and tab lookups.

The core helpers take a Sheets ``service``, a ``spreadsheet_id``, and an A1
``range_`` (e.g. ``"Sheet1!A1:C10"``). Values are plain ``list[list[str]]`` —
what the API returns with the default ``FORMATTED_VALUE`` render and what it
accepts on write. The Sheets API returns rows truncated at the last non-empty
cell, so display padding lives in ``format_values``; writes send rows as-is
(Sheets pads short rows with blanks).

Tab titles and ``sheetId`` values come from ``spreadsheets.get``, and structural
edits go through ``spreadsheets.batchUpdate``; both wrappers live here beside
the value operations.
"""

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
    """Append rows after the table in ``range_``; return the API append summary.

    The rows are inserted (``INSERT_ROWS``), shifting anything below the table
    down. The API's default, ``OVERWRITE``, writes into whatever follows the
    table, so a second block of data one blank row below would lose its first
    rows.
    """
    return (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=input_option,
            insertDataOption="INSERT_ROWS",
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


# -- tab lookups and structural updates --


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
