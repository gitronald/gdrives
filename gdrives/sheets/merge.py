"""The three-way merge of a local record file and a sheet tab, by row key.

:func:`merge` is a pure function: it reads three lists of records (the base
both sides held after the last applied sync, the local file, and the sheet)
and returns a :class:`MergePlan` saying what to write where. It writes nothing
and never changes its inputs.

Per cell, for a row on both sides, with base ``b``, local ``l``, and sheet
``r``: ``l == r`` is in sync, ``r == b`` is a local edit (pushed), ``l == b``
is a sheet edit (folded into the local file), and anything else is a conflict,
reported with the base left at ``b`` so it is reported again on every run
until a person makes the two sides agree. A row on both sides but not in the
base merges against a base of blanks.

Per row, against the base: a row on one side only is new (appended to the
sheet, or folded into the local file) when the base lacks it, and deleted on
the other side (flagged, never applied) when the base has it. Neither side's
rows are ever removed.

Records are ``dict[str, str]`` of canonical cell strings (see
:mod:`gdrives.sheets.cells`); a cell a record lacks counts as blank. Rows are
matched by their normalized key (:func:`~gdrives.sheets.cells.row_key`), and
every plan entry names its row by that key.

Three options refine the cell rule without changing it. ``types`` compares
the cells of a declared column as values of its type, so ``l == r`` holds for
two spellings of one value. ``schema`` holds a sheet value that fails it
instead of folding it. ``carry`` names the local columns outside the
projection.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from gdrives.sheets.cells import (
    ColumnSchema,
    ColumnType,
    cell_problem,
    check_blank_keys,
    column_type,
    index_rows,
    normalize_cell,
    row_key,
)

#: The sides a plan entry can keep, and ``prefer`` can name.
SIDES = frozenset({"local", "sheet"})

#: Why an :class:`Override` discarded a value.
OVERRIDE_REASONS = frozenset({"local_owned", "sheet_owned", "prefer"})

#: The row flags: a row one side lost, one the sheet added (``owns_rows``), or
#: one the sheet added with a cell that fails the schema.
ROW_FLAGS = frozenset(
    {"remote_deleted", "local_deleted", "remote_added", "remote_invalid"}
)

_Record = Mapping[str, str]
_Key = tuple[str, ...]


@dataclass(frozen=True)
class Cell:
    """One projection cell of one row, with its value on each side.

    ``base`` is blank for a row the base lacks. A push writes ``local`` to the
    sheet; a fold writes ``sheet`` into the local file; a conflict writes
    neither.
    """

    key: _Key
    column: str
    base: str
    local: str
    sheet: str


@dataclass(frozen=True)
class Override:
    """A changed value an ownership rule or ``prefer`` discarded.

    ``kept`` is the side whose value won (``"local"`` or ``"sheet"``), and
    ``reason`` the rule that chose it: ``"local_owned"``, ``"sheet_owned"``, or
    ``"prefer"``. The value on the other side is the one discarded.
    """

    key: _Key
    column: str
    base: str
    local: str
    sheet: str
    kept: str
    reason: str


@dataclass(frozen=True)
class HeldCell:
    """A sheet value that fails its column's schema, held instead of folded.

    The fields are those of :class:`Cell`, with ``reason`` saying why the
    ``sheet`` value does not fit, as :func:`~gdrives.sheets.cells.problems`
    words it. ``base`` and ``local`` are blank for a cell of a new sheet row.
    """

    key: _Key
    column: str
    base: str
    local: str
    sheet: str
    reason: str


@dataclass(frozen=True)
class NewRow:
    """A row to add to one side: its key and its projection cells, in order."""

    key: _Key
    values: dict[str, str]


@dataclass(frozen=True)
class RowFlag:
    """A row that needs a person: ``flag`` is one of :data:`ROW_FLAGS`."""

    key: _Key
    flag: str


@dataclass(frozen=True)
class MergePlan:
    """What a merge found, and the records each side should hold after it.

    - ``pushes``: cells whose ``local`` value is written to the sheet.
    - ``appends``: new local rows to add to the sheet.
    - ``fold_cells``: cells whose ``sheet`` value is written to the local file.
    - ``fold_rows``: new sheet rows added to the local file.
    - ``conflicts``: cells changed differently on both sides; neither is written.
    - ``overrides``: changed values an ownership rule or ``prefer`` discarded.
    - ``row_flags``: rows deleted on one side, added on a row-owning sheet,
      or added on the sheet with an invalid cell.
    - ``held``: sheet values that fail the schema; neither side takes them.

    ``new_local`` is the local file after the merge: its rows in order, with
    rows folded from the sheet after them. ``new_base`` is the next base, in
    the same order, holding the projection columns only. Rows a flag names
    keep their base row, so the flag repeats until a person resolves it; the
    base rows of rows deleted locally come last, in sheet order.
    """

    pushes: list[Cell] = field(default_factory=list)
    appends: list[NewRow] = field(default_factory=list)
    fold_cells: list[Cell] = field(default_factory=list)
    fold_rows: list[NewRow] = field(default_factory=list)
    conflicts: list[Cell] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)
    row_flags: list[RowFlag] = field(default_factory=list)
    held: list[HeldCell] = field(default_factory=list)
    new_local: list[dict[str, str]] = field(default_factory=list)
    new_base: list[dict[str, str]] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        """True when a conflict, a row flag, or a held cell is left for a person."""
        return bool(self.conflicts or self.row_flags or self.held)

    @property
    def sheet_writes(self) -> bool:
        """True when the plan writes to the sheet: a push or a new row."""
        return bool(self.pushes or self.appends)

    @property
    def local_writes(self) -> bool:
        """True when the plan writes to the local side: a folded cell or row."""
        return bool(self.fold_cells or self.fold_rows)

    @property
    def has_writes(self) -> bool:
        """True when the plan writes to either side. A held cell is no write."""
        return self.sheet_writes or self.local_writes


def _check_columns(
    key: Sequence[str],
    columns: Sequence[str],
    local_owned: Collection[str],
    sheet_owned: Collection[str],
    prefer: str | None,
) -> None:
    """Refuse a malformed merge request, before any row is looked at."""
    if not key:
        raise ValueError("merge: no key columns to match rows by")
    repeated = sorted({c for c in columns if list(columns).count(c) > 1})
    if repeated:
        raise ValueError(f"merge: column(s) {repeated} named twice")
    outside = [k for k in key if k not in columns]
    if outside:
        raise ValueError(f"merge: key column(s) {outside} not in columns")
    for name, owned in (("local_owned", local_owned), ("sheet_owned", sheet_owned)):
        keyed = sorted(c for c in owned if c in key)
        if keyed:
            raise ValueError(f"merge: key column(s) {keyed} cannot be {name}")
        stray = sorted(c for c in owned if c not in columns)
        if stray:
            raise ValueError(f"merge: {name} column(s) {stray} not in columns")
    both = sorted(set(local_owned) & set(sheet_owned))
    if both:
        raise ValueError(f"merge: column(s) {both} both local_owned and sheet_owned")
    if prefer is not None and prefer not in SIDES:
        raise ValueError(
            f"merge: prefer must be one of {sorted(SIDES)} or None, not {prefer!r}"
        )


def _project(record: _Record, columns: Sequence[str]) -> dict[str, str]:
    """``record``'s projection cells, in ``columns`` order, blank when missing."""
    return {column: record.get(column, "") for column in columns}


