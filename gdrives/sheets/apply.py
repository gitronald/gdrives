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

With ``typed_writes`` the declared columns are written as values instead
(:mod:`~gdrives.sheets.typed`), and steps 2 and 3 are one
``spreadsheets.batchUpdate``: the new rows first, then each pushed cell by
``updateCells`` at its row as it is once they are in, then the date formats
of the date cells written that have none. Every sheet write of the run is
one request, so it lands whole or not at all. The read-back compares a typed
column by value (:func:`~gdrives.sheets.cells.normalize_cell`): ``3.0``
written reads back ``3``.

New rows never go through ``values.append``, which takes the first blank row
it finds as the table's end and so writes over rows below a cleared gap. They
go to explicit rows: after the last row holding anything, or with
``insert_above`` into rows opened above a named row, which take the
formatting of the row above them. Only the projection's columns are written;
other columns of the new rows are left alone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.cells import (
    SERIAL_TYPES,
    cell_data,
    normalize_cell,
    row_key,
    to_cell,
)
from gdrives.sheets.merge import MergePlan
from gdrives.sheets.structure import (
    CELL_LINK_FIELD,
    RUNS_FIELD,
    LinkedCell,
    UrlLinkProblem,
    _fix_url_links,
    _rgb,
    link_clear,
    linked_cells,
)
from gdrives.sheets.table import Table, read_tab
from gdrives.sheets.typed import (
    _VALUE_FIELD,
    _typed_problems,
    _value_request,
    dated_cells,
    format_requests,
    typed_columns,
)
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

    ``pushed_cells`` is each pushed cell as ``(row, column)``, in the plan's
    order, the row as it is after the apply. ``appended_columns`` is the
    columns written in each new row, in header order, so the cells of the
    new rows are every ``appended_rows`` row by every such column. Together
    they are the cells the run wrote, for a pass over them that need not read
    the tab again.

    ``linked`` is each URL cell a run with ``link_urls`` gave a link, as it
    was before the fix.
    """

    pushed: int
    appended: int
    pushed_rows: list[int]
    appended_rows: list[int]
    pushed_cells: list[tuple[int, str]] = field(default_factory=list)
    appended_columns: list[str] = field(default_factory=list)
    linked: list[UrlLinkProblem] = field(default_factory=list)


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
    inserts at, on a tab that has not changed in between. The apply calls it
    on its own fresh read, and its guard compares the projection's columns
    only: an edit to an ``insert_above`` column outside the projection is not
    refused, and the rows go where the column puts them as re-read. Raises
    ValueError for an ``insert_above`` that does not name
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
    column); the comparison covers the projection only. The tab is read with
    ``table``'s own types, ``blank_keys``, and ``render``, as it was first read.
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
            render=table.render,
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


def _row_requests(
    fresh: Table,
    plan: MergePlan,
    sheet_id: int,
    row_count: int,
    at: int,
    insert: bool,
    clear_links: bool = False,
    types: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The requests that open rows at 0-based row ``at`` and fill them.

    With ``insert`` the rows are inserted there, shifting the rows below down;
    otherwise they are written in place, after appending grid rows if they
    would not fit. With ``clear_links`` the cell link is in the mask of the
    write, so a URL is written with no link, in the same request. A column
    ``types`` names is written as a value of its type, and every other
    column as a literal string.
    """
    types = types or {}
    fields = _VALUE_FIELD
    if clear_links:
        fields += f",{CELL_LINK_FIELD}"
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
        rows = [
            [
                cell_data(new.values.get(column, ""), types.get(column, "str"))
                for column in columns
            ]
            for new in plan.appends
        ]
        requests.append(_value_request(sheet_id, at, first, rows, fields))
    return requests


def _typed_refusals(table: Table, plan: MergePlan, types: Mapping[str, str]) -> None:
    """Refuse a plan holding a value its column's type cannot be written as."""
    cells = [(f"row {cell.key}", cell.column, cell.local) for cell in plan.pushes]
    cells.extend(
        (f"new row {new.key}", column, text)
        for new in plan.appends
        for column, text in new.values.items()
    )
    problems = _typed_problems(cells, types)
    if problems:
        raise ValueError(
            f"tab {table.tab!r}: cannot write typed values: " + "; ".join(problems)
        )


def _unformatted_dates(
    fresh: Table,
    plan: MergePlan,
    types: Mapping[str, str],
    dated: set[tuple[int, str]],
    at: int,
    inserted: bool,
    after: Any,
) -> list[tuple[int, int, str]]:
    """The date cells a typed run writes that will have no date format.

    Each is ``(row, column, type)``, 0-based, placed as the tab is once the
    new rows are in. A pushed cell has the format it had. A new row inserted
    has the format of the row it inherits from (the row above, or below when
    that is the header), and one written in place has the format of the row
    it is written over, or none past the grid's end. ``dated`` is what
    :func:`~gdrives.sheets.typed.dated_cells` found before the write.
    """
    found: list[tuple[int, int, str]] = []
    for cell in plan.pushes:
        type_ = types.get(cell.column, "str")
        row = fresh.row_numbers[cell.key]
        if type_ in SERIAL_TYPES and cell.local and (row, cell.column) not in dated:
            found.append((after(row) - 1, fresh.header.index(cell.column), type_))
    source = at if at > 1 else at + 1  # the spreadsheet row inserted rows copy
    for offset, new in enumerate(plan.appends):
        was = source if inserted else at + offset + 1
        for column, text in new.values.items():
            type_ = types.get(column, "str")
            if type_ in SERIAL_TYPES and text and (was, column) not in dated:
                found.append((at + offset, fresh.header.index(column), type_))
    return found


