"""Local files: delimited rows of string cells, and records by file extension.

``read_values_csv`` / ``write_values_csv`` move a grid of cells as-is.
``read_records`` / ``write_records`` move header-named records (see
:mod:`gdrives.sheets.cells`), picking the format from the extension:

- ``.csv`` / ``.tsv``: every cell is a string, so leading zeros, booleans, and
  dates stay exactly as written.
- ``.json``: an array of objects holding typed values per the declared column
  types, written byte-stably (column order, two-space indent, final newline)
  so rewriting unchanged records leaves the file byte-for-byte the same.

Every write is atomic (``gdrives.local.write_text``).
"""

import csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

from gdrives.local import write_text
from gdrives.sheets.cells import from_cell, to_cell

# The record file formats, by lower-cased extension; the value is the delimiter
# for a delimited format, None for JSON.
_FORMATS: dict[str, str | None] = {".csv": ",", ".tsv": "\t", ".json": None}


def read_values_csv(path: str, *, delimiter: str = ",") -> list[list[str]]:
    """Read a local delimited file into rows of string cells.

    A UTF-8 byte-order mark (Excel's "CSV UTF-8" format starts with one) is
    dropped instead of becoming part of the first cell, where it would break a
    later header lookup. A file the csv module rejects (an oversized field, a
    stray NUL) raises ValueError naming the line.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            return list(reader)
        except csv.Error as e:
            raise ValueError(f"{path}, line {reader.line_num}: {e}")


def write_values_csv(
    path: str, values: list[list[str]], *, delimiter: str = ",", bom: bool = False
) -> None:
    """Write rows of cells to a local delimited file, creating parent dirs.

    Written atomically, so a failed run never leaves a partial file behind.
    ``bom`` starts the file with a UTF-8 byte-order mark, which some
    spreadsheet apps need to read it as UTF-8.
    """
    buf = io.StringIO()
    if bom:
        buf.write("\ufeff")
    csv.writer(buf, delimiter=delimiter).writerows(values)
    write_text(Path(path), buf.getvalue())


# -- records --


class Records(NamedTuple):
    """A record file's columns, in file order, and its rows as canonical strings."""

    columns: list[str]
    rows: list[dict[str, str]]


def _format(path: str | Path) -> str | None:
    """The delimiter for ``path``'s format (None for JSON), refusing others."""
    suffix = Path(path).suffix.lower()
    if suffix not in _FORMATS:
        raise ValueError(
            f"{path}: unsupported record file type {suffix!r}; "
            f"expected one of {sorted(_FORMATS)}"
        )
    return _FORMATS[suffix]


def _check_columns(path: str | Path, columns: Sequence[str]) -> None:
    """Refuse a blank or repeated column name: records are keyed by name."""
    if "" in columns:
        raise ValueError(f"{path}: blank column name in {list(columns)}")
    repeated = sorted({c for c in columns if columns.count(c) > 1})
    if repeated:
        raise ValueError(f"{path}: repeated column name(s) {repeated}")


def _is_blank(row: Mapping[str, str]) -> bool:
    return all(text == "" for text in row.values())


def _read_delimited(path: str | Path, delimiter: str) -> Records:
    grid = read_values_csv(str(path), delimiter=delimiter)
    if not grid:
        return Records([], [])
    columns = [name.strip() for name in grid[0]]
    _check_columns(path, columns)
    rows: list[dict[str, str]] = []
    for number, cells in enumerate(grid[1:], start=2):
        if len(cells) > len(columns):
            raise ValueError(
                f"{path}, row {number}: {len(cells)} cells but "
                f"{len(columns)} columns in the header"
            )
        cells = cells + [""] * (len(columns) - len(cells))
        row = dict(zip(columns, cells, strict=True))
        if not _is_blank(row):
            rows.append(row)
    return Records(columns, rows)


def _read_json(path: str | Path) -> Records:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON: {e}") from None
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of objects")
    columns: dict[str, None] = {}  # insertion-ordered set
    items: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: item {index} is not an object")
        named: dict[str, Any] = {}
        for column, value in item.items():
            if isinstance(value, (list, dict)):
                raise ValueError(
                    f"{path}: item {index}, {column!r}: nested values are not cells"
                )
            name = column.strip()
            if name in named:
                raise ValueError(f"{path}: item {index} repeats column name {name!r}")
            named[name] = value
            columns.setdefault(name, None)
        items.append(named)
    _check_columns(path, list(columns))
    rows = [{column: to_cell(item.get(column)) for column in columns} for item in items]
    return Records(list(columns), [row for row in rows if not _is_blank(row)])


def read_records(path: str | Path) -> Records:
    """Read a ``.csv``, ``.tsv``, or ``.json`` file as records of canonical strings.

    A delimited file's first row is the header; short rows are padded with
    blanks, and a row longer than the header raises (its extra cells have no
    column to go in). A JSON file is an array of flat objects; its columns are
    every key in first-seen order, and a key an object lacks is blank. Typed
    JSON values become canonical strings (``true`` -> ``"TRUE"``). Entirely
    blank rows are skipped in every format, as they are on a tab. Column names
    are stripped of surrounding whitespace, as a tab's header cells are, so a
    padded name matches the sheet's column. A blank or repeated column name
    raises.
    """
    delimiter = _format(path)
    if delimiter is None:
        return _read_json(path)
    return _read_delimited(path, delimiter)


def _json_value(text: str, type_: str) -> Any:
    """The JSON value for one canonical cell string under its declared type.

    Dates have no JSON type, so a date or datetime column keeps its canonical
    string, once it has been checked to parse.
    """
    value = from_cell(text, type_)
    return text if isinstance(value, date) else value


def _row_cells(
    path: str | Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]
) -> list[list[str]]:
    """Each row's cells in ``columns`` order, refusing a cell with no column."""
    wanted = set(columns)
    grid: list[list[str]] = []
    for number, row in enumerate(rows, start=1):
        unknown = [column for column in row if column not in wanted]
        if unknown:
            raise ValueError(f"{path}: record {number} has unknown columns {unknown}")
        grid.append([row.get(column, "") for column in columns])
    return grid


def write_records(
    path: str | Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    *,
    types: Mapping[str, str] | None = None,
    bom: bool = False,
) -> None:
    """Atomically write records to a ``.csv``, ``.tsv``, or ``.json`` file.

    ``columns`` fixes the column order; a column a row lacks is written blank,
    and a row holding a column not in ``columns`` raises rather than being
    dropped. A delimited file gets a header row and every cell as-is; ``bom``
    starts it with a UTF-8 byte-order mark. A JSON file gets one object per row
    with keys in ``columns`` order and each value parsed as its column's type
    in ``types`` (default ``str``); a blank cell is ``null``. A JSON array
    has no header, so a JSON file with no rows does not record its columns.
    """
    delimiter = _format(path)
    _check_columns(path, columns)
    grid = _row_cells(path, columns, rows)
    if delimiter is not None:
        write_values_csv(
            str(path), [list(columns), *grid], delimiter=delimiter, bom=bom
        )
        return
    if bom:
        raise ValueError(f"{path}: a byte-order mark applies only to .csv and .tsv")
    declared = types or {}
    try:
        records = [
            {
                column: _json_value(text, declared.get(column, "str"))
                for column, text in zip(columns, cells, strict=True)
            }
            for cells in grid
        ]
        # allow_nan=False: NaN and Infinity are not JSON, so refuse to write them.
        text = json.dumps(records, indent=2, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None
    write_text(Path(path), text + "\n")
