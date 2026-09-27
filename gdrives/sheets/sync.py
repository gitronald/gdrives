"""Keep a tab and a local file in step: a keyed sync, a whole-tab pull or push.

The functions here take a Sheets ``service``, a spreadsheet ID, and the
:mod:`~gdrives.sheets.config` dataclasses, and return a :class:`TabReport`
per tab. Every one of them previews by default: it reads the sheet and the
local files and says what it would do, without writing anything anywhere or
creating a directory. The writes happen only in :func:`apply_tab` and with
``apply=True``.

The local side of a tab and its base are reached through a
:class:`~gdrives.sheets.stores.Store`: the file a config names, or a store a
caller gives the tab (``TabConfig.store``, ``Target.base_stores``).

A **sync** tab is merged three ways (:func:`~gdrives.sheets.merge.merge`)
against its base snapshot, one CSV per tab under the target's ``base``
directory. :func:`plan_tab` reads and merges; :func:`apply_tab` writes, in an
order that is a safety property, stopping at the first failure:

1. The schema, ``validate``, and ``check`` checks, on the local rows and on
   the merged result. Any problem means nothing is written. These are the
   only checks: every later step writes what they passed.
2. The structure steps asked for: create a missing tab with its header row,
   add missing columns, delete extra ones. The tab is then read and merged
   again, and the run stops if that merge differs from the checked one.
3. :func:`~gdrives.sheets.apply.apply_plan`: the re-read guard, the pushed
   cells, the new rows, and the read-back check.
4. The local store, then the base store, then the column widths.

A caller's own checks are three hooks. ``validate`` takes rows, ``check`` a
:class:`CheckContext` (the rows, the columns of both sides, and the merge),
and both block a write. ``warn`` takes a :class:`CheckContext` too, runs once
after every blocking check has passed, and blocks nothing.

A run that fails part way has recorded no sync that did not land: a failed
guard or read-back leaves the local file and the base as they were, and the
next run sees a partial sheet write as already in sync.

A **pull** tab (:func:`pull_tab`) replaces its local file with the tab, and a
**push** tab (:func:`push_tab`) replaces the tab's values with its local file.
:func:`push_rows` is the push of rows held in memory, with no config.
:func:`pull_all_tabs` dumps every tab of a spreadsheet with no config.
:func:`run_target` runs every tab of one mode of a target and collects a
:class:`SyncReport`, whose ``exit_code`` tells success, failure, and work
left for a person apart; :func:`format_report` renders it as text.
"""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

from gdrives.files import Service
from gdrives.local import printable, safe_filename
from gdrives.sheets.a1 import a1_quote, column_letter
from gdrives.sheets.apply import (
    ApplyResult,
    ReadBackError,
    SheetChangedError,
    apply_plan,
    insert_point,
)
from gdrives.sheets.cells import (
    ColumnSchema,
    index_rows,
    problems,
    row_key,
    to_cell,
)
from gdrives.sheets.config import (
    LOCAL_EXTENSIONS,
    MODES,
    TabConfig,
    Target,
)
from gdrives.sheets.files import Records, read_records, write_records
from gdrives.sheets.merge import SIDES, Cell, MergePlan, merge
from gdrives.sheets.stores import FileStore, Store
from gdrives.sheets.structure import (
    add_columns,
    delete_columns,
    ensure_tabs,
    place_columns,
    set_column_widths,
    strip_links,
)
from gdrives.sheets.table import (
    EmptyTabError,
    Serials,
    Table,
    parse_tab,
    pull_serials,
)
from gdrives.sheets.values import (
    FORMATTED_STRING,
    RAW,
    UNFORMATTED_VALUE,
    batch_update_spreadsheet,
    list_tabs,
    pull_many,
    pull_values,
    tab_grid,
    update_values,
)

#: A caller's own check: rows in, one message per problem out.
Validate = Callable[[Sequence[Mapping[str, str]]], list[str]]

#: The stages a check runs at: the local rows of a sync or a push, the merged
#: result of a sync, and the rows a pull read from the sheet.
STAGES = frozenset({"local", "merged", "sheet"})


@dataclass(frozen=True)
class CheckContext:
    """What a ``check`` or a ``warn`` hook is given: the rows, and what is around them.

    ``stage`` is where ``rows`` come from, one of :data:`STAGES`, and
    ``columns`` the columns they hold. ``projection`` is the columns the
    sheet carries. ``sheet_columns`` is the sheet header's named columns, or
    None when the sheet has not been read (the local stage runs before any
    request) or the tab is missing or has no header. ``adding`` and
    ``dropping`` are the columns this run adds to the sheet and deletes from
    it. ``plan`` is the merge at the merged stage, and None at the others.
    """

    tab: str
    stage: str
    rows: Sequence[Mapping[str, str]]
    columns: tuple[str, ...]
    projection: tuple[str, ...]
    sheet_columns: tuple[str, ...] | None = None
    adding: tuple[str, ...] = ()
    dropping: tuple[str, ...] = ()
    plan: MergePlan | None = None

    @property
    def extra_columns(self) -> tuple[str, ...]:
        """The sheet's columns outside the projection, less the ones being dropped.

        A column a run is about to delete is not one to check: the usual
        reason to drop it is that nothing declares it any more.
        """
        outside = (*self.projection, *self.dropping)
        return tuple(c for c in self.sheet_columns or () if c not in outside)


#: A caller's own check with the context: one message per problem out.
Check = Callable[[CheckContext], list[str]]

_Key = tuple[str, ...]

#: The errors a run reports per tab instead of stopping: a refusal or a
#: failed guard (ValueError), an API error, or a local file error.
TAB_ERRORS = (ValueError, HttpError, OSError)


@dataclass(frozen=True)
class Replacement:
    """What a whole-file pull or a whole-tab push replaces on its destination.

    ``before_rows`` and ``before_cells`` count the destination's rows and
    non-blank cells (header excluded), ``after_rows`` the rows written. With
    a key (``keyed``), ``added``, ``removed``, and ``changed`` list rows by
    key: ``removed`` rows are the ones the write discards. ``dropped_columns``
    are destination columns the write does not carry, each with its count of
    non-blank cells. ``unchanged`` is True when the write would change
    nothing, so none is made.
    """

    before_rows: int
    after_rows: int
    before_cells: int = 0
    keyed: bool = False
    added: list[_Key] = field(default_factory=list)
    removed: list[_Key] = field(default_factory=list)
    changed: list[_Key] = field(default_factory=list)
    dropped_columns: dict[str, int] = field(default_factory=dict)
    unchanged: bool = False

    @property
    def row_drop(self) -> int:
        """How many rows fewer the destination holds after the write."""
        return max(0, self.before_rows - self.after_rows)


