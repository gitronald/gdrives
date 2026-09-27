"""Keyed row updates: find rows by header-named column values and set cells."""

from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.values import USER_ENTERED, batch_update_values, pull_values


def _column_indices(
    header: list[str], columns: dict[str, str], role: str
) -> dict[str, int]:
    """Resolve named columns, refusing absent or ambiguous headers."""
    unknown = [col for col in columns if col not in header]
    if unknown:
        raise ValueError(f"{role} column(s) not in header {header}: {unknown}")
    duplicates = [col for col in columns if header.count(col) > 1]
    if duplicates:
        raise ValueError(f"ambiguous {role} column(s) in header: {duplicates}")
    return {col: header.index(col) for col in columns}


def find_rows(grid: list[list[str]], match: dict[str, str]) -> list[int]:
    """Return the 1-based row numbers of data rows matching all ``match`` conditions.

    ``grid`` is the tab's values with row 0 as the header; ``match`` maps header
    names to required cell values and is ANDed (a composite key). Missing cells
    (ragged rows are truncated at the last non-empty cell) compare as ``""``. An
    empty ``match`` matches every data row.
    """
    if not grid:
        return []
    idx = _column_indices(grid[0], match, "match")
    hits = []
    for r in range(1, len(grid)):
        row = grid[r]
        if all(
            (row[idx[col]] if idx[col] < len(row) else "") == value
            for col, value in match.items()
        ):
            hits.append(r + 1)  # grid row r is spreadsheet row r + 1 (row 1 = header)
    return hits


def set_by_match(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    match: dict[str, str],
    updates: dict[str, str],
    *,
    input_option: str = USER_ENTERED,
    allow_multiple: bool = False,
) -> dict[str, Any]:
    """Set ``updates`` column(s) on the row(s) whose cells satisfy ``match``.

    Reads ``tab``, locates rows with :func:`find_rows` (composite AND key over
    header-named columns), and writes every ``updates`` cell in one
    :func:`batch_update_values` call. Columns are addressed by header name.
    Refuses when nothing matches, or when more than one row matches unless
    ``allow_multiple`` is set — so a keyed update never silently rewrites the
    wrong row or a whole column. Returns ``{"rows": [...], "updated_cells": N}``.

    The write addresses rows by number, and Sheets has no revision precondition
    to tie it to the read. So the tab is read again just before writing, and
    the update is refused if the header or the matching rows moved: a row
    inserted or a sort above the match would otherwise redirect the write to a
    different record.
    """
    if not updates:
        raise ValueError("no columns to set")
    quoted = a1_quote(tab)
    grid = pull_values(service, spreadsheet_id, quoted)
    if not grid:
        raise ValueError(f"tab {tab!r} is empty (no header row)")
    targets = _column_indices(grid[0], updates, "target")

    rows = find_rows(grid, match)
    condition = ", ".join(f"{col}={value!r}" for col, value in match.items())
    if not rows:
        raise ValueError(f"no row matching {condition}")
    if len(rows) > 1 and not allow_multiple:
        raise ValueError(
            f"{condition} matches rows {rows}; pass --all to update every match"
        )

    fresh = pull_values(service, spreadsheet_id, quoted)
    if not fresh or fresh[0] != grid[0] or find_rows(fresh, match) != rows:
        raise ValueError(
            f"tab {tab!r} changed while it was being read; nothing was written, "
            "rerun to update the current rows"
        )

    letters = {col: column_letter(index) for col, index in targets.items()}
    data = [
        (f"{quoted}!{letters[col]}{row}", [[value]])
        for row in rows
        for col, value in updates.items()
    ]
    result = batch_update_values(
        service, spreadsheet_id, data, input_option=input_option
    )
    return {"rows": rows, "updated_cells": result.get("totalUpdatedCells", len(data))}


def parse_pairs(pairs: list[str], flag: str) -> dict[str, str]:
    """Parse ``COLUMN=VALUE`` CLI arguments into a dict (last wins on repeats).

    Splits on the first ``=`` so values may contain ``=``; the column name is
    stripped, the value kept verbatim. ``flag`` names the option in error text.
    """
    out: dict[str, str] = {}
    for pair in pairs:
        col, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"{flag} must be COLUMN=VALUE, got {pair!r}")
        col = col.strip()
        if not col:
            raise ValueError(f"{flag} has an empty column name: {pair!r}")
        out[col] = value
    return out
