"""Read a whole tab as header-named, keyed records.

A tab is read in one request, with every cell turned into its canonical string
(:func:`gdrives.sheets.cells.to_cell`), and columns are found by header name,
never by position, so a column moved on the sheet is still read correctly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote
from gdrives.sheets.cells import index_rows, to_cell
from gdrives.sheets.values import FORMATTED_STRING, UNFORMATTED_VALUE, pull_values


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
    """

    tab: str
    header: list[str]
    columns: list[str]
    key: tuple[str, ...]
    rows: list[dict[str, str]]
    row_numbers: dict[tuple[str, ...], int]
    extra_columns: list[str]
    wide_rows: list[int]


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


def read_tab(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    columns: Sequence[str] | None,
    key: Sequence[str] = (),
) -> Table:
    """Read ``tab`` in one request and return its ``columns`` as keyed records.

    ``columns=None`` reads every named header column. Cells are read unformatted
    (numbers and booleans as values, dates as the sheet displays them) and
    turned into canonical strings, so a number typed on the sheet reads as
    ``"3"`` whatever its display format.

    Raises ValueError, before any row is looked at, when the tab has no header
    row, a header name repeats, or a wanted column is missing. Short rows are
    padded (the API truncates each row at its last non-empty cell), and rows
    that are entirely blank are skipped. With a ``key``, a row that has data
    but a blank key cell, or that repeats another row's key, raises with every
    such spreadsheet row listed (see :func:`~gdrives.sheets.cells.index_rows`).
    """
    _check_request(tab, columns, key)
    grid = pull_values(
        service,
        spreadsheet_id,
        a1_quote(tab),
        render=UNFORMATTED_VALUE,
        date_time_render=FORMATTED_STRING,
    )
    first: list[Any] = grid[0] if grid else []
    header = [to_cell(cell).strip() for cell in first]
    if not any(header):
        raise ValueError(f"tab {tab!r} has no header row")
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
        rows.append({column: cells[index] for column, index in positions.items()})
        numbers.append(number)

    row_numbers = (
        index_rows(rows, key, side=f"tab {tab!r}", numbers=numbers) if key else {}
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
    )
