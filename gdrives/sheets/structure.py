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
:func:`styled_cells` finds the cells that look like a link and hold none.
:func:`url_link_problems` and :func:`set_url_links` do the opposite for cells
whose whole text is a URL: each is to hold a link to its own text, in a
colour the caller names, and not underlined.
"""

import re
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

#: :data:`LINK_FIELDS` and, for :func:`linked_cells` with ``detail``, each
#: cell's displayed text and its formula, if it has one.
LINK_DETAIL_FIELDS = (
    "sheets(data(rowData(values(hyperlink,formattedValue,"
    "userEnteredValue(formulaValue),textFormatRuns(format(link))))))"
)

#: The format field of a link on the whole cell, which a write gives a URL.
CELL_LINK_FIELD = "userEnteredFormat.textFormat.link"

#: The format fields a link's look is written in: the link, the underline, and
#: the text colour. ``clear_link_format`` with ``style`` clears all three.
CELL_STYLE_FIELDS = ",".join(
    f"userEnteredFormat.textFormat.{name}"
    for name in ("link", "underline", "foregroundColorStyle")
)

#: The colour the API shows a link in, which a cell that looks like a link has.
LINK_COLOR = "#1155cc"

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

    ``text`` and ``formula`` are filled by :func:`linked_cells` with
    ``detail`` only, and otherwise keep their defaults. ``text`` is the
    cell's displayed text, empty for an empty cell, which can hold a link
    all the same. ``formula`` is True when the link on the whole cell comes
    from a ``HYPERLINK`` formula: the cell's formula calls ``HYPERLINK``,
    read without regard to case. The rule is the formula's text, so a
    ``HYPERLINK`` inside a string, or inside a branch of another function
    that is not the one taken, also counts. A format link cannot tell the
    two apart, since a formula's link is also copied into the cell's format
    once anything else is written to it. Clearing the link format of such a
    cell removes the link and leaves the formula, which then shows its label
    as plain text.

    A caller that wants plain text looks for any linked cell. A caller that
    wants links looks for a target that differs from the cell's text, as a
    bare domain's does: ``example.com`` is given the target
    ``http://example.com``.
    """

    row: int
    column: str
    targets: tuple[str, ...]
    in_runs: bool
    text: str = ""
    formula: bool = False