@dataclass
class TabReport:
    """What one run did, or would do, to one tab and its local file.

    ``local`` is the tab's local file and ``local_label`` the label of its
    local store, which is the file's path for a file.
    ``apply`` says whether writes were asked for; a preview has it False and
    writes nothing. ``tab_state`` is ``"present"``, ``"missing"`` (no such
    tab), or ``"empty"`` (no header row). ``error`` is set when the run
    stopped: a refusal, an API error, or a failed guard or read-back.
    ``problems`` lists schema, ``validate``, and ``check`` problems, which
    write nothing. ``warnings`` lists what the ``warn`` hook said, which
    blocks nothing and leaves the exit code as it is.

    For a sync tab: ``plan`` is the merge; ``add_columns`` and
    ``drop_columns`` (with non-blank cell counts) are the structure steps
    asked for; ``bootstrapped`` marks a first run that took the local file as
    the base, and ``deferred`` the pushes such a run held back, which go on
    the next run; ``adopted`` marks an ``adopt`` run. On a tab with
    ``insert_above`` and new rows for the sheet, ``insert_row`` is the
    spreadsheet row they go above, as :func:`~gdrives.sheets.apply.insert_point`
    finds it, or None when no row matches and they go after ``last_row``, the
    last row holding anything; both are None otherwise. For pull and push,
    ``replacement`` says what the write replaces. ``applied`` is what
    :func:`~gdrives.sheets.apply.apply_plan` wrote, and the ``wrote_*`` flags
    record the writes made, so a failed run shows how far it got: a structure
    step that landed is flagged even when a later one fails. A single sheet
    write that fails with an API error part way is not flagged; the error
    says what failed.
    """

    tab: str
    mode: str
    local: Path | None = None
    local_label: str | None = None
    apply: bool = False
    tab_state: str = "present"
    error: str | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    plan: MergePlan | None = None
    add_columns: list[str] = field(default_factory=list)
    drop_columns: dict[str, int] = field(default_factory=dict)
    bootstrapped: bool = False
    adopted: bool = False
    deferred: list[Cell] = field(default_factory=list)
    insert_row: int | None = None
    last_row: int | None = None
    replacement: Replacement | None = None
    applied: ApplyResult | None = None
    skipped: bool = False
    wrote_sheet: bool = False
    wrote_local: bool = False
    wrote_base: bool = False
    wrote_widths: bool = False

    @property
    def failed(self) -> bool:
        """True when the run stopped on an error or found problems."""
        return self.error is not None or bool(self.problems)

    @property
    def needs_attention(self) -> bool:
        """True when conflicts, row flags, or held cells are left for a person."""
        return self.plan is not None and self.plan.needs_attention

    @property
    def exit_code(self) -> int:
        """1 for an error or problems, 2 for work left to a person, else 0."""
        if self.failed:
            return 1
        return 2 if self.needs_attention else 0


@dataclass
class SyncReport:
    """The reports of one run, one per tab, and the exit code they add up to."""

    tabs: list[TabReport] = field(default_factory=list)
    target: str | None = None

    @property
    def exit_code(self) -> int:
        """1 if any tab failed, else 2 if any needs a person, else 0.

        0 means in sync, or every change applied (or, in a preview, that the
        run can go ahead); 2 means conflicts, row flags, or held cells remain.
        """
        codes = {tab.exit_code for tab in self.tabs}
        return 1 if 1 in codes else 2 if 2 in codes else 0


@dataclass(frozen=True)
class TabPlan:
    """A sync tab read and merged by :func:`plan_tab`, ready for :func:`apply_tab`.

    ``columns`` is the projection. ``table`` is the tab as read, over the
    projection columns it has (None when the tab is missing or empty), and
    ``plan`` the merge against it (None when the local rows had problems).
    ``base`` is the base file as read, None when there is none yet. The
    options are kept so :func:`apply_tab` can merge again after changing the
    tab's structure.
    """

    target: Target
    tab: TabConfig
    report: TabReport
    columns: list[str]
    local: Records
    base: Records | None
    table: Table | None
    plan: MergePlan | None
    adopt: bool = False
    add_missing: bool = False
    drop_extra: bool = False
    prefer: str | None = None
    validate: Validate | None = None
    check: Check | None = None
    warn: Check | None = None
    created: bool = False
    added: tuple[str, ...] = ()


# -- reading --


def _read_grid(service: Service, spreadsheet_id: str, title: str) -> list[list[Any]]:
    """The whole tab's values, read as :func:`~gdrives.sheets.table.read_tab` reads."""
    return pull_values(
        service,
        spreadsheet_id,
        a1_quote(title),
        render=UNFORMATTED_VALUE,
        date_time_render=FORMATTED_STRING,
    )


def _canonical(grid: Sequence[Sequence[Any]]) -> list[list[str]]:
    """``grid`` as canonical strings, trailing blanks trimmed from rows and the end."""
    rows = [[to_cell(cell) for cell in row] for row in grid]
    for row in rows:
        while row and row[-1] == "":
            row.pop()
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _named(store: Store, role: str = "local") -> str:
    """What a message calls a store: ``local file <path>``, ``local store <label>``."""
    kind = "file" if isinstance(store, FileStore) else "store"
    return f"{role} {kind} {store.label}"


def _started(report: TabReport, tab: TabConfig) -> Store:
    """Name the tab's local side in ``report``, and return its store."""
    store = tab.local_store
    report.local, report.local_label = tab.local, store.label
    return store


def _read_local(tab: TabConfig) -> Records:
    """The tab's local side, refusing one that does not exist."""
    store = tab.local_store
    if not store.exists():
        raise ValueError(f"tab {tab.title!r}: {_named(store)} does not exist")
    return store.read()


def _projection(tab: TabConfig, local: Records) -> list[str]:
    """The columns the tab carries: the configured ones, or every local column.

    Refuses a local file that lacks a configured column: its rows would read
    as blank there and push blanks over the sheet.
    """
    columns = list(tab.columns) if tab.columns is not None else list(local.columns)
    named = _named(tab.local_store)
    if not columns:
        raise ValueError(f"tab {tab.title!r}: {named} has no columns")
    lacking = [column for column in columns if column not in local.columns]
    if lacking:
        raise ValueError(f"tab {tab.title!r}: {named} lacks column(s) {lacking}")
    return columns


def _check(
    tab: TabConfig,
    context: CheckContext,
    validate: Validate | None,
    check: Check | None,
) -> list[str]:
    """Every schema, ``validate``, and ``check`` problem of a tab at one stage."""
    return _problems(tab.schema, tab.key, context, validate, check)


def _problems(
    schema: Mapping[str, ColumnSchema],
    key: Sequence[str],
    context: CheckContext,
    validate: Validate | None,
    check: Check | None,
) -> list[str]:
    """Every schema, ``validate``, and ``check`` problem at one stage, as messages."""
    label = f"{context.tab} ({context.stage})"
    found = [
        str(problem) for problem in problems(context.rows, schema, tab=label, key=key)
    ]
    if validate is not None:
        found.extend(f"{label}: {text}" for text in validate(context.rows))
    if check is not None:
        found.extend(f"{label}: {text}" for text in check(context))
    return found


def _warn(report: TabReport, context: CheckContext, warn: Check | None) -> None:
    """Run ``warn`` at a run's last stage, unless the run has problems.

    Its messages would describe rows that are not written.
    """
    if warn is not None and not report.problems:
        report.warnings = list(warn(context))


def _nonblank(rows: Iterable[Mapping[str, str]], column: str) -> int:
    return sum(1 for row in rows if row.get(column, "") != "")


# -- sync --


