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

A run lists the spreadsheet's tabs once
(:func:`~gdrives.sheets.values.tab_listing`) and passes the listing to each
tab, which says whether the tab exists and, for a tab the config names by
``sheet_id``, what its title is now. A function called by itself reads its own.

A caller's own checks are three hooks. ``validate`` takes rows, ``check`` a
:class:`CheckContext` (the rows, the columns of both sides, and the merge),
and both block a write. ``warn`` takes a :class:`CheckContext` too, runs once
after every blocking check has passed, and blocks nothing. A ``transform``
(pull and sync) cleans the rows read from the sheet before anything else sees
them.

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
    _links_left,
    apply_plan,
    insert_point,
)
from gdrives.sheets.cells import (
    SERIAL_TYPES,
    ColumnSchema,
    _header_row,
    cell_data,
    cell_problem,
    index_rows,
    normalize_cell,
    problems,
    row_key,
    to_cell,
)
from gdrives.sheets.config import (
    _STRICT_LOCAL,
    LOCAL_EXTENSIONS,
    MODES,
    TabConfig,
    Target,
    _is_strict_schema,
)
from gdrives.sheets.files import Records, read_records, write_records
from gdrives.sheets.hooks import (
    _chained,
    _joined,
    resolve_tab,
    resolve_target,
    tab_hooks,
)
from gdrives.sheets.merge import SIDES, Cell, MergePlan, merge
from gdrives.sheets.stores import FileStore, MemoryStore, Store
from gdrives.sheets.structure import (
    CELL_LINK_FIELD,
    UrlLinkProblem,
    _fix_url_links,
    _rgb,
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
    _dated,
    parse_tab,
    pull_serials,
)
from gdrives.sheets.typed import (
    _VALUE_FIELD,
    _typed_problems,
    _value_request,
    dated_cells,
    format_requests,
    typed_columns,
)
from gdrives.sheets.values import (
    FORMATTED_STRING,
    RAW,
    UNFORMATTED_VALUE,
    TabListing,
    _check_render,
    _pull_rendered,
    batch_update_spreadsheet,
    list_tabs,
    pull_many,
    tab_grid,
    tab_listing,
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

#: A caller's own cleaning of the rows a tab is read as: rows in, rows out,
#: one for each row given, in order, with the same columns.
Transform = Callable[[Sequence[Mapping[str, str]]], Sequence[Mapping[str, str]]]

#: The transform of :func:`pull_all_tabs`, which is also given the tab's title.
TitledTransform = Callable[
    [Sequence[Mapping[str, str]], str], Sequence[Mapping[str, str]]
]

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

    ``linked`` is each URL cell a run with ``link_urls`` gave a link.
    ``stale_base`` and ``stale_local`` are set when a sync tab is planned: an
    apply would save the base again, or write the local file, whether or not
    it has anything to write to the sheet. They are not printed by
    :func:`format_report`.
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
    linked: list[UrlLinkProblem] = field(default_factory=list)
    stale_base: bool = False
    stale_local: bool = False

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
        """1 for an error or problems, 2 for work left to a person, else 0.

        :attr:`SyncReport.exit_code` of a run of this one tab is the same
        number: it takes the worst of its tabs' codes.
        """
        if self.failed:
            return 1
        return 2 if self.needs_attention else 0

    @property
    def _changes(self) -> bool:
        """True when an apply would write more than the base.

        The tab to create, or a header to write to an empty one (a sync writes
        that; a push writes every row it replaces); columns to add or drop;
        the cell writes of a merge, the pushes and new rows to the sheet and
        the folded cells and rows to the local file; a local file whose rows
        the merge completes (a column they lacked); and a replacement (a pull
        or a push) that differs.
        """
        if self.replacement is not None:
            return not self.replacement.unchanged
        if self.plan is None:
            return False
        created = self.tab_state == "missing" or self.tab_state == "empty"
        return bool(
            created
            or self.add_columns
            or self.drop_columns
            or self.plan.has_writes
            or self.stale_local
        )

    @property
    def pending(self) -> bool:
        """True when a preview found something ``apply`` would write.

        That is exactly when an apply of the same run would write the sheet,
        the local file, or the base, or change the tab's columns or create it:
        what :attr:`_changes` counts, or a sync whose base would be saved
        (:attr:`stale_base`, which a first sync has). A preview whose only
        change is a column is pending, and so is one whose only change is the
        base. A report of a run that applied has nothing pending, and neither
        has one that stopped on an error or found problems, since ``apply``
        writes nothing then.
        """
        if self.apply or self.failed:
            return False
        return self._changes or (self.stale_base and self.plan is not None)

    @property
    def base_only(self) -> bool:
        """True when :attr:`pending` and the base is all an apply would write."""
        return self.pending and not self._changes


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

    @property
    def pending(self) -> bool:
        """True when any tab is :attr:`TabReport.pending`."""
        return any(tab.pending for tab in self.tabs)

    @property
    def base_only(self) -> bool:
        """True when the tabs :attr:`pending` are all pending for the base alone."""
        pending = [tab for tab in self.tabs if tab.pending]
        return bool(pending) and all(tab.base_only for tab in pending)


@dataclass(frozen=True)
class TabPlan:
    """A sync tab read and merged by :func:`plan_tab`, ready for :func:`apply_tab`.

    ``columns`` is the projection. ``table`` is the tab as read, over the
    projection columns it has (None when the tab is missing or empty), and
    ``plan`` the merge against it (None when the local rows had problems).
    ``base`` is the base file as read, None when there is none yet. ``title``
    is the title the tab has on the sheet, which differs from the config's
    for a tab found by its ``sheet_id`` and renamed. The options are kept so
    :func:`apply_tab` can merge again after changing the tab's structure, and
    ``listing`` is the tab listing the plan was made with.

    With a ``transform``, ``seen`` is ``table`` with the rows the transform
    returned, which the merge compared; ``table`` stays the tab as read, for
    the re-read guard and the read-back.
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
    title: str = ""
    listing: TabListing | None = None
    transform: Transform | None = None
    seen: Table | None = None


# -- reading --


def _read_grid(
    service: Service, spreadsheet_id: str, title: str, render: str = "unformatted"
) -> list[list[Any]]:
    """The whole tab's values, read as :func:`~gdrives.sheets.table.read_tab` reads.

    ``render`` is the tab's setting, as for :func:`~gdrives.sheets.table.read_tab`.
    """
    return _pull_rendered(service, spreadsheet_id, a1_quote(title), render)


def _canonical(grid: Sequence[Sequence[Any]]) -> list[list[str]]:
    """``grid`` as canonical strings, trailing blanks trimmed from rows and the end."""
    rows = [[to_cell(cell) for cell in row] for row in grid]
    for row in rows:
        while row and row[-1] == "":
            row.pop()
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _typed_grid(
    grid: Sequence[Sequence[Any]], types: Mapping[str, str], serials: Serials
) -> list[list[str]]:
    """``grid`` as :func:`_canonical` gives it, each typed cell in its value's form.

    Below the header, a cell of a column ``types`` names is normalized
    (:func:`~gdrives.sheets.cells.normalize_cell`), a date column's cell
    from its serial in ``serials`` where it has one, so a grid read back
    compares with the rows written by value.
    """
    rows = _canonical(grid)
    header = _header_row(grid)
    for number, row in enumerate(rows[1:], start=2):
        for index, text in enumerate(row):
            column = header[index] if index < len(header) else ""
            if column not in types:
                continue
            type_ = types[column]
            if column in serials:
                text = _dated(text, serials[column], number, type_)
            row[index] = normalize_cell(text, type_)
    return rows


def _side(store: Store) -> str:
    """What a message calls the local side: ``local file`` or ``local store``."""
    return "local file" if isinstance(store, FileStore) else "local store"


def _named(store: Store) -> str:
    """What a message calls a store: ``local file <path>``, ``local store <label>``."""
    return f"{_side(store)} {store.label}"


def _started(report: TabReport, tab: TabConfig) -> Store:
    """Name the tab's local side in ``report``, and return its store."""
    store = tab.local_store
    report.local, report.local_label = tab.local, store.label
    return store


def _refuse_exclude(tab: TabConfig) -> None:
    """Refuse ``exclude`` on a tab that is not pulled.

    A sync or a push would carry the named columns all the same, and a tab
    built in code has not been through the config's check.
    """
    if tab.exclude:
        raise ValueError(
            f"tab {tab.title!r}: 'exclude' applies only to a pull, and names "
            f"{list(tab.exclude)}"
        )


def _excluded(tab: TabConfig) -> None:
    """Refuse an ``exclude`` that names a column the pull would read anyway."""
    for what, names in (("key", tab.key), ("schema", tab.schema)):
        both = [name for name in names if name in tab.exclude]
        if both:
            raise ValueError(
                f"tab {tab.title!r}: 'exclude' names {what} column(s) {both}, "
                "which would be read anyway"
            )


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
    *,
    strict_columns: Sequence[str] | None = None,
) -> list[str]:
    """Every schema, ``validate``, and ``check`` problem of a tab at one stage.

    ``strict_columns`` is the columns :attr:`TabConfig.strict_schema` checks
    for a schema entry, when it differs from ``context.columns`` (as for a
    pull, where the sheet's header holds more than the projection).
    """
    return _problems(
        tab.schema,
        tab.key,
        context,
        validate,
        check,
        strict_schema=tab.strict_schema,
        strict_columns=strict_columns,
    )