# A formula that calls HYPERLINK, as a function and not as part of a longer name.
_HYPERLINK_CALL = re.compile(r"(?<![\w.])HYPERLINK\s*\(", re.IGNORECASE)


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
    detail: bool = False,
) -> list[LinkedCell]:
    """Every cell of ``columns`` that holds a link, in row then column order.

    ``columns`` defaults to every named column of the header, and the header
    row is a row like any other. One grid read of the tab
    (:func:`~gdrives.sheets.values.pull_grid`) under :data:`LINK_FIELDS`,
    over the columns from the first wanted to the last. ``header`` is the
    tab's header row when the caller has it, which saves the read of row 1.

    With ``detail`` the read is under :data:`LINK_DETAIL_FIELDS` and fills
    each cell's ``text`` and ``formula``; without it they keep their
    defaults (``""`` and False), and the read and the result are those of
    0.14.

    Raises ValueError, before the grid read, for a blank or repeated name, or
    a name the header lacks or repeats.
    """
    positions, _ = _wanted(service, spreadsheet_id, tab, columns, header)
    if not positions:
        return []
    first, last = min(positions.values()), max(positions.values())
    names = {index: name for name, index in positions.items()}
    span = f"{a1_quote(tab)}!{column_letter(first)}:{column_letter(last)}"
    fields = LINK_DETAIL_FIELDS if detail else LINK_FIELDS
    data = pull_grid(service, spreadsheet_id, span, fields)
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
            if not (whole or parts):
                continue
            extra: dict[str, Any] = {}
            if detail:
                formula = cell.get("userEnteredValue", {}).get("formulaValue", "")
                extra = {
                    "text": cell.get("formattedValue", ""),
                    "formula": bool(whole) and bool(_HYPERLINK_CALL.search(formula)),
                }
            found.append(
                LinkedCell(
                    row, names[index], (*whole, *parts), in_runs=bool(parts), **extra
                )
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
    style: bool = False,
    formulas: bool = True,
    header: Sequence[str] | None = None,
    sheet_id: int | None = None,
) -> None:
    """Take the link format off the cells of ``columns``, and nothing else.

    ``columns`` defaults to every named column of the header, and ``rows``
    (1-based spreadsheet rows) to every row. The link on the whole cell is
    cleared by one ``repeatCell`` per run of adjacent columns and rows, under
    the mask :data:`CELL_LINK_FIELD`, so a cell keeps its bold, its fill, and
    the rest of its format.

    With ``style`` the same request also resets the cells' underline and text
    colour, under the mask :data:`CELL_STYLE_FIELDS`, which leaves black text
    that is not underlined, with the bold and the rest kept. It clears them
    on every cell of the block, linked or not, so a caller with text of
    another colour in those columns passes the ``rows`` of the cells it
    means (from :func:`linked_cells` and :func:`styled_cells`). A reset
    changes what the cell itself says, so a look that comes from conditional
    formatting or the theme stays. ``style`` and ``runs`` are independent:
    ``runs`` clears the text format runs of a cell with a link in them, and
    ``style`` does not touch runs.

    A cell whose link comes from a ``HYPERLINK`` formula loses the link and
    keeps the formula, which then shows its label as plain text. With
    ``formulas=False`` such a cell is left as it is: its link, its
    underline, and its colour, and its runs if it had any. Each block is
    split around those cells, so a column with none is still cleared by one
    block, and the rows of a block with no ``rows`` end where the last
    skipped cell of its columns is passed (an open range, to the end of the
    tab). The cells are found by the same grid read as the runs,
    ``linked_cells(..., detail=True)``, so ``formulas=False`` costs that
    read even with ``runs=False``. ``formulas`` is named beside ``runs`` and
    ``style`` for what the clear also takes: the formula cells, by default.

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
    found: list[LinkedCell] = []
    if runs or not formulas:
        found = linked_cells(
            service,
            spreadsheet_id,
            tab,
            columns=list(positions),
            header=known,
            detail=not formulas,
        )
    partial = found if runs else []
    left = [] if formulas else [cell for cell in found if cell.formula]
    if sheet_id is None:
        sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    field = CELL_STYLE_FIELDS if style else CELL_LINK_FIELD
    requests = _link_clears(sheet_id, positions, rows, partial, field, left)
    if requests:
        batch_update_spreadsheet(service, spreadsheet_id, requests)


def link_clear(
    sheet_id: int,
    field: str,
    across: tuple[int, int],
    down: tuple[int, int | None] | None = None,
) -> dict[str, Any]:
    """The ``repeatCell`` that clears ``field`` over a block of cells.

    ``across`` and ``down`` are 0-based, end-exclusive column and row bounds;
    with no ``down`` the block is every row, and a ``down`` with no end runs
    from its start to the last row. The cell sent is empty, so each field the
    mask names is cleared, and no other.
    """
    span: dict[str, Any] = {"sheetId": sheet_id}
    if down is not None:
        span["startRowIndex"] = down[0]
        if down[1] is not None:
            span["endRowIndex"] = down[1]
    span |= {"startColumnIndex": across[0], "endColumnIndex": across[1]}
    return {"repeatCell": {"range": span, "cell": {}, "fields": field}}


def _skipped_rows(
    positions: Mapping[str, int], rows: Sequence[int] | None, left: Sequence[LinkedCell]
) -> dict[int, frozenset[int]]:
    """The 0-based rows to leave, by column index, among the wanted ones."""
    skipped: dict[int, set[int]] = {}
    for cell in left:
        if rows is None or cell.row in rows:
            skipped.setdefault(positions[cell.column], set()).add(cell.row - 1)
    return {column: frozenset(held) for column, held in skipped.items()}


def _blocks(
    positions: Mapping[str, int], skipped: Mapping[int, frozenset[int]]
) -> list[tuple[tuple[int, int], frozenset[int]]]:
    """Runs of adjacent columns that leave the same rows, as ``(across, rows)``."""
    blocks: list[tuple[tuple[int, int], frozenset[int]]] = []
    for first, end in _adjacent(list(positions.values())):
        for column in range(first, end):
            gap = skipped.get(column, frozenset())
            if blocks and blocks[-1][0][1] == column and blocks[-1][1] == gap:
                blocks[-1] = ((blocks[-1][0][0], column + 1), gap)
            else:
                blocks.append(((column, column + 1), gap))
    return blocks


def _row_spans(
    rows: Sequence[int] | None, gap: frozenset[int]
) -> list[tuple[int, int | None] | None]:
    """The row bounds of a block that leaves the 0-based rows of ``gap``.

    With no ``rows`` the block is every row: ``None`` when it leaves none,
    else the stretches between the rows left, the last with no end.
    """
    if rows is not None:
        return list(_adjacent([row - 1 for row in rows if row - 1 not in gap]))
    if not gap:
        return [None]
    spans: list[tuple[int, int | None] | None] = []
    start = 0
    for row in sorted(gap):
        if row > start:
            spans.append((start, row))
        start = row + 1
    spans.append((start, None))
    return spans


def _link_clears(
    sheet_id: int,
    positions: Mapping[str, int],
    rows: Sequence[int] | None,
    partial: Sequence[LinkedCell],
    field: str = CELL_LINK_FIELD,
    left: Sequence[LinkedCell] = (),
) -> list[dict[str, Any]]:
    """The requests that clear ``field`` of a block, and the runs of ``partial``.

    The cells of ``left`` are excluded from both: a block is split around
    them, and their runs are kept.
    """
    skipped = _skipped_rows(positions, rows, left)
    requests = [
        link_clear(sheet_id, field, across, down)
        for across, gap in _blocks(positions, skipped)
        for down in _row_spans(rows, gap)
    ]
    kept = {(cell.column, cell.row) for cell in left}
    requests.extend(
        link_clear(
            sheet_id,
            RUNS_FIELD,
            (positions[cell.column], positions[cell.column] + 1),
            (cell.row - 1, cell.row),
        )
        for cell in partial
        if cell.in_runs
        and (rows is None or cell.row in rows)
        and (cell.column, cell.row) not in kept
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


# -- URL links --

# Why a URL cell is not as set_url_links leaves it, in the order a problem
# lists them: it holds no link, its link points somewhere other than its
# text, its text is not in the colour named, its text is underlined, or it
# has text format runs.
_URL_REASON_ORDER = ("no_link", "target", "color", "underline", "runs")

#: The reasons a :class:`UrlLinkProblem` can give.
URL_LINK_REASONS = frozenset(_URL_REASON_ORDER)

# A cell's whole text, stripped, as a URL.
_URL_CELL = re.compile(r"https?://\S+", re.IGNORECASE)

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

# The grid read of URL cells. A link underlines and colours its text with no
# user-entered property saying so, so the colour and underline are read from
# the effective format.
_URL_FIELDS = (
    "sheets(data(rowData(values(hyperlink,textFormatRuns,"
    "effectiveFormat(textFormat(underline,foregroundColorStyle))))))"
)

# The properties the fix writes, and no others, so a cell keeps its bold, its
# fill, and its font. The runs are not among them: in the request that sets a
# link they take the link away, so they are cleared by a request of their own.
_URL_FORMAT_FIELDS = CELL_STYLE_FIELDS

_RGB = tuple[int, int, int]


@dataclass(frozen=True)
class UrlLinkProblem:
    """A URL cell whose link is not as wanted, and why.

    ``row`` is the 1-based spreadsheet row, ``column`` the header name, and
    ``text`` the cell's text, stripped, which is the link's wanted target.
    ``reasons`` are names from :data:`URL_LINK_REASONS`, in the order
    ``no_link``, ``target``, ``color``, ``underline``, ``runs``.
    """

    row: int
    column: str
    text: str
    reasons: tuple[str, ...]


def _rgb(color: str) -> _RGB:
    """The channels of a ``#rrggbb`` colour, each 0 to 255."""
    if not isinstance(color, str) or not _HEX_COLOR.fullmatch(color):
        raise ValueError(f"a colour is written '#rrggbb', not {color!r}")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _channels(color: Mapping[str, Any]) -> _RGB:
    """An API colour's channels, each to the nearest of 255 steps.

    The API omits a channel that is zero, so a missing one is 0.
    """
    red, green, blue = (
        round(float(color.get(name, 0)) * 255) for name in ("red", "green", "blue")
    )
    return red, green, blue


