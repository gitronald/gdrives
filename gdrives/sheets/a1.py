"""A1 notation and ``GridRange`` helpers.

Values address cells by A1 strings (``'Q3 Budget'!A2:C``); the structural
``spreadsheets.batchUpdate`` requests address them by ``GridRange`` (numeric
``sheetId`` + 0-based, half-open indices). These helpers convert between the
two and quote tab names for A1.
"""

import re
from typing import Any

from gdrives.files import Service
from gdrives.sheets.values import _lookup_tab, tab_sheet_ids

_CELL_RE = re.compile(r"(?:\$?([A-Za-z]+))?(?:\$?([0-9]+))?")


def column_letter(index: int) -> str:
    """Convert a 0-based column index to its A1 letter (0 -> A, 26 -> AA)."""
    if index < 0:
        raise ValueError(f"column index must be non-negative, got {index}")
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def a1_quote(tab: str) -> str:
    """Quote a tab name for A1 notation, escaping embedded single quotes.

    ``'Q3 Budget'`` and names that look like cell refs need quoting; doubling any
    ``'`` (A1's escape) makes an arbitrary tab name safe to interpolate.
    """
    return "'" + tab.replace("'", "''") + "'"


def column_index(letters: str) -> int:
    """Convert an A1 column letter to its 0-based index (A -> 0, AA -> 26)."""
    if not (letters.isascii() and letters.isalpha()):
        raise ValueError(f"not a column letter: {letters!r}")
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _unquote(tab: str) -> str:
    """Undo :func:`a1_quote`: strip surrounding quotes and un-double ``''``."""
    if len(tab) >= 2 and tab.startswith("'") and tab.endswith("'"):
        return tab[1:-1].replace("''", "'")
    return tab


def split_a1(range_: str) -> tuple[str | None, str]:
    """Split an A1 range into ``(tab title or None, cell span)``, unquoting the tab.

    Splits on the *last* ``!``, since a quoted tab name may contain one but the
    cell span never does.
    """
    if "!" not in range_ or (range_.startswith("'") and range_.endswith("'")):
        return None, range_
    tab, cells = range_.rsplit("!", 1)
    return _unquote(tab), cells


def _parse_cell(ref: str, range_: str) -> tuple[int | None, int | None]:
    """Parse one A1 corner (``B3``, ``AA``, ``7``) to (column index, row number)."""
    match = _CELL_RE.fullmatch(ref)
    if match is None or not ref:
        raise ValueError(f"bad A1 cell reference {ref!r} in {range_!r}")
    letters, digits = match.groups()
    row = int(digits) if digits else None
    if row == 0:
        raise ValueError(f"row numbers start at 1, got {ref!r} in {range_!r}")
    return (column_index(letters) if letters else None), row


def _cell_span(cells: str) -> dict[str, int]:
    """Convert an A1 cell span to GridRange indices (0-based, end-exclusive).

    An omitted row or column on a corner leaves that index unset, which is how
    the API expresses an open-ended range: ``A2:AA`` has no ``endRowIndex``.
    (A stored conditional format rule does not keep the open end: the API clamps
    it to the tab's current size, so ``A2:AA`` lists back as ``A2:AA1000``.)
    """
    start, sep, end = cells.partition(":")
    s_col, s_row = _parse_cell(start, cells)
    e_col, e_row = _parse_cell(end, cells) if sep else (s_col, s_row)
    span: dict[str, int] = {}
    if s_row is not None:
        span["startRowIndex"] = s_row - 1
    if e_row is not None:
        span["endRowIndex"] = e_row
    if s_col is not None:
        span["startColumnIndex"] = s_col
    if e_col is not None:
        span["endColumnIndex"] = e_col + 1
    for axis in ("Row", "Column"):
        lo, hi = span.get(f"start{axis}Index"), span.get(f"end{axis}Index")
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError(f"range {cells!r} ends before it starts")
    return span


def a1_to_grid_range(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    *,
    tab_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Convert an A1 range to a ``GridRange`` dict for ``spreadsheets.batchUpdate``.

    Resolves the tab title to its ``sheetId`` (a bare span like ``A2:C`` targets
    the first tab; a bare tab name, or ``Tab!``, covers the whole tab). Pass
    ``tab_ids`` from :func:`tab_sheet_ids` to convert several ranges with one
    lookup.
    """
    if not range_.strip():
        raise ValueError("A1 range must not be empty")
    ids = tab_ids if tab_ids is not None else tab_sheet_ids(service, spreadsheet_id)
    tab, cells = split_a1(range_)
    if tab is None and _unquote(cells) in ids:
        tab, cells = _unquote(cells), ""
    tab = _lookup_tab(ids, tab)
    grid: dict[str, Any] = {"sheetId": ids[tab]}
    if cells:
        grid.update(_cell_span(cells))
    return grid


def grid_range_to_a1(grid: dict[str, Any]) -> str:
    """Render a GridRange's cell span in A1 notation, without the tab.

    An absent end index is an open end (``A2:AA``); an absent span is the whole
    tab, rendered as ``""``.
    """
    sc, ec = grid.get("startColumnIndex"), grid.get("endColumnIndex")
    sr, er = grid.get("startRowIndex"), grid.get("endRowIndex")
    cols = sc is not None or ec is not None
    rows = sr is not None or er is not None
    if not (cols or rows):
        return ""
    start = (column_letter(sc or 0) if cols else "") + (
        str((sr or 0) + 1) if rows else ""
    )
    end = (column_letter(ec - 1) if ec is not None else "") + (
        str(er) if er is not None else ""
    )
    return start if cols and rows and start == end else f"{start}:{end}"