def plan_tab(
    service: Service,
    spreadsheet_id: str,
    target: Target,
    tab: TabConfig,
    *,
    adopt: bool = False,
    add_missing: bool = False,
    drop_extra: bool = False,
    prefer: str | None = None,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    report: TabReport | None = None,
) -> TabPlan:
    """Read a sync tab, its local file, and its base, and merge them. Writes nothing.

    The local rows are checked against the tab's schema, ``validate``, and
    ``check`` first; with any problem the plan stops there (``plan`` is None
    and the report lists the problems). The merged result is checked the same
    way, and ``check`` is then given the columns of both sides and the merge
    (:class:`CheckContext`). ``warn`` runs once, on the merged result, when
    no check found a problem; its messages go to the report's ``warnings``
    and block nothing.
    The tab is read with the schema's types: a column declared ``date`` or
    ``datetime`` costs a second read, and its date cells arrive as ISO 8601.

    The schema's types also decide how cells compare: two spellings of one
    value in a typed column are one value
    (:func:`~gdrives.sheets.merge.merge`'s ``types``). A sheet value that
    fails the schema reaches the merged rows and stops the tab, unless the
    tab's ``on_invalid`` is ``"hold"``: then it is held out of the merge and
    reported, and the rest of the tab is written. The local columns outside
    the projection are carried, by name.

    With no base file yet, the tab is **bootstrapped**: the local file's
    projection is taken as the base, so sheet-only edits and rows fold in, a
    local row the sheet lacks is flagged ``remote_deleted``, and nothing is
    written to the sheet on that run (a push that ownership would make, such
    as a ``local_owned`` column, is held back to the next run). With
    ``adopt`` instead, the local file wins every difference: it is merged
    against an empty base with every non-key column local-owned and the row
    set local-owned, so local-only rows are appended and a sheet-only row is
    flagged, never removed. ``adopt`` is refused once a base exists.

    A missing tab, or one with no header row, is reported, and merged as an
    empty tab against an empty base, so :func:`apply_tab` creates it and
    appends every local row. When a base exists for such a tab, the tab was
    emptied after a sync, and that is refused for a person to look at.

    A projection column the tab lacks is refused unless ``add_missing``, in
    which case it is planned as a blank column, to be added at its place in
    the projection (:func:`~gdrives.sheets.structure.place_columns`).
    ``drop_extra`` lists the tab's columns outside the projection, with their
    non-blank cell counts. On a tab with ``insert_above``, the report names
    the row the new rows go above.
    ``report`` is filled in place when given (a caller keeping a partial
    report on error), else created.
    """
    if prefer is not None and prefer not in SIDES:
        raise ValueError(
            f"prefer must be one of {sorted(SIDES)} or None, not {prefer!r}"
        )
    report = report if report is not None else TabReport(tab=tab.title, mode="sync")
    _started(report, tab)
    report.adopted = adopt
    options: dict[str, Any] = {
        "adopt": adopt,
        "add_missing": add_missing,
        "drop_extra": drop_extra,
        "prefer": prefer,
        "validate": validate,
        "check": check,
        "warn": warn,
    }
    return _plan(service, spreadsheet_id, target, tab, report, options)


def _plan(
    service: Service,
    spreadsheet_id: str,
    target: Target,
    tab: TabConfig,
    report: TabReport,
    options: dict[str, Any],
    *,
    created: bool = False,
    added: Sequence[str] = (),
    check: bool = True,
) -> TabPlan:
    """:func:`plan_tab`'s body. ``created`` marks a tab this run created, and
    ``added`` the columns this run added, both of which the base cannot hold.
    ``check=False`` runs no check and no hook, for the merge after a
    restructure, which must match one that already passed them. The
    warnings of the first merge are then kept.
    """
    report.problems, report.notes = list[str](), list[str]()
    if check:
        report.warnings = list[str]()
    report.deferred = list[Cell]()
    report.plan, report.bootstrapped = None, False
    report.insert_row, report.last_row = None, None
    report.add_columns, report.drop_columns = list[str](), dict[str, int]()
    local = _read_local(tab)
    columns = _projection(tab, local)

    def planned(
        table: Table | None, plan: MergePlan | None, base: Records | None
    ) -> TabPlan:
        return TabPlan(
            target=target,
            tab=tab,
            report=report,
            columns=columns,
            local=local,
            base=base,
            table=table,
            plan=plan,
            created=created,
            added=tuple(added),
            **options,
        )

    hooks = (options["validate"], options["check"])
    if check:
        before = CheckContext(
            tab=tab.title,
            stage="local",
            rows=local.rows,
            columns=tuple(local.columns),
            projection=tuple(columns),
        )
        report.problems = _check(tab, before, *hooks)
        if report.problems:
            return planned(None, None, None)

    base_store = target.base_store(tab)
    base = base_store.read() if base_store.exists() else None
    if options["adopt"] and base is not None:
        raise ValueError(
            f"tab {tab.title!r}: adopt is only for a first sync, and a base exists "
            f"at {base_store.label} (delete the base to start over)"
        )

    table: Table | None = None
    sheet_columns: tuple[str, ...] | None = None
    grid: list[list[Any]] = []
    serials: Serials = {}
    remote: list[dict[str, str]] = []
    fresh = set(added)
    if tab.title not in list_tabs(service, spreadsheet_id):
        report.tab_state = "missing"
    else:
        grid = _read_grid(service, spreadsheet_id, tab.title)
        try:
            whole = parse_tab(tab.title, grid, None)
        except EmptyTabError:
            if _canonical(grid):
                raise ValueError(
                    f"tab {tab.title!r} has no header row but holds values; "
                    "give it a header row or clear it"
                ) from None
            report.tab_state = "empty"
        else:
            report.tab_state = "present"
            sheet_columns = tuple(whole.columns)
            serials = pull_serials(service, spreadsheet_id, tab.title, grid, tab.types)
            table, remote = _sheet_side(
                tab, columns, grid, serials, whole, report, options
            )
            fresh.update(report.add_columns)
    if report.tab_state != "present" and base is not None:
        raise ValueError(
            f"tab {tab.title!r} is {report.tab_state} but a base exists at "
            f"{base_store.label}: the tab was emptied after a sync, so look before "
            "syncing (delete the base to start over)"
        )

    # The schema's types decide how cells compare, and under "hold" the schema
    # keeps a sheet value that fails it out of the merge.
    shared: dict[str, Any] = {
        "blank_keys": tab.blank_keys,
        "types": tab.types,
        "schema": tab.schema if tab.on_invalid == "hold" else None,
        "carry": [column for column in local.columns if column not in columns],
    }
    if options["adopt"]:
        plan = merge(
            [],
            local.rows,
            remote,
            tab.key,
            columns,
            local_owned=[column for column in columns if column not in tab.key],
            owns_rows=True,
            **shared,
        )
    else:
        if base is not None:
            base_rows: list[dict[str, str]] = base.rows
        elif report.tab_state != "present" or created:
            base_rows = []
        else:
            # Bootstrap: the local file is the base, except in columns the
            # sheet is only now getting, which hold nothing yet.
            report.bootstrapped = True
            base_rows = [
                {c: "" if c in fresh else row[c] for c in columns} for row in local.rows
            ]
        plan = merge(
            base_rows,
            local.rows,
            remote,
            tab.key,
            columns,
            local_owned=tab.local_owned,
            sheet_owned=tab.sheet_owned,
            owns_rows=tab.owns_rows,
            prefer=options["prefer"],
            **shared,
        )
        if report.bootstrapped and plan.pushes:
            plan = _defer_pushes(plan, tab.key, report)
    report.plan = plan
    if table is not None and tab.insert_above is not None:
        above = _insert_row(tab, table, grid, serials, plan, report.add_columns)
        if plan.appends:
            report.insert_row, report.last_row = above, table.last_row
    if check:
        merged = CheckContext(
            tab=tab.title,
            stage="merged",
            rows=plan.new_local,
            columns=tuple(local.columns),
            projection=tuple(columns),
            sheet_columns=sheet_columns,
            adding=tuple(report.add_columns),
            dropping=tuple(report.drop_columns),
            plan=plan,
        )
        report.problems = _check(tab, merged, *hooks)
        _warn(report, merged, options["warn"])
    return planned(table, plan, base)


