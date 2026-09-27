"""Edit a spreadsheet's structure: columns by header name, missing tabs, widths.

Each helper sends at most one ``spreadsheets.batchUpdate`` and none when there
is nothing to do, and refuses a bad request before sending anything. Columns
are named by their header cell, never by position, so a helper still hits the
right column after someone moves it. Header cells are read the way
:func:`~gdrives.sheets.table.read_tab` reads them: canonical strings, stripped.

:func:`add_columns` adds one group of columns at one place, and
:func:`place_columns` puts each column a header lacks at its place in a wanted
order.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote
from gdrives.sheets.cells import to_cell
from gdrives.sheets.values import (
    FORMATTED_STRING,
    UNFORMATTED_VALUE,
    TabGrid,
    batch_update_spreadsheet,
    list_tabs,
    pull_values,
    tab_grid,
)


def _check_names(names: Sequence[str], what: str) -> None:
    """Refuse a blank or repeated name in a request."""
    if "" in names:
        raise ValueError(f"blank {what} name in {list(names)}")
    repeated = sorted({name for name in names if list(names).count(name) > 1})
    if repeated:
        raise ValueError(f"{what}(s) {repeated} named twice")


def _header(service: Service, spreadsheet_id: str, tab: str) -> list[str]:
    """``tab``'s header row, each cell a stripped canonical string."""
    grid = pull_values(
        service,
        spreadsheet_id,
        f"{a1_quote(tab)}!1:1",
        render=UNFORMATTED_VALUE,
        date_time_render=FORMATTED_STRING,
    )
    return [to_cell(cell).strip() for cell in (grid[0] if grid else [])]


def _positions(header: list[str], names: Sequence[str], tab: str) -> dict[str, int]:
    """Each name's 0-based header index, refusing a name absent or repeated."""
    missing = [name for name in names if name not in header]
    if missing:
        raise ValueError(f"tab {tab!r} has no column(s) {missing}; header: {header}")
    repeated = [name for name in names if header.count(name) > 1]
    if repeated:
        raise ValueError(f"tab {tab!r}: header repeats {repeated}")
    return {name: header.index(name) for name in names}


def _columns(sheet_id: int, start: int, end: int) -> dict[str, Any]:
    """A ``DimensionRange`` over the 0-based, end-exclusive columns ``start:end``."""
    return {
        "sheetId": sheet_id,
        "dimension": "COLUMNS",
        "startIndex": start,
        "endIndex": end,
    }


def _open_columns(
    grid: TabGrid, start: int, names: Sequence[str], inherit: bool
) -> list[dict[str, Any]]:
    """The requests that open columns at 0-based ``start`` and name them.

    The columns are inserted, taking the formatting of the column to their
    left with ``inherit`` and to their right without, or appended when
    ``start`` is past the grid's last column.
    """
    count = len(names)
    if start < grid.column_count:
        opened: dict[str, Any] = {
            "insertDimension": {
                "range": _columns(grid.sheet_id, start, start + count),
                "inheritFromBefore": inherit,
            }
        }
    else:
        # Nothing lies past the grid's last column, so appending is inserting.
        opened = {
            "appendDimension": {
                "sheetId": grid.sheet_id,
                "dimension": "COLUMNS",
                "length": start + count - grid.column_count,
            }
        }
    named = {
        "updateCells": {
            "start": {
                "sheetId": grid.sheet_id,
                "rowIndex": 0,
                "columnIndex": start,
            },
            "rows": [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": name}} for name in names
                    ]
                }
            ],
            "fields": "userEnteredValue",
        }
    }
    return [opened, named]


def add_columns(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    names: Sequence[str],
    *,
    before: str | None = None,
    after: str | None = None,
) -> None:
    """Add a column for each of ``names``, in order, with its header cell.

    By default the columns go directly after the header's last named column
    (anything to their right moves over, never under a new header); with
    ``before`` they go directly before that header column, and with ``after``
    directly after it. One request opens the columns, growing the grid when
    needed, and writes the header cells, so no edit lands between the two.
    The new columns take the formatting of the column beside them: the one to
    their right with ``before``, to their left otherwise.

    Raises ValueError, with nothing written, for a blank or repeated name, a
    name the header already has, both ``before`` and ``after``, or one of
    them the header lacks or repeats.
    """
    if before is not None and after is not None:
        raise ValueError(
            f"columns go before or after, not both: {before!r} and {after!r}"
        )
    if not names:
        return
    _check_names(names, "column")
    header = _header(service, spreadsheet_id, tab)
    present = [name for name in names if name in header]
    if present:
        raise ValueError(f"tab {tab!r} already has column(s) {present}")
    if before is not None:
        start = _positions(header, [before], tab)[before]
    elif after is not None:
        start = _positions(header, [after], tab)[after] + 1
    else:
        start = max((i + 1 for i, name in enumerate(header) if name), default=0)
    grid = tab_grid(service, spreadsheet_id, tab)
    # Inherit from the left, except before a named or the first column.
    requests = _open_columns(grid, start, names, before is None and start > 0)
    batch_update_spreadsheet(service, spreadsheet_id, requests)


