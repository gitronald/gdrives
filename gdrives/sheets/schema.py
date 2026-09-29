"""A config target's schemas as rows of documentation.

:func:`schema_rows` turns the ``schema`` of a target's tabs into one row per
declared column, with its type, its rules, and its description, ready for
:func:`~gdrives.sheets.files.write_records` (a ``.csv``, ``.tsv``, or
``.json`` file) or :func:`format_schema` (CSV text). It reads the config's
objects only and makes no request.
"""

import csv
import io
import json
from collections.abc import Sequence
from typing import Any

from gdrives.local import escape_formula
from gdrives.sheets.cells import to_cell
from gdrives.sheets.config import Target

#: The columns of a row of :func:`schema_rows`, in the order they are written.
SCHEMA_COLUMNS = (
    "tab",
    "column",
    "key",
    "type",
    "required",
    "present",
    "strict",
    "allowed",
    "pattern",
    "description",
)


def _allowed(values: Any) -> str:
    """``values`` as canonical cell strings in a JSON array, or blank for None.

    A list or tuple keeps its order; a set is sorted, so the text is the same
    on every run. The array reads back unambiguously whatever the values hold.
    """
    if values is None:
        return ""
    cells = [to_cell(value) for value in values]
    if isinstance(values, (set, frozenset)):
        cells.sort()
    return json.dumps(cells, ensure_ascii=False)


def schema_rows(
    target: Target, tabs: Sequence[str] | None = None
) -> list[dict[str, str]]:
    """One row per declared schema column of ``target``'s tabs (or just ``tabs``).

    The rows follow the tabs' order in the config, and each tab's schema
    order; every row holds the :data:`SCHEMA_COLUMNS` as strings. ``key`` is
    ``TRUE`` for a column in the tab's ``key``, and ``required``,
    ``present``, and ``strict`` are ``TRUE`` or ``FALSE``, as cells are.
    ``allowed`` is the permitted values as a JSON array of canonical cell
    strings, and ``pattern`` and ``description`` are blank when not set.

    Only the columns a ``schema`` declares are listed: a column of ``columns``
    or of ``key`` that it leaves out is not, since a config does not know the
    columns of a tab it does not project, and half a list would mislead. A
    tab with an empty schema has no rows. Raises ValueError for a tab not in
    the target, and for one whose ``schema_ref`` is unresolved, which
    :func:`~gdrives.sheets.hooks.resolve_schemas` fills.
    """
    wanted = [target.tab(title) for title in tabs] if tabs is not None else target.tabs
    rows: list[dict[str, str]] = []
    for tab in wanted:
        if tab.schema_ref is not None:
            raise ValueError(
                f"tab {tab.title!r}: schema {tab.schema_ref!r} is not resolved; "
                "resolve it first (resolve_schemas)"
            )
        for column, spec in tab.schema.items():
            rows.append(
                {
                    "tab": tab.title,
                    "column": column,
                    "key": to_cell(column in tab.key),
                    "type": spec.type,
                    "required": to_cell(spec.required),
                    "present": to_cell(spec.present),
                    "strict": to_cell(spec.strict),
                    "allowed": _allowed(spec.allowed),
                    "pattern": spec.pattern or "",
                    "description": spec.description or "",
                }
            )
    return rows


def format_schema(
    rows: Sequence[dict[str, str]], *, escape_formulas: bool = False
) -> str:
    """``rows`` of :func:`schema_rows` as CSV text with a header row and LF endings.

    ``escape_formulas`` passes every cell through
    :func:`~gdrives.local.escape_formula`, for text a spreadsheet application
    will open, since a ``pattern`` or a ``description`` may start with ``=``,
    ``+``, ``-``, or ``@``. Off by default, so the values are exact.
    """
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=SCHEMA_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(
        {key: escape_formula(cell) for key, cell in row.items()}
        if escape_formulas
        else row
        for row in rows
    )
    return out.getvalue()