def _check_rows(rows: Sequence[int] | None) -> None:
    below = [row for row in rows or () if row < 1]
    if below:
        raise ValueError(f"rows are spreadsheet rows, from 1: {below}")


def _url_reasons(cell: Mapping[str, Any], text: str, rgb: _RGB) -> tuple[str, ...]:
    """Why one URL cell, as a grid read returned it, is not as wanted."""
    shown = cell.get("effectiveFormat", {}).get("textFormat", {})
    color = shown.get("foregroundColorStyle", {}).get("rgbColor", {})
    link = cell.get("hyperlink")
    found = {
        "no_link": link is None,
        "target": link is not None and link != text,
        "color": _channels(color) != rgb,
        "underline": bool(shown.get("underline")),
        "runs": bool(cell.get(RUNS_FIELD)),
    }
    return tuple(reason for reason in _URL_REASON_ORDER if found[reason])


def _url_problems(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    rgb: _RGB,
    columns: Sequence[str] | None,
    rows: Sequence[int] | None,
    cells: Collection[tuple[int, str]] | None = None,
) -> tuple[list[UrlLinkProblem], dict[str, int]]:
    """The problems of the URL cells named, and each wanted column's index.

    The tab's values are read first, and the grid read is bounded to the
    rows and columns of the URL cells among them; with none, it is not made.
    ``cells`` limits the cells to those ``(row, column)`` pairs, for a run
    that wrote cells rather than whole rows and columns.
    """
    grid = _pull_rendered(service, spreadsheet_id, a1_quote(tab), "unformatted")
    positions, _ = _wanted(service, spreadsheet_id, tab, columns, _header_row(grid))
    names = {index: name for name, index in positions.items()}
    wanted = None if rows is None else set(rows)
    found: dict[tuple[int, int], str] = {}
    for row, values in enumerate(grid, start=1):
        if wanted is not None and row not in wanted:
            continue
        for index, value in enumerate(values):
            if index not in names:
                continue
            if cells is not None and (row, names[index]) not in cells:
                continue
            text = value.strip() if isinstance(value, str) else ""
            if _URL_CELL.fullmatch(text):
                found[(row, index)] = text
    if not found:
        return [], positions
    top, bottom = min(row for row, _ in found), max(row for row, _ in found)
    left, right = min(index for _, index in found), max(index for _, index in found)
    span = f"{a1_quote(tab)}!{column_letter(left)}{top}:{column_letter(right)}{bottom}"
    data = pull_grid(service, spreadsheet_id, span, _URL_FIELDS)
    read: dict[tuple[int, int], dict[str, Any]] = {
        (row, index): cell
        for row, held in enumerate(data.get("rowData", []), start=top)
        for index, cell in enumerate(held.get("values", []), start=left)
    }
    problems: list[UrlLinkProblem] = []
    for (row, index), text in sorted(found.items()):
        reasons = _url_reasons(read.get((row, index), {}), text, rgb)
        if reasons:
            problems.append(UrlLinkProblem(row, names[index], text, reasons))
    return problems, positions