def _links_left(tab: str, left: Sequence[LinkedCell], by: str) -> ReadBackError:
    """The error for links that ``by`` (the run, the push) wrote and did not clear."""
    return ReadBackError(
        f"tab {tab!r}: the read-back found links the {by} did not clear: "
        + "; ".join(
            f"row {cell.row}, column {cell.column!r} still holds a link to "
            f"{list(cell.targets)}"
            for cell in left
        )
    )


def _check_links(
    service: Service, spreadsheet_id: str, fresh: Table, result: ApplyResult
) -> None:
    """Read the links of the cells a run wrote, raising if one holds any."""
    written = {*result.pushed_cells}
    written.update(
        (row, column)
        for row in result.appended_rows
        for column in result.appended_columns
    )
    columns = sorted({column for _, column in written}, key=fresh.header.index)
    found = linked_cells(
        service, spreadsheet_id, fresh.tab, columns=columns, header=fresh.header
    )
    left = [cell for cell in found if (cell.row, cell.column) in written]
    if left:
        raise _links_left(fresh.tab, left, "run")


def _written(result: ApplyResult) -> set[tuple[int, str]]:
    """The cells a run wrote, as ``(row, column)``."""
    return {*result.pushed_cells} | {
        (row, column)
        for row in result.appended_rows
        for column in result.appended_columns
    }


def _link_written(
    service: Service,
    spreadsheet_id: str,
    fresh: Table,
    result: ApplyResult,
    color: str,
) -> ApplyResult:
    """Give the URL cells a run wrote a link to their text, in ``color``."""
    written = _written(result)
    linked = _fix_url_links(
        service,
        spreadsheet_id,
        fresh.tab,
        color,
        columns=sorted({column for _, column in written}, key=fresh.header.index),
        rows=sorted({row for row, _ in written}),
        cells=written,
    )
    return replace(result, linked=linked)