def _sheet_side(
    tab: TabConfig,
    columns: Sequence[str],
    grid: Sequence[Sequence[Any]],
    serials: Serials,
    whole: Table,
    report: TabReport,
    options: Mapping[str, Any],
) -> tuple[Table, list[dict[str, str]]]:
    """Read the projection from a tab that has a header, and note its structure.

    Returns the keyed table over the projection columns the tab has, and its
    rows with any column still to be added as blank. ``serials`` holds the
    serial read of the tab's declared date columns.
    """
    missing = [column for column in columns if column not in whole.header]
    keyed = [column for column in missing if column in tab.key]
    if keyed:
        raise ValueError(
            f"tab {tab.title!r} lacks key column(s) {keyed}; add them on the sheet"
        )
    if missing and not options["add_missing"]:
        raise ValueError(
            f"tab {tab.title!r} lacks column(s) {missing}; add_missing "
            "(--add-missing) adds them"
        )
    report.add_columns = missing
    extra = [column for column in whole.columns if column not in columns]
    report.drop_columns = (
        {column: _nonblank(whole.rows, column) for column in extra}
        if options["drop_extra"]
        else {}
    )
    present = [column for column in columns if column not in missing]
    table = parse_tab(
        tab.title,
        grid,
        present,
        tab.key,
        types=tab.types,
        serials=serials,
        blank_keys=tab.blank_keys,
    )
    blank = dict.fromkeys(missing, "")
    return table, [row | blank for row in table.rows]


def _insert_row(
    tab: TabConfig,
    table: Table,
    grid: Sequence[Sequence[Any]],
    serials: Serials,
    plan: MergePlan,
    missing: Sequence[str],
) -> int | None:
    """The row ``plan``'s new rows go above, on the tab ``grid`` was read from.

    ``table`` holds the projection, which the ``insert_above`` column may be
    outside of: it is then taken from the same grid, with no further request.
    One of the ``missing`` columns, which the run is still to add, is blank in
    every row. Raises ValueError for a column the tab lacks, as the apply
    does.
    """
    insert_above = tab.insert_above or {}
    column = next(iter(insert_above), "")
    if column in missing:
        table = replace(
            table,
            header=[*table.header, column],
            columns=[*table.columns, column],
            rows=[row | {column: ""} for row in table.rows],
        )
    elif column in table.header and column not in table.columns:
        table = parse_tab(
            tab.title,
            grid,
            [*table.columns, column],
            tab.key,
            types=tab.types,
            serials=serials,
            blank_keys=tab.blank_keys,
        )
    return insert_point(table, plan, insert_above)


def _defer_pushes(plan: MergePlan, key: Sequence[str], report: TabReport) -> MergePlan:
    """Hold a bootstrap's pushes back to the next run.

    A bootstrap writes nothing to the sheet. Its base takes each held cell's
    sheet value, so the next run sees the local value as a local edit and
    pushes it then. Overrides reported for those cells are dropped, since no
    sheet value was discarded.
    """
    held = {(cell.key, cell.column): cell.sheet for cell in plan.pushes}
    report.deferred = list(plan.pushes)
    new_base = []
    for row in plan.new_base:
        found = row_key(row, key)
        new_base.append(
            {column: held.get((found, column), text) for column, text in row.items()}
        )
    return replace(
        plan,
        pushes=[],
        overrides=[o for o in plan.overrides if (o.key, o.column) not in held],
        new_base=new_base,
    )


def apply_tab(service: Service, spreadsheet_id: str, planned: TabPlan) -> TabReport:
    """Write what :func:`plan_tab` planned, in order, stopping at the first failure.

    Nothing is written when the plan found schema, ``validate``, or ``check``
    problems.
    Otherwise: the structure steps (create a missing tab and its header row,
    add missing columns, delete extra ones), after which the tab is read and
    merged again, refusing a merge that differs from the checked one; then
    :func:`~gdrives.sheets.apply.apply_plan` (re-read guard, pushes, new
    rows, read-back); then the local store, the base store, and
    the column widths. The local side and the base are written only when
    they change, and widths only on a run that wrote to the sheet. Both files
    end their lines as the tab's ``newline`` says (LF by default), so a file
    written with other line endings keeps them until a run changes it. Raises on
    the first failure, with the report recording every write made before it.
    """
    report = planned.report
    report.apply = True
    if planned.plan is None or report.problems:
        return report
    tab = planned.tab
    if planned.table is None or report.add_columns or report.drop_columns:
        planned = _restructure(service, spreadsheet_id, planned)
    table, plan = planned.table, planned.plan
    if table is None or plan is None:
        # Created or given a header above, and now missing or empty again.
        raise SheetChangedError(
            f"tab {tab.title!r} changed while it was restructured; run again"
        )

    try:
        result = apply_plan(
            service,
            spreadsheet_id,
            table,
            plan,
            insert_above=tab.insert_above,
            clear_links=tab.clear_links,
        )
    except ReadBackError:
        report.wrote_sheet = True  # the writes went out; they did not read back
        raise
    report.applied = result
    if result.pushed or result.appended:
        report.wrote_sheet = True

    if plan.new_local != planned.local.rows:
        tab.local_store.write(planned.local.columns, plan.new_local)
        report.wrote_local = True
    base = planned.base
    if base is None or base.columns != planned.columns or base.rows != plan.new_base:
        planned.target.base_store(tab).write(planned.columns, plan.new_base)
        report.wrote_base = True
    if tab.widths and report.wrote_sheet:
        set_column_widths(service, spreadsheet_id, tab.title, tab.widths)
        report.wrote_widths = True
    return report


def _restructure(service: Service, spreadsheet_id: str, planned: TabPlan) -> TabPlan:
    """Run the structure steps a plan asked for, then read and merge again.

    The report keeps what the first read found (the tab's state and the
    columns added or dropped), since the second read sees the result.

    The second merge is not checked again: the checks gate every write, and
    the structure writes have already happened. Instead it must equal the
    merge the checks passed, with the same local file; a sheet or local edit
    made in between raises :class:`SheetChangedError` rather than writing
    unchecked rows.
    """
    report, tab = planned.report, planned.tab
    state, added, dropped = report.tab_state, report.add_columns, report.drop_columns
    # Flagged step by step, so a failure shows the steps that landed before it.
    if state == "missing":
        ensure_tabs(service, spreadsheet_id, [tab.title])
        report.wrote_sheet = True
    if state != "present":
        add_columns(service, spreadsheet_id, tab.title, planned.columns)
        report.wrote_sheet = True
    if added:
        # Placed on the sheet's header as it is now: the deletes run after.
        place_columns(service, spreadsheet_id, tab.title, planned.columns)
        report.wrote_sheet = True
    if dropped:
        delete_columns(service, spreadsheet_id, tab.title, list(dropped))
        report.wrote_sheet = True

    options: dict[str, Any] = {
        "adopt": planned.adopt,
        "add_missing": planned.add_missing,
        "drop_extra": planned.drop_extra,
        "prefer": planned.prefer,
        "validate": planned.validate,
        "check": planned.check,
        "warn": planned.warn,
    }
    again = _plan(
        service,
        spreadsheet_id,
        planned.target,
        tab,
        report,
        options,
        created=planned.created or state != "present",
        added=[*planned.added, *added],
        check=False,
    )
    if (
        report.add_columns
        or report.drop_columns
        or again.plan != planned.plan
        or again.local != planned.local
    ):
        raise SheetChangedError(
            f"tab {tab.title!r} changed while it was restructured; run again"
        )
    report.tab_state, report.add_columns, report.drop_columns = state, added, dropped
    return again