def url_link_problems(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    color: str,
    columns: Sequence[str] | None = None,
    rows: Sequence[int] | None = None,
) -> list[UrlLinkProblem]:
    """Every URL cell of ``columns`` and ``rows`` whose link is not as wanted.

    A URL cell is one whose whole text, stripped, is ``http://`` or
    ``https://`` followed by characters with no whitespace; a bare domain, a
    URL inside a sentence, and an email address are not. Each is to hold a
    link to its own text, in ``color`` (``#rrggbb``, compared to the nearest
    of 255 steps a channel), not underlined, and with no text format runs.
    Returns the cells that break that rule, in row then column order.

    ``columns`` defaults to every named column of the header, and ``rows``
    (1-based spreadsheet rows) to every row, so a caller can pass the cells
    an :class:`~gdrives.sheets.apply.ApplyResult` wrote. One read of the
    tab's values finds the URL cells, and one grid read
    (:func:`~gdrives.sheets.values.pull_grid`), bounded to the rows and
    columns that hold them, reads their links and formats; a tab with no URL
    cell costs the first read only.

    Raises ValueError, before any request, for a colour that is not
    ``#rrggbb`` or a row below 1, and before the grid read for a blank or
    repeated column name, or a name the header lacks or repeats.
    """
    rgb = _rgb(color)
    _check_rows(rows)
    return _url_problems(service, spreadsheet_id, tab, rgb, columns, rows)[0]


