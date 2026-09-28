"""Put a keyed tab's rows in a given order by moving whole rows.

:func:`reorder_rows` takes the order a tab's rows should be in, as keys the
caller computed, and moves rows until the sheet matches. It moves rows with
``moveDimension``, never rewrites values, so each row keeps its formatting,
its notes, its validation, and its cells in columns that were never read.

The fewest single-row moves are sent. The rows that already stand in the
right order relative to each other (a longest increasing subsequence of the
current rows, by their place in the target) stay where they are, and every
other row is moved once, to directly after the row that precedes it in the
target. Entirely blank rows inside the tab keep their positions, so a keyed
row that must cross one is always among the rows moved.

The moves are computed on a list, and each is sent in ``moveDimension``'s
coordinates: its ``destinationIndex`` is counted before the moved row is
taken out, so a move down names one past the position the row ends up at.
All of them go in one ``spreadsheets.batchUpdate``, which the API applies in
order and all or nothing.
"""

from bisect import bisect_left
from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from itertools import accumulate
from typing import Any, NamedTuple

from gdrives.files import Service
from gdrives.sheets.apply import ReadBackError, _reread
from gdrives.sheets.cells import normalize_key, row_key
from gdrives.sheets.table import Table, read_tab
from gdrives.sheets.values import batch_update_spreadsheet, tab_grid

_Key = tuple[str, ...]

#: A key in an order: one cell string per key column, or a plain string for
#: a one-column key.
_OrderKey = str | Sequence[str]


@dataclass(frozen=True)
class ReorderResult:
    """What :func:`reorder_rows` found, and did.

    ``moves`` is the number of ``moveDimension`` requests the order takes,
    and ``moved`` the keys of the rows they move, one per request, in the
    order sent; the other rows keep their places relative to each other.
    ``unchanged`` is True when the tab is already in order, and ``applied``
    when the moves were written.
    """

    moves: int
    moved: list[_Key]
    unchanged: bool
    applied: bool


@dataclass(frozen=True)
class _Blank:
    """The identity of an entirely blank row, at its 0-based place in the block."""

    at: int


def _increasing(values: Sequence[int]) -> set[int]:
    """The indices of one longest strictly increasing subsequence of ``values``."""
    tails: list[int] = []  # the smallest last value of a run of each length
    ends: list[int] = []  # the index that value is at
    before: list[int | None] = []
    for index, value in enumerate(values):
        length = bisect_left(tails, value)
        if length == len(tails):
            tails.append(value)
            ends.append(index)
        else:
            tails[length] = value
            ends[length] = index
        before.append(ends[length - 1] if length else None)
    kept: set[int] = set()
    at = ends[-1] if ends else None
    while at is not None:
        kept.add(at)
        at = before[at]
    return kept


class _Move(NamedTuple):
    """One single-row move: the row, and where it goes, as ``moveDimension`` counts.

    ``start`` is the row's 0-based index when the move is made, and
    ``destination`` the index it goes before, counted before the row is
    taken out: a move down names one past the index the row ends up at.
    """

    row: Hashable
    start: int
    destination: int


def _blanks_before(rows: Sequence[Hashable]) -> list[int]:
    """For each index of ``rows``, how many :class:`_Blank` rows come before it."""
    return [0, *accumulate(isinstance(row, _Blank) for row in rows)][:-1]


def _plan_moves(current: Sequence[Hashable], target: Sequence[Hashable]) -> list[_Move]:
    """The single-row moves that turn ``current`` into ``target``, in order.

    Both hold the same distinct identities. A :class:`_Blank` must be at the
    same index in both, and is never moved. No move leaves its row where it
    was: a ``destination`` is never ``start`` (which the API refuses) or
    ``start + 1``.

    The rows that are not moved keep their order, and the blank rows are not
    moved, so a row that must cross a blank row is moved. Of the rest, a
    longest run whose indices in ``target`` increase stays, and each other
    row is moved once, in ``target`` order, to directly after its
    predecessor in ``target``, which has reached its place by then.
    """
    place = {row: index for index, row in enumerate(target)}
    now, then = _blanks_before(current), _blanks_before(target)
    staying = [
        row
        for index, row in enumerate(current)
        if not isinstance(row, _Blank) and now[index] == then[place[row]]
    ]
    kept = {staying[i] for i in _increasing([place[row] for row in staying])}
    rows = list(current)
    moves: list[_Move] = []
    for index, row in enumerate(target):
        if row in kept or isinstance(row, _Blank):
            continue
        start = rows.index(row)
        del rows[start]
        to = rows.index(target[index - 1]) + 1 if index else 0
        rows.insert(to, row)
        moves.append(_Move(row, start, to + 1 if to > start else to))
    return moves


def _as_key(given: _OrderKey) -> _Key:
    """An order's key as a normalized tuple."""
    parts = (given,) if isinstance(given, str) else tuple(given)
    return tuple(normalize_key(part) for part in parts)