def apply_plan(
    service: Service,
    spreadsheet_id: str,
    table: Table,
    plan: MergePlan,
    *,
    insert_above: Mapping[str, Any] | None = None,
    clear_links: bool = False,
    link_urls: str | None = None,
    typed_writes: bool = False,
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

    The Sheets API links text that is a URL or a bare domain when it is
    written. ``clear_links`` leaves the cells this run writes, and no others,
    with no link. New rows are written with the link in their mask, which
    costs nothing. A pushed cell has its link and its text format runs
    cleared in the run's ``spreadsheets.batchUpdate``, after the inserts, so
    a run with pushes and no new rows sends one request more. The links of
    the written cells are then read back
    (:func:`~gdrives.sheets.structure.linked_cells`), and
    :class:`ReadBackError` is raised when one remains.

    ``link_urls``, a ``#rrggbb`` colour, does the opposite for the URL cells
    this run writes, and no others: after the read-back, each is given a
    link to its own text in that colour, not underlined
    (:func:`~gdrives.sheets.structure.set_url_links`), and
    :attr:`ApplyResult.linked` lists them. It costs a read of the tab's
    values and a grid read of the URL cells written, and when any needs a
    link, a read of the tab's ``sheetId``, one write, and the two reads again.
    It contradicts ``clear_links``.

    ``typed_writes`` writes each column ``table.types`` declares, less the
    key, as a value of its type (:func:`~gdrives.sheets.cells.cell_data`),
    and every sheet write in the one ``spreadsheets.batchUpdate``. A date
    cell written that has no date or time format is given ``yyyy-mm-dd``
    (``yyyy-mm-dd hh:mm:ss`` for a datetime) in that request, which costs a
    grid read of the date columns first (:func:`~gdrives.sheets.typed.dated_cells`).
    It needs a table read ``unformatted``: a formatted read returns what a
    number format shows, not the value written.

    A plan with nothing to push or add makes no request at all. Raises
    ValueError, before any request, when the plan does not fit ``table`` (a
    push to a row or column the table lacks, a new row whose key the tab
    already has), ``insert_above`` names a column the header lacks,
    ``link_urls`` is not ``#rrggbb`` or is given with ``clear_links``, or,
    with ``typed_writes``, the table was read ``formatted`` or a value
    cannot be written as its column's type.
    """
    if link_urls is not None:
        if clear_links:
            raise ValueError("clear_links and link_urls contradict each other")
        _rgb(link_urls)
    _check_plan(table, plan)
    types: dict[str, str] = {}
    if typed_writes:
        if table.render != "unformatted":
            raise ValueError(
                f"tab {table.tab!r}: typed writes need a table read unformatted, "
                f"not {table.render!r}"
            )
        types = typed_columns(table.types, table.key)
        _typed_refusals(table, plan, types)
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
    if count and insert_above is not None:
        above = insert_point(fresh, plan, insert_above)
        if above is not None:
            at, inserted = above - 1, True
    shift = count if inserted else 0

    def after(row: int) -> int:
        """Where a row of the fresh read sits once the new rows are in."""
        return row + shift if row > at else row

    pushed_cells = [
        (after(fresh.row_numbers[cell.key]), cell.column) for cell in plan.pushes
    ]
    dated: set[tuple[int, str]] = set()
    if typed_writes:
        written = {cell.column for cell in plan.pushes if cell.local}
        written.update(
            c for new in plan.appends for c, text in new.values.items() if text
        )
        dates = [
            c for c in fresh.columns if types.get(c) in SERIAL_TYPES and c in written
        ]
        # Read before any write, so a failed read leaves the tab untouched.
        dated = dated_cells(service, spreadsheet_id, fresh.tab, fresh.header, dates)
    if count or (clear_links or typed_writes) and pushed_cells:
        # Read before any write, so a failed read leaves the tab untouched.
        grid = tab_grid(service, spreadsheet_id, fresh.tab)
        if count:
            requests = _row_requests(
                fresh,
                plan,
                grid.sheet_id,
                grid.row_count,
                at,
                inserted,
                clear_links,
                types,
            )
        if typed_writes:
            # After the inserts, so by the rows as they are once those are in.
            fields = _VALUE_FIELD
            if clear_links:
                fields += f",{CELL_LINK_FIELD},{RUNS_FIELD}"
            requests.extend(
                _value_request(
                    grid.sheet_id,
                    row - 1,
                    fresh.header.index(cell.column),
                    [[cell_data(cell.local, types.get(cell.column, "str"))]],
                    fields,
                )
                for (row, _), cell in zip(pushed_cells, plan.pushes, strict=True)
            )
            requests.extend(
                format_requests(
                    grid.sheet_id,
                    _unformatted_dates(fresh, plan, types, dated, at, inserted, after),
                )
            )
        elif clear_links:
            # After the inserts, so by the rows as they are once those are in.
            requests.extend(
                link_clear(
                    grid.sheet_id,
                    f"{CELL_LINK_FIELD},{RUNS_FIELD}",
                    (fresh.header.index(column), fresh.header.index(column) + 1),
                    (row - 1, row),
                )
                for row, column in pushed_cells
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
    if data and not typed_writes:
        batch_update_values(service, spreadsheet_id, data, input_option=RAW)
    if requests:
        batch_update_spreadsheet(service, spreadsheet_id, requests)
    verify(service, spreadsheet_id, table, plan, typed_writes=typed_writes)

    pushed_rows = sorted({fresh.row_numbers[cell.key] for cell in plan.pushes})
    written = sorted(table.columns, key=fresh.header.index) if count else []
    result = ApplyResult(
        pushed=len(plan.pushes),
        appended=count,
        pushed_rows=[after(row) for row in pushed_rows],
        appended_rows=list(range(at + 1, at + 1 + count)),
        pushed_cells=pushed_cells,
        appended_columns=written,
    )
    if clear_links:
        _check_links(service, spreadsheet_id, fresh, result)
    if link_urls is not None:
        result = _link_written(service, spreadsheet_id, fresh, result, link_urls)
    return result


def verify(
    service: Service,
    spreadsheet_id: str,
    table: Table,
    plan: MergePlan,
    *,
    typed_writes: bool = False,
) -> None:
    """Read ``table``'s tab back and check that ``plan``'s sheet writes landed.

    Rows are found by key, not by number, so rows inserted above them do not
    matter. The tab is read with ``table``'s declared types and ``render``,
    as it was read for the plan. Every pushed cell must hold its ``local``
    value, and every new row must exist with its projection cells as sent. Raises
    :class:`ReadBackError` listing every mismatch at once, or when the tab no
    longer reads cleanly (a key now blank or repeated, a column gone).

    With ``typed_writes`` a column the write sent as values is compared by
    value (:func:`~gdrives.sheets.cells.normalize_cell`), since a sheet holds
    ``3.0`` as ``3``; the key and every text column are compared as text.
    """
    types: dict[str, str] = {}
    if typed_writes:
        types = typed_columns(table.types, table.key)

    def differs(read: str, sent: str, column: str) -> bool:
        if column in types:
            type_ = types[column]
            return normalize_cell(read, type_) != normalize_cell(sent, type_)
        return read != sent

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
            render=table.render,
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
        elif differs(row[cell.column], cell.local, cell.column):
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
            if differs(row[column], sent, column):
                problems.append(
                    f"new row {new.key}, column {column!r}: wrote {sent!r}, "
                    f"read {row[column]!r}"
                )
    if missing:
        problems.insert(0, f"rows {missing} not found")
    if problems:
        raise ReadBackError(f"{failed}: " + "; ".join(problems))
