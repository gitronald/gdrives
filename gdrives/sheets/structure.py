"""Edit a spreadsheet's structure: columns by header name, tabs, widths, links.

Each helper sends at most one ``spreadsheets.batchUpdate`` and none when there
is nothing to do, and refuses a bad request before sending anything. Columns
are named by their header cell, never by position, so a helper still hits the
right column after someone moves it. Header cells are read the way
:func:`~gdrives.sheets.table.read_tab` reads them: canonical strings, stripped.
The helpers a run calls take the tab's ``render`` setting, so a run reads its
header as it reads the rest of the tab.

:func:`add_columns` adds one group of columns at one place, and
:func:`place_columns` puts each column a header lacks at its place in a wanted
order.

The Sheets API formats text that is a URL or a bare domain as a link when it
is written, under ``RAW`` input too. :func:`linked_cells` finds the cells
that hold a link, and :func:`clear_link_format` takes the link format off
cells meant to hold plain text, leaving every other format alone.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.cells import _header_row
from gdrives.sheets.values import (
    TabGrid,
    _pull_rendered,
    batch_update_spreadsheet,
    list_tabs,
    pull_grid,
    tab_grid,
)

#: The ``fields`` mask of a grid read for links. No one field reports every
#: link: ``hyperlink`` has a link on the whole cell, however it was set, and
#: ``textFormatRuns`` a link on part of the cell's text.
LINK_FIELDS = "sheets(data(rowData(values(hyperlink,textFormatRuns(format(link))))))"

#: The format field of a link on the whole cell, which a write gives a URL.
CELL_LINK_FIELD = "userEnteredFormat.textFormat.link"

#: The field of a cell's text format runs, where a link on part of its text is.
RUNS_FIELD = "textFormatRuns"

#: The ``fields`` mask of a grid read of column widths.
WIDTH_FIELDS = "sheets(data(columnMetadata(pixelSize)))"


def _check_names(names: Sequence[str], what: str) -> None:
    """Refuse a blank or repeated name in a request."""
    if "" in names:
        raise ValueError(f"blank {what} name in {list(names)}")
    repeated = sorted({name for name in names if list(names).count(name) > 1})
    if repeated:
        raise ValueError(f"{what}(s) {repeated} named twice")


def _header(
    service: Service, spreadsheet_id: str, tab: str, render: str = "unformatted"
) -> list[str]:
    """``tab``'s header row, each cell a stripped canonical string.

    ``render`` is the tab's setting, as for :func:`~gdrives.sheets.table.read_tab`.
    """
    grid = _pull_rendered(service, spreadsheet_id, f"{a1_quote(tab)}!1:1", render)
    return _header_row(grid)


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
    render: str = "unformatted",
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
    them the header lacks or repeats. ``render`` is how the header is read,
    as for :func:`~gdrives.sheets.table.read_tab`.
    """
    if before is not None and after is not None:
        raise ValueError(
            f"columns go before or after, not both: {before!r} and {after!r}"
        )
    if not names:
        return
    _check_names(names, "column")
    header = _header(service, spreadsheet_id, tab, render)
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
    service: Service,
    spreadsheet_id: str,
    tab: str,
    columns: Sequence[str],
    *,
    render: str = "unformatted",
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
    a name the header repeats. ``render`` is how the header is read, as for
    :func:`~gdrives.sheets.table.read_tab`.
    """
    _check_names(columns, "column")
    if not columns:
        return []
    header = _header(service, spreadsheet_id, tab, render)
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


# -- links --


@dataclass(frozen=True)
class LinkedCell:
    """A cell that holds a link: where it is, and where its links point.

    ``row`` is the 1-based spreadsheet row and ``column`` the header name.
    ``targets`` is the target of each link, the link on the whole cell first.
    ``in_runs`` says whether any of them is a link on part of the cell's
    text, which lives in the cell's text format runs.

    A caller that wants plain text looks for any linked cell. A caller that
    wants links looks for a target that differs from the cell's text, as a
    bare domain's does: ``example.com`` is given the target
    ``http://example.com``.
    """

    row: int
    column: str
    targets: tuple[str, ...]
    in_runs: bool


def _wanted(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    columns: Sequence[str] | None,
    header: Sequence[str] | None,
) -> tuple[dict[str, int], list[str]]:
    """Each wanted column's header index, and the header, read unless it is given."""
    if columns is not None:
        _check_names(columns, "column")
    found = (
        list(header) if header is not None else _header(service, spreadsheet_id, tab)
    )
    names = list(columns) if columns is not None else [name for name in found if name]
    return _positions(found, names, tab), found


def _adjacent(numbers: Sequence[int]) -> list[tuple[int, int]]:
    """Group numbers into runs of adjacent ones, each as ``(first, past the last)``."""
    runs: list[tuple[int, int]] = []
    for number in sorted(set(numbers)):
        if runs and runs[-1][1] == number:
            runs[-1] = (runs[-1][0], number + 1)
        else:
            runs.append((number, number + 1))
    return runs