def _url_link(
    sheet_id: int, column: int, row: int, text: str, rgb: _RGB
) -> dict[str, Any]:
    """The ``repeatCell`` linking one cell to ``text``, in ``rgb``, not underlined."""
    red, green, blue = (channel / 255 for channel in rgb)
    text_format = {
        "link": {"uri": text},
        "underline": False,
        "foregroundColorStyle": {
            "rgbColor": {"red": red, "green": green, "blue": blue}
        },
    }
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row - 1,
                "endRowIndex": row,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            },
            "cell": {"userEnteredFormat": {"textFormat": text_format}},
            "fields": _URL_FORMAT_FIELDS,
        }
    }


def _fix_url_links(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    color: str,
    *,
    columns: Sequence[str] | None = None,
    rows: Sequence[int] | None = None,
    cells: Collection[tuple[int, str]] | None = None,
    sheet_id: int | None = None,
) -> list[UrlLinkProblem]:
    """:func:`set_url_links`, limited to ``cells`` when given.

    For a run that wrote a set of ``(row, column)`` cells, and knows the
    tab's ``sheet_id``, which saves its read.
    """
    rgb = _rgb(color)
    _check_rows(rows)
    problems, positions = _url_problems(
        service, spreadsheet_id, tab, rgb, columns, rows, cells
    )
    if not problems:
        return []
    if sheet_id is None:
        sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    _write_url_links(service, spreadsheet_id, tab, rgb, problems, positions, sheet_id)
    return problems