def _strict_schema_problems(
    tab: str, stage: str, schema: Mapping[str, ColumnSchema], columns: Iterable[str]
) -> list[str]:
    """One problem per column of ``columns`` that ``schema`` does not declare."""
    label = f"{tab} ({stage})"
    return [
        f"{label}: column {column!r} has no schema entry, and the tab is strict_schema"
        for column in columns
        if column not in schema
    ]


def _respelling_problems(
    tab: str,
    local_rows: Sequence[Mapping[str, str]],
    remote: Sequence[Mapping[str, str]],
    schema: Mapping[str, ColumnSchema],
    key: Sequence[str],
) -> list[str]:
    """A strict column's sheet text that fails it though the row compares in sync.

    The merge's own schema check runs only on a sheet value about to be
    folded (:func:`~gdrives.sheets.merge.merge`'s ``schema``); a cell that
    compares equal after typed normalization (``true`` against ``TRUE``) is
    never folded or pushed, so it never reaches that check. This finds it
    anyway: nothing would be written for such a cell either way, so it always
    refuses the tab, under either ``on_invalid`` setting, since there is
    nothing for ``hold`` to hold back.
    """
    strict = [column for column, spec in schema.items() if spec.strict]
    if not strict:
        return []
    label = f"{tab} (sheet)"
    by_key = {row_key(row, key): row for row in local_rows}
    found: list[str] = []
    for sheet_row in remote:
        local_row = by_key.get(row_key(sheet_row, key))
        if local_row is None:
            continue
        for column in strict:
            local_text = local_row.get(column, "")
            sheet_text = sheet_row.get(column, "")
            if local_text == sheet_text:
                continue
            type_ = schema[column].type
            if normalize_cell(local_text, type_) != normalize_cell(sheet_text, type_):
                continue  # a real difference; the merge's own check covers it
            reason = cell_problem(sheet_text, schema[column])
            if reason is not None:
                where = row_key(sheet_row, key)
                found.append(f"{label}: key {where}, column {column!r}: {reason}")
    return found


def _presence_problems(
    tab: str, stage: str, schema: Mapping[str, ColumnSchema], available: Collection[str]
) -> list[str]:
    """One problem per schema column declared ``present`` that ``available`` lacks.

    Reported once per column, independent of the row count: a header check,
    not a per-cell one, so it fires on a tab with no rows too.
    """
    label = f"{tab} ({stage})"
    return [
        f"{label}: column {column!r} is declared present and the header lacks it"
        for column, spec in schema.items()
        if spec.present and column not in available
    ]


def _problems(
    schema: Mapping[str, ColumnSchema],
    key: Sequence[str],
    context: CheckContext,
    validate: Validate | None,
    check: Check | None,
    *,
    strict_schema: bool | str = False,
    strict_columns: Sequence[str] | None = None,
) -> list[str]:
    """Every schema, ``validate``, and ``check`` problem at one stage, as messages.

    With ``strict_schema``, a column of ``strict_columns`` (``context.columns``
    when None) that ``schema`` does not declare is a problem too, one per
    column, independent of ``on_invalid`` and of the row-by-row schema check
    above.
    """
    label = f"{context.tab} ({context.stage})"
    found = [
        str(problem) for problem in problems(context.rows, schema, tab=label, key=key)
    ]
    if strict_schema:
        checked = strict_columns if strict_columns is not None else context.columns
        found.extend(
            _strict_schema_problems(context.tab, context.stage, schema, checked)
        )
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


def _sheet_title(
    title: str, sheet_id: int | None, listing: TabListing, report: TabReport
) -> str | None:
    """The title a tab has on the sheet, or None when the sheet has no such tab.

    A tab with a ``sheet_id`` is found by it, and a title that differs from
    the config's is noted in the report. A ``sheet_id`` the spreadsheet lacks
    raises: the run never falls back to the title, which another tab may
    have taken, and never creates the tab.
    """
    if sheet_id is None:
        return title if title in listing.grids else None
    found = listing.title_of(sheet_id)
    if found is None:
        raise ValueError(
            f"tab {title!r}: the spreadsheet has no tab with sheet_id {sheet_id}; "
            "a tab named by its sheet_id is not looked for by title, and is "
            "not created"
        )
    if found != title:
        report.notes.append(f"renamed on the sheet: {title!r} is now {found!r}")
    return found


def _nonblank(rows: Iterable[Mapping[str, str]], column: str) -> int:
    return sum(1 for row in rows if row.get(column, "") != "")