def sync_tab(
    service: Service,
    spreadsheet_id: str,
    target: Target,
    tab: TabConfig,
    *,
    apply: bool = False,
    adopt: bool = False,
    add_missing: bool = False,
    drop_extra: bool = False,
    prefer: str | None = None,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    report: TabReport | None = None,
) -> TabReport:
    """:func:`plan_tab`, then :func:`apply_tab` when ``apply``."""
    planned = plan_tab(
        service,
        spreadsheet_id,
        target,
        tab,
        adopt=adopt,
        add_missing=add_missing,
        drop_extra=drop_extra,
        prefer=prefer,
        validate=validate,
        check=check,
        warn=warn,
        report=report,
    )
    if not apply:
        return planned.report
    return apply_tab(service, spreadsheet_id, planned)


# -- pull and push --


def _compare(
    before: Records,
    after_columns: Sequence[str],
    after_rows: Sequence[Mapping[str, str]],
    key: Sequence[str],
) -> Replacement:
    """What replacing ``before`` with ``after_rows`` changes, by key when there is one.

    Rows are matched by normalized key; a key repeated on one side counts
    once. Without a key, or when ``before`` lacks a key column, only counts
    are reported.
    """
    dropped = {
        column: _nonblank(before.rows, column)
        for column in before.columns
        if column not in after_columns
    }
    cells = sum(1 for row in before.rows for text in row.values() if text != "")
    unchanged = before.columns == list(after_columns) and before.rows == list(
        after_rows
    )
    keyed = bool(key) and all(column in before.columns for column in key)
    added: list[_Key] = []
    removed: list[_Key] = []
    changed: list[_Key] = []
    if keyed:
        was = {row_key(row, key): row for row in before.rows}
        now = {row_key(row, key): row for row in after_rows}
        added = [found for found in now if found not in was]
        removed = [found for found in was if found not in now]
        changed = [
            found
            for found, row in now.items()
            if found in was
            and any(row.get(c, "") != was[found].get(c, "") for c in after_columns)
        ]
    return Replacement(
        before_rows=len(before.rows),
        after_rows=len(after_rows),
        before_cells=cells,
        keyed=keyed,
        added=added,
        removed=removed,
        changed=changed,
        dropped_columns=dropped,
        unchanged=unchanged,
    )


def pull_tab(
    service: Service,
    spreadsheet_id: str,
    tab: TabConfig,
    *,
    apply: bool = False,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    report: TabReport | None = None,
) -> TabReport:
    """Replace ``tab``'s local file with the tab's records (with ``apply``).

    The tab is read as :func:`~gdrives.sheets.table.read_tab` reads, over the
    configured columns (every named column by default) and key, and with the
    schema's types, so a column declared ``date`` or ``datetime`` arrives as
    ISO 8601 at the cost of a second read. A missing
    tab, a tab with no header row, or one with no rows is refused, and the
    local file is left alone. The records are checked against the schema,
    ``validate``, and ``check`` before anything is written, at the stage
    ``"sheet"``, and ``warn`` runs when they pass. The report's ``replacement``
    compares them with the current local file (rows added, removed, and
    changed by key when there is one, and the drop in row count); a missing
    local file is simply created. An unchanged file is not rewritten. A
    delimited file ends its lines as the tab's ``newline`` says.
    """
    report = report if report is not None else TabReport(tab=tab.title, mode="pull")
    store = _started(report, tab)
    report.apply = apply
    if tab.title not in list_tabs(service, spreadsheet_id):
        report.tab_state = "missing"
        raise ValueError(f"no tab named {tab.title!r}; the local file is left alone")
    grid = _read_grid(service, spreadsheet_id, tab.title)
    serials = pull_serials(service, spreadsheet_id, tab.title, grid, tab.types)
    try:
        table = parse_tab(
            tab.title,
            grid,
            tab.columns,
            tab.key,
            types=tab.types,
            serials=serials,
            blank_keys=tab.blank_keys,
        )
    except EmptyTabError:
        report.tab_state = "empty"
        raise ValueError(
            f"tab {tab.title!r} has no header row; the local file is left alone"
        ) from None
    if not table.rows:
        raise ValueError(f"tab {tab.title!r} has no rows; the local file is left alone")
    context = CheckContext(
        tab=tab.title,
        stage="sheet",
        rows=table.rows,
        columns=tuple(table.columns),
        projection=tuple(table.columns),
        sheet_columns=tuple(name for name in table.header if name),
    )
    report.warnings = list[str]()
    report.problems = _check(tab, context, validate, check)
    if report.problems:
        return report
    _warn(report, context, warn)
    before = store.read() if store.exists() else Records([], [])
    report.replacement = _compare(before, table.columns, table.rows, tab.key)
    if apply and not report.replacement.unchanged:
        store.write(table.columns, table.rows)
        report.wrote_local = True
    return report


def _sheet_records(title: str, grid: Sequence[Sequence[Any]]) -> Records | None:
    """A tab's grid as records of its named columns, or None if they don't parse."""
    try:
        table = parse_tab(title, grid, None)
    except EmptyTabError:
        return Records([], [])
    except ValueError:  # a repeated header name: counts are all that can be said
        return None
    return Records(table.columns, table.rows)


def _grid_counts(grid: Sequence[Sequence[Any]]) -> tuple[int, int]:
    """Rows holding anything below row 1, and their non-blank cells."""
    body = _canonical(grid)[1:]
    rows = [row for row in body if any(row)]
    return len(rows), sum(1 for row in rows for text in row if text != "")


def push_tab(
    service: Service,
    spreadsheet_id: str,
    tab: TabConfig,
    *,
    input_option: str = RAW,
    apply: bool = False,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    report: TabReport | None = None,
) -> TabReport:
    """Replace ``tab``'s values with its local file (with ``apply``).

    The local side is read from the tab's store, and pushed by
    :func:`push_rows`, which says what is checked, written, and refused. The
    header row and every local row are written in the local file's column
    order (the configured columns only, when there are some), and the tab's
    ``key``, ``blank_keys``, ``schema``, and ``widths`` are passed on. A
    local side that does not exist, holds no rows, or lacks a configured
    column is refused.
    """
    report = report if report is not None else TabReport(tab=tab.title, mode="push")
    store = _started(report, tab)
    report.apply = apply
    local = _read_local(tab)
    if not local.rows:
        raise ValueError(f"tab {tab.title!r}: {_named(store)} has no rows")
    _projection(tab, local)  # refuses a configured column the file lacks
    wanted = tab.columns if tab.columns is not None else local.columns
    return push_rows(
        service,
        spreadsheet_id,
        tab.title,
        [column for column in local.columns if column in wanted],
        local.rows,
        key=tab.key,
        blank_keys=tab.blank_keys,
        input_option=input_option,
        apply=apply,
        schema=tab.schema,
        validate=validate,
        check=check,
        warn=warn,
        widths=tab.widths,
        clear_links=tab.clear_links,
        label=_named(store),
        report=report,
    )