def _write_url_links(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    rgb: _RGB,
    problems: Sequence[UrlLinkProblem],
    positions: Mapping[str, int],
    sheet_id: int,
) -> None:
    """Fix ``problems`` in one batch, then read them back.

    Raises :class:`~gdrives.sheets.apply.ReadBackError` for any cell that
    still breaks the rule.
    """
    # The runs go in requests of their own, before the links they would drop.
    requests = [
        link_clear(
            sheet_id,
            RUNS_FIELD,
            (positions[cell.column], positions[cell.column] + 1),
            (cell.row - 1, cell.row),
        )
        for cell in problems
        if "runs" in cell.reasons
    ]
    requests.extend(
        _url_link(sheet_id, positions[cell.column], cell.row, cell.text, rgb)
        for cell in problems
    )
    batch_update_spreadsheet(service, spreadsheet_id, requests)
    fixed = {(cell.row, cell.column) for cell in problems}
    left, _ = _url_problems(
        service,
        spreadsheet_id,
        tab,
        rgb,
        list(dict.fromkeys(cell.column for cell in problems)),
        sorted({cell.row for cell in problems}),
        fixed,
    )
    if left:
        # apply imports this module, so its error is imported here.
        from gdrives.sheets.apply import ReadBackError

        raise ReadBackError(
            f"tab {tab!r}: the read-back found URL cells the fix did not link: "
            + "; ".join(
                f"row {cell.row}, column {cell.column!r} ({', '.join(cell.reasons)})"
                for cell in left
            )
        )


def set_url_links(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    color: str,
    columns: Sequence[str] | None = None,
    rows: Sequence[int] | None = None,
) -> list[UrlLinkProblem]:
    """Link each URL cell of ``columns`` and ``rows`` to its text; return the cells.

    Runs :func:`url_link_problems` and writes only to the cells it returns,
    in one ``spreadsheets.batchUpdate``: for each, one ``repeatCell`` sets the
    link, the colour, and ``underline: false`` under a mask of exactly those
    three properties, so the cell keeps its bold, its fill, and its font. A
    cell with text format runs has them cleared by a ``repeatCell`` of its
    own, earlier in the batch, since the API drops a link sent in the request
    that clears the runs. A tab of N such cells, R of them with runs, costs
    one batch of N + R requests.

    **The cell's text is the authority**: a link that points elsewhere is
    pointed at the text, and the text is never changed. The cells are then
    checked again, and :class:`~gdrives.sheets.apply.ReadBackError` is raised
    for any that still break the rule. With no problem, nothing is written.
    Raises ValueError as :func:`url_link_problems` does.
    """
    return _fix_url_links(
        service, spreadsheet_id, tab, color, columns=columns, rows=rows
    )


# -- link styling --

# Why a cell looks like a link and holds none, in the order a result lists them.
_STYLE_REASON_ORDER = ("underline", "color")

#: The reasons a :class:`StyledCell` can give.
LINK_STYLE_REASONS = frozenset(_STYLE_REASON_ORDER)

# The grid read of styled cells: whether the cell holds a link, its text, and
# the look of its text as shown (a link's look has no user-entered property)
# and as set. The older ``foregroundColor`` is read because a theme colour has
# no rgb under ``foregroundColorStyle``.
_STYLE_FIELDS = (
    "sheets(data(rowData(values(hyperlink,formattedValue,textFormatRuns(format(link)),"
    "userEnteredFormat(textFormat(underline,foregroundColorStyle,foregroundColor)),"
    "effectiveFormat(textFormat(underline,foregroundColorStyle,foregroundColor))))))"
)


@dataclass(frozen=True)
class StyledCell:
    """A cell that looks like a link and holds none, and why.

    ``row`` is the 1-based spreadsheet row, ``column`` the header name, and
    ``text`` the cell's displayed text. ``reasons`` are names from
    :data:`LINK_STYLE_REASONS`, in the order ``underline``, ``color``.
    ``resettable`` says whether every reason is a property the cell sets
    itself, which ``clear_link_format(..., style=True)`` resets; when False,
    some of the look comes from conditional formatting or the theme, and a
    reset leaves it.
    """

    row: int
    column: str
    text: str
    reasons: tuple[str, ...]
    resettable: bool


def _shown_rgb(text_format: Mapping[str, Any]) -> _RGB:
    """The channels of the text colour a format shows.

    A theme colour has no rgb under ``foregroundColorStyle``, so it is read
    from the resolved ``foregroundColor`` beside it.
    """
    style = text_format.get("foregroundColorStyle", {})
    if "themeColor" in style and "rgbColor" not in style:
        return _channels(text_format.get("foregroundColor", {}))
    return _channels(style.get("rgbColor", {}))


