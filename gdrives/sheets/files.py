"""Local files: delimited rows of string cells, and records by file extension.

``read_values_csv`` / ``write_values_csv`` move a grid of cells as-is.
``read_records`` / ``write_records`` move header-named records (see
:mod:`gdrives.sheets.cells`), picking the format from the extension:

- ``.csv`` / ``.tsv``: every cell is a string, so leading zeros, booleans, and
  dates stay exactly as written. Records end their lines with LF unless asked
  otherwise; a grid keeps the CRLF of the ``csv`` module.
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
from gdrives.sheets.cells import ColumnType, decode_rows, encode_rows

# The record file formats, by lower-cased extension; the value is the delimiter
# for a delimited format, None for JSON.
_FORMATS: dict[str, str | None] = {".csv": ",", ".tsv": "\t", ".json": None}

#: The names of the line endings a delimited file can be written with.
NEWLINES = frozenset({"lf", "crlf"})

_TERMINATORS = {"lf": "\n", "crlf": "\r\n"}


def _terminator(newline: str) -> str:
    """The line ending called ``newline``, refusing a name that is not one."""
    if newline not in NEWLINES:
        raise ValueError(f"newline must be one of {sorted(NEWLINES)}, not {newline!r}")
    return _TERMINATORS[newline]


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
    path: str,
    values: list[list[str]],
    *,
    delimiter: str = ",",
    bom: bool = False,
    newline: str = "crlf",
) -> None:
    """Write rows of cells to a local delimited file, creating parent dirs.

    Written atomically, so a failed run never leaves a partial file behind.
    ``bom`` starts the file with a UTF-8 byte-order mark, which some
    spreadsheet apps need to read it as UTF-8. ``newline`` ends each row with
    ``"crlf"`` (the default, and the ``csv`` module's) or ``"lf"``; a line
    break inside a cell is written as the cell holds it.
    """
    terminator = _terminator(newline)
    buf = io.StringIO()
    if bom:
        buf.write("\ufeff")
    csv.writer(buf, delimiter=delimiter, lineterminator=terminator).writerows(values)
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


def _records_from_array(data: Any, where: str | Path) -> Records:
    """Parsed JSON ``data``, an array of flat objects, as records.

    ``where`` begins every error: a file's path, or the entry of one.
    """
    if not isinstance(data, list):
        raise ValueError(f"{where}: expected a JSON array of objects")
    columns: dict[str, None] = {}  # insertion-ordered set
    items: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{where}: item {index} is not an object")
        named: dict[str, Any] = {}
        for column, value in item.items():
            if isinstance(value, (list, dict)):
                raise ValueError(
                    f"{where}: item {index}, {column!r}: nested values are not cells"
                )
            name = column.strip()
            if name in named:
                raise ValueError(f"{where}: item {index} repeats column name {name!r}")
            named[name] = value
            columns.setdefault(name, None)
        items.append(named)
    _check_columns(where, list(columns))
    rows = encode_rows(items, list(columns))
    return Records(list(columns), [row for row in rows if not _is_blank(row)])


def _read_json(path: str | Path) -> Records:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON: {e}") from None
    return _records_from_array(data, path)


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
    types: Mapping[str, ColumnType] | None = None,
    bom: bool = False,
    newline: str = "lf",
) -> None:
    """Atomically write records to a ``.csv``, ``.tsv``, or ``.json`` file.

    ``columns`` fixes the column order; a column a row lacks is written blank,
    and a row holding a column not in ``columns`` raises rather than being
    dropped. A delimited file gets a header row and every cell as-is; ``bom``
    starts it with a UTF-8 byte-order mark, and ``newline`` ends its lines
    with ``"lf"`` (the default) or ``"crlf"``. A JSON file gets one object per row
    with keys in ``columns`` order and each value parsed as its column's type
    in ``types``, a name or a class (default ``str``); a blank cell is
    ``null``, and a date or datetime keeps its canonical string, since JSON
    has no date type. Every cell that does not parse as its type is listed in
    one ValueError, by row position and column. A JSON array
    has no header, so a JSON file with no rows does not record its columns. It
    is written with LF, and refuses ``bom`` and any other ``newline``.
    """
    delimiter = _format(path)
    _terminator(newline)
    if delimiter is None:
        if bom:
            raise ValueError(f"{path}: a byte-order mark applies only to .csv and .tsv")
        if newline != "lf":
            raise ValueError(f"{path}: newline applies only to .csv and .tsv")
        write_text(
            Path(path), _json_text(path, _json_array(path, columns, rows, types))
        )
        return
    _check_columns(path, columns)
    write_values_csv(
        str(path),
        [list(columns), *_row_cells(path, columns, rows)],
        delimiter=delimiter,
        bom=bom,
        newline=newline,
    )


def _json_array(
    where: str | Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    types: Mapping[str, ColumnType] | None,
) -> list[dict[str, Any]]:
    """``rows`` as the objects of a JSON array, each value typed by ``types``.

    Keys are in ``columns`` order, a blank cell is None, and a date or
    datetime keeps its canonical string. ``where`` begins every error: a
    file's path, or the entry of one. The result is dumped with
    :func:`_json_text`, which refuses the ``NaN`` a ``float`` column can parse.
    """
    _check_columns(where, columns)
    grid = _row_cells(where, columns, rows)
    cells = [dict(zip(columns, row, strict=True)) for row in grid]
    try:
        typed_rows = decode_rows(cells, types or {})
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None
    # Dates have no JSON type, so a parsed one keeps its canonical string.
    return [
        {
            column: row[column] if isinstance(value, date) else value
            for column, value in typed.items()
        }
        for row, typed in zip(cells, typed_rows, strict=True)
    ]


def _json_text(where: str | Path, data: Any) -> str:
    """``data`` as the library writes a JSON file: two-space indent, final newline.

    Byte-stable: the same values dump to the same text. Non-ASCII text is
    kept as it is, and NaN or Infinity is refused, since neither is JSON.
    """
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None
    return text + "\n"