def place_columns(
    service: Service, spreadsheet_id: str, tab: str, columns: Sequence[str]
) -> list[str]:
    """Add each of ``columns`` the header lacks, at its place in that order.

    ``columns`` is the wanted order. A column the header lacks goes directly
    after the nearest column before it in ``columns`` that the header has, or
    that this call has just placed, and at the front when there is none.
    Columns the header has are never moved: where its order differs from
    ``columns``, a new column still follows its nearest earlier one, wherever
    that sits. Columns outside ``columns`` stay where they are, as does
    anything past the header.

    One request opens every group of columns and writes its header cells,
    growing the grid when needed. The groups are opened right to left, so
    each position is still true when its turn comes. New columns take the
    formatting of the column to their left, and of the one to their right at
    the front. Returns the columns added, in ``columns`` order; with none to
    add, nothing is sent.

    Raises ValueError, with nothing written, for a blank or repeated name, or
    a name the header repeats.
    """
    _check_names(columns, "column")
    if not columns:
        return []
    header = _header(service, spreadsheet_id, tab)
    repeated = [name for name in columns if header.count(name) > 1]
    if repeated:
        raise ValueError(f"tab {tab!r}: header repeats {repeated}")
    groups: dict[int, list[str]] = {}
    start = 0
    for name in columns:
        if name in header:
            start = header.index(name) + 1
        else:
            groups.setdefault(start, []).append(name)
    if not groups:
        return []
    grid = tab_grid(service, spreadsheet_id, tab)
    requests: list[dict[str, Any]] = []
    for start in sorted(groups, reverse=True):
        requests.extend(_open_columns(grid, start, groups[start], start > 0))
    batch_update_spreadsheet(service, spreadsheet_id, requests)
    return [name for name in columns if name not in header]


def delete_columns(
    service: Service, spreadsheet_id: str, tab: str, names: Sequence[str]
) -> None:
    """Delete the columns whose header cells are ``names``, with their data.

    The columns are deleted right to left in one request, so each index is
    still true when its delete runs. Raises ValueError, with nothing deleted,
    for a blank or repeated name, or a name the header lacks or repeats.
    """
    if not names:
        return
    _check_names(names, "column")
    positions = _positions(_header(service, spreadsheet_id, tab), names, tab)
    sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    requests = [
        {"deleteDimension": {"range": _columns(sheet_id, index, index + 1)}}
        for index in sorted(positions.values(), reverse=True)
    ]
    batch_update_spreadsheet(service, spreadsheet_id, requests)


def ensure_tabs(
    service: Service, spreadsheet_id: str, tabs: Sequence[str]
) -> list[str]:
    """Create each of ``tabs`` the spreadsheet lacks; return the titles created.

    New tabs go after the existing ones, in the order given. A tab is never
    deleted or renamed: one this list does not name belongs to whoever added
    it. Raises ValueError for a blank title; a repeated title is created once.
    """
    if "" in tabs:
        raise ValueError(f"blank tab title in {list(tabs)}")
    if not tabs:
        return []
    existing = set(list_tabs(service, spreadsheet_id))
    missing = [title for title in dict.fromkeys(tabs) if title not in existing]
    if missing:
        batch_update_spreadsheet(
            service,
            spreadsheet_id,
            [{"addSheet": {"properties": {"title": title}}} for title in missing],
        )
    return missing


def set_column_widths(
    service: Service, spreadsheet_id: str, tab: str, widths: Mapping[str, int]
) -> None:
    """Set each named column's width in pixels: ``{header name: pixels}``.

    Raises ValueError, with nothing changed, for a width below 1, a blank name,
    or a name the header lacks or repeats.
    """
    if not widths:
        return
    _check_names(list(widths), "column")
    narrow = {name: width for name, width in widths.items() if width < 1}
    if narrow:
        raise ValueError(f"column widths must be at least 1 pixel: {narrow}")
    positions = _positions(_header(service, spreadsheet_id, tab), list(widths), tab)
    sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    requests = [
        {
            "updateDimensionProperties": {
                "range": _columns(sheet_id, positions[name], positions[name] + 1),
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }
        for name, width in widths.items()
    ]
    batch_update_spreadsheet(service, spreadsheet_id, requests)