def _resolved(tab: TabConfig) -> TabConfig:
    """``tab`` with its ``schema_ref`` resolved, before anything reads its schema.

    :func:`~gdrives.sheets.hooks.resolve_tab` checks the tab's hooks in the
    same pass, so one ConfigError lists both. A tab with no ``schema_ref`` is
    returned as it is, and its hooks are found where they always were.
    """
    return tab if tab.resolved else resolve_tab(tab)


def _with_tab_hooks(
    tab: TabConfig,
    validate: Validate | None,
    check: Check | None,
    warn: Check | None,
    transform: Transform | None,
) -> tuple[Validate | None, Check | None, Check | None, Transform | None]:
    """The hooks ``tab``'s config names joined with those given, the config's first.

    Messages are the config hook's, then the given one's; a config
    ``transform`` runs first, and the given one cleans what it returns.
    Nothing is imported for a tab with no ``hooks``.
    """
    if not tab.hooks:
        return validate, check, warn, transform
    named = tab_hooks(tab)
    return (
        _joined(named.get("validate"), validate),
        _joined(named.get("check"), check),
        _joined(named.get("warn"), warn),
        _chained(named.get("transform"), transform),
    )


# -- transform --


def _transformed(table: Table, transform: Transform) -> Table:
    """``table`` with the rows ``transform`` returns for its rows, keyed again.

    The hook is given copies, so the rows as read are never changed. What it
    returns must be one row for each row given, each with exactly the
    columns it was given, every value a string; anything else raises
    ValueError saying which. With a key the rows are indexed again, with the
    table's ``blank_keys``, so a key the transform made blank or made equal
    to another is refused as the tab itself would be. Row numbers still
    refer to the sheet's rows.
    """
    given = [dict(row) for row in table.rows]
    returned = list(transform(given))
    label = f"tab {table.tab!r}"
    if len(returned) != len(given):
        raise ValueError(
            f"{label}: the transform returned {len(returned)} rows for "
            f"{len(given)}; it returns one row for each row given"
        )
    rows: list[dict[str, str]] = []
    for position, row in enumerate(returned, start=1):
        if set(row) != set(table.columns):
            raise ValueError(
                f"{label}: the transform returned row {position} with columns "
                f"{sorted(row)}, not {sorted(table.columns)}; it returns each "
                "row with exactly the columns it was given"
            )
        wrong = [column for column in table.columns if not isinstance(row[column], str)]
        if wrong:
            raise ValueError(
                f"{label}: the transform returned row {position} with a value "
                f"that is not a string in column(s) {wrong}"
            )
        rows.append({column: row[column] for column in table.columns})
    if not table.key:
        return replace(table, rows=rows)
    numbers = [table.row_numbers[row_key(row, table.key)] for row in table.rows]
    row_numbers = index_rows(
        rows,
        table.key,
        side=f"{label}, as transformed",
        numbers=numbers,
        blank_keys=table.blank_keys,
    )
    return replace(table, rows=rows, row_numbers=row_numbers)


def _on_sheet(plan: MergePlan, table: Table, seen: Table | None) -> MergePlan:
    """``plan`` with each push named by the key its row has on the sheet as read.

    A merge over transformed rows names a row by its transformed key;
    ``seen`` is the table it merged (None with no transform), and ``table``
    the tab as read, which the re-read guard and the read-back compare with
    the sheet. The two share row numbers, which map one key to the other.
    """
    if seen is None:
        return plan
    read = {number: found for found, number in table.row_numbers.items()}
    keys = {found: read[number] for found, number in seen.row_numbers.items()}
    pushes = [replace(cell, key=keys[cell.key]) for cell in plan.pushes]
    return replace(plan, pushes=pushes)


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
    listing: TabListing | None = None,
    report: TabReport | None = None,
    transform: Transform | None = None,
) -> TabPlan:
    """Read a sync tab, its local file, and its base, and merge them. Writes nothing.

    The local rows are checked against the tab's schema, ``validate``, and
    ``check`` first; with any problem the plan stops there (``plan`` is None
    and the report lists the problems). The merged result is checked the same
    way, and ``check`` is then given the columns of both sides and the merge
    (:class:`CheckContext`). ``warn`` runs once, on the merged result, when
    no check found a problem; its messages go to the report's ``warnings``
    and block nothing.
    With the tab's ``strict_schema``, a local column with no ``schema`` entry
    is a problem too, at the ``"local"`` stage (every local column, in and out
    of the projection); a sheet column outside the projection is the same,
    at the ``"sheet"`` stage, once the tab is read, less a column already
    reported at the local stage and one this run drops with ``drop_extra``.
    A schema column declared ``present`` is checked the same two stages, the
    other way round: it must be in the local file's columns at ``"local"``,
    and in the sheet's header at ``"sheet"`` (less a column this run is
    adding with ``add_missing``, not yet there but about to be). The tab is
    read with the schema's types: a column declared ``date`` or ``datetime``
    costs a second read, and its date cells arrive as ISO 8601. Every read of
    the tab in the run, the re-read guard and the read-back of
    :func:`apply_tab` included, follows the tab's ``render``.

    A row both sides hold, whose ``strict`` column compares equal only after
    typed normalization (``true`` against ``TRUE``), is never folded or
    pushed, so the merge's own schema check never sees the sheet's spelling.
    It is still reported, at the ``"sheet"`` stage: nothing would be written
    for that cell either way, so it refuses the tab under either
    ``on_invalid`` setting.

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

    ``listing`` is the spreadsheet's tab listing when the caller has read it,
    as :func:`run_target` has; with None it is read here. A tab with a
    ``sheet_id`` is found by it, under whatever title it has now, and one the
    spreadsheet lacks is an error.

    ``transform`` is given the sheet's rows as read, over the projection
    columns the tab has, and returns them cleaned (see :func:`pull_tab`). The
    merge, the checks, the report, the local file, and the base see the
    cleaned rows, so a sheet cell that differs from the local side only by
    what the transform removes is in sync, and is not pushed: the sheet keeps
    its text. The re-read guard and the read-back of :func:`apply_tab`
    compare the tab as read, and ``insert_above`` matches its values as read.
    A merge after a restructure runs the transform again. It must be
    idempotent: a local edit is pushed as written, and one the transform
    would change is read back as a sheet edit on the next run and folded in,
    once. The local side is never transformed.
    """
    if prefer is not None and prefer not in SIDES:
        raise ValueError(
            f"prefer must be one of {sorted(SIDES)} or None, not {prefer!r}"
        )
    tab = _resolved(tab)
    report = report if report is not None else TabReport(tab=tab.title, mode="sync")
    _started(report, tab)
    _refuse_exclude(tab)
    report.adopted = adopt
    validate, check, warn, transform = _with_tab_hooks(
        tab, validate, check, warn, transform
    )
    options: dict[str, Any] = {
        "adopt": adopt,
        "add_missing": add_missing,
        "drop_extra": drop_extra,
        "prefer": prefer,
        "validate": validate,
        "check": check,
        "warn": warn,
        "transform": transform,
    }
    return _plan(service, spreadsheet_id, target, tab, report, options, listing=listing)


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
    listing: TabListing | None = None,
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
    report.stale_base = report.stale_local = False
    report.insert_row, report.last_row = None, None
    report.add_columns, report.drop_columns = list[str](), dict[str, int]()
    local = _read_local(tab)
    columns = _projection(tab, local)

    title = tab.title
    seen: Table | None = None

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
            title=title,
            listing=listing,
            seen=seen,
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
        report.problems = [
            *_check(tab, before, *hooks),
            *_presence_problems(tab.title, "local", tab.schema, local.columns),
        ]
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
    if listing is None:
        listing = tab_listing(service, spreadsheet_id)
    found = _sheet_title(tab.title, tab.sheet_id, listing, report)
    if found is None:
        report.tab_state = "missing"
    else:
        title = found
        grid = _read_grid(service, spreadsheet_id, title, tab.render)
        try:
            whole = parse_tab(title, grid, None)
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
            serials = pull_serials(service, spreadsheet_id, title, grid, tab.types)
            table, remote = _sheet_side(
                tab, columns, grid, serials, whole, report, options
            )
            if options["transform"] is not None:
                seen = _transformed(table, options["transform"])
                blank = dict.fromkeys(report.add_columns, "")
                remote = [row | blank for row in seen.rows]
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
        above = _insert_row(
            tab, table, grid, serials, _on_sheet(plan, table, seen), report.add_columns
        )
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
        # strict_schema already checked the local side at the "local" stage
        # above, before the sheet was read; a column it found undeclared
        # there already stopped the plan, so a column that reaches here is
        # never one the local side also carries: reported once, at "local".
        report.problems = _check(tab, merged, *hooks, strict_columns=())
        if tab.strict_schema is True and sheet_columns is not None:
            sheet_extra = [
                column
                for column in sheet_columns
                if column not in columns and column not in report.drop_columns
            ]
            report.problems = [
                *report.problems,
                *_strict_schema_problems(tab.title, "sheet", tab.schema, sheet_extra),
            ]
        if sheet_columns is not None:
            # A column this run is adding with add_missing is not yet in the
            # header, but will be: it is not reported as absent.
            report.problems = [
                *report.problems,
                *_presence_problems(
                    tab.title,
                    "sheet",
                    tab.schema,
                    (*sheet_columns, *report.add_columns),
                ),
            ]
        report.problems = [
            *report.problems,
            *_respelling_problems(tab.title, local.rows, remote, tab.schema, tab.key),
        ]
        _warn(report, merged, options["warn"])
    report.stale_base = _base_stale(base, columns, plan)
    report.stale_local = _local_stale(local, plan)
    return planned(table, plan, base)


