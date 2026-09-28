"""Turn the text a typed column already holds into values: :func:`retype_columns`.

A tab that turns on ``typed_writes`` holds text in its typed columns until
each cell is pushed again, since a sync rewrites only the cells that change.
:func:`retype_columns` rewrites them all at once: every cell of a declared
column that holds text parsing as the column's type becomes a value of it,
and a date cell with no date format gets one, as a typed write does. It
previews by default and leaves every other cell alone.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote
from gdrives.sheets.apply import ReadBackError, SheetChangedError
from gdrives.sheets.cells import (
    SERIAL_TYPES,
    ColumnSchema,
    _header_row,
    cell_data,
    normalize_cell,
    serial_to_cell,
    to_cell,
)
from gdrives.sheets.table import EmptyTabError
from gdrives.sheets.typed import (
    _VALUE_FIELD,
    _value_request,
    dated_cells,
    format_requests,
    typed_columns,
)
from gdrives.sheets.values import (
    SERIAL_NUMBER,
    UNFORMATTED_VALUE,
    batch_update_spreadsheet,
    pull_values,
    tab_grid,
)


@dataclass(frozen=True)
class RetypeCell:
    """One cell of a typed column that holds text.

    ``row`` is the 1-based spreadsheet row and ``text`` what the cell holds.
    ``problem`` says why the text is not a value of the column's type, and is
    empty for a cell that is rewritten.
    """

    row: int
    column: str
    text: str
    problem: str = ""


@dataclass(frozen=True)
class RetypeReport:
    """What :func:`retype_columns` found, and whether it wrote.

    ``changes`` are the cells rewritten as values (with ``applied``) or that
    would be, and ``unparsed`` the cells holding text that is not a value of
    the column's type, which are left as they are. ``types`` is the columns
    looked at, by type.
    """

    tab: str
    types: dict[str, str]
    changes: list[RetypeCell] = field(default_factory=list)
    unparsed: list[RetypeCell] = field(default_factory=list)
    applied: bool = False


def _read(service: Service, spreadsheet_id: str, tab: str) -> list[list[Any]]:
    """The tab's values: text as strings, and every number and date as a number."""
    return pull_values(
        service,
        spreadsheet_id,
        a1_quote(tab),
        render=UNFORMATTED_VALUE,
        date_time_render=SERIAL_NUMBER,
    )


def _cell(grid: list[list[Any]], row: int, index: int) -> Any:
    """The value at 1-based ``row`` and 0-based ``index``, blank past the data."""
    values: list[Any] = grid[row - 1] if row <= len(grid) else []
    return values[index] if index < len(values) else ""


def _as_text(value: Any, type_: str) -> str:
    """A value read back as its canonical string; a date column's from its serial."""
    if type_ in SERIAL_TYPES and isinstance(value, (int, float)):
        try:
            return serial_to_cell(value, type_)
        except ValueError:
            return to_cell(value)
    return to_cell(value)


def retype_columns(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    schema: Mapping[str, ColumnSchema],
    *,
    key: Collection[str] = (),
    apply: bool = False,
) -> RetypeReport:
    """Rewrite as values the cells of ``tab``'s typed columns that hold text.

    The columns are those ``schema`` declares ``int``, ``float``, ``bool``,
    ``date``, or ``datetime``, less the ``key`` columns, which stay text. The
    tab is read in one request, numbers and dates as numbers, so a cell
    holding text is told from one holding a value. Each non-blank text cell
    whose text is a value of its column's type
    (:func:`~gdrives.sheets.cells.cell_data`) is a change, and one whose text
    is not is listed in ``unparsed`` and left alone.

    With ``apply`` and changes to make, the tab is read again and
    :class:`~gdrives.sheets.apply.SheetChangedError` is raised with nothing
    written if it differs. Then every change goes in one
    ``spreadsheets.batchUpdate``, with a date format for each date cell that
    has no date or time format (a grid read of those columns finds out
    first), and the changed cells are read back:
    :class:`~gdrives.sheets.apply.ReadBackError` is raised for one that does
    not hold its value.

    Raises :class:`~gdrives.sheets.table.EmptyTabError` for a tab with no
    header row, and ValueError for a typed column the header lacks.
    """
    declared = {column: spec.type for column, spec in schema.items()}
    types = typed_columns(declared, key)
    grid = _read(service, spreadsheet_id, tab)
    header = _header_row(grid)
    if not any(header):
        raise EmptyTabError(f"tab {tab!r} has no header row")
    missing = [column for column in types if column not in header]
    if missing:
        raise ValueError(f"tab {tab!r} has no column(s) {missing}; header: {header}")
    report = RetypeReport(tab=tab, types=types)
    for row in range(2, len(grid) + 1):
        for column, type_ in types.items():
            value = _cell(grid, row, header.index(column))
            if not isinstance(value, str) or value == "":
                continue
            try:
                cell_data(value, type_)
            except ValueError as e:
                report.unparsed.append(RetypeCell(row, column, value, str(e)))
            else:
                report.changes.append(RetypeCell(row, column, value))
    if not apply or not report.changes:
        return report

    if _read(service, spreadsheet_id, tab) != grid:
        raise SheetChangedError(
            f"tab {tab!r} changed since it was read, so nothing was written"
        )
    dates = {
        cell.column for cell in report.changes if types[cell.column] in SERIAL_TYPES
    }
    dated = dated_cells(service, spreadsheet_id, tab, header, dates)
    sheet_id = tab_grid(service, spreadsheet_id, tab).sheet_id
    requests = [
        _value_request(
            sheet_id,
            cell.row - 1,
            header.index(cell.column),
            [[cell_data(cell.text, types[cell.column])]],
            _VALUE_FIELD,
        )
        for cell in report.changes
    ]
    requests.extend(
        format_requests(
            sheet_id,
            [
                (cell.row - 1, header.index(cell.column), types[cell.column])
                for cell in report.changes
                if cell.column in dates and (cell.row, cell.column) not in dated
            ],
        )
    )
    batch_update_spreadsheet(service, spreadsheet_id, requests)
    report = replace(report, applied=True)

    back = _read(service, spreadsheet_id, tab)
    wrong: list[str] = []
    for cell in report.changes:
        type_ = types[cell.column]
        value = _cell(back, cell.row, header.index(cell.column))
        read = _as_text(value, type_)
        if isinstance(value, str) or normalize_cell(read, type_) != normalize_cell(
            cell.text, type_
        ):
            wrong.append(
                f"row {cell.row}, column {cell.column!r}: wrote {cell.text!r}, "
                f"read {value!r}"
            )
    if wrong:
        raise ReadBackError(
            f"tab {tab!r}: the read-back does not match the retype: " + "; ".join(wrong)
        )
    return report