def push_rows(
    service: Service,
    spreadsheet_id: str,
    title: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    *,
    key: Sequence[str] = (),
    blank_keys: str = "refuse",
    input_option: str = RAW,
    apply: bool = False,
    schema: Mapping[str, ColumnSchema] | None = None,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    widths: Mapping[str, int] | None = None,
    clear_links: bool = False,
    label: str = "rows",
    report: TabReport | None = None,
) -> TabReport:
    """Replace the values of the tab ``title`` with ``rows`` (with ``apply``).

    A whole-tab push of records held in memory: :func:`push_tab` without the
    config and the store. ``rows`` are records of canonical cell strings
    (:func:`~gdrives.sheets.cells.encode_rows` makes them from typed rows),
    and ``columns`` the columns to write, in order. A column a row lacks is
    written blank, and a column of the rows outside ``columns`` is not
    written. ``label`` is what the messages call the rows, where
    :func:`push_tab` names its local file.

    The header row and every row are written in one ``values.update`` over
    the old extent of the tab, padded with blanks where the new data is
    smaller, so a failure cannot leave the tab empty; the grid is grown first
    when the data does not fit. A missing tab is created. An empty list of
    rows is refused, and the rows are checked against ``schema``,
    ``validate``, and ``check`` first, before any request, at the stage
    ``"local"``; ``warn`` runs when they pass. With a ``key`` the rows are
    indexed by it, which refuses a blank or repeated key (``blank_keys`` as
    for :func:`~gdrives.sheets.cells.index_rows`).

    The report's ``replacement`` says what the sheet holds that the rows do
    not: rows by key when there is a key, and always row and cell counts and
    the columns dropped. On apply the tab is read again and must be unchanged
    since the preview read (:class:`SheetChangedError`), and it is read back
    after the write (:class:`ReadBackError`). Under ``RAW`` the read-back
    compares every cell; under ``USER_ENTERED`` the sheet rewrites values on
    entry, so only the header and the row count are checked. A tab already
    holding exactly the rows is not written. ``widths`` are set after a
    write. ``report`` is filled in place when given, so a caller keeps a
    partial report on an error.

    The Sheets API links text that is a URL or a bare domain when it is
    written. With ``clear_links`` the links of the columns pushed are cleared
    after the write (:func:`~gdrives.sheets.structure.strip_links`), which
    costs one grid read when the push left none, and a write and a second
    read when it left some. A link that remains raises
    :class:`ReadBackError`.
    """
    report = report if report is not None else TabReport(tab=title, mode="push")
    report.apply = apply
    out = list(columns)
    if not out or "" in out or len(set(out)) != len(out):
        raise ValueError(
            f"tab {title!r}: columns must be one or more names, each once: {out}"
        )
    if not rows:
        named = "no rows to push" if label == "rows" else f"{label} has no rows"
        raise ValueError(f"tab {title!r}: {named}")
    context = CheckContext(
        tab=title,
        stage="local",
        rows=rows,
        columns=tuple(dict.fromkeys(column for row in rows for column in row)),
        projection=tuple(out),
    )
    report.warnings = list[str]()
    report.problems = _problems(schema or {}, key, context, validate, check)
    if report.problems:
        return report
    _warn(report, context, warn)
    if key:
        index_rows(rows, key, side=label, blank_keys=blank_keys)
    expected = [out, *([row.get(column, "") for column in out] for row in rows)]

    exists = title in list_tabs(service, spreadsheet_id)
    grid = _read_grid(service, spreadsheet_id, title) if exists else []
    if not exists:
        report.tab_state = "missing"
    records = _sheet_records(title, grid)
    before_rows, before_cells = _grid_counts(grid)
    report.replacement = replace(
        _compare(records or Records([], []), out, rows, key),
        before_rows=before_rows,
        before_cells=before_cells,
        unchanged=_canonical(grid) == _canonical(expected),
    )
    if input_option != RAW:
        report.notes.append(
            f"{input_option} rewrites values on entry, so the read-back checks "
            "the header and the row count only"
        )
    if not apply or report.replacement.unchanged:
        return report

    if exists:
        again = _read_grid(service, spreadsheet_id, title)
        if again != grid:
            raise SheetChangedError(
                f"tab {title!r} changed since it was read, so nothing was written"
            )
    else:
        ensure_tabs(service, spreadsheet_id, [title])
        report.wrote_sheet = True
    height = max(len(grid), len(expected))
    width = max(max((len(row) for row in grid), default=0), len(out))
    # The grid is grown first: a write outside it fails.
    sheet_id = _grow(service, spreadsheet_id, title, height, width)
    padded = [
        [*row, *[""] * (width - len(row))]
        for row in [*expected, *[list[str]()] * (height - len(expected))]
    ]
    update_values(
        service,
        spreadsheet_id,
        f"{a1_quote(title)}!A1:{column_letter(width - 1)}{height}",
        padded,
        input_option=input_option,
    )
    report.wrote_sheet = True
    _check_push(service, spreadsheet_id, title, expected, input_option)
    if clear_links:
        left = strip_links(
            service, spreadsheet_id, title, out, header=out, sheet_id=sheet_id
        )
        if left:
            raise ReadBackError(
                f"tab {title!r}: the read-back found links the push did not "
                "clear: "
                + "; ".join(
                    f"row {cell.row}, column {cell.column!r} still holds a link "
                    f"to {list(cell.targets)}"
                    for cell in left
                )
            )
    if widths:
        set_column_widths(service, spreadsheet_id, title, widths)
        report.wrote_widths = True
    return report


def _grow(
    service: Service, spreadsheet_id: str, title: str, rows: int, columns: int
) -> int:
    """Grow the tab's grid to ``rows`` by ``columns``; no request when it fits.

    Returns the tab's ``sheetId``, which the read of its size came with.
    """
    grid = tab_grid(service, spreadsheet_id, title)
    requests: list[dict[str, Any]] = []
    for dimension, need, have in (
        ("ROWS", rows, grid.row_count),
        ("COLUMNS", columns, grid.column_count),
    ):
        if need > have:
            requests.append(
                {
                    "appendDimension": {
                        "sheetId": grid.sheet_id,
                        "dimension": dimension,
                        "length": need - have,
                    }
                }
            )
    if requests:
        batch_update_spreadsheet(service, spreadsheet_id, requests)
    return grid.sheet_id


def _row(grid: Sequence[list[str]], number: int) -> list[str]:
    """Row ``number`` (1-based) of ``grid``, blank past its end."""
    return grid[number - 1] if number <= len(grid) else []


