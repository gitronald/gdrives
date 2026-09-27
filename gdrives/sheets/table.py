"""Read a whole tab as header-named, keyed records.

A tab is read in one request, with every cell turned into its canonical string
(:func:`gdrives.sheets.cells.to_cell`), and columns are found by header name,
never by position, so a column moved on the sheet is still read correctly.

A date cell reads as the text its number format shows, which depends on the
format and the locale. A column **declared** ``date`` or ``datetime`` is read
a second time, as serial numbers (:func:`pull_serials`), and each of its date
cells becomes ISO 8601 whatever the sheet displays. Nothing is converted by
guess: a number in an undeclared column cannot be told from a date's serial.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.cells import (
    SERIAL_TYPES,
    ColumnType,
    _declared,
    _header_row,
    check_blank_keys,
    index_rows,
    serial_to_cell,
    to_cell,
)
from gdrives.sheets.values import (
    FORMATTED_STRING,
    SERIAL_NUMBER,
    UNFORMATTED_VALUE,
    pull_many,
    pull_values,
)

#: One grid per column, as a serial read of that column returned it.
Serials = Mapping[str, Sequence[Sequence[Any]]]


class EmptyTabError(ValueError):
    """A tab has no header row: it is empty, or its first row is blank."""


@dataclass(frozen=True)
class Table:
    """One tab read as records.

    ``header`` is the tab's header row as on the sheet (cells stripped), and
    ``columns`` the columns read, in the order asked. Each record in ``rows``
    holds exactly ``columns``, in that order, as canonical strings.
    ``row_numbers`` maps each row's normalized key to its 1-based spreadsheet
    row, for writes (empty when no key was given). ``extra_columns`` are named
    header columns that were not asked for: never read into records, and never
    written. ``wide_rows`` lists the spreadsheet rows holding cells past the
    header's last column; those cells belong to no column and are not read.
    ``last_row`` is the last spreadsheet row holding a value in any column,
    columns outside ``columns`` and past the header included (1 when the tab
    holds only its header): new rows go after it. ``types`` holds the declared
    type of each column read that has one, by name, and ``blank_keys`` the
    setting the rows were indexed with, so that a later read of the same tab
    reads it the same way.
    """

    tab: str
    header: list[str]
    columns: list[str]
    key: tuple[str, ...]
    rows: list[dict[str, str]]
    row_numbers: dict[tuple[str, ...], int]
    extra_columns: list[str]
    wide_rows: list[int]
    last_row: int
    types: dict[str, str] = field(default_factory=dict)
    blank_keys: str = "refuse"


def _check_request(tab: str, columns: Sequence[str] | None, key: Sequence[str]) -> None:
    """Refuse a malformed request before it costs an API call."""
    if columns is not None:
        if "" in columns:
            raise ValueError(f"tab {tab!r}: blank column name in {list(columns)}")
        repeated = sorted({c for c in columns if list(columns).count(c) > 1})
        if repeated:
            raise ValueError(f"tab {tab!r}: column(s) {repeated} asked for twice")
        outside = [k for k in key if k not in columns]
        if outside:
            raise ValueError(f"tab {tab!r}: key column(s) {outside} not in columns")


def pull_serials(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    grid: Sequence[Sequence[Any]],
    types: Mapping[str, ColumnType],
) -> dict[str, list[list[Any]]]:
    """Read the declared date columns of ``tab`` again, as serial numbers.

    ``grid`` is the tab as already read, whose header row places the columns.
    Every column of it that ``types`` declares ``date`` or ``datetime`` is
    read whole (``Tab!C:C``) in one ``values.batchGet`` with the
    ``SERIAL_NUMBER`` render, and returned by name for :func:`parse_tab`'s
    ``serials``. The whole tab is never read that way: it would turn the
    dates of undeclared columns into numbers. With no such column on the tab
    no request is made.
    """
    header = _header_row(grid)
    dated = [
        column
        for column, name in _declared(types).items()
        if name in SERIAL_TYPES and column in header
    ]
    quoted = a1_quote(tab)
    letters = [column_letter(header.index(column)) for column in dated]
    grids = pull_many(
        service,
        spreadsheet_id,
        [f"{quoted}!{letter}:{letter}" for letter in letters],
        render=UNFORMATTED_VALUE,
        date_time_render=SERIAL_NUMBER,
    )
    return dict(zip(dated, grids, strict=True))


def read_tab(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    columns: Sequence[str] | None,
    key: Sequence[str] = (),
    *,
    types: Mapping[str, ColumnType] | None = None,
    blank_keys: str = "refuse",
) -> Table:
    """Read ``tab`` and return its ``columns`` as keyed records.

    ``columns=None`` reads every named header column. Cells are read unformatted
    (numbers and booleans as values, dates as the sheet displays them) and
    turned into canonical strings, so a number typed on the sheet reads as
    ``"3"`` whatever its display format. The grid is parsed by
    :func:`parse_tab`, which says what is refused.

    ``types`` declares column types by name or class. The tab is read in one
    request, and in two when it has a column read that is declared ``date``
    or ``datetime``: those columns are read again as serial numbers
    (:func:`pull_serials`), and their date cells arrive as ISO 8601. The two
    reads are not one moment, so rows inserted between them put a date
    against the wrong row; :func:`~gdrives.sheets.apply.apply_plan` reads the
    tab again before it writes.

    ``blank_keys`` is as for :func:`~gdrives.sheets.cells.index_rows`.
    """
    _check_request(tab, columns, key)
    check_blank_keys(blank_keys)
    declared = {
        column: name
        for column, name in _declared(types).items()
        if columns is None or column in columns
    }
    grid = pull_values(
        service,
        spreadsheet_id,
        a1_quote(tab),
        render=UNFORMATTED_VALUE,
        date_time_render=FORMATTED_STRING,
    )
    serials = pull_serials(service, spreadsheet_id, tab, grid, declared)
    return parse_tab(
        tab,
        grid,
        columns,
        key,
        types=declared,
        serials=serials,
        blank_keys=blank_keys,
    )


def _dated(text: str, serials: Sequence[Sequence[Any]], number: int, type_: str) -> str:
    """The cell of spreadsheet row ``number`` in a declared date column.

    Its serial as ISO 8601 when the serial read gave a number for it that
    fits the type, and ``text``, the first read's value, otherwise.
    """
    cells: Sequence[Any] = serials[number - 1] if number <= len(serials) else []
    value = cells[0] if cells else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return text
    try:
        return serial_to_cell(value, type_)
    except ValueError:
        return text


def parse_tab(
    tab: str,
    grid: Sequence[Sequence[Any]],
    columns: Sequence[str] | None,
    key: Sequence[str] = (),
    *,
    types: Mapping[str, ColumnType] | None = None,
    serials: Serials | None = None,
    blank_keys: str = "refuse",
) -> Table:
    """Parse ``grid``, a whole tab's rows as read, into ``columns`` as keyed records.

    The parsing half of :func:`read_tab`, for a grid already read (one
    :func:`~gdrives.sheets.values.pull_many` request can fetch several tabs).
    ``grid`` holds the values an unformatted read returns.

    ``types`` declares column types, and ``serials`` holds the serial read of
    each column declared ``date`` or ``datetime``, as :func:`pull_serials`
    returns it. A cell of such a column becomes ISO 8601 when the serial read
    gave a number for it, and keeps the value of ``grid`` otherwise: text, a
    boolean, a row ``serials`` does not reach, or a serial that does not fit
    the type (a time of day in a ``date`` column), which a schema check then
    reports. So a column holding both date cells and ISO text reads as ISO
    8601 throughout. A plain number in such a column is converted like any
    other, since the declaration is what says the column holds dates.

    Raises :class:`EmptyTabError` when the tab has no header row, and
    ValueError, before any row is looked at, when a header name repeats or a
    wanted column is missing. Short rows are padded (the API truncates each
    row at its last non-empty cell), and rows that are entirely blank are
    skipped. With a ``key``, a row that has data but a blank key cell, or that
    repeats another row's key, raises with every such spreadsheet row listed
    (see :func:`~gdrives.sheets.cells.index_rows`, which ``blank_keys`` is
    passed to).
    """
    _check_request(tab, columns, key)
    check_blank_keys(blank_keys)
    declared = _declared(types)
    header = _header_row(grid)
    if not any(header):
        raise EmptyTabError(f"tab {tab!r} has no header row")
    repeated = sorted({name for name in header if name and header.count(name) > 1})
    if repeated:
        raise ValueError(f"tab {tab!r}: header repeats {repeated}")
    wanted = list(columns) if columns is not None else [h for h in header if h]
    # The key is checked too: with columns=None it was not checked up front.
    missing = [c for c in dict.fromkeys([*wanted, *key]) if c not in header]
    if missing:
        raise ValueError(f"tab {tab!r} has no column(s) {missing}; header: {header}")

    positions = {column: header.index(column) for column in wanted}
    extra = [name for name in header if name and name not in positions]
    read_types = {c: name for c, name in declared.items() if c in positions}
    dated = {
        column: (serials[column], name)
        for column, name in read_types.items()
        if name in SERIAL_TYPES and serials is not None and column in serials
    }
    width = len(header)
    rows: list[dict[str, str]] = []
    numbers: list[int] = []
    wide: list[int] = []
    for number, raw in enumerate(grid[1:], start=2):  # row 1 is the header
        cells = [to_cell(cell) for cell in raw]
        if all(cell == "" for cell in cells):
            continue
        if len(cells) > width:
            wide.append(number)
        cells += [""] * (width - len(cells))
        row = {column: cells[index] for column, index in positions.items()}
        for column, (serial, name) in dated.items():
            row[column] = _dated(row[column], serial, number, name)
        rows.append(row)
        numbers.append(number)

    row_numbers = (
        index_rows(
            rows, key, side=f"tab {tab!r}", numbers=numbers, blank_keys=blank_keys
        )
        if key
        else {}
    )
    return Table(
        tab=tab,
        header=header,
        columns=wanted,
        key=tuple(key),
        rows=rows,
        row_numbers=row_numbers,
        extra_columns=extra,
        wide_rows=wide,
        # Only rows blank in every column are skipped, so the last row read is
        # the last one holding anything.
        last_row=numbers[-1] if numbers else 1,
        types=read_types,
        blank_keys=blank_keys,
    )