def _base_stale(base: Records | None, columns: Sequence[str], plan: MergePlan) -> bool:
    """True when an apply saves the base: none yet, or it differs from the plan's."""
    return base is None or base.columns != columns or base.rows != plan.new_base


def _local_stale(local: Records, plan: MergePlan) -> bool:
    """True when an apply writes the local file: the plan's rows differ from it."""
    return plan.new_local != local.rows


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
        whole.tab,
        grid,
        present,
        tab.key,
        types=tab.types,
        serials=serials,
        blank_keys=tab.blank_keys,
        render=tab.render,
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
            table.tab,
            grid,
            [*table.columns, column],
            tab.key,
            types=tab.types,
            serials=serials,
            blank_keys=tab.blank_keys,
            render=tab.render,
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
            _on_sheet(plan, table, planned.seen),
            insert_above=tab.insert_above,
            clear_links=tab.clear_links,
            link_urls=tab.link_urls,
            typed_writes=tab.typed_writes,
        )
    except ReadBackError:
        report.wrote_sheet = True  # the writes went out; they did not read back
        raise
    report.applied = result
    report.linked = result.linked
    if result.pushed or result.appended:
        report.wrote_sheet = True

    if _local_stale(planned.local, plan):
        tab.local_store.write(planned.local.columns, plan.new_local)
        report.wrote_local = True
    base = planned.base
    if _base_stale(base, planned.columns, plan):
        planned.target.base_store(tab).write(planned.columns, plan.new_base)
        report.wrote_base = True
    if tab.widths and report.wrote_sheet:
        set_column_widths(
            service, spreadsheet_id, planned.title, tab.widths, render=tab.render
        )
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
    report, tab, title = planned.report, planned.tab, planned.title
    state, added, dropped = report.tab_state, report.add_columns, report.drop_columns
    notes = list(report.notes)
    listing = planned.listing
    # Flagged step by step, so a failure shows the steps that landed before it.
    if state == "missing":
        titles = listing.titles if listing is not None else None
        ensure_tabs(service, spreadsheet_id, [title], existing=titles)
        report.wrote_sheet = True
        # A listing from before the tab was created is not used again.
        listing = None
    if state != "present":
        add_columns(service, spreadsheet_id, title, planned.columns, render=tab.render)
        report.wrote_sheet = True
    if added:
        # Placed on the sheet's header as it is now: the deletes run after.
        place_columns(
            service, spreadsheet_id, title, planned.columns, render=tab.render
        )
        report.wrote_sheet = True
    if dropped:
        delete_columns(service, spreadsheet_id, title, list(dropped), render=tab.render)
        report.wrote_sheet = True

    options: dict[str, Any] = {
        "adopt": planned.adopt,
        "add_missing": planned.add_missing,
        "drop_extra": planned.drop_extra,
        "prefer": planned.prefer,
        "validate": planned.validate,
        "check": planned.check,
        "warn": planned.warn,
        "transform": planned.transform,
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
        listing=listing,
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
    report.notes = notes
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
    listing: TabListing | None = None,
    report: TabReport | None = None,
    transform: Transform | None = None,
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
        listing=listing,
        report=report,
        transform=transform,
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
    listing: TabListing | None = None,
    report: TabReport | None = None,
    transform: Transform | None = None,
) -> TabReport:
    """Replace ``tab``'s local file with the tab's records (with ``apply``).

    The tab is read as :func:`~gdrives.sheets.table.read_tab` reads, over the
    configured columns (every named column by default) and key, and with the
    schema's types, so a column declared ``date`` or ``datetime`` arrives as
    ISO 8601 at the cost of a second read. A missing
    tab, a tab with no header row, or one with no rows is refused, and the
    local file is left alone. The records are checked against the schema,
    ``validate``, and ``check`` before anything is written, at the stage
    ``"sheet"``, and ``warn`` runs when they pass. With ``strict_schema``, a
    named header column with no ``schema`` entry is a problem too, checked at
    the same stage, whether or not it is read: every named column, less any
    ``exclude`` names, not just ``columns``. A schema column declared
    ``present`` is checked there too, against the same named header columns,
    the other way round: a problem when the header lacks it, before the "no
    rows" refusal, so it is reported on a tab with no rows too. The report's
    ``replacement``
    compares them with the current local file (rows added, removed, and
    changed by key when there is one, and the drop in row count); a missing
    local file is simply created. An unchanged file is not rewritten. A
    delimited file ends its lines as the tab's ``newline`` says.

    With ``exclude``, the header is checked first: a name it lists that is
    not one of the header's named columns refuses the pull, leaving the
    local side alone, and lists every such name (a renamed sensitive column
    is a likely cause). The columns read are then the header's named columns
    less ``exclude``, in header order, passed to :func:`~gdrives.sheets.table.parse_tab`
    as an explicit list, so an excluded column's values never enter the
    ``Table`` and cannot reach a hook, a report, or the local file. An
    ``exclude`` that names a ``key`` or ``schema`` column is refused before
    any request, since that column would be read after all, and so is a tab
    whose named columns are all excluded, with its own message.
    :func:`plan_tab` and :func:`push_tab` refuse a tab with ``exclude``.

    ``listing`` and the tab's ``sheet_id`` are as for :func:`plan_tab`. The
    tab is read as its ``render`` says.

    ``transform`` cleans the records after the tab is parsed, declared date
    columns already ISO 8601, and before anything else: the checks, the
    hooks, the report, and the file see what it returns, never the rows as
    read. It is given a copy of the rows and returns one row for each, in
    the same order, each with exactly the columns it was given and every
    value a string; anything else is refused, saying which. The keys are
    indexed again after it, with the tab's ``blank_keys``, so a transform
    that makes a key blank or makes two equal is refused, and the local
    side is left alone.
    """
    tab = _resolved(tab)
    report = report if report is not None else TabReport(tab=tab.title, mode="pull")
    store = _started(report, tab)
    report.apply = apply
    _excluded(tab)
    validate, check, warn, transform = _with_tab_hooks(
        tab, validate, check, warn, transform
    )
    left = f"the {_side(store)} is left alone"
    if listing is None:
        listing = tab_listing(service, spreadsheet_id)
    title = _sheet_title(tab.title, tab.sheet_id, listing, report)
    if title is None:
        report.tab_state = "missing"
        raise ValueError(f"no tab named {tab.title!r}; {left}")
    grid = _read_grid(service, spreadsheet_id, title, tab.render)
    columns: Sequence[str] | None = tab.columns
    if tab.exclude:
        header = _header_row(grid)
        if not any(header):
            report.tab_state = "empty"
            raise ValueError(f"tab {tab.title!r} has no header row; {left}")
        missing = [name for name in tab.exclude if name not in header]
        if missing:
            raise ValueError(
                f"tab {tab.title!r}: 'exclude' names column(s) {missing} not in "
                f"the header {header}; a renamed sensitive column is a likely "
                f"cause. {left}"
            )
        columns = [name for name in header if name and name not in tab.exclude]
        if not columns:
            raise ValueError(
                f"tab {tab.title!r}: 'exclude' names every column the header "
                f"has, so there is nothing to pull; {left}"
            )
    serials = pull_serials(service, spreadsheet_id, title, grid, tab.types)
    try:
        table = parse_tab(
            title,
            grid,
            columns,
            tab.key,
            types=tab.types,
            serials=serials,
            blank_keys=tab.blank_keys,
            render=tab.render,
        )
    except EmptyTabError:
        report.tab_state = "empty"
        raise ValueError(f"tab {tab.title!r} has no header row; {left}") from None
    # strict_schema and 'present' check every named header column, not just
    # the ones read (table.columns), less any 'exclude' names, which are not
    # read at all. 'present' is checked here, before the "no rows" refusal
    # below, so it is reported on a tab with no rows too.
    checked = [name for name in table.header if name and name not in tab.exclude]
    presence = _presence_problems(tab.title, "sheet", tab.schema, checked)
    if presence:
        report.warnings = list[str]()
        report.problems = presence
        return report
    if not table.rows:
        raise ValueError(f"tab {tab.title!r} has no rows; {left}")
    if transform is not None:
        table = _transformed(table, transform)
    context = CheckContext(
        tab=tab.title,
        stage="sheet",
        rows=table.rows,
        columns=tuple(table.columns),
        projection=tuple(table.columns),
        sheet_columns=tuple(name for name in table.header if name),
    )
    report.warnings = list[str]()
    # "local" checks the columns read, which become the local file's; true
    # checks every named header column, read or not.
    report.problems = _check(
        tab,
        context,
        validate,
        check,
        strict_columns=checked if tab.strict_schema is True else None,
    )
    if report.problems:
        return report
    _warn(report, context, warn)
    before = store.read() if store.exists() else Records([], [])
    report.replacement = _compare(before, table.columns, table.rows, tab.key)
    if apply and not report.replacement.unchanged:
        store.write(table.columns, table.rows)
        report.wrote_local = True
    return report


