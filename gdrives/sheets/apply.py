"""Write a merge plan's sheet side to a tab: guarded, batched, and read back.

:func:`apply_plan` takes the :class:`~gdrives.sheets.table.Table` a
:class:`~gdrives.sheets.merge.MergePlan` was computed from, and writes the
plan's pushed cells and new rows to the tab, in this order:

1. **Re-read the tab** and compare its header, rows, and row numbers with the
   table. Any difference raises :class:`SheetChangedError` with nothing
   written. The Sheets API has no revision precondition, so this re-read is
   the only tie between the plan and the write. The tab is read as the table
   was, with the table's declared types and its setting for blank keys, so a
   date cell of a typed column compares as the ISO 8601 it was read as.
2. **Push changed cells** in one ``values.batchUpdate`` call, with ``RAW``
   input, each cell addressed by its header position and its row number in
   the fresh read.
3. **Write new rows** in one ``spreadsheets.batchUpdate`` call. They are sent
   after the pushes, so the row numbers the pushes use are still true, and
   placed (:func:`insert_point`) by the values the pushes leave behind.
4. **Read the tab back** (:func:`verify`) and check every pushed cell and
   every new row, raising :class:`ReadBackError` on any mismatch.

Every value is written as a literal string: ``USER_ENTERED`` rewrites values
on the way in (``01`` becomes ``1``), which would make the same cell differ,
and push again, on every run.

New rows never go through ``values.append``, which takes the first blank row
it finds as the table's end and so writes over rows below a cleared gap. They
go to explicit rows: after the last row holding anything, or with
``insert_above`` into rows opened above a named row, which take the
formatting of the row above them. Only the projection's columns are written;
other columns of the new rows are left alone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.cells import row_key, to_cell
from gdrives.sheets.merge import MergePlan
from gdrives.sheets.table import Table, read_tab
from gdrives.sheets.values import (
    RAW,
    batch_update_spreadsheet,
    batch_update_values,
    tab_grid,
)

_Key = tuple[str, ...]


class ApplyError(ValueError):
    """A plan could not be applied as computed.

    A ValueError, so a caller that reports ValueError reports this too.
    """


class SheetChangedError(ApplyError):
    """The tab changed after the plan was computed; nothing was written."""


class ReadBackError(ApplyError):
    """The tab, read back after the write, does not hold what was written."""


@dataclass(frozen=True)
class ApplyResult:
    """What :func:`apply_plan` wrote.

    ``pushed`` counts the cells pushed and ``appended`` the rows added.
    ``pushed_rows`` and ``appended_rows`` are the spreadsheet rows they sit in
    after the apply (a pushed row below an ``insert_above`` point has moved
    down by the rows inserted).
    """

    pushed: int
    appended: int
    pushed_rows: list[int]
    appended_rows: list[int]


def _insert_target(
    insert_above: Mapping[str, Any], header: Sequence[str]
) -> tuple[str, set[str]]:
    """The column ``insert_above`` names and its values as canonical strings."""
    if len(insert_above) != 1:
        raise ValueError(
            f"insert_above names one column, not {len(insert_above)}: "
            f"{dict(insert_above)}"
        )
    ((column, given),) = insert_above.items()
    if column == "" or column not in header:
        raise ValueError(
            f"insert_above column {column!r} is not in the header: {list(header)}"
        )
    values = list(given) if isinstance(given, (list, tuple)) else [given]
    if not values:
        raise ValueError(f"insert_above column {column!r} lists no values")
    return column, {to_cell(value) for value in values}


def insert_point(
    table: Table, plan: MergePlan, insert_above: Mapping[str, Any]
) -> int | None:
    """The spreadsheet row ``plan``'s new rows go above, as the tab will be.

    ``insert_above`` is ``{column: value or [values]}``. Returns the 1-based
    row of the first row of ``table`` whose ``column`` will hold one of the
    values once the plan's pushes are in: a row's value is the plan's push to
    that cell when there is one, else the value read. Returns None when no row
    will, and the new rows go after ``table.last_row``. Pushes move no rows,
    so the row numbers of ``table`` hold until the rows are inserted.

    A column outside the projection gets no pushes, so its rows count as
    read. Folds change the local file only, and are not applied. Pure: the
    preview and the apply both call it, so a preview names the row the apply
    inserts at. Raises ValueError for an ``insert_above`` that does not name
    one header column with its values, or a column ``table`` did not read.
    """
    column, values = _insert_target(insert_above, table.header)
    if column not in table.columns:
        raise ValueError(
            f"tab {table.tab!r}: insert_above column {column!r} was not read; "
            f"columns read: {table.columns}"
        )
    pushed = {cell.key: cell.local for cell in plan.pushes if cell.column == column}
    for row in table.rows:
        found = row_key(row, table.key)
        if pushed.get(found, row[column]) in values:
            return table.row_numbers[found]
    return None


def _check_plan(table: Table, plan: MergePlan) -> None:
    """Refuse a plan that does not fit ``table``, before any request is sent."""
    if not table.key:
        raise ValueError(f"tab {table.tab!r}: a plan applies to a table read by key")
    problems: list[str] = []
    seen: set[tuple[_Key, str]] = set()
    for cell in plan.pushes:
        if cell.column not in table.columns or cell.column in table.key:
            problems.append(f"push to column {cell.column!r} is not a data column")
        if cell.key not in table.row_numbers:
            problems.append(f"push to row {cell.key} is not in the table")
        if (cell.key, cell.column) in seen:
            problems.append(f"cell {cell.key}, {cell.column!r} is pushed twice")
        seen.add((cell.key, cell.column))
    added: set[_Key] = set()
    for new in plan.appends:
        if new.key in table.row_numbers or new.key in added:
            problems.append(f"new row {new.key} is already in the tab or the plan")
        added.add(new.key)
        outside = [column for column in new.values if column not in table.columns]
        if outside:
            problems.append(f"new row {new.key} has columns {outside} not read")
    if problems:
        raise ValueError(
            f"tab {table.tab!r}: the plan does not fit the table: "
            + "; ".join(problems)
        )


def _changes(before: Table, after: Table) -> list[str]:
    """How the tab ``after`` shows differs from ``before``, in the projection."""
    if after.header != before.header:
        return [f"header was {before.header}, now {after.header}"]
    was = {row_key(row, before.key): row for row in before.rows}
    now = {
        row_key(row, before.key): {column: row[column] for column in before.columns}
        for row in after.rows
    }
    gone = [found for found in was if found not in now]
    new = [found for found in now if found not in was]
    edited = [found for found in was if found in now and was[found] != now[found]]
    moved = [
        found
        for found in was
        if found in now and before.row_numbers[found] != after.row_numbers[found]
    ]
    changes: list[str] = []
    for label, keys in (
        ("rows removed", gone),
        ("rows added", new),
        ("rows edited", edited),
        ("rows moved", moved),
    ):
        if keys:
            changes.append(f"{label}: {keys}")
    return changes


def _reread(
    service: Service, spreadsheet_id: str, table: Table, also: str | None
) -> Table:
    """Read the tab again, raising :class:`SheetChangedError` if it moved on.

    ``also`` is a column to read beyond the projection (the ``insert_above``
    column); the comparison covers the projection only.
    """
    columns = list(table.columns)
    if also is not None and also not in columns:
        columns.append(also)
    stale = f"tab {table.tab!r} changed since it was read, so nothing was written"
    try:
        fresh = read_tab(
            service,
            spreadsheet_id,
            table.tab,
            columns,
            table.key,
            types=table.types,
            blank_keys=table.blank_keys,
        )
    except ValueError as e:
        raise SheetChangedError(f"{stale}: {e}") from e
    changes = _changes(table, fresh)
    if changes:
        raise SheetChangedError(f"{stale}: " + "; ".join(changes))
    return fresh


def _runs(positions: Sequence[tuple[int, str]]) -> list[tuple[int, list[str]]]:
    """Group ``(header index, column)`` pairs into runs of adjacent columns.

    Each run is written by one ``updateCells``, so a column between two
    projection columns (an extra column, or a blank header gap) is never
    touched.
    """
    runs: list[tuple[int, list[str]]] = []
    for index, column in sorted(positions):
        if runs and runs[-1][0] + len(runs[-1][1]) == index:
            runs[-1][1].append(column)
        else:
            runs.append((index, [column]))
    return runs


def _string_cell(text: str) -> dict[str, Any]:
    """A literal-string ``CellData``; a blank one is left empty."""
    return {"userEnteredValue": {"stringValue": text}} if text else {}


def _row_requests(
    fresh: Table,
    plan: MergePlan,
    sheet_id: int,
    row_count: int,
    at: int,
    insert: bool,
) -> list[dict[str, Any]]:
    """The requests that open rows at 0-based row ``at`` and fill them.

    With ``insert`` the rows are inserted there, shifting the rows below down;
    otherwise they are written in place, after appending grid rows if they
    would not fit.
    """
    count = len(plan.appends)
    requests: list[dict[str, Any]] = []
    if insert:
        requests.append(
            {
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": at,
                        "endIndex": at + count,
                    },
                    # Take the formatting of the row above, which is outside
                    # the block the new rows are kept out of. Directly below
                    # the header that row is the header, so inherit from below.
                    "inheritFromBefore": at > 1,
                }
            }
        )
    elif at + count > row_count:
        requests.append(
            {
                "appendDimension": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "length": at + count - row_count,
                }
            }
        )
    positions = [(fresh.header.index(column), column) for column in fresh.columns]
    for first, columns in _runs(positions):
        requests.append(
            {
                "updateCells": {
                    "start": {
                        "sheetId": sheet_id,
                        "rowIndex": at,
                        "columnIndex": first,
                    },
                    "rows": [
                        {
                            "values": [
                                _string_cell(new.values.get(column, ""))
                                for column in columns
                            ]
                        }
                        for new in plan.appends
                    ],
                    "fields": "userEnteredValue",
                }
            }
        )
    return requests


def apply_plan(
    service: Service,
    spreadsheet_id: str,
    table: Table,
    plan: MergePlan,
    *,
    insert_above: Mapping[str, Any] | None = None,
) -> ApplyResult:
    """Write ``plan``'s pushed cells and new rows to ``table``'s tab, then verify.

    ``table`` is the read the plan was computed from, with a key. The tab is
    read again first, and :class:`SheetChangedError` is raised with nothing
    written when its header, projection rows, or row numbers differ. Then the
    pushes go in one ``values.batchUpdate``, the new rows in one
    ``spreadsheets.batchUpdate``, and :func:`verify` reads the tab back.

    New rows go directly after the last row holding a value in any column,
    growing the grid in the same request when they would not fit. With
    ``insert_above={column: value or [values]}`` they are inserted directly
    above the first row whose ``column`` holds one of the values (compared as
    canonical strings) once the plan's pushes are in, or go after the last
    row when no row does (:func:`insert_point`). ``column`` may be any header
    column, in the projection or not. Inserted rows take the formatting of
    the row above them, or of the row below when that row is the header.

    A plan with nothing to push or add makes no request at all. Raises
    ValueError, before any request, when the plan does not fit ``table`` (a
    push to a row or column the table lacks, a new row whose key the tab
    already has) or ``insert_above`` names a column the header lacks.
    """
    _check_plan(table, plan)
    column = (
        _insert_target(insert_above, table.header)[0]
        if insert_above is not None
        else None
    )
    if not plan.pushes and not plan.appends:
        return ApplyResult(pushed=0, appended=0, pushed_rows=[], appended_rows=[])

    fresh = _reread(service, spreadsheet_id, table, column)
    count = len(plan.appends)
    requests: list[dict[str, Any]] = []
    at = fresh.last_row  # 0-based: the row after the last one holding anything
    inserted = False
    if count:
        if insert_above is not None:
            above = insert_point(fresh, plan, insert_above)
            if above is not None:
                at, inserted = above - 1, True
        # Read before any write, so a failed read leaves the tab untouched.
        grid = tab_grid(service, spreadsheet_id, fresh.tab)
        requests = _row_requests(
            fresh, plan, grid.sheet_id, grid.row_count, at, inserted
        )

    quoted = a1_quote(fresh.tab)
    data = [
        (
            f"{quoted}!{column_letter(fresh.header.index(cell.column))}"
            f"{fresh.row_numbers[cell.key]}",
            [[cell.local]],
        )
        for cell in plan.pushes
    ]
    # Pushes first: the row numbers they use are only true before any insert.
    if data:
        batch_update_values(service, spreadsheet_id, data, input_option=RAW)
    if requests:
        batch_update_spreadsheet(service, spreadsheet_id, requests)
    verify(service, spreadsheet_id, table, plan)

    shift = count if inserted else 0
    pushed_rows = sorted({fresh.row_numbers[cell.key] for cell in plan.pushes})
    return ApplyResult(
        pushed=len(plan.pushes),
        appended=count,
        pushed_rows=[row + shift if row > at else row for row in pushed_rows],
        appended_rows=list(range(at + 1, at + 1 + count)),
    )


def verify(
    service: Service, spreadsheet_id: str, table: Table, plan: MergePlan
) -> None:
    """Read ``table``'s tab back and check that ``plan``'s sheet writes landed.

    Rows are found by key, not by number, so rows inserted above them do not
    matter. The tab is read with ``table``'s declared types, as it was read
    for the plan. Every pushed cell must hold its ``local`` value, and every new row
    must exist with its projection cells as sent. Raises
    :class:`ReadBackError` listing every mismatch at once, or when the tab no
    longer reads cleanly (a key now blank or repeated, a column gone).
    """
    failed = f"tab {table.tab!r}: the read-back does not match the write"
    try:
        after = read_tab(
            service,
            spreadsheet_id,
            table.tab,
            table.columns,
            table.key,
            types=table.types,
            blank_keys=table.blank_keys,
        )
    except ValueError as e:
        raise ReadBackError(f"{failed}: {e}") from e
    rows = {row_key(row, table.key): row for row in after.rows}
    problems: list[str] = []
    missing: list[_Key] = []
    for cell in plan.pushes:
        row = rows.get(cell.key)
        if row is None:
            if cell.key not in missing:
                missing.append(cell.key)
        elif row[cell.column] != cell.local:
            problems.append(
                f"row {cell.key}, column {cell.column!r}: wrote {cell.local!r}, "
                f"read {row[cell.column]!r}"
            )
    for new in plan.appends:
        row = rows.get(new.key)
        if row is None:
            missing.append(new.key)
            continue
        for column in table.columns:
            sent = new.values.get(column, "")
            if row[column] != sent:
                problems.append(
                    f"new row {new.key}, column {column!r}: wrote {sent!r}, "
                    f"read {row[column]!r}"
                )
    if missing:
        problems.insert(0, f"rows {missing} not found")
    if problems:
        raise ReadBackError(f"{failed}: " + "; ".join(problems))