def _check_push(
    service: Service,
    spreadsheet_id: str,
    title: str,
    expected: list[list[str]],
    input_option: str,
) -> None:
    """Read a pushed tab back and check it, raising :class:`ReadBackError`."""
    back = _canonical(_read_grid(service, spreadsheet_id, title))
    failed = f"tab {title!r}: the read-back does not match the push"
    want = _canonical(expected)
    if input_option == RAW:
        if back != want:
            wrong = [
                number
                for number in range(1, max(len(back), len(want)) + 1)
                if _row(back, number) != _row(want, number)
            ]
            raise ReadBackError(f"{failed}: rows {wrong[:10]} differ")
        return
    header = _row(back, 1)
    rows = sum(1 for row in back[1:] if row)
    if header != want[0] or rows != len(want) - 1:
        raise ReadBackError(
            f"{failed}: wrote header {want[0]} and {len(want) - 1} rows, read "
            f"header {header} and {rows} rows"
        )


def pull_all_tabs(
    service: Service,
    spreadsheet_id: str,
    out_dir: str | Path,
    *,
    extension: str = ".csv",
    skip: Collection[str] = (),
    apply: bool = False,
) -> SyncReport:
    """Dump every tab to ``out_dir``, one record file per tab, with no config.

    All tabs are read in one ``values.batchGet``. Each file is named
    :func:`~gdrives.local.safe_filename` of the title plus ``extension``
    (``.csv``, ``.tsv``, or ``.json``); tabs titled in ``skip`` are left out,
    which protects a local file that shares a name with a tab but is made
    elsewhere. Refused before any read of values: an unknown extension, a
    ``skip`` title the spreadsheet lacks, and two titles whose file names
    collide (compared case-insensitively). A tab with no values, or no header
    row, is reported and skipped, never written as an empty file. With
    ``apply`` the files are written (and ``out_dir`` created), a delimited
    one with LF line endings; an unchanged file is not rewritten.
    """
    if extension.lower() not in LOCAL_EXTENSIONS:
        raise ValueError(
            f"extension {extension!r} must be one of {sorted(LOCAL_EXTENSIONS)}"
        )
    titles = list_tabs(service, spreadsheet_id)
    unknown = [title for title in skip if title not in titles]
    if unknown:
        raise ValueError(f"no tab(s) named {unknown} to skip; tabs: {titles}")
    wanted = [title for title in titles if title not in skip]
    names: dict[str, list[str]] = {}
    for title in wanted:
        names.setdefault(safe_filename(title).casefold(), []).append(title)
    collisions = [group for group in names.values() if len(group) > 1]
    if collisions:
        raise ValueError(
            "tabs whose file names collide: "
            + "; ".join(str(group) for group in collisions)
            + " (skip one of each)"
        )

    out = Path(out_dir)
    grids = pull_many(
        service,
        spreadsheet_id,
        [a1_quote(title) for title in wanted],
        render=UNFORMATTED_VALUE,
        date_time_render=FORMATTED_STRING,
    )
    report = SyncReport()
    for title, grid in zip(wanted, grids, strict=True):
        path = out / f"{safe_filename(title)}{extension}"
        tab_report = TabReport(tab=title, mode="pull", local=path, apply=apply)
        report.tabs.append(tab_report)
        try:
            _dump_tab(tab_report, title, grid, path, apply)
        except TAB_ERRORS as e:
            tab_report.error = str(e)
    return report


def _dump_tab(
    report: TabReport,
    title: str,
    grid: Sequence[Sequence[Any]],
    path: Path,
    apply: bool,
) -> None:
    """Write one tab of :func:`pull_all_tabs`, or say why it was skipped."""
    try:
        table = parse_tab(title, grid, None)
    except EmptyTabError:
        report.tab_state = "empty"
        report.skipped = True
        report.notes.append(
            "no header row; skipped" if _canonical(grid) else "no values; skipped"
        )
        return
    if table.wide_rows:
        report.notes.append(
            f"rows {table.wide_rows} hold cells past the header, which are not written"
        )
    before = read_records(path) if path.exists() else Records([], [])
    report.replacement = _compare(before, table.columns, table.rows, ())
    if apply and not report.replacement.unchanged:
        write_records(path, table.columns, table.rows)
        report.wrote_local = True


# -- a whole target --


