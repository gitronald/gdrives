"""Typed writes: declared columns written as numbers, booleans, and dates.

A sync or a push writes every cell as a literal string unless its tab sets
``typed_writes``. With it, each cell of a column declared ``int``, ``float``,
``bool``, ``date``, or ``datetime`` is written as a value of that type
(:func:`~gdrives.sheets.cells.cell_data`), by ``updateCells`` in a
``spreadsheets.batchUpdate``, so the people who use the sheet can sort it,
filter it, and compute over it. A key column is written as text whatever its
type: keys are matched by their text, and ``007`` would come back ``7``.

A date is sent as its serial number, which shows as a number unless the cell
has a date format. A date cell written that has no date or time format is
given one (:data:`~gdrives.sheets.cells.DATE_FORMATS`); one that has its own
keeps it. Finding out costs one grid read of the date columns
(:func:`dated_cells`).
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.cells import DATE_FORMATS, ColumnType, _declared, cell_data
from gdrives.sheets.structure import _adjacent
from gdrives.sheets.values import pull_grid

# The field of a cell's value, the mask of a value write.
_VALUE_FIELD = "userEnteredValue"

#: The field of a cell's number format, where a date format is.
NUMBER_FORMAT_FIELD = "userEnteredFormat.numberFormat"

# The mask of a grid read of number format types.
_FORMAT_READ = "sheets(data(rowData(values(userEnteredFormat(numberFormat(type))))))"

# The number format types that show a serial as a date or a time.
_DATE_KINDS = frozenset({"DATE", "TIME", "DATE_TIME"})


def typed_columns(
    types: Mapping[str, ColumnType] | None, key: Collection[str] = ()
) -> dict[str, str]:
    """The declared columns a typed write sends as values, by name: all but ``str``.

    The ``key`` columns are left out: a key is always written as text.
    """
    return {
        column: name
        for column, name in _declared(types).items()
        if name != "str" and column not in key
    }


def _typed_problems(
    cells: Iterable[tuple[str, str, str]], types: Mapping[str, str]
) -> list[str]:
    """Why each ``(where, column, text)`` cannot be written as its column's type.

    ``where`` names the cell in a message. A column ``types`` does not name
    is text, and always can be. Used to refuse a write before any request.
    """
    problems: list[str] = []
    for where, column, text in cells:
        if column in types:
            try:
                cell_data(text, types[column])
            except ValueError as e:
                problems.append(f"{where}, column {column!r}: {e}")
    return problems


def dated_cells(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    header: Sequence[str],
    columns: Collection[str],
) -> set[tuple[int, str]]:
    """The cells of ``columns`` with a date or time number format, as ``(row, column)``.

    ``row`` is the 1-based spreadsheet row, and ``header`` the tab's header,
    which places the columns. One grid read (:func:`~gdrives.sheets.values.pull_grid`)
    of the columns from the first of them to the last, under a mask of the
    number format's type alone; no request when ``columns`` is empty.
    """
    names = {header.index(column): column for column in columns}
    if not names:
        return set()
    first, last = min(names), max(names)
    span = f"{a1_quote(tab)}!{column_letter(first)}:{column_letter(last)}"
    data = pull_grid(service, spreadsheet_id, span, _FORMAT_READ)
    found: set[tuple[int, str]] = set()
    for row, held in enumerate(data.get("rowData", []), start=1):
        for index, cell in enumerate(held.get("values", []), start=first):
            number = cell.get("userEnteredFormat", {}).get("numberFormat", {})
            if index in names and number.get("type") in _DATE_KINDS:
                found.add((row, names[index]))
    return found


def _value_request(
    sheet_id: int,
    row: int,
    column: int,
    rows: Sequence[Sequence[dict[str, Any]]],
    fields: str,
) -> dict[str, Any]:
    """The ``updateCells`` that writes ``rows`` of ``CellData`` from a 0-based cell."""
    return {
        "updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": column},
            "rows": [{"values": list(values)} for values in rows],
            "fields": fields,
        }
    }


def format_requests(
    sheet_id: int, cells: Iterable[tuple[int, int, str]]
) -> list[dict[str, Any]]:
    """The requests that give date cells a date format (:data:`DATE_FORMATS`).

    ``cells`` are ``(row, column, type)``, 0-based, ``type`` being ``date``
    or ``datetime``. Adjacent rows of one column and type share one
    ``repeatCell``, whose mask names the number format alone, so the rest of
    each cell's format is left as it is.
    """
    by_column: dict[tuple[int, str], list[int]] = {}
    for row, column, type_ in cells:
        by_column.setdefault((column, type_), []).append(row)
    return [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": first,
                    "endRowIndex": past,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "cell": {
                    "userEnteredFormat": {"numberFormat": dict(DATE_FORMATS[type_])}
                },
                "fields": NUMBER_FORMAT_FIELD,
            }
        }
        for (column, type_), rows in sorted(by_column.items())
        for first, past in _adjacent(rows)
    ]