class PullError(ValueError):
    """A pull of :func:`pull_records` that was refused or found problems.

    ``report`` is the tab's :class:`TabReport`, and the message is
    ``format_report(report)``, so printing the error prints what the commands
    would.
    """

    def __init__(self, report: TabReport) -> None:
        super().__init__(format_report(report))
        self.report = report


def pull_records(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    *,
    columns: Sequence[str] | None = None,
    key: Sequence[str] = (),
    blank_keys: str = "refuse",
    schema: Mapping[str, ColumnSchema] | None = None,
    strict_schema: bool | str = False,
    exclude: Sequence[str] = (),
    render: str = "unformatted",
    sheet_id: int | None = None,
    validate: Validate | None = None,
    check: Check | None = None,
    warn: Check | None = None,
    transform: Transform | None = None,
    listing: TabListing | None = None,
    report: TabReport | None = None,
) -> Records:
    """Read the tab titled ``tab`` into memory, checked as a pull checks it.

    This is :func:`pull_tab` with ``apply`` on and the tab's local side a
    :class:`~gdrives.sheets.stores.MemoryStore`, so nothing is read from or
    written to a file, and a pull tab in a config and this call refuse the
    same things. The arguments are those of a pull tab (``columns``, ``key``,
    ``blank_keys``, ``schema``, ``strict_schema``, ``exclude``, ``render``,
    ``sheet_id``) and of :func:`pull_tab` (the hooks, ``transform``, and
    ``listing``). A combination of them a tab refuses, such as ``exclude``
    with ``columns``, raises the ValueError :class:`TabConfig` raises, before
    any request.

    Returns the rows as the store holds them: :class:`Records` of the columns
    read and their rows, as canonical cell strings. :func:`decode_rows`
    turns them into typed values.

    Raises :class:`PullError` when the pull was refused (a ValueError: no such
    tab, no header row, no rows) or found ``problems`` (schema,
    ``validate``, ``check``), which is when ``report.failed`` is true and a
    pull's exit code is 1. Its ``report`` and message are the tab's
    :class:`TabReport` and its rendering. An ``HttpError`` or an ``OSError``
    is not caught: it propagates as it was raised, with the retries of the
    value calls already made, so a caller's handling of them needs no change.
    What ``warn`` says fails nothing; pass a ``report`` of your own and read
    its ``warnings`` afterwards.
    """
    report = report if report is not None else TabReport(tab=tab, mode="pull")
    store = MemoryStore()
    pull = TabConfig(
        title=tab,
        store=store,
        mode="pull",
        key=tuple(key),
        columns=tuple(columns) if columns is not None else None,
        exclude=tuple(exclude),
        schema=schema if schema is not None else {},
        blank_keys=blank_keys,
        render=render,
        sheet_id=sheet_id,
        strict_schema=strict_schema,
    )
    try:
        pull_tab(
            service,
            spreadsheet_id,
            pull,
            apply=True,
            validate=validate,
            check=check,
            warn=warn,
            listing=listing,
            report=report,
            transform=transform,
        )
    except ValueError as e:
        report.error = str(e)
        raise PullError(report) from e
    if report.failed:
        raise PullError(report)
    return store.read()


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
    listing: TabListing | None = None,
    report: TabReport | None = None,
) -> TabReport:
    """Replace ``tab``'s values with its local file (with ``apply``).

    The local side is read from the tab's store, and pushed by
    :func:`push_rows`, which says what is checked, written, and refused. The
    header row and every local row are written in the local file's column
    order (the configured columns only, when there are some), and the tab's
    ``key``, ``blank_keys``, ``schema``, ``widths``, ``sheet_id``,
    ``render``, and ``strict_schema`` are passed on, with ``listing``. A local
    side that does not exist, holds no rows, or lacks a configured column is
    refused, in that order. A schema column declared ``present`` is checked
    against the columns being written (the configured columns the local side
    has, or every local column), before the "no rows" refusal, so a local
    side with no rows is still checked for it.
    """
    tab = _resolved(tab)
    report = report if report is not None else TabReport(tab=tab.title, mode="push")
    store = _started(report, tab)
    _refuse_exclude(tab)
    report.apply = apply
    validate, check, warn, _ = _with_tab_hooks(tab, validate, check, warn, None)
    local = _read_local(tab)
    wanted = tab.columns if tab.columns is not None else local.columns
    out = [column for column in local.columns if column in wanted]
    presence = _presence_problems(tab.title, "local", tab.schema, out)
    if presence:
        report.warnings = list[str]()
        report.problems = presence
        return report
    if not local.rows:
        raise ValueError(f"tab {tab.title!r}: {_named(store)} has no rows")
    _projection(tab, local)  # refuses a configured column the file lacks
    return push_rows(
        service,
        spreadsheet_id,
        tab.title,
        out,
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
        link_urls=tab.link_urls,
        label=_named(store),
        sheet_id=tab.sheet_id,
        strict_schema=tab.strict_schema,
        listing=listing,
        report=report,
        render=tab.render,
        typed_writes=tab.typed_writes,
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
    link_urls: str | None = None,
    label: str = "rows",
    sheet_id: int | None = None,
    strict_schema: bool | str = False,
    listing: TabListing | None = None,
    report: TabReport | None = None,
    render: str = "unformatted",
    typed_writes: bool = False,
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
    for :func:`~gdrives.sheets.cells.index_rows`). With ``strict_schema``, a
    column any row holds that ``schema`` does not declare is a problem too,
    checked at the same stage. A schema column declared ``present`` is
    checked there too, against ``columns``: a problem when it is not one of
    them, before the "no rows" refusal, so it is reported on an empty push
    too. A push replaces the tab whole, so only ``columns``, what is written,
    is the "local side" a push checks presence against; the sheet's own
    header, about to be overwritten, means nothing here.

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

    ``link_urls``, a ``#rrggbb`` colour, does the opposite: after the write,
    each URL cell of the columns pushed is given a link to its own text in
    that colour, not underlined
    (:func:`~gdrives.sheets.structure.set_url_links`), and the report's
    ``linked`` lists them. It costs a read of the tab's values and a grid
    read of its URL cells, and when any needs a link, one write and the two
    reads again. A colour that is not ``#rrggbb``, or ``link_urls`` with
    ``clear_links``, is refused before any request.

    With ``sheet_id`` the tab is found by it, under whatever title it has
    now, and ``title`` is only what the report calls it; a ``sheet_id`` the
    spreadsheet lacks is an error, and no tab is created. ``listing`` is the
    spreadsheet's tab listing when the caller has read it.

    ``render`` is how the tab is read, as for
    :func:`~gdrives.sheets.table.read_tab`: the read of what it holds, the
    read before the write, the read-back, and the header read of ``widths``
    all follow it. The rows are written as ``RAW`` strings either way, and a
    string reads as written under either setting.

    ``typed_writes`` writes each column ``schema`` declares, less the
    ``key``, as a value of its type (:mod:`~gdrives.sheets.typed`), with the
    whole tab in one ``updateCells`` in place of the ``values.update``, and
    gives each date cell written that has no date or time format one, which
    costs a grid read of the date columns. Whether the tab already holds the
    rows, and the read-back, then compare a typed column by value, a date
    column read as serial numbers. It is refused, before any request, with
    an ``input_option`` other than ``RAW``, with ``render="formatted"``, and
    for a value its column's type cannot hold exactly.
    """
    report = report if report is not None else TabReport(tab=title, mode="push")
    report.apply = apply
    _check_render(render)
    if typed_writes and input_option != RAW:
        raise ValueError(
            f"tab {title!r}: typed writes send values themselves; "
            f"input_option {input_option} contradicts them"
        )
    if typed_writes and render != "unformatted":
        raise ValueError(
            f"tab {title!r}: typed writes need the tab read unformatted, not {render!r}"
        )
    if not _is_strict_schema(strict_schema):
        raise ValueError(
            f"tab {title!r}: 'strict_schema' must be true, false, or "
            f"{_STRICT_LOCAL!r}, not {strict_schema!r}"
        )
    if link_urls is not None:
        if clear_links:
            raise ValueError(
                f"tab {title!r}: clear_links and link_urls contradict each other"
            )
        _rgb(link_urls)
    out = list(columns)
    if not out or "" in out or len(set(out)) != len(out):
        raise ValueError(
            f"tab {title!r}: columns must be one or more names, each once: {out}"
        )
    # 'present' checks the columns being written, before the "no rows"
    # refusal below, so it is reported on an empty push too.
    presence = _presence_problems(title, "local", schema or {}, out)
    if presence:
        report.warnings = list[str]()
        report.problems = presence
        return report
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
    report.problems = _problems(
        schema or {}, key, context, validate, check, strict_schema=strict_schema
    )
    if report.problems:
        return report
    _warn(report, context, warn)
    if key:
        index_rows(rows, key, side=label, blank_keys=blank_keys)
    expected = [out, *([row.get(column, "") for column in out] for row in rows)]
    types: dict[str, str] = {}
    if typed_writes:
        declared = {c: spec.type for c, spec in (schema or {}).items() if c in out}
        types = typed_columns(declared, key)
        refused = _typed_problems(
            (
                (f"row {number}", column, text)
                for number, values in enumerate(expected[1:], start=2)
                for column, text in zip(out, values, strict=True)
            ),
            types,
        )
        if refused:
            raise ValueError(
                f"tab {title!r}: cannot write typed values: " + "; ".join(refused)
            )

    if listing is None:
        listing = tab_listing(service, spreadsheet_id)
    found = _sheet_title(title, sheet_id, listing, report)
    exists = found is not None
    title = found if found is not None else title
    grid = _read_grid(service, spreadsheet_id, title, render) if exists else []
    if not exists:
        report.tab_state = "missing"
    records = _sheet_records(title, grid)
    before_rows, before_cells = _grid_counts(grid)
    if types and grid:
        serials = pull_serials(service, spreadsheet_id, title, grid, types)
        unchanged = _typed_grid(grid, types, serials) == _typed_grid(
            expected, types, {}
        )
    else:
        unchanged = _canonical(grid) == _canonical(expected)
    report.replacement = replace(
        _compare(records or Records([], []), out, rows, key),
        before_rows=before_rows,
        before_cells=before_cells,
        unchanged=unchanged,
    )
    if input_option != RAW:
        report.notes.append(
            f"{input_option} rewrites values on entry, so the read-back checks "
            "the header and the row count only"
        )
    if not apply or report.replacement.unchanged:
        return report

    if exists:
        again = _read_grid(service, spreadsheet_id, title, render)
        if again != grid:
            raise SheetChangedError(
                f"tab {title!r} changed since it was read, so nothing was written"
            )
    else:
        ensure_tabs(service, spreadsheet_id, [title], existing=listing.titles)
        report.wrote_sheet = True
    height = max(len(grid), len(expected))
    width = max(max((len(row) for row in grid), default=0), len(out))
    # The grid is grown first: a write outside it fails.
    sheet_id = _grow(service, spreadsheet_id, title, height, width)
    padded = [
        [*row, *[""] * (width - len(row))]
        for row in [*expected, *[list[str]()] * (height - len(expected))]
    ]
    if typed_writes:
        _write_typed(
            service, spreadsheet_id, title, sheet_id, padded, out, types, clear_links
        )
    else:
        update_values(
            service,
            spreadsheet_id,
            f"{a1_quote(title)}!A1:{column_letter(width - 1)}{height}",
            padded,
            input_option=input_option,
        )
    report.wrote_sheet = True
    _check_push(service, spreadsheet_id, title, expected, input_option, render, types)
    if clear_links:
        left = strip_links(
            service, spreadsheet_id, title, out, header=out, sheet_id=sheet_id
        )
        if left:
            raise _links_left(title, left, "push")
    if link_urls is not None:
        report.linked = _fix_url_links(
            service, spreadsheet_id, title, link_urls, columns=out, sheet_id=sheet_id
        )
    if widths:
        set_column_widths(service, spreadsheet_id, title, widths, render=render)
        report.wrote_widths = True
    return report


def _write_typed(
    service: Service,
    spreadsheet_id: str,
    title: str,
    sheet_id: int,
    padded: Sequence[Sequence[str]],
    out: Sequence[str],
    types: Mapping[str, str],
    clear_links: bool,
) -> None:
    """Write a push's ``padded`` grid from A1 as typed values, in one request.

    The header row and every column ``types`` does not name are literal
    strings. A date cell written that has no date or time format now is
    given one in the same request, which a grid read of the date columns
    finds out first.
    """
    dates = [
        column
        for index, column in enumerate(out)
        if types.get(column) in SERIAL_TYPES and any(row[index] for row in padded[1:])
    ]
    dated = dated_cells(service, spreadsheet_id, title, out, dates)
    cells = [
        [
            cell_data(
                text,
                types.get(out[index], "str") if number and index < len(out) else "str",
            )
            for index, text in enumerate(row)
        ]
        for number, row in enumerate(padded)
    ]
    fields = _VALUE_FIELD
    if clear_links:
        fields += f",{CELL_LINK_FIELD}"
    unformatted = [
        (number - 1, index, types[column])
        for column, index in ((column, out.index(column)) for column in dates)
        for number, row in enumerate(padded[1:], start=2)
        if row[index] and (number, column) not in dated
    ]
    batch_update_spreadsheet(
        service,
        spreadsheet_id,
        [
            _value_request(sheet_id, 0, 0, cells, fields),
            *format_requests(sheet_id, unformatted),
        ],
    )


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
    render: str = "unformatted",
    types: Mapping[str, str] | None = None,
) -> None:
    """Read a pushed tab back and check it, raising :class:`ReadBackError`.

    A column ``types`` names, which a typed push wrote as values, is
    compared by value, a date column read as serial numbers.
    """
    read = _read_grid(service, spreadsheet_id, title, render)
    back = _canonical(read)
    want = _canonical(expected)
    if types:
        serials = pull_serials(service, spreadsheet_id, title, read, types)
        back = _typed_grid(read, types, serials)
        want = _typed_grid(expected, types, {})
    failed = f"tab {title!r}: the read-back does not match the push"
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
    bom: bool = False,
    name: Callable[[str], str] | None = None,
    transform: TitledTransform | None = None,
) -> SyncReport:
    """Dump every tab to ``out_dir``, one record file per tab, with no config.

    All tabs are read in one ``values.batchGet``. Each file is named
    :func:`~gdrives.local.safe_filename` of the title plus ``extension``
    (``.csv``, ``.tsv``, or ``.json``); tabs titled in ``skip`` are left out,
    which protects a local file that shares a name with a tab but is made
    elsewhere. ``name`` maps a title to a file stem of the caller's choosing,
    such as :func:`~gdrives.local.slug`; a title it raises ValueError for is
    reported for its tab, which is not written. The stem goes through
    :func:`~gdrives.local.safe_filename` too, so a title cannot name a file
    outside ``out_dir``. ``bom`` starts each file with a byte-order mark.

    Refused before any read of values: an unknown extension, ``bom`` with
    ``.json``, a ``skip`` title the spreadsheet lacks, and two titles whose
    file names collide (compared case-insensitively, as ``name`` maps them). A
    tab with no values, or no header
    row, is reported and skipped, never written as an empty file. With
    ``apply`` the files are written (and ``out_dir`` created), a delimited
    one with LF line endings; an unchanged file is not rewritten.

    ``transform`` cleans each tab's rows before they are compared and
    written, as for :func:`pull_tab`, and is given the tab's title as a
    second argument, since one function serves every tab. A tab it fails
    for is reported with the error and not written.
    """
    if extension.lower() not in LOCAL_EXTENSIONS:
        raise ValueError(
            f"extension {extension!r} must be one of {sorted(LOCAL_EXTENSIONS)}"
        )
    if bom and extension.lower() == ".json":
        raise ValueError("a byte-order mark applies only to .csv and .tsv")
    titles = list_tabs(service, spreadsheet_id)
    unknown = [title for title in skip if title not in titles]
    if unknown:
        raise ValueError(f"no tab(s) named {unknown} to skip; tabs: {titles}")
    wanted = [title for title in titles if title not in skip]
    stems: dict[str, str] = {}
    unnamed: dict[str, str] = {}
    for title in wanted:
        try:
            # A stem is a file name, never a path, whoever made it.
            stems[title] = safe_filename(title if name is None else name(title))
        except ValueError as e:
            unnamed[title] = str(e)
    names: dict[str, list[str]] = {}
    for title, stem in stems.items():
        names.setdefault(stem.casefold(), []).append(title)
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
        if title in unnamed:
            failed = TabReport(tab=title, mode="pull", apply=apply)
            failed.error = f"no file name for the tab: {unnamed[title]}"
            report.tabs.append(failed)
            continue
        path = out / f"{stems[title]}{extension}"
        tab_report = TabReport(tab=title, mode="pull", local=path, apply=apply)
        report.tabs.append(tab_report)
        try:
            _dump_tab(tab_report, title, grid, path, apply, bom, transform)
        except TAB_ERRORS as e:
            tab_report.error = str(e)
    return report


def _dump_tab(
    report: TabReport,
    title: str,
    grid: Sequence[Sequence[Any]],
    path: Path,
    apply: bool,
    bom: bool = False,
    transform: TitledTransform | None = None,
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
    if transform is not None:
        table = _transformed(table, lambda rows: transform(rows, title))
    before = read_records(path) if path.exists() else Records([], [])
    report.replacement = _compare(before, table.columns, table.rows, ())
    if apply and not report.replacement.unchanged:
        write_records(path, table.columns, table.rows, bom=bom)
        report.wrote_local = True


# -- a whole target --


def run_target(
    service: Service,
    spreadsheet_id: str | None,
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
    transform: Transform | None = None,
) -> SyncReport:
    """Run every ``mode`` tab of ``target`` (or just ``tabs``), one report each.

    ``validate``, ``check``, and ``warn`` are passed to every tab, and
    ``transform`` to every pull and sync tab, given the rows alone (a caller
    that needs the tab closes over it, or runs a function per tab). The
    spreadsheet's tabs are listed once, and again only after a tab was
    created, so a run of N tabs makes one listing and not N.
    ``spreadsheet_id`` is the target's spreadsheet, already resolved, or None
    for :attr:`~gdrives.sheets.config.Target.spreadsheet_id`, which is
    refused for a Drive path. A tab that fails (a refusal, an API error, a
    failed guard) is reported with its error and the run goes on to the next
    tab, since tabs are independent.
    Raises ValueError, before any request, for an unknown mode or tab, a
    selected tab of another mode, a sync-only option on another mode, or a
    ``transform`` on a push.

    The hooks a selected tab's config names, and the schema its
    ``schema_ref`` names, are found first
    (:func:`~gdrives.sheets.hooks.resolve_target`), and a
    :class:`~gdrives.sheets.config.ConfigError` lists every name that does
    not resolve and every problem of a schema found, before any request.
    Each tab runs with its schema resolved, and runs its config's hooks,
    then the ones given here.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}, not {mode!r}")
    if spreadsheet_id is None:
        spreadsheet_id = target.spreadsheet_id
    if mode != "sync" and (adopt or add_missing or drop_extra or prefer is not None):
        raise ValueError(
            "adopt, add_missing, drop_extra, and prefer apply only to sync tabs"
        )
    if prefer is not None and prefer not in SIDES:
        raise ValueError(
            f"prefer must be one of {sorted(SIDES)} or None, not {prefer!r}"
        )
    if mode == "push" and transform is not None:
        raise ValueError("transform applies only to pull and sync tabs")
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
    target = resolve_target(target, [tab.title for tab in selected])
    # The same tabs again, now with any schema_ref resolved.
    selected = (
        [target.tab(title) for title in tabs]
        if tabs
        else [tab for tab in target.tabs if tab.mode == mode]
    )

    report = SyncReport(target=target.name)
    listing: TabListing | None = None
    for tab in selected:
        tab_report = TabReport(tab=tab.title, mode=mode, apply=apply)
        _started(tab_report, tab)
        report.tabs.append(tab_report)
        try:
            if listing is None:
                listing = tab_listing(service, spreadsheet_id)
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
                    listing=listing,
                    report=tab_report,
                    transform=transform,
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
                    listing=listing,
                    report=tab_report,
                    transform=transform,
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
                    listing=listing,
                    report=tab_report,
                )
        except TAB_ERRORS as e:
            tab_report.error = str(e)
        if tab_report.tab_state == "missing" and tab_report.wrote_sheet:
            # The run created the tab, so the listing is read again.
            listing = None
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