def _check_order(table: Table, order: Sequence[_OrderKey]) -> list[_Key]:
    """The order's keys, normalized, refusing an order that does not fit ``table``."""
    keys = [_as_key(given) for given in order]
    width = len(table.key)
    wrong = [key for key in keys if len(key) != width]
    counts = Counter(key for key in keys if len(key) == width)
    repeated = [key for key, count in counts.items() if count > 1]
    unknown = [key for key in counts if key not in table.row_numbers]
    unnamed = [key for key in table.row_numbers if key not in counts]
    problems: list[str] = []
    for label, found in (
        ("rows the order does not name", unnamed),
        ("keys the tab lacks", unknown),
        ("keys the order repeats", repeated),
        (f"keys that are not {width} cell(s) long", wrong),
    ):
        if found:
            problems.append(f"{label}: {found}")
    if problems:
        raise ValueError(
            f"tab {table.tab!r}: the order does not fit the tab: " + "; ".join(problems)
        )
    return keys


def _identities(table: Table, order: Sequence[_Key]) -> tuple[list[Any], list[Any]]:
    """The block's rows as they are, and as ``order`` puts them.

    The block is spreadsheet rows 2 to ``table.last_row``. A blank row is a
    :class:`_Blank` at its place in both; the keyed rows fill the other
    places, in the tab's order and in ``order``.
    """
    by_row = {number: key for key, number in table.row_numbers.items()}
    current: list[Any] = [
        by_row.get(number, _Blank(number - 2))
        for number in range(2, table.last_row + 1)
    ]
    keys = iter(order)
    target = [row if isinstance(row, _Blank) else next(keys) for row in current]
    return current, target


def _read_back(
    service: Service, spreadsheet_id: str, table: Table, target: Sequence[Any]
) -> None:
    """Check that every row holds what it held, at its place in ``target``."""
    failed = f"tab {table.tab!r}: the read-back does not match the moves"
    try:
        after = read_tab(
            service,
            spreadsheet_id,
            table.tab,
            table.columns,
            table.key,
            blank_keys=table.blank_keys,
        )
    except ValueError as e:
        raise ReadBackError(f"{failed}: {e}") from e
    rows = {row_key(row, table.key): row for row in after.rows}
    problems: list[str] = []
    missing: list[_Key] = []
    for row in table.rows:
        key = row_key(row, table.key)
        now = rows.get(key)
        if now is None:
            missing.append(key)
        elif now != row:
            problems.append(f"row {key} held {row}, now holds {now}")
    for index, row in enumerate(target):
        got = None if isinstance(row, _Blank) else after.row_numbers.get(row)
        if got is not None and got != index + 2:
            problems.append(f"row {row} is in row {got}, not {index + 2}")
    if missing:
        problems.insert(0, f"rows {missing} not found")
    if problems:
        raise ReadBackError(f"{failed}: " + "; ".join(problems))


def reorder_rows(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    key: Sequence[str],
    order: Sequence[_OrderKey],
    *,
    apply: bool = False,
    blank_keys: str = "refuse",
) -> ReorderResult:
    """Put the rows of ``tab`` in ``order``, by moving whole rows (with ``apply``).

    ``key`` names the key columns, and ``order`` lists every row's key in the
    order wanted: a sequence of cell strings per key, one per key column, or
    a plain string for a one-column key. Keys are compared normalized
    (:func:`~gdrives.sheets.cells.normalize_key`).

    The tab is read whole (:func:`~gdrives.sheets.table.read_tab`, with
    ``key`` and ``blank_keys``), which refuses a blank or repeated key. The
    rows reordered are spreadsheet rows 2 to the last row holding anything.
    Entirely blank rows among them keep their positions, and the keyed rows
    fill the other positions in ``order``. Raises ValueError, listing every
    problem, when ``order`` leaves out a row of the tab, names a key the tab
    lacks, repeats a key, or holds a key of the wrong length.

    Previews by default: reads, plans, and writes nothing. With ``apply``,
    the tab is read again first and :class:`~gdrives.sheets.apply.SheetChangedError`
    is raised, with nothing written, when its header, rows, or row numbers
    differ from the first read. The moves then go in one
    ``spreadsheets.batchUpdate``, and the tab is read back:
    :class:`~gdrives.sheets.apply.ReadBackError` is raised when a row no
    longer holds the cells it held, or is not at its place in the order. A
    tab already in order gets no write, and no second read.

    ``moveDimension`` moves a row with its formatting and every column, and
    adjusts formulas, conditional format ranges, and named ranges as a drag
    on the sheet does. A filter view or a sort applied on the sheet is not
    applied again.
    """
    if not key:
        raise ValueError(f"tab {tab!r}: reordering rows needs key columns")
    table = read_tab(service, spreadsheet_id, tab, None, key, blank_keys=blank_keys)
    keys = _check_order(table, order)
    current, target = _identities(table, keys)
    moves = _plan_moves(current, target)
    # The moves are made in the order's order, one per row moved.
    moving = {move.row for move in moves}
    moved = [found for found in keys if found in moving]
    if not apply or not moves:
        return ReorderResult(
            moves=len(moves), moved=moved, unchanged=not moves, applied=False
        )

    grid = tab_grid(service, spreadsheet_id, tab)
    _reread(service, spreadsheet_id, table, None)
    requests = [
        {
            "moveDimension": {
                "source": {
                    "sheetId": grid.sheet_id,
                    "dimension": "ROWS",
                    # The block starts at the second row, grid row 1.
                    "startIndex": move.start + 1,
                    "endIndex": move.start + 2,
                },
                "destinationIndex": move.destination + 1,
            }
        }
        for move in moves
    ]
    batch_update_spreadsheet(service, spreadsheet_id, requests)
    _read_back(service, spreadsheet_id, table, target)
    return ReorderResult(moves=len(moves), moved=moved, unchanged=False, applied=True)
