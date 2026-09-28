"""Canonical cell strings, declared column types, typed rows, row keys, and schemas.

A record is ``dict[str, str]`` keyed by header name, and every value in it is a
**canonical cell string**: the text the Sheets API returns for a value written
with ``RAW`` input. Comparing canonical strings is what lets a sync tell a real
edit from a difference in representation (``3.0`` against ``"3"``, ``True``
against ``"TRUE"``).

Column types are declared, never inferred, because an all-blank column carries
no type to infer. A blank cell is ``None`` under every type. A type is
declared by its name or by its class (:func:`column_type`), and
:func:`encode_rows` and :func:`decode_rows` move whole rows of typed values to
records and back.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

#: A typed cell value, as :func:`from_cell` returns it.
CellValue = str | int | float | bool | date | datetime | None

#: A column type as it is declared: by its name, or by its class.
ColumnType = str | type

#: The declarable column types. ``str`` is the default.
COLUMN_TYPES = frozenset({"str", "int", "float", "bool", "date", "datetime"})

# The class that declares each type. bool subclasses int and datetime
# subclasses date, so a class is matched by identity.
_CLASSES: tuple[tuple[type, str], ...] = (
    (str, "str"),
    (int, "int"),
    (float, "float"),
    (bool, "bool"),
    (date, "date"),
    (datetime, "datetime"),
)

#: How a blank key cell is taken: ``refuse`` refuses a row with any, and
#: ``partial`` only a row whose every key cell is blank.
BLANK_KEYS = frozenset({"refuse", "partial"})

#: The column types a sheet holds as serial numbers.
SERIAL_TYPES = frozenset({"date", "datetime"})

# An integer as to_cell writes one: digits with an optional minus sign.
_INTEGER = re.compile(r"-?\d+")

# The strict form of a date: four digits, two, two, and nothing else.
_STRICT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: The column types a schema may declare ``strict`` for.
STRICT_TYPES = frozenset({"bool", "date"})

# Day 0 of a sheet's serial numbers, and the milliseconds in one day.
_SERIAL_EPOCH = datetime(1899, 12, 30)
_DAY_MILLISECONDS = 86_400_000


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


def _header_row(grid: Sequence[Sequence[Any]]) -> list[str]:
    """The header row of a grid as read: canonical strings, stripped."""
    first: Sequence[Any] = grid[0] if grid else []
    return [to_cell(cell).strip() for cell in first]


def _check_type(type_: Any) -> None:
    if not isinstance(type_, str) or type_ not in COLUMN_TYPES:
        raise ValueError(
            f"unknown column type {type_!r}; expected one of {sorted(COLUMN_TYPES)}"
        )


def column_type(type_: ColumnType) -> str:
    """The name of a column type declared by its name or by its class.

    ``"int"`` and ``int`` are both ``"int"``. The classes are ``str``,
    ``int``, ``float``, ``bool``, ``date``, and ``datetime``, matched by
    identity: ``bool`` is ``"bool"`` and not ``"int"``, and a subclass of one
    of them is none of them. Raises ValueError for anything else.
    """
    for declared, name in _CLASSES:
        if type_ is declared:
            return name
    _check_type(type_)
    return str(type_)


def _declared(types: Mapping[str, ColumnType] | None, who: str = "") -> dict[str, str]:
    """Each declared type by name, refusing any that is not a column type.

    Every unknown type is listed in one ValueError, each by its column.
    ``who`` starts each message, for a caller that names itself in its
    errors (``"merge: "``).
    """
    declared: dict[str, str] = {}
    found: list[str] = []
    for column, type_ in (types or {}).items():
        try:
            declared[column] = column_type(type_)
        except ValueError as e:
            found.append(f"{who}column {column!r}: {e}")
    if found:
        raise ValueError("; ".join(found))
    return declared


def from_cell(text: str, type_: ColumnType = "str") -> CellValue:
    """Parse a canonical cell string as the declared ``type_``, a name or a class.

    A blank cell is ``None`` under every type. ``from_cell(to_cell(v), t)``
    returns ``v`` for any ``v`` of type ``t`` but the empty string, which is a
    blank cell and comes back ``None``. Raises ValueError when ``text``
    is not a value of that type: surrounding whitespace, digit-group
    underscores, and a fractional ``int`` are refused rather than coerced, so a
    cell never silently changes meaning.

    - ``int``: digits with an optional leading minus
    - ``float``: anything Python's ``float`` reads (``3``, ``2.5``, ``1e-07``)
    - ``bool``: ``TRUE`` or ``FALSE``, in any case
    - ``date`` / ``datetime``: ISO 8601, as ``date.isoformat`` and ``str`` of a
      datetime write them
    """
    type_ = column_type(type_)
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


def serial_to_cell(number: float, type_: ColumnType) -> str:
    """The canonical cell string of a date or datetime given as a serial number.

    A sheet holds a date as the count of days since 1899-12-30, with the time
    of day as the fraction, and returns that number under the
    ``SERIAL_NUMBER`` render. A ``datetime`` is rounded to the millisecond,
    which a serial keeps exactly, and written in one fixed-width form,
    ``YYYY-MM-DD HH:MM:SS.mmm``, whole seconds included, so every cell of a
    column has one width. :func:`from_cell` reads it back, and it compares
    equal to :func:`to_cell`'s form of the same moment under
    :func:`normalize_cell`. A ``date`` takes a serial with no time of day.
    The value is naive: a serial carries no time zone, and is in the
    spreadsheet's own.

    Raises ValueError for a type that is neither, a ``date`` serial holding a
    time of day, a value that is not a number (a boolean is not one here), and
    a number no date can hold.
    """
    name = column_type(type_)
    if name not in SERIAL_TYPES:
        raise ValueError(f"a serial number is a date or a datetime, not {name!r}")
    problem = f"{number!r} is not a valid {name} serial"
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise ValueError(problem)
    try:
        elapsed = timedelta(milliseconds=round(number * _DAY_MILLISECONDS))
        moment = _SERIAL_EPOCH + elapsed
    except (ValueError, OverflowError):
        raise ValueError(problem) from None
    if name == "datetime":
        return moment.isoformat(sep=" ", timespec="milliseconds")
    if moment.time() != time():
        raise ValueError(f"{problem}: it holds a time of day")
    return to_cell(moment.date())


def normalize_cell(text: str, type_: ColumnType = "str") -> str:
    """``text`` as :func:`to_cell` writes its value, for comparing two cells.

    Two cell strings can differ and mean one value: ``true`` and ``TRUE`` in
    a ``bool`` column, ``3.0`` and ``3`` in a ``float`` column. A cell that
    parses as ``type_`` is returned in the one form its value has; a cell that
    does not is returned unchanged, to be compared as text. Parsing is as
    strict as :func:`from_cell`: nothing is coerced. Only comparisons use
    this form; the stored text is never rewritten.
    """
    name = column_type(type_)
    try:
        return to_cell(from_cell(text, name))
    except ValueError:
        return text


# -- typed rows --


def encode_rows(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None
) -> list[dict[str, str]]:
    """Turn rows of typed values into records of canonical cell strings.

    Every value goes through :func:`to_cell`. Each record holds ``columns``
    in that order, with a column its row lacks blank; ``columns=None`` takes
    every key of every row, in first-seen order. Column names are used as
    given: a row built in code has the names its code gave it.

    Raises one ValueError listing every problem, each by the row's 1-based
    position: a ``list`` or ``dict`` value, which is not a cell, and a column
    that ``columns`` does not name, which would be dropped.
    """
    if columns is None:
        names = list(dict.fromkeys(column for row in rows for column in row))
    else:
        names = list(columns)
    known = set(names)
    found: list[str] = []
    records: list[dict[str, str]] = []
    for position, row in enumerate(rows, start=1):
        found.extend(
            f"row {position}, column {column!r}: nested values are not cells"
            for column, value in row.items()
            if column in known and isinstance(value, (list, dict))
        )
        unknown = [column for column in row if column not in known]
        if unknown:
            found.append(f"row {position} has unknown columns {unknown}")
        records.append({column: to_cell(row.get(column)) for column in names})
    if found:
        raise ValueError("; ".join(found))
    return records


def decode_rows(
    records: Sequence[Mapping[str, str]], types: Mapping[str, ColumnType]
) -> list[dict[str, CellValue]]:
    """Parse records of canonical cell strings into rows of typed values.

    Each cell is parsed by :func:`from_cell` as its column's type in
    ``types``, a name or a class, and as ``str`` for a column ``types`` does
    not name. A blank cell is ``None``.

    ``decode_rows(encode_rows(rows), types) == rows`` for rows whose values
    match ``types``, with two exceptions: a value of ``""`` comes back
    ``None``, and so does a column a row lacked.

    Raises one ValueError listing every cell that does not parse, each by the
    row's 1-based position and its column, and an unknown type in ``types``
    whatever the records hold.
    """
    declared = _declared(types)
    found: list[str] = []
    rows: list[dict[str, CellValue]] = []
    for position, record in enumerate(records, start=1):
        row: dict[str, CellValue] = {}
        for column, text in record.items():
            try:
                row[column] = from_cell(text, declared.get(column, "str"))
            except ValueError as e:
                found.append(f"row {position}, column {column!r}: {e}")
        rows.append(row)
    if found:
        raise ValueError("; ".join(found))
    return rows


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


def check_blank_keys(blank_keys: str) -> None:
    """Refuse a ``blank_keys`` setting that is not one of :data:`BLANK_KEYS`."""
    if blank_keys not in BLANK_KEYS:
        raise ValueError(
            f"blank_keys must be one of {sorted(BLANK_KEYS)}, not {blank_keys!r}"
        )


def index_rows(
    rows: Sequence[Mapping[str, str]],
    key: Sequence[str],
    *,
    side: str,
    numbers: Sequence[int] | None = None,
    blank_keys: str = "refuse",
) -> dict[tuple[str, ...], int]:
    """Map each row's normalized key to its row number, refusing bad keys.

    ``numbers`` labels each row (a tab passes its 1-based spreadsheet rows);
    without it a row is labelled by its 1-based position in ``rows``. Raises
    ValueError naming ``side`` (``"local"``, ``"tab 'Members'"``) when any row
    has a blank key cell, or when two rows share a key, listing every such
    row at once so a single run shows everything to fix.

    ``blank_keys="partial"`` is for a composite key of which a component is
    absent on some rows, where the others still identify the row: a row is
    refused only when its every key cell is blank. Two rows share a key when
    their components agree, blank ones included. With a one-column key the
    two settings are the same.
    """
    check_blank_keys(blank_keys)
    if not key:
        raise ValueError(f"{side}: no key columns to index rows by")
    labels = list(numbers) if numbers is not None else list(range(1, len(rows) + 1))
    index: dict[tuple[str, ...], int] = {}
    blank: list[int] = []
    duplicates: dict[tuple[str, ...], list[int]] = {}
    for label, row in zip(labels, rows, strict=True):
        found = row_key(row, key)
        if not any(found) or (blank_keys == "refuse" and "" in found):
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
    blank cell is checked by ``required``, never by ``allowed``. ``type`` is
    the type's name; :meth:`of` takes a class as well.

    ``present`` and ``strict`` are checked outside :func:`cell_problem`'s
    per-cell rules, by a caller that has the column's header to check against
    (:mod:`~gdrives.sheets.sync`); ``present`` says nothing about a cell's
    value, only that the column must be declared in the header. ``strict`` is
    for ``bool`` and ``date`` columns, and is refused for any other type, here
    and in the config check: ``TRUE``/``FALSE`` only for ``bool``, and
    ``YYYY-MM-DD`` only for ``date``. A value that fails it is a schema
    problem like any other, from :func:`cell_problem`, so ``on_invalid:
    "hold"`` holds it; comparison is unchanged, so a respelling
    (``true``/``TRUE``) is a problem, not an edit.
    """

    type: str = "str"
    required: bool = False
    allowed: Collection[Any] | None = None
    present: bool = False
    strict: bool = False

    def __post_init__(self) -> None:
        _check_type(self.type)
        if self.strict and self.type not in STRICT_TYPES:
            raise ValueError(
                f"strict is only for a column of {sorted(STRICT_TYPES)}, "
                f"not {self.type!r}"
            )

    @classmethod
    def of(
        cls,
        type_: ColumnType = "str",
        *,
        required: bool = False,
        allowed: Collection[Any] | None = None,
        present: bool = False,
        strict: bool = False,
    ) -> "ColumnSchema":
        """A schema whose type is given by name or by class (``int``, ``date``).

        The name is what is stored, so the result equals the schema built
        from the name.
        """
        return cls(
            type=column_type(type_),
            required=required,
            allowed=allowed,
            present=present,
            strict=strict,
        )


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


def cell_problem(text: str, schema: ColumnSchema) -> str | None:
    """Why ``text`` does not fit ``schema``, or None when it does.

    The reason is the text :func:`problems` reports for the cell. With
    ``schema.strict``, a ``bool`` cell must be ``TRUE`` or ``FALSE`` and a
    ``date`` cell must be ``YYYY-MM-DD``, both exactly; a cell that parses as
    the type but not in that exact form is a problem under ``strict`` and
    passes without it. A ``date`` cell read from its serial number
    (:func:`serial_to_cell`) already arrives in that form.
    """
    if text == "":
        return "is required" if schema.required else None
    try:
        from_cell(text, schema.type)
    except ValueError as e:
        return str(e)
    if schema.strict:
        if schema.type == "bool" and text not in ("TRUE", "FALSE"):
            return f"{text!r} is not TRUE or FALSE, and the column is strict"
        if schema.type == "date" and not _STRICT_DATE.fullmatch(text):
            return f"{text!r} is not YYYY-MM-DD, and the column is strict"
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
            reason = cell_problem(text, spec)
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
