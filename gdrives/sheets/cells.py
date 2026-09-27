"""Canonical cell strings, declared column types, row keys, and schema checks.

A record is ``dict[str, str]`` keyed by header name, and every value in it is a
**canonical cell string**: the text the Sheets API returns for a value written
with ``RAW`` input. Comparing canonical strings is what lets a sync tell a real
edit from a difference in representation (``3.0`` against ``"3"``, ``True``
against ``"TRUE"``).

Column types are declared, never inferred, because an all-blank column carries
no type to infer. A blank cell is ``None`` under every type.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

#: A typed cell value, as :func:`from_cell` returns it.
CellValue = str | int | float | bool | date | datetime | None

#: The declarable column types. ``str`` is the default.
COLUMN_TYPES = frozenset({"str", "int", "float", "bool", "date", "datetime"})

# An integer as to_cell writes one: digits with an optional minus sign.
_INTEGER = re.compile(r"-?\d+")


def to_cell(value: Any) -> str:
    """Return the canonical cell string for ``value``.

    ``None`` is blank, booleans are ``TRUE`` / ``FALSE`` (as Sheets shows
    them), and an integer-valued float drops its ``.0`` (Sheets returns ``3``
    for a cell holding 3.0). Anything else is ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _check_type(type_: str) -> None:
    if type_ not in COLUMN_TYPES:
        raise ValueError(
            f"unknown column type {type_!r}; expected one of {sorted(COLUMN_TYPES)}"
        )


def from_cell(text: str, type_: str = "str") -> CellValue:
    """Parse a canonical cell string as the declared ``type_``.

    A blank cell is ``None`` under every type. ``from_cell(to_cell(v), t)``
    returns ``v`` for any ``v`` of type ``t``. Raises ValueError when ``text``
    is not a value of that type: surrounding whitespace, digit-group
    underscores, and a fractional ``int`` are refused rather than coerced, so a
    cell never silently changes meaning.

    - ``int``: digits with an optional leading minus
    - ``float``: anything Python's ``float`` reads (``3``, ``2.5``, ``1e-07``)
    - ``bool``: ``TRUE`` or ``FALSE``, in any case
    - ``date`` / ``datetime``: ISO 8601, as ``date.isoformat`` and ``str`` of a
      datetime write them
    """
    _check_type(type_)
    if text == "":
        return None
    if type_ == "str":
        return text
    problem = f"{text!r} is not a valid {type_}"
    if text != text.strip() or "_" in text:
        raise ValueError(problem)
    if type_ == "int":
        if not _INTEGER.fullmatch(text):
            raise ValueError(problem)
        return int(text)
    if type_ == "bool":
        folded = text.upper()
        if folded not in ("TRUE", "FALSE"):
            raise ValueError(problem)
        return folded == "TRUE"
    try:
        if type_ == "float":
            return float(text)
        if type_ == "date":
            return date.fromisoformat(text)
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(problem) from None


# -- row keys --


def normalize_key(text: str) -> str:
    """Normalize one key cell for comparison: strip it, collapse inner whitespace.

    A trailing space typed on the sheet, or a doubled space, must not split one
    row into two. Only comparisons use this form; the stored text is never
    rewritten.
    """
    return " ".join(text.split())


def row_key(record: Mapping[str, str], key: Sequence[str]) -> tuple[str, ...]:
    """The normalized key of ``record``: one entry per key column, in order.

    A key column the record lacks counts as blank.
    """
    return tuple(normalize_key(record.get(column, "")) for column in key)


def index_rows(
    rows: Sequence[Mapping[str, str]],
    key: Sequence[str],
    *,
    side: str,
    numbers: Sequence[int] | None = None,
) -> dict[tuple[str, ...], int]:
    """Map each row's normalized key to its row number, refusing bad keys.

    ``numbers`` labels each row (a tab passes its 1-based spreadsheet rows);
    without it a row is labelled by its 1-based position in ``rows``. Raises
    ValueError naming ``side`` (``"local"``, ``"tab 'Members'"``) when any row
    has a blank key cell, or when two rows share a key, listing every such
    row at once so a single run shows everything to fix.
    """
    if not key:
        raise ValueError(f"{side}: no key columns to index rows by")
    labels = list(numbers) if numbers is not None else list(range(1, len(rows) + 1))
    index: dict[tuple[str, ...], int] = {}
    blank: list[int] = []
    duplicates: dict[tuple[str, ...], list[int]] = {}
    for label, row in zip(labels, rows, strict=True):
        found = row_key(row, key)
        if "" in found:
            blank.append(label)
        elif found in index:
            duplicates.setdefault(found, [index[found]]).append(label)
        else:
            index[found] = label
    problems: list[str] = []
    if blank:
        problems.append(f"blank key {list(key)} in rows {blank}")
    problems.extend(
        f"duplicate key {found} in rows {labelled}"
        for found, labelled in duplicates.items()
    )
    if problems:
        raise ValueError(f"{side}: " + "; ".join(problems))
    return index


# -- schema checks --


@dataclass(frozen=True)
class ColumnSchema:
    """What one column must hold: its type, whether it may be blank, its values.

    ``allowed`` lists the permitted values, compared as canonical strings; a
    blank cell is checked by ``required``, never by ``allowed``.
    """

    type: str = "str"
    required: bool = False
    allowed: Collection[Any] | None = None

    def __post_init__(self) -> None:
        _check_type(self.type)


@dataclass(frozen=True)
class Problem:
    """One cell that does not fit its column's schema."""

    tab: str
    row: int  # 1-based position of the record in the rows checked
    key: tuple[str, ...]
    column: str
    text: str
    reason: str

    def __str__(self) -> str:
        where = f"key {self.key}" if self.key else f"row {self.row}"
        return f"{self.tab}: {where}, column {self.column!r}: {self.reason}"


def _cell_problem(text: str, schema: ColumnSchema) -> str | None:
    """Why ``text`` does not fit ``schema``, or None when it does."""
    if text == "":
        return "is required" if schema.required else None
    try:
        from_cell(text, schema.type)
    except ValueError as e:
        return str(e)
    if schema.allowed is not None:
        allowed = [to_cell(value) for value in schema.allowed]
        if text not in allowed:
            return f"{text!r} is not one of {allowed}"
    return None


def problems(
    rows: Sequence[Mapping[str, str]],
    schema: Mapping[str, ColumnSchema],
    *,
    tab: str,
    key: Sequence[str] = (),
) -> list[Problem]:
    """Every cell in ``rows`` that does not fit ``schema``, in row then column order.

    Each problem names ``tab``, the row (by its key when ``key`` is given), the
    column, and the offending text. A column the schema names but a row lacks
    counts as blank. Nothing is coerced or dropped: a caller that finds any
    problem writes nothing.
    """
    found: list[Problem] = []
    for position, row in enumerate(rows, start=1):
        for column, spec in schema.items():
            text = row.get(column, "")
            reason = _cell_problem(text, spec)
            if reason is not None:
                found.append(
                    Problem(
                        tab=tab,
                        row=position,
                        key=tuple(row.get(k, "") for k in key),
                        column=column,
                        text=text,
                        reason=reason,
                    )
                )
    return found