def _by_key(
    rows: Sequence[_Record], key: Sequence[str], side: str, blank_keys: str
) -> dict[_Key, _Record]:
    """Map each row's normalized key to the row, refusing blank or repeated keys."""
    index_rows(rows, key, side=side, blank_keys=blank_keys)
    return {row_key(row, key): row for row in rows}


def _carried(
    carry: Sequence[str] | None, local: Sequence[_Record], columns: Sequence[str]
) -> list[str]:
    """The carried columns: the ones named, or every local column outside
    the projection."""
    if carry is None:
        return list(dict.fromkeys(c for row in local for c in row if c not in columns))
    repeated = sorted({c for c in carry if list(carry).count(c) > 1})
    if repeated:
        raise ValueError(f"merge: carry column(s) {repeated} named twice")
    shared = [c for c in carry if c in columns]
    if shared:
        raise ValueError(f"merge: carry column(s) {shared} are in columns")
    return list(carry)


def _declared(
    types: Mapping[str, ColumnType] | None, cells: Sequence[str]
) -> dict[str, str]:
    """The declared type, by name, of each cell column that has one."""
    declared: dict[str, str] = {}
    for column, type_ in (types or {}).items():
        try:
            name = column_type(type_)
        except ValueError as e:
            raise ValueError(f"merge: column {column!r}: {e}") from None
        if column in cells:
            declared[column] = name
    return declared


@dataclass(frozen=True)
class _Rules:
    """What a merge was asked for, as its helpers need it."""

    columns: Sequence[str]
    cells: Sequence[str]
    local_owned: Collection[str]
    sheet_owned: Collection[str]
    prefer: str | None
    types: Mapping[str, str]
    schema: Mapping[str, ColumnSchema]
    fill: Sequence[str]

    def normal(self, column: str, text: str) -> str:
        """``text`` in the form cells of ``column`` are compared in."""
        if column not in self.types:
            return text
        return normalize_cell(text, self.types[column])

    def problem(self, column: str, text: str) -> str | None:
        """Why a sheet value does not fit its column's schema, if it does not."""
        if column not in self.schema:
            return None
        return cell_problem(text, self.schema[column])

    def local_row(self, row: _Record) -> dict[str, str]:
        """A copy of a local row, holding every carried column that was named."""
        return {**row, **{c: row.get(c, "") for c in self.fill}}