def _style_reasons(
    cell: Mapping[str, Any], rgbs: Collection[_RGB]
) -> tuple[tuple[str, ...], bool]:
    """Why a cell with text and no link looks like a link, and if a reset undoes it."""
    shown = cell.get("effectiveFormat", {}).get("textFormat", {})
    entered = cell.get("userEnteredFormat", {}).get("textFormat", {})
    found = {
        "underline": bool(shown.get("underline")),
        "color": _shown_rgb(shown) in rgbs,
    }
    own = {
        "underline": bool(entered.get("underline")),
        "color": "foregroundColorStyle" in entered or "foregroundColor" in entered,
    }
    reasons = tuple(reason for reason in _STYLE_REASON_ORDER if found[reason])
    return reasons, all(own[reason] for reason in reasons)


def styled_cells(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    columns: Sequence[str] | None = None,
    rows: Sequence[int] | None = None,
    header: Sequence[str] | None = None,
    colors: Sequence[str] = (LINK_COLOR,),
) -> list[StyledCell]:
    """Every cell of ``columns`` and ``rows`` that looks like a link and holds none.

    A cell holds no link when it has no link on the whole cell and none in its
    text format runs. It looks like one when its text is underlined
    (``underline``), or shown in one of ``colors`` (``color``): each
    ``#rrggbb``, compared to the nearest of 255 steps a channel. The default
    is the colour the API gives a link, :data:`LINK_COLOR`; a caller whose
    links are in another colour, as ``link_urls`` sets, names it, and a
    caller that wants underlines alone passes none. Either reason is enough,
    so a cell underlined in black is found. Returns the cells in row then
    column order.

    The look is read from the effective format, since a link's own look has
    no user-entered property and conditional formatting or a theme can
    produce it; a theme colour is read from its resolved colour.
    ``StyledCell.resettable`` says whether the cell sets the look itself. A
    cell with no text is never returned: nothing of it shows. Styling inside
    text format runs is not read, only a link there counts as a link.

    ``columns`` defaults to every named column of the header, the header row
    a row like any other, and ``rows`` (1-based spreadsheet rows) to every
    row; ``rows`` filters the result of one grid read
    (:func:`~gdrives.sheets.values.pull_grid`) over the columns from the first
    wanted to the last. ``header`` saves the read of row 1.

    Raises ValueError, before any request, for a colour that is not
    ``#rrggbb`` or a row below 1, and before the grid read for a blank or
    repeated column name, or a name the header lacks or repeats.
    """
    rgbs = {_rgb(color) for color in colors}
    _check_rows(rows)
    positions, _ = _wanted(service, spreadsheet_id, tab, columns, header)
    if not positions:
        return []
    first, last = min(positions.values()), max(positions.values())
    names = {index: name for name, index in positions.items()}
    wanted = None if rows is None else set(rows)
    span = f"{a1_quote(tab)}!{column_letter(first)}:{column_letter(last)}"
    data = pull_grid(service, spreadsheet_id, span, _STYLE_FIELDS)
    found: list[StyledCell] = []
    for row, held in enumerate(data.get("rowData", []), start=1):
        if wanted is not None and row not in wanted:
            continue
        for index, cell in enumerate(held.get("values", []), start=first):
            text = cell.get("formattedValue", "")
            if index not in names or not text or _holds_link(cell):
                continue
            reasons, resettable = _style_reasons(cell, rgbs)
            if reasons:
                found.append(StyledCell(row, names[index], text, reasons, resettable))
    return found


def _holds_link(cell: Mapping[str, Any]) -> bool:
    """Whether a cell of a grid read has a link, on the whole cell or in its runs."""
    return "hyperlink" in cell or any(
        "uri" in run.get("format", {}).get("link", {})
        for run in cell.get(RUNS_FIELD, [])
    )


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