def format_report(report: SyncReport | TabReport) -> str:
    """Render ``report`` as text, one block per tab. Pure: prints nothing.

    A :class:`TabReport`, as :func:`pull_tab` and :func:`push_rows` return,
    renders as the one block a run of that tab would.

    Every string that came from the sheet or a file (values, keys, titles,
    column names, paths) goes through :func:`~gdrives.local.printable`.
    """
    tabs = [report] if isinstance(report, TabReport) else report.tabs
    blocks = ["\n".join(_format_tab(tab)) for tab in tabs]
    return "\n\n".join(blocks)


def _local(tab: TabReport) -> str:
    """What a report calls the tab's local side: a file, or a store with none."""
    stored = tab.local is None and tab.local_label is not None
    return "local store" if stored else "local file"


def _format_tab(tab: TabReport) -> list[str]:
    run = "apply" if tab.apply else "preview"
    local = _local(tab)
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
            f"  bootstrapped: there is no base yet, so the {local} was taken as "
            "the base; nothing is written to the sheet on this run. Sheet edits "
            "fold in, and local rows the sheet lacks are flagged remote_deleted. "
            "To write local-only rows to the sheet, run with --adopt instead "
            "(it is refused once a base is saved)."
        )
    if tab.adopted:
        lines.append(
            f"  adopt: the {local} wins every difference on the sheet; "
            "sheet-only rows are flagged, never removed"
        )
    if tab.plan is not None:
        lines.extend(_format_plan(tab.plan, tab.deferred, _placement(tab), local))
    if tab.replacement is not None:
        lines.extend(_format_replacement(tab, tab.replacement))
    if tab.problems:
        lines.append(f"  problems ({len(tab.problems)}), so nothing is written:")
        lines.extend(f"    {printable(problem)}" for problem in tab.problems)
    if tab.warnings:
        lines.append(f"  warnings ({len(tab.warnings)}):")
        lines.extend(f"    {printable(warning)}" for warning in tab.warnings)
    if tab.linked:
        lines.append(f"  URL cells given a link: {len(tab.linked)}")
    wrote = [
        name
        for name, done in (
            ("sheet", tab.wrote_sheet),
            (local, tab.wrote_local),
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
    plan: MergePlan,
    deferred: Sequence[Cell],
    placement: str = "",
    local: str = "local file",
) -> list[str]:
    lines = [
        *_cells(
            "push to the sheet",
            plan.pushes,
            lambda c: f"{_q(c.sheet)} -> {_q(c.local)}",
        ),
        *_cells(
            f"fold into the {local}",
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
        (f"new rows for the {local}", plan.fold_rows, ""),
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
    target = f"the {_local(tab)}" if tab.mode == "pull" else "the sheet"
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