def merge(
    base: Sequence[_Record],
    local: Sequence[_Record],
    remote: Sequence[_Record],
    key: Sequence[str],
    columns: Sequence[str],
    *,
    local_owned: Collection[str] = (),
    sheet_owned: Collection[str] = (),
    owns_rows: bool = False,
    prefer: str | None = None,
    blank_keys: str = "refuse",
    types: Mapping[str, ColumnType] | None = None,
    schema: Mapping[str, ColumnSchema] | None = None,
    carry: Sequence[str] | None = None,
) -> MergePlan:
    """Merge ``local`` and ``remote`` (the sheet) against ``base``, by ``key``.

    ``columns`` is the projection: the columns the two sides share, key
    included. Local columns outside it are **carried**: they pass through
    unchanged on existing local rows, are blank on rows folded from the sheet,
    and never reach the sheet or the base. Key columns are identity: they are
    compared normalized, never pushed, folded, or reported, and each side keeps
    its own stored key text.

    Ownership overrides the cell rule. A ``local_owned`` column always pushes
    the local value, and a ``sheet_owned`` column always folds the sheet value;
    a changed value that loses is reported in ``overrides``. A new local row is
    appended with its ``sheet_owned`` cells blank, on both sides. With
    ``owns_rows``, a new sheet row is flagged ``remote_added`` instead of being
    folded in. ``prefer`` (``"local"`` or ``"sheet"``) resolves each cell
    conflict toward that side, reported as an override; it never affects row
    flags.

    ``blank_keys="partial"`` lets a row of a composite key leave a component
    blank, on every side; see :func:`~gdrives.sheets.cells.index_rows`.

    ``types`` declares column types, by name or class, and the cells of a
    declared column are **compared** in the form of their type
    (:func:`~gdrives.sheets.cells.normalize_cell`): ``true`` and ``TRUE`` in a
    ``bool`` column are one value, so a respelling is not an edit, and cannot
    turn the other side's edit into a conflict. Only comparison changes. A
    push sends the local side's stored text and a fold takes the sheet's, and
    two sides that agree are in sync, with the base taking the local text. A
    cell that does not parse as its type is compared as text. Key columns are
    never compared by type: ``007`` and ``7`` are one ``int`` and two keys.

    ``schema`` holds sheet values that fail it. A sheet cell that would be
    folded (a sheet edit, a ``sheet_owned`` column, a conflict that ``prefer``
    resolves toward the sheet) but does not fit its column's schema is listed
    in ``held`` instead: the local side and the base keep their values, so it
    is held again on every run until the sheet is corrected. A new sheet row
    with any such cell is flagged ``remote_invalid`` and not folded, with its
    cells in ``held``. The local side is never held, and with no ``schema``
    every value folds.

    ``carry`` names the carried columns, and every row of ``new_local`` then
    holds each of them, blank where its row lacked it. None infers them from
    the local rows, which leaves none to infer when there are no rows.

    Raises ValueError when a side (``"base"``, ``"local"``, or ``"sheet"``) has
    a blank or repeated key, when the key is empty or outside ``columns``, when
    an ownership set names a key column, a column outside ``columns``, or a
    column the other set also names, when ``prefer`` is not a side, when
    ``types`` holds an unknown type, or when ``carry`` repeats a column or
    names one of ``columns``.
    """
    _check_columns(key, columns, local_owned, sheet_owned, prefer)
    check_blank_keys(blank_keys)
    cells = [c for c in columns if c not in key]
    carried = _carried(carry, local, columns)
    rules = _Rules(
        columns=columns,
        cells=cells,
        local_owned=local_owned,
        sheet_owned=sheet_owned,
        prefer=prefer,
        types=_declared(types, cells),
        schema={c: spec for c, spec in (schema or {}).items() if c in columns},
        fill=carried if carry is not None else (),
    )
    base_rows = _by_key(base, key, "base", blank_keys)
    local_rows = _by_key(local, key, "local", blank_keys)
    sheet_rows = _by_key(remote, key, "sheet", blank_keys)
    plan = MergePlan()

    for found, row in local_rows.items():
        sheet = sheet_rows.get(found)
        before = base_rows.get(found)
        if sheet is None and before is not None:
            plan.row_flags.append(RowFlag(key=found, flag="remote_deleted"))
            plan.new_local.append(rules.local_row(row))
            plan.new_base.append(_project(before, columns))
        elif sheet is None:
            _append(plan, found, row, rules)
        else:
            _merge_row(plan, found, row, sheet, before or {}, rules)

    # Base rows kept for rows deleted locally go last, after the folded rows,
    # so the next run (where those rows are local) builds the same order.
    kept: list[dict[str, str]] = []
    for found, sheet in sheet_rows.items():
        if found in local_rows:
            continue
        before = base_rows.get(found)
        if before is not None:
            plan.row_flags.append(RowFlag(key=found, flag="local_deleted"))
            kept.append(_project(before, columns))
        elif owns_rows:
            plan.row_flags.append(RowFlag(key=found, flag="remote_added"))
        else:
            _fold_row(plan, found, sheet, rules, carried)
    plan.new_base.extend(kept)
    return plan