def run_target(
    service: Service,
    spreadsheet_id: str,
    target: Target,
    mode: str = "sync",
    *,
    tabs: Sequence[str] | None = None,
    apply: bool = False,
    adopt: bool = False,
    add_missing: bool = False,
    drop_extra: bool = False,
    prefer: str | None = None,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
) -> SyncReport:
    """Run every ``mode`` tab of ``target`` (or just ``tabs``), one report each.

    ``validate``, ``check``, and ``warn`` are passed to every tab.
    ``spreadsheet_id`` is the target's spreadsheet, already resolved. A tab
    that fails (a refusal, an API error, a failed guard) is reported with its
    error and the run goes on to the next tab, since tabs are independent.
    Raises ValueError, before any request, for an unknown mode or tab, a
    selected tab of another mode, or a sync-only option on another mode.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}, not {mode!r}")
    if mode != "sync" and (adopt or add_missing or drop_extra or prefer is not None):
        raise ValueError(
            "adopt, add_missing, drop_extra, and prefer apply only to sync tabs"
        )
    if prefer is not None and prefer not in SIDES:
        raise ValueError(
            f"prefer must be one of {sorted(SIDES)} or None, not {prefer!r}"
        )
    if tabs:
        selected = [target.tab(title) for title in tabs]
        other = [tab.title for tab in selected if tab.mode != mode]
        if other:
            raise ValueError(
                f"tab(s) {other} of target {target.name!r} are not {mode} tabs"
            )
    else:
        selected = [tab for tab in target.tabs if tab.mode == mode]
        if not selected:
            raise ValueError(f"target {target.name!r} has no {mode} tabs")

    report = SyncReport(target=target.name)
    for tab in selected:
        tab_report = TabReport(tab=tab.title, mode=mode, apply=apply)
        _started(tab_report, tab)
        report.tabs.append(tab_report)
        try:
            if mode == "sync":
                sync_tab(
                    service,
                    spreadsheet_id,
                    target,
                    tab,
                    apply=apply,
                    adopt=adopt,
                    add_missing=add_missing,
                    drop_extra=drop_extra,
                    prefer=prefer,
                    validate=validate,
                    check=check,
                    warn=warn,
                    report=tab_report,
                )
            elif mode == "pull":
                pull_tab(
                    service,
                    spreadsheet_id,
                    tab,
                    apply=apply,
                    validate=validate,
                    check=check,
                    warn=warn,
                    report=tab_report,
                )
            else:
                push_tab(
                    service,
                    spreadsheet_id,
                    tab,
                    input_option=target.input_option,
                    apply=apply,
                    validate=validate,
                    check=check,
                    warn=warn,
                    report=tab_report,
                )
        except TAB_ERRORS as e:
            tab_report.error = str(e)
    return report


# -- the text report --


def _q(text: str) -> str:
    """A sheet- or file-supplied string, quoted and safe for a terminal."""
    return printable(repr(text))


def _key(found: _Key) -> str:
    return printable(", ".join(found))


def _keys(keys: Sequence[_Key], limit: int = 20) -> str:
    shown = "; ".join(_key(found) for found in keys[:limit])
    more = len(keys) - limit
    return shown + (f"; and {more} more" if more > 0 else "")


def _names(names: Iterable[str]) -> str:
    return ", ".join(_q(name) for name in names)


def format_report(report: SyncReport) -> str:
    """Render ``report`` as text, one block per tab. Pure: prints nothing.

    Every string that came from the sheet or a file (values, keys, titles,
    column names, paths) goes through :func:`~gdrives.local.printable`.
    """
    blocks = ["\n".join(_format_tab(tab)) for tab in report.tabs]
    return "\n\n".join(blocks)


def _format_tab(tab: TabReport) -> list[str]:
    run = "apply" if tab.apply else "preview"
    lines = [f"{tab.mode} tab {_q(tab.tab)} ({run})"]
    if tab.local is not None:
        lines.append(f"  local file: {printable(str(tab.local))}")
    elif tab.local_label is not None:
        lines.append(f"  local store: {printable(tab.local_label)}")
    will = "" if tab.apply else "would be "
    lines.extend(f"  note: {printable(note)}" for note in tab.notes)
    if tab.tab_state == "missing" and tab.mode != "pull":
        lines.append(f"  the tab does not exist: {will}created with a header row")
    elif tab.tab_state == "empty" and tab.mode == "sync":
        lines.append(f"  the tab has no header row: one {will}written")
    if tab.add_columns:
        lines.append(f"  columns {will}added: {_names(tab.add_columns)}")
    if tab.drop_columns:
        dropped = ", ".join(
            f"{_q(name)} ({count} non-blank cells)"
            for name, count in tab.drop_columns.items()
        )
        lines.append(f"  columns {will}deleted, with their data: {dropped}")
    if tab.bootstrapped:
        lines.append(
            "  bootstrapped: there is no base yet, so the local file was taken as "
            "the base; nothing is written to the sheet on this run. Sheet edits "
            "fold in, and local rows the sheet lacks are flagged remote_deleted. "
            "To write local-only rows to the sheet, run with --adopt instead "
            "(it is refused once a base is saved)."
        )
    if tab.adopted:
        lines.append(
            "  adopt: the local file wins every difference on the sheet; "
            "sheet-only rows are flagged, never removed"
        )
    if tab.plan is not None:
        lines.extend(_format_plan(tab.plan, tab.deferred, _placement(tab)))
    if tab.replacement is not None:
        lines.extend(_format_replacement(tab, tab.replacement))
    if tab.problems:
        lines.append(f"  problems ({len(tab.problems)}), so nothing is written:")
        lines.extend(f"    {printable(problem)}" for problem in tab.problems)
    if tab.warnings:
        lines.append(f"  warnings ({len(tab.warnings)}):")
        lines.extend(f"    {printable(warning)}" for warning in tab.warnings)
    wrote = [
        name
        for name, done in (
            ("sheet", tab.wrote_sheet),
            ("local file", tab.wrote_local),
            ("base", tab.wrote_base),
            ("column widths", tab.wrote_widths),
        )
        if done
    ]
    if wrote:
        lines.append(f"  wrote: {', '.join(wrote)}")
    if tab.error is not None:
        lines.append(f"  error: {printable(tab.error)}")
    elif not tab.skipped and not tab.failed and not wrote and _in_sync(tab):
        lines.append("  in sync: nothing to write")
    return lines


def _in_sync(tab: TabReport) -> bool:
    """True when the run found nothing to write, and nothing left for a person."""
    if tab.plan is not None:
        plan = tab.plan
        return (
            tab.tab_state == "present"
            and not plan.has_writes
            and not plan.needs_attention
            and not (tab.add_columns or tab.drop_columns or tab.deferred)
        )
    return tab.replacement is not None and tab.replacement.unchanged


def _cells(label: str, cells: Sequence[Cell], show: Callable[[Cell], str]) -> list[str]:
    if not cells:
        return []
    lines = [f"  {label} ({len(cells)}):"]
    lines.extend(f"    {_key(c.key)} / {_q(c.column)}: {show(c)}" for c in cells)
    return lines


def _placement(tab: TabReport) -> str:
    """Where the new rows of a tab with ``insert_above`` go, or went."""
    if tab.last_row is None:
        return ""
    if tab.applied is not None and tab.applied.appended_rows:
        rows = tab.applied.appended_rows
        if len(rows) == 1:
            return f", in row {rows[0]}"
        return f", in rows {rows[0]} to {rows[-1]}"
    if tab.insert_row is not None:
        return f", above row {tab.insert_row}"
    return f", after row {tab.last_row}"


def _format_plan(
    plan: MergePlan, deferred: Sequence[Cell], placement: str = ""
) -> list[str]:
    lines = [
        *_cells(
            "push to the sheet",
            plan.pushes,
            lambda c: f"{_q(c.sheet)} -> {_q(c.local)}",
        ),
        *_cells(
            "fold into the local file",
            plan.fold_cells,
            lambda c: f"{_q(c.local)} -> {_q(c.sheet)}",
        ),
        *_cells(
            "conflicts, left as they are",
            plan.conflicts,
            lambda c: f"base {_q(c.base)}, local {_q(c.local)}, sheet {_q(c.sheet)}",
        ),
        *_cells(
            "held back to the next run",
            deferred,
            lambda c: f"{_q(c.sheet)} -> {_q(c.local)}",
        ),
    ]
    for label, rows, where in (
        ("new rows for the sheet", plan.appends, placement),
        ("new rows for the local file", plan.fold_rows, ""),
    ):
        if rows:
            lines.append(
                f"  {label} ({len(rows)}){where}: {_keys([r.key for r in rows])}"
            )
    if plan.overrides:
        lines.append(f"  overrides ({len(plan.overrides)}):")
        for o in plan.overrides:
            lost = o.sheet if o.kept == "local" else o.local
            lines.append(
                f"    {_key(o.key)} / {_q(o.column)}: kept {o.kept} "
                f"({o.reason}), discarded {_q(lost)}"
            )
    invalid = [f.key for f in plan.row_flags if f.flag == "remote_invalid"]
    if plan.held:
        lines.append(f"  sheet values held, left for a person ({len(plan.held)}):")
        for h in plan.held:
            # A cell of a held row has no local value to keep.
            stays = "" if h.key in invalid else f"; the local value stays {_q(h.local)}"
            lines.append(
                f"    {_key(h.key)} / {_q(h.column)}: {printable(h.reason)}{stays}"
            )
    if invalid:
        lines.append(
            f"  new sheet rows held for their invalid cells ({len(invalid)}): "
            f"{_keys(invalid)}"
        )
    flags = [f for f in plan.row_flags if f.flag != "remote_invalid"]
    if flags:
        lines.append(f"  row flags ({len(flags)}), left for a person:")
        lines.extend(f"    {_key(f.key)}: {f.flag}" for f in flags)
    return lines


def _format_replacement(tab: TabReport, change: Replacement) -> list[str]:
    target = "the local file" if tab.mode == "pull" else "the sheet"
    if change.unchanged:
        return []
    lines = [
        f"  {target} holds {change.before_rows} rows ({change.before_cells} "
        f"non-blank cells); {'it now holds' if tab.apply else 'it would hold'} "
        f"{change.after_rows}"
    ]
    if change.row_drop:
        lines.append(f"  row count drops by {change.row_drop}")
    if change.keyed:
        for label, keys in (
            ("rows removed", change.removed),
            ("rows added", change.added),
            ("rows changed", change.changed),
        ):
            if keys:
                lines.append(f"  {label} ({len(keys)}): {_keys(keys)}")
    if change.dropped_columns:
        dropped = ", ".join(
            f"{_q(name)} ({count} non-blank cells)"
            for name, count in change.dropped_columns.items()
        )
        lines.append(f"  columns dropped: {dropped}")
    return lines