def linked_cells(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    columns: Sequence[str] | None = None,
    header: Sequence[str] | None = None,
) -> list[LinkedCell]:
    """Every cell of ``columns`` that holds a link, in row then column order.

    ``columns`` defaults to every named column of the header, and the header
    row is a row like any other. One grid read of the tab
    (:func:`~gdrives.sheets.values.pull_grid`) under :data:`LINK_FIELDS`,
    over the columns from the first wanted to the last. ``header`` is the
    tab's header row when the caller has it, which saves the read of row 1.

    Raises ValueError, before the grid read, for a blank or repeated name, or
    a name the header lacks or repeats.
    """
    positions, _ = _wanted(service, spreadsheet_id, tab, columns, header)
    if not positions:
        return []
    first, last = min(positions.values()), max(positions.values())
    names = {index: name for name, index in positions.items()}
    span = f"{a1_quote(tab)}!{column_letter(first)}:{column_letter(last)}"
    data = pull_grid(service, spreadsheet_id, span, LINK_FIELDS)
    found: list[LinkedCell] = []
    for row, held in enumerate(data.get("rowData", []), start=1):
        for index, cell in enumerate(held.get("values", []), start=first):
            if index not in names:
                continue
            whole: list[str] = [cell["hyperlink"]] if "hyperlink" in cell else []
            parts: list[str] = [
                run["format"]["link"]["uri"]
                for run in cell.get(RUNS_FIELD, [])
                if "uri" in run.get("format", {}).get("link", {})
            ]
            if whole or parts:
                found.append(
                    LinkedCell(row, names[index], (*whole, *parts), in_runs=bool(parts))
                )
    return found


def clear_link_format(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    columns: Sequence[str] | None = None,
    rows: Sequence[int] | None = None,
    runs: bool = True,
    header: Sequence[str] | None = None,
    sheet_id: int | None = None,
) -> None:
    """Take the link format off the cells of ``columns``, and nothing else.

    ``columns`` defaults to every named column of the header, and ``rows``
    (1-based spreadsheet rows) to every row. The link on the whole cell is
    cleared by one ``repeatCell`` per run of adjacent columns and rows, under
    the mask :data:`CELL_LINK_FIELD`, so a cell keeps its bold, its fill, and
    the rest of its format.

    With ``runs``, a cell that holds a link on part of its text has its text
    format runs cleared whole, in the same request. The API cannot take a
    link out of a run without rewriting the run, and a rewritten run keeps
    the link's colour and underline. A cell whose runs hold no link keeps
    them. Finding those cells costs a grid read (:func:`linked_cells`), which
    ``runs=False`` skips.

    Writing a value puts the link back, so a clear follows every write of a
    URL. ``header`` and ``sheet_id`` save their reads when the caller has
    them. With nothing to clear no request is sent.

    Raises ValueError, with nothing written, for a blank or repeated column
    name, a name the header lacks or repeats, or a row below 1.
    """
    below = [row for row in rows or () if row < 1]
    if below:
        raise ValueError(f"rows are spreadsheet rows, from 1: {below}")
    positions, known = _wanted(service, spreadsheet_id, tab, columns, header)
    if not positions or (rows is not None and not rows):
        return
    partial: list[LinkedCell] = []
    if runs:
        partial = linked_cells(
            service, spreadsheet_id, tab, columns=list(positions), header=known
        )
    if sheet_id is None:
        sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    requests = _link_clears(sheet_id, positions, rows, partial)
    batch_update_spreadsheet(service, spreadsheet_id, requests)