def _fold_row(
    plan: MergePlan,
    found: _Key,
    sheet: _Record,
    rules: _Rules,
    carried: Sequence[str],
) -> None:
    """Add a new sheet row to the local side, or hold it for its invalid cells."""
    values = _project(sheet, rules.columns)
    invalid = [
        HeldCell(key=found, column=column, base="", local="", sheet=text, reason=reason)
        for column, text in values.items()
        if (reason := rules.problem(column, text)) is not None
    ]
    if invalid:
        plan.row_flags.append(RowFlag(key=found, flag="remote_invalid"))
        plan.held.extend(invalid)
        return
    plan.fold_rows.append(NewRow(key=found, values=values))
    plan.new_local.append({**values, **dict.fromkeys(carried, "")})
    plan.new_base.append(dict(values))


def _append(plan: MergePlan, found: _Key, row: _Record, rules: _Rules) -> None:
    """Add a new local row to the plan, its sheet-owned cells blank everywhere."""
    values = _project(row, rules.columns)
    new_local = rules.local_row(row)
    for column in rules.columns:
        if column not in rules.sheet_owned:
            continue
        if values[column] != "":
            plan.overrides.append(
                Override(
                    key=found,
                    column=column,
                    base="",
                    local=values[column],
                    sheet="",
                    kept="sheet",
                    reason="sheet_owned",
                )
            )
        values[column] = ""
        new_local[column] = ""
    plan.appends.append(NewRow(key=found, values=values))
    plan.new_local.append(new_local)
    plan.new_base.append(dict(values))


def _decide(cell: Cell, rules: _Rules) -> tuple[str | None, str | None]:
    """Which side's value ``cell`` keeps (None for a conflict), and why an override.

    The values are compared in the form of the column's declared type. The
    reason is None unless a changed value on the other side is discarded.
    """
    base, local, sheet = (
        rules.normal(cell.column, text) for text in (cell.base, cell.local, cell.sheet)
    )
    if local == sheet:
        return "local", None
    if cell.column in rules.local_owned:
        return "local", "local_owned" if sheet != base else None
    if cell.column in rules.sheet_owned:
        return "sheet", "sheet_owned" if local != base else None
    if sheet == base:
        return "local", None
    if local == base:
        return "sheet", None
    if rules.prefer is not None:
        return rules.prefer, "prefer"
    return None, None


def _merge_row(
    plan: MergePlan,
    found: _Key,
    row: _Record,
    sheet: _Record,
    before: _Record,
    rules: _Rules,
) -> None:
    """Merge one row both sides hold, cell by cell, against its base row.

    ``before`` is the row's base, empty when the base lacks it.
    """
    new_local = rules.local_row(row)
    # The base keeps the local key text: each side keeps its own.
    new_base = _project(row, rules.columns)
    for column in rules.cells:
        cell = Cell(
            key=found,
            column=column,
            base=before.get(column, ""),
            local=row.get(column, ""),
            sheet=sheet.get(column, ""),
        )
        kept, reason = _decide(cell, rules)
        problem = rules.problem(column, cell.sheet) if kept == "sheet" else None
        if problem is not None:
            # Held: neither side takes the value, and nothing was discarded.
            new_base[column] = cell.base
            plan.held.append(
                HeldCell(
                    key=found,
                    column=column,
                    base=cell.base,
                    local=cell.local,
                    sheet=cell.sheet,
                    reason=problem,
                )
            )
            continue
        if kept == "local":
            new_base[column] = cell.local
            # In sync when the two agree in the form they are compared in.
            if rules.normal(column, cell.local) != rules.normal(column, cell.sheet):
                plan.pushes.append(cell)
        elif kept == "sheet":
            new_base[column] = cell.sheet
            new_local[column] = cell.sheet
            plan.fold_cells.append(cell)
        else:
            new_base[column] = cell.base
            plan.conflicts.append(cell)
        if kept is not None and reason is not None:
            plan.overrides.append(
                Override(
                    key=cell.key,
                    column=cell.column,
                    base=cell.base,
                    local=cell.local,
                    sheet=cell.sheet,
                    kept=kept,
                    reason=reason,
                )
            )
    plan.new_local.append(new_local)
    plan.new_base.append(new_base)