def link_clear(
    sheet_id: int,
    field: str,
    across: tuple[int, int],
    down: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """The ``repeatCell`` that clears ``field`` over a block of cells.

    ``across`` and ``down`` are 0-based, end-exclusive column and row bounds;
    with no ``down`` the block is every row. The cell sent is empty, so each
    field the mask names is cleared, and no other.
    """
    span: dict[str, Any] = {"sheetId": sheet_id}
    if down is not None:
        span |= {"startRowIndex": down[0], "endRowIndex": down[1]}
    span |= {"startColumnIndex": across[0], "endColumnIndex": across[1]}
    return {"repeatCell": {"range": span, "cell": {}, "fields": field}}


def _link_clears(
    sheet_id: int,
    positions: Mapping[str, int],
    rows: Sequence[int] | None,
    partial: Sequence[LinkedCell],
) -> list[dict[str, Any]]:
    """The requests that clear the cell link of a block, and the runs of ``partial``."""
    spans = [None] if rows is None else _adjacent([row - 1 for row in rows])
    requests = [
        link_clear(sheet_id, CELL_LINK_FIELD, across, down)
        for across in _adjacent(list(positions.values()))
        for down in spans
    ]
    requests.extend(
        link_clear(
            sheet_id,
            RUNS_FIELD,
            (positions[cell.column], positions[cell.column] + 1),
            (cell.row - 1, cell.row),
        )
        for cell in partial
        if cell.in_runs and (rows is None or cell.row in rows)
    )
    return requests


def strip_links(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    columns: Sequence[str],
    *,
    header: Sequence[str],
    sheet_id: int,
    rows: Sequence[int] | None = None,
) -> list[LinkedCell]:
    """Clear the links the cells of ``columns`` hold, and return the ones left.

    For a run that has just written those cells and knows the tab's
    ``header`` and ``sheet_id``. One grid read finds the links. With none
    there is nothing to write, and the result is empty. Otherwise the links
    found are cleared as :func:`clear_link_format` clears them, and a second
    grid read returns the cells that still hold one, which the caller reports
    as a failed read-back. ``rows`` bounds both what is cleared and what is
    returned.
    """

    def found() -> list[LinkedCell]:
        cells = linked_cells(
            service, spreadsheet_id, tab, columns=columns, header=header
        )
        return [cell for cell in cells if rows is None or cell.row in rows]

    linked = found()
    if not linked:
        return []
    positions = _positions(list(header), list(columns), tab)
    requests = _link_clears(sheet_id, positions, rows, linked)
    batch_update_spreadsheet(service, spreadsheet_id, requests)
    return found()


def delete_columns(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    names: Sequence[str],
    *,
    render: str = "unformatted",
) -> None:
    """Delete the columns whose header cells are ``names``, with their data.

    The columns are deleted right to left in one request, so each index is
    still true when its delete runs. Raises ValueError, with nothing deleted,
    for a blank or repeated name, or a name the header lacks or repeats.
    ``render`` is how the header is read, as for
    :func:`~gdrives.sheets.table.read_tab`.
    """
    if not names:
        return
    _check_names(names, "column")
    header = _header(service, spreadsheet_id, tab, render)
    positions = _positions(header, names, tab)
    sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    requests = [
        {"deleteDimension": {"range": _columns(sheet_id, index, index + 1)}}
        for index in sorted(positions.values(), reverse=True)
    ]
    batch_update_spreadsheet(service, spreadsheet_id, requests)


def ensure_tabs(
    service: Service,
    spreadsheet_id: str,
    tabs: Sequence[str],
    *,
    existing: Collection[str] | None = None,
) -> list[str]:
    """Create each of ``tabs`` the spreadsheet lacks; return the titles created.

    New tabs go after the existing ones, in the order given. A tab is never
    deleted or renamed: one this list does not name belongs to whoever added
    it. ``existing`` is the spreadsheet's tab titles when the caller has just
    read them, which saves the read here. Raises ValueError for a blank
    title; a repeated title is created once.
    """
    if "" in tabs:
        raise ValueError(f"blank tab title in {list(tabs)}")
    if not tabs:
        return []
    if existing is None:
        existing = list_tabs(service, spreadsheet_id)
    missing = [title for title in dict.fromkeys(tabs) if title not in existing]
    if missing:
        batch_update_spreadsheet(
            service,
            spreadsheet_id,
            [{"addSheet": {"properties": {"title": title}}} for title in missing],
        )
    return missing


def get_column_widths(
    service: Service, spreadsheet_id: str, tab: str
) -> dict[str, int]:
    """Each named header column's width in pixels: ``{header name: pixels}``.

    In header order, and ready to give :func:`set_column_widths` or to paste
    under a tab's ``widths`` in the config. A column whose header cell is
    blank is left out. The header is read as every helper here reads it, so
    a name is the one :func:`set_column_widths` and a config look for, and
    the widths come from one grid read of row 1
    (:func:`~gdrives.sheets.values.pull_grid`): two reads, and one for a tab
    with no header. Raises ValueError for a header that repeats a name.
    """
    header = _header(service, spreadsheet_id, tab)
    names = [name for name in header if name]
    positions = _positions(header, names, tab)
    if not positions:
        return {}
    data = pull_grid(service, spreadsheet_id, f"{a1_quote(tab)}!1:1", WIDTH_FIELDS)
    sizes = data.get("columnMetadata", [])
    return {name: sizes[index]["pixelSize"] for name, index in positions.items()}


def set_column_widths(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    widths: Mapping[str, int],
    *,
    render: str = "unformatted",
) -> None:
    """Set each named column's width in pixels: ``{header name: pixels}``.

    Raises ValueError, with nothing changed, for a width below 1, a blank name,
    or a name the header lacks or repeats. ``render`` is how the header is
    read, as for :func:`~gdrives.sheets.table.read_tab`.
    """
    if not widths:
        return
    _check_names(list(widths), "column")
    narrow = {name: width for name, width in widths.items() if width < 1}
    if narrow:
        raise ValueError(f"column widths must be at least 1 pixel: {narrow}")
    header = _header(service, spreadsheet_id, tab, render)
    positions = _positions(header, list(widths), tab)
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
