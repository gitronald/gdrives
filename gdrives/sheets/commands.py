"""CLI entry points for the ``sheets-*`` commands, and their display helpers.

Each ``run_*`` resolves its source to a spreadsheet ID, builds a Sheets service
with the scopes it needs (imported lazily, so the CLI starts fast), and calls
the helpers in the sibling modules.
"""

import csv
import functools
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from gdrives.local import escape_formula, printable, slug
from gdrives.sheets.a1 import a1_quote, a1_to_grid_range
from gdrives.sheets.config import ConfigError, Target, find_config, load_config
from gdrives.sheets.create import (
    check_source,
    check_tabs,
    create_spreadsheet,
    name_tabs,
    spreadsheet_url,
)
from gdrives.sheets.files import read_values_csv, write_records, write_values_csv
from gdrives.sheets.hooks import resolve_schemas, resolve_target
from gdrives.sheets.links import LinkSweep, _colors, format_sweep, sweep_url_links
from gdrives.sheets.match import set_by_match
from gdrives.sheets.retry import retry_notices
from gdrives.sheets.rules import (
    _flatten_rules,
    _rule_tabs,
    add_conditional_rule,
    build_formula_rule,
    delete_conditional_rule,
    describe_rule,
    format_rules,
    hex_to_color,
    list_conditional_rules,
    read_rule_json,
)
from gdrives.sheets.schema import SCHEMA_COLUMNS, format_schema, schema_rows
from gdrives.sheets.structure import get_column_widths
from gdrives.sheets.sync import (
    SyncReport,
    TabReport,
    _sheet_title,
    format_report,
    pending_hint,
    pull_all_tabs,
    run_target,
)
from gdrives.sheets.values import (
    RAW,
    USER_ENTERED,
    TabListing,
    _lookup_tab,
    _tab_ids,
    append_values,
    clear_values,
    first_tab,
    pull_values,
    tab_listing,
    tab_sheet_ids,
    update_values,
)

# -- display --


def format_values(values: list[list[str]]) -> str:
    """Render rows as left-aligned columns, padding ragged rows to full width."""
    if not values:
        return ""
    width = max(len(row) for row in values)
    padded = [row + [""] * (width - len(row)) for row in values]
    col_widths = [max(len(row[i]) for row in padded) for i in range(width)]
    lines = [
        "  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in padded
    ]
    return "\n".join(lines)


# -- CLI entry points --

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _noticed(run: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run a command inside :func:`retry_notices`, printing each wait."""

    @functools.wraps(run)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with retry_notices():
            return run(*args, **kwargs)

    return wrapped


def _print_report(report: SyncReport) -> int:
    """Print a sync command's report; return its exit code.

    The report goes to stdout. A preview that ``apply`` would change adds a
    hint on stderr, so stdout stays the report alone.
    """
    print(format_report(report))
    _hint_pending(report)
    return report.exit_code


def _hint_pending(report: SyncReport | LinkSweep) -> None:
    """Print :func:`pending_hint` to stderr, when the preview has one."""
    hint = pending_hint(report)
    if hint is not None:
        print(hint, file=sys.stderr)


def _resolve_and_report(source: str) -> str:
    """Resolve ``source`` to a spreadsheet ID and echo it to stderr.

    A Drive path must name a native spreadsheet; a URL or ID is taken as given.

    Every command opens the same way, so the resolve-then-announce step lives
    here once instead of in each ``run_*`` entry point.
    """
    from gdrives.resolve import resolve_and_report

    return resolve_and_report(source, "Spreadsheet", spreadsheet=True)


@_noticed
def run_get(
    source: str,
    range_: str | None = None,
    *,
    output: str | None = None,
    delimiter: str = ",",
    aligned: bool = True,
    escape_formulas: bool = False,
) -> None:
    """Read a range and print it, or write it to a delimited file with ``output``.

    With no ``range_``, defaults to the first tab. To stdout: aligned columns by
    default, or delimited rows when ``aligned`` is False. With ``output``: writes
    a delimited file (``delimiter``) and reports the row count to stderr.
    ``escape_formulas`` passes every cell through :func:`escape_formula`, for
    output a spreadsheet app will open; it is off by default because it changes
    values such as ``-5``.
    """
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()

    if range_ is None:
        # Quote the bare tab title so names with spaces or cell-like forms
        # ('Q3 Budget', '2026') stay valid A1 ranges, matching set_by_match.
        range_ = a1_quote(first_tab(service, spreadsheet_id))

    values = pull_values(service, spreadsheet_id, range_)
    if escape_formulas:
        values = [[escape_formula(cell) for cell in row] for row in values]

    if output:
        write_values_csv(output, values, delimiter=delimiter)
        print(f"Wrote {len(values)} row(s) to {output}", file=sys.stderr)
    elif not values:
        print("(empty range)", file=sys.stderr)
    elif aligned:
        print(format_values(values))
    else:
        # Force "\n" line endings: sys.stdout is a text stream, so csv's default
        # "\r\n" terminator would leave a stray CR (and "\r\r\n" on Windows).
        csv.writer(sys.stdout, delimiter=delimiter, lineterminator="\n").writerows(
            values
        )


@_noticed
def run_update(
    source: str, range_: str, values_file: str, *, raw: bool = False
) -> None:
    """Overwrite a range with rows read from a local CSV file."""
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    values = read_values_csv(values_file)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = update_values(
        service,
        spreadsheet_id,
        range_,
        values,
        input_option=RAW if raw else USER_ENTERED,
    )
    print(
        f"Updated {result.get('updatedCells', 0)} cell(s) in "
        f"{result.get('updatedRange', range_)}"
    )


@_noticed
def run_append(
    source: str, range_: str, values_file: str, *, raw: bool = False
) -> None:
    """Append rows read from a local CSV file after the table in ``range_``."""
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    values = read_values_csv(values_file)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = append_values(
        service,
        spreadsheet_id,
        range_,
        values,
        input_option=RAW if raw else USER_ENTERED,
    )
    updates = result.get("updates", {})
    print(
        f"Appended {updates.get('updatedRows', 0)} row(s) to "
        f"{updates.get('updatedRange', range_)}"
    )


@_noticed
def run_clear(source: str, range_: str, *, yes: bool = False) -> None:
    """Clear the values in a range, confirming first unless ``yes``."""
    import typer

    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    if not yes and not typer.confirm(f"Clear values in {range_}?", default=False):
        print("Aborted.", file=sys.stderr)
        return
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    result = clear_values(service, spreadsheet_id, range_)
    print(f"Cleared {result.get('clearedRange', range_)}")


@_noticed
def run_set(
    source: str,
    match: dict[str, str],
    updates: dict[str, str],
    *,
    tab: str | None = None,
    raw: bool = False,
    allow_multiple: bool = False,
) -> None:
    """Set ``updates`` column(s) on the row(s) matching ``match``.

    Targets the first tab when ``tab`` is None.
    """
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)

    if tab is None:
        tab = first_tab(service, spreadsheet_id)

    summary = set_by_match(
        service,
        spreadsheet_id,
        tab,
        match,
        updates,
        input_option=RAW if raw else USER_ENTERED,
        allow_multiple=allow_multiple,
    )
    rows = summary["rows"]
    row_list = ", ".join(str(r) for r in rows)
    print(
        f"Set {summary['updated_cells']} cell(s) across {len(rows)} row(s) "
        f"(row {row_list}) in {tab}"
    )


def _describe_new(
    title: str,
    folder: dict[str, Any],
    tabs: Sequence[str],
    source: tuple[Path, int] | None = None,
) -> str:
    """Phrase a spreadsheet's creation for the dry run and the result message.

    ``source`` is the workbook it is made from, and that file's size.
    """
    text = (
        f"create spreadsheet '{printable(title)}' in "
        f"'{printable(folder['name'])}' ({folder['id']})"
    )
    if source is not None:
        path, size = source
        text += f" from '{printable(str(path))}' ({size} bytes)"
    if tabs:
        text += f" with tabs: {', '.join(printable(tab) for tab in tabs)}"
    return text


@_noticed
def run_create(
    title: str | None = None,
    *,
    folder: str | None = None,
    folder_id: str | None = None,
    tabs: Sequence[str] = (),
    source: str | None = None,
    dry_run: bool = False,
) -> None:
    """Create a native spreadsheet in a folder, printing its URL.

    The folder is a Drive path (``folder``) or an ID (``folder_id``), and the
    root of My Drive with neither. A file of the same name in the folder is
    noted on stderr and is no obstacle, since Drive permits duplicates. The
    URL goes to stdout and the ID to stderr. ``dry_run`` reads and prints,
    on the read-only scope, and creates nothing.

    ``source`` is a local ``.xlsx`` or ``.csv`` file that Drive converts into
    the spreadsheet; ``title`` is then the file's stem by default, and is
    required otherwise. The workbook names its own tabs, so ``tabs`` is
    refused with it.
    """
    from gdrives.auth import (
        DRIVE_WRITE_SCOPES,
        build_drive_service,
        build_sheets_service,
    )
    from gdrives.files import find_named, get_folder
    from gdrives.resolve import resolve_path

    if title is None:
        if source is None:
            raise ValueError("--title is required unless --from is given")
        title = Path(source).stem
    if not title.strip():
        raise ValueError("--title must not be empty")
    if folder is not None and folder_id is not None:
        raise ValueError("pass at most one of --folder or --folder-id")
    for label, value in (("--folder", folder), ("--folder-id", folder_id)):
        if value is not None and not value.strip():
            raise ValueError(f"{label} must not be empty")
    titles = check_tabs(tabs)
    workbook: tuple[Path, int] | None = None
    if source is not None:
        if titles:
            raise ValueError(
                "--from names its own tabs; pass --from or --tab, not both"
            )
        path, _ = check_source(source)
        workbook = (path, path.stat().st_size)

    # A dry run only reads, so it keeps the read-only default scope.
    drive = build_drive_service(None if dry_run else DRIVE_WRITE_SCOPES)
    if folder is not None:
        folder_id = resolve_path(folder, drive)
    # "root" is the alias of My Drive's root; the API answers with its real ID.
    parent = get_folder(drive, folder_id or "root")

    same = find_named(drive, parent["id"], title)
    if same:
        ids = ", ".join(f["id"] for f in same)
        print(
            f"Note: '{printable(parent['name'])}' already holds {len(same)} "
            f"file(s) named '{printable(title)}': {ids}",
            file=sys.stderr,
        )

    action = _describe_new(title, parent, titles, workbook)
    if dry_run:
        print(f"Would {action}")
        return

    # The drive scope serves the Sheets API too, so one consent covers both.
    sheets = build_sheets_service(DRIVE_WRITE_SCOPES)
    spreadsheet_id = create_spreadsheet(
        drive,
        sheets,
        title,
        folder_id=parent["id"],
        source=source,
    )
    # Said before the tabs are named, so a failure there still names the file.
    # A converted workbook has no tabs to name, and a failed conversion names
    # the file in its own error.
    print(f"Spreadsheet ID: {spreadsheet_id}", file=sys.stderr)
    name_tabs(sheets, spreadsheet_id, titles)
    print(f"Done: {action}", file=sys.stderr)
    print(spreadsheet_url(spreadsheet_id))


@_noticed
def run_widths(source: str, *, tab: str | None = None) -> None:
    """Print a tab's column widths as a JSON object, by header name.

    The object is ready to paste under a tab's ``widths`` in the config.
    Targets the first tab when ``tab`` is None, and reads with the read-only
    scope.
    """
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()
    if tab is None:
        tab = first_tab(service, spreadsheet_id)
    widths = get_column_widths(service, spreadsheet_id, tab)
    print(json.dumps(widths, indent=2, ensure_ascii=False))


@_noticed
def run_rules(source: str, *, as_json: bool = False) -> None:
    """List conditional format rules grouped by tab, or as raw JSON for replay."""
    from gdrives.auth import build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()
    rules = list_conditional_rules(service, spreadsheet_id)
    if as_json:
        print(json.dumps(rules, indent=2))
    elif not rules:
        print("(no conditional format rules)", file=sys.stderr)
    else:
        print(format_rules(rules))


@_noticed
def run_add_rule(
    source: str,
    *,
    ranges: list[str] | None = None,
    formula: str | None = None,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    underline: bool = False,
    text_color: str | None = None,
    background: str | None = None,
    rule_json: str | None = None,
    index: int = 0,
) -> None:
    """Add a custom-formula rule over ``ranges``, or replay one from ``rule_json``.

    Options, colors, and the JSON file are validated before any API call, so a
    typo fails without a round-trip; only a range's tab needs the tab lookup to
    check.
    """
    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    ranges = ranges or []
    flags = {
        "bold": bold,
        "italic": italic,
        "strikethrough": strikethrough,
        "underline": underline,
    }
    if rule_json is not None:
        if (
            ranges
            or formula is not None
            or any(flags.values())
            or text_color
            or background
        ):
            raise ValueError(
                "--rule-json cannot be combined with --range, --formula, "
                "or format options"
            )
        rule = read_rule_json(rule_json)
        spreadsheet_id = _resolve_and_report(source)
        service = build_sheets_service(SHEETS_WRITE_SCOPES)
    else:
        if not ranges or formula is None:
            raise ValueError("pass --range and --formula, or --rule-json")
        if not (any(flags.values()) or text_color or background):
            raise ValueError(
                "a rule needs at least one format option (--bold, --italic, "
                "--strikethrough, --underline, --text-color, or --background)"
            )
        fg = hex_to_color(text_color) if text_color else None
        bg = hex_to_color(background) if background else None
        spreadsheet_id = _resolve_and_report(source)
        service = build_sheets_service(SHEETS_WRITE_SCOPES)
        ids = tab_sheet_ids(service, spreadsheet_id)
        rule = build_formula_rule(
            [a1_to_grid_range(service, spreadsheet_id, r, tab_ids=ids) for r in ranges],
            formula,
            text_color=fg,
            background=bg,
            # Flags are on/off switches at the CLI: off means "leave unset".
            **{k: v or None for k, v in flags.items()},
        )
    add_conditional_rule(service, spreadsheet_id, rule, index=index)
    print(f"Added rule at index {index}: {describe_rule(rule)}")


@_noticed
def run_delete_rule(
    source: str, index: int, *, tab: str | None = None, yes: bool = False
) -> None:
    """Delete the rule at ``index`` on ``tab`` (default: first tab), confirming first.

    Reads the rule list fresh, so the prompt shows the rule actually at that
    position and an out-of-range index is refused before anything is sent.
    Sheets has no revision precondition and a prompt can sit open for a while,
    so after a confirmation the list is read again and the delete is refused
    unless the same rule is still at that index.
    """
    import typer

    from gdrives.auth import SHEETS_WRITE_SCOPES, build_sheets_service

    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service(SHEETS_WRITE_SCOPES)
    # One read supplies both the tab lookup and the rule list.
    tabs = _rule_tabs(service, spreadsheet_id)
    ids = _tab_ids(tabs)
    tab = _lookup_tab(ids, tab)
    on_tab = [r for r in _flatten_rules(tabs) if r["tab"] == tab]
    if not 0 <= index < len(on_tab):
        raise ValueError(
            f"tab {tab!r} has {len(on_tab)} rule(s); no rule at index {index}"
        )
    summary = describe_rule(on_tab[index]["rule"])
    if not yes:
        if not typer.confirm(
            f"Delete rule [{index}] on {tab}: {summary}?", default=False
        ):
            print("Aborted.", file=sys.stderr)
            return
        now = [
            r
            for r in _flatten_rules(_rule_tabs(service, spreadsheet_id))
            if r["tab"] == tab
        ]
        if index >= len(now) or now[index] != on_tab[index]:
            raise ValueError(
                f"the rules on {tab!r} changed while waiting for confirmation; "
                "nothing was deleted, run sheets-rules and try again"
            )
    delete_conditional_rule(service, spreadsheet_id, ids[tab], index)
    print(f"Deleted rule [{index}] on {tab}: {summary}")


# -- sync, pull, and push --


def _config_tabs(target: Target, mode: str, tabs: Sequence[str]) -> list[str]:
    """The ``tabs`` asked for, checked against ``target`` before any request.

    Lists every unknown tab and every tab of another mode at once, and refuses
    a target with no ``mode`` tabs when none are named. Returns the titles
    with repeats dropped, so a tab named twice runs once.
    """
    titles = [tab.title for tab in target.tabs]
    wanted = list(dict.fromkeys(tabs))
    problems: list[str] = []
    unknown = [title for title in wanted if title not in titles]
    if unknown:
        problems.append(f"target {target.name!r} has no tab(s) {unknown}")
    other = [
        tab.title for tab in target.tabs if tab.title in wanted and tab.mode != mode
    ]
    if other:
        problems.append(f"tab(s) {other} of target {target.name!r} are not {mode} tabs")
    if not wanted and not any(tab.mode == mode for tab in target.tabs):
        problems.append(f"target {target.name!r} has no {mode} tabs")
    if problems:
        problems.append(
            "tabs: " + ", ".join(f"{tab.title!r} ({tab.mode})" for tab in target.tabs)
        )
        raise ValueError("\n".join(problems))
    return wanted


def _run_config(
    mode: str,
    name: str,
    *,
    config: str | None,
    tabs: Sequence[str],
    apply: bool,
    **options: Any,
) -> int:
    """Run the ``mode`` tabs of config target ``name``; return the exit code.

    The config, the target, the tabs, and the hooks and schema references
    the config names (found with
    :func:`~gdrives.sheets.hooks.resolve_target`) are checked before any
    request. A preview reads with the read-only scope; ``apply`` on a sync or
    push tab needs the Sheets write scope, and announces the credential first
    (as does a pull, which writes only local files and stays read-only).
    """
    from gdrives.auth import (
        SHEETS_WRITE_SCOPES,
        announce_credentials,
        build_sheets_service,
    )

    target = load_config(config).target(name)
    wanted = _config_tabs(target, mode, tabs)
    # The config's hooks and schema references are imported before any
    # request. run_target is given the resolved target, and finds the hooks
    # again from sys.modules.
    target = resolve_target(
        target, wanted or [tab.title for tab in target.tabs if tab.mode == mode]
    )
    scopes = SHEETS_WRITE_SCOPES if apply and mode != "pull" else None
    if apply:
        announce_credentials(scopes, always=True)
    spreadsheet_id = _resolve_and_report(target.spreadsheet)
    service = build_sheets_service(scopes)
    report = run_target(
        service,
        spreadsheet_id,
        target,
        mode,
        tabs=wanted or None,
        apply=apply,
        **options,
    )
    return _print_report(report)


@_noticed
def run_sync(
    target: str,
    *,
    config: str | None = None,
    tabs: Sequence[str] = (),
    apply: bool = False,
    adopt: bool = False,
    add_missing: bool = False,
    drop_extra: bool = False,
    prefer: str | None = None,
) -> int:
    """Sync the ``sync`` tabs of config target ``target`` (or just ``tabs``).

    Previews unless ``apply``. Prints the report to stdout and returns its exit
    code: 0 in sync or applied, 1 for an error, 2 for work left to a person.
    """
    return _run_config(
        "sync",
        target,
        config=config,
        tabs=tabs,
        apply=apply,
        adopt=adopt,
        add_missing=add_missing,
        drop_extra=drop_extra,
        prefer=prefer,
    )


@_noticed
def run_push(
    target: str,
    *,
    config: str | None = None,
    tabs: Sequence[str] = (),
    apply: bool = False,
) -> int:
    """Replace the ``push`` tabs of ``target`` from their local files (``apply``).

    Previews unless ``apply``; prints the report and returns its exit code.
    """
    return _run_config("push", target, config=config, tabs=tabs, apply=apply)


# The file formats ``sheets-pull --all-tabs`` writes, by ``--format`` name.
_PULL_FORMATS = ("csv", "tsv", "json")


@_noticed
def run_pull(
    source: str,
    *,
    config: str | None = None,
    tabs: Sequence[str] = (),
    apply: bool = False,
    all_tabs: bool = False,
    output: str | None = None,
    skip: Sequence[str] = (),
    file_format: str | None = None,
    bom: bool = False,
    slugs: bool = False,
) -> int:
    """Replace local files from the ``pull`` tabs of config target ``source``.

    With ``all_tabs``, ``source`` is a spreadsheet (URL, file ID, or Drive
    path) instead, no config is read, and every tab but ``skip`` is written
    to ``output`` in ``file_format`` (``csv`` by default), with a byte-order
    mark under ``bom`` and named by :func:`~gdrives.local.slug` of its title
    under ``slugs``. The options that belong to the other form are refused
    before any request. Previews unless ``apply``; prints the report and
    returns its exit code.
    """
    if all_tabs:
        misused = [
            flag
            for flag, given in (("--tab", tabs), ("--config", config is not None))
            if given
        ]
        if misused:
            raise ValueError(f"--all-tabs cannot be combined with {', '.join(misused)}")
        if output is None:
            raise ValueError("--all-tabs needs -o/--output DIR")
        if file_format is not None and file_format not in _PULL_FORMATS:
            raise ValueError(
                f"--format must be one of {list(_PULL_FORMATS)}, not {file_format!r}"
            )
        if bom and file_format == "json":
            raise ValueError("--bom applies only to --format csv and tsv")
        return _run_all_tabs(
            source,
            output,
            skip=skip,
            file_format=file_format or "csv",
            apply=apply,
            bom=bom,
            slugs=slugs,
        )
    misused = [
        flag
        for flag, given in (
            ("-o/--output", output is not None),
            ("--skip", skip),
            ("--format", file_format is not None),
            ("--bom", bom),
            ("--slug", slugs),
        )
        if given
    ]
    if misused:
        raise ValueError(f"{', '.join(misused)} apply only with --all-tabs")
    return _run_config("pull", source, config=config, tabs=tabs, apply=apply)


def _run_all_tabs(
    source: str,
    output: str,
    *,
    skip: Sequence[str],
    file_format: str,
    apply: bool,
    bom: bool = False,
    slugs: bool = False,
) -> int:
    """Dump every tab of ``source`` to ``output`` with no config; the exit code."""
    from gdrives.auth import announce_credentials, build_sheets_service

    if apply:
        announce_credentials(always=True)
    spreadsheet_id = _resolve_and_report(source)
    service = build_sheets_service()
    report = pull_all_tabs(
        service,
        spreadsheet_id,
        output,
        extension=f".{file_format}",
        skip=list(dict.fromkeys(skip)),
        apply=apply,
        bom=bom,
        name=slug if slugs else None,
    )
    return _print_report(report)


# -- links --


def _links_target(source: str, config: str | None) -> Target | None:
    """The config target ``source`` names, or None when it is a spreadsheet.

    With ``config``, ``source`` is a target. Without it, a URL or a Drive
    path (anything with a ``/``) is a spreadsheet, and a bare word is a target
    when the config found from the working directory upward has one of that
    name, and otherwise a spreadsheet ID. A working directory with no config
    file has only spreadsheets; a config file that cannot be read is an
    error, since it may be the one meant.
    """
    if config is None:
        if "/" in source or source.startswith(("http://", "https://")):
            return None
        try:
            found = find_config()
        except ConfigError:
            return None
        loaded = load_config(found)
        return loaded.targets.get(source)
    return load_config(config).target(source)


def _links_plan(
    target: Target | None, tabs: Sequence[str], color: str | None
) -> tuple[list[str] | None, str | Mapping[str, str]]:
    """The tabs to sweep and their colour, checked before any request.

    A spreadsheet needs ``color``, and sweeps ``tabs`` or every tab. A target
    sweeps ``tabs`` (each must be one of its tabs), or, with none, every tab
    when ``color`` is given and otherwise the tabs that have ``link_urls``;
    each takes its ``link_urls`` colour unless ``color`` overrides it. A tab
    with no colour is skipped, with a note on stderr, when it was not named,
    and refused when it was.
    """
    if target is None:
        if color is None:
            raise ValueError(
                "a spreadsheet needs --color; a target's tabs may set link_urls"
            )
        return list(dict.fromkeys(tabs)) or None, color
    known = {tab.title: tab for tab in target.tabs}
    named = list(dict.fromkeys(tabs))
    unknown = [title for title in named if title not in known]
    if unknown:
        raise ValueError(
            f"target {target.name!r} has no tab(s) {unknown}\ntabs: {list(known)}"
        )
    if color is not None:
        return named or list(known), color
    colors = {title: tab.link_urls for title, tab in known.items() if tab.link_urls}
    lacking = [title for title in named if title not in colors]
    if lacking:
        raise ValueError(
            f"tab(s) {lacking} of target {target.name!r} have no link_urls; "
            "pass --color"
        )
    if not named:
        for title in known:
            if title not in colors:
                print(
                    f"Skipping tab {printable(repr(title))}: no link_urls; "
                    "pass --color to check it",
                    file=sys.stderr,
                )
        if not colors:
            raise ValueError(
                f"no tab of target {target.name!r} has link_urls; pass --color"
            )
    return named or list(colors), colors


def _links_on_sheet(
    target: Target,
    titles: Sequence[str],
    colors: str | Mapping[str, str],
    listing: TabListing,
) -> tuple[list[str], str | Mapping[str, str]]:
    """The tabs to sweep and their colours, by the titles they have on the sheet.

    A tab of the target with a ``sheet_id`` is found by it, as a sync finds
    it, and a title that differs from the config's is noted on stderr. A
    ``sheet_id`` the spreadsheet lacks raises ValueError, before any tab is
    swept. A tab with none keeps its title, which the sweep looks for.
    """
    found: dict[str, str] = {}
    for title in titles:
        report = TabReport(tab=title, mode=target.tab(title).mode)
        on_sheet = _sheet_title(title, target.tab(title).sheet_id, listing, report)
        found[title] = title if on_sheet is None else on_sheet
        for note in report.notes:
            print(f"Tab {printable(repr(title))}: {printable(note)}", file=sys.stderr)
    if not isinstance(colors, str):
        colors = {found[title]: colors[title] for title in titles}
    return list(found.values()), colors


def run_schema(
    name: str,
    *,
    config: str | None = None,
    tabs: Sequence[str] = (),
    output: str | None = None,
    escape_formulas: bool = False,
) -> int:
    """Print the schemas of config target ``name`` as CSV, or write them to ``output``.

    One row per declared column of the target's tabs (or just ``tabs``): see
    :func:`~gdrives.sheets.schema.schema_rows`. Reads the config alone, with no
    request and no credential. A tab whose ``schema`` names a
    ``module:attribute`` is resolved, which imports that module, as a run does;
    hooks are neither imported nor checked. ``output`` is a ``.csv``, ``.tsv``,
    or ``.json`` file, written atomically with LF line endings; without it the
    rows go to stdout as CSV. ``escape_formulas`` passes every cell of that CSV
    or delimited file through :func:`escape_formula`, as :func:`run_get` does,
    since a ``pattern`` or a ``description`` may start with ``=``, ``+``,
    ``-``, or ``@``; a ``.json`` output refuses it, as it does a byte-order
    mark. Returns 0; a config, tab, or schema problem raises.
    """
    target = load_config(config).target(name)
    titles = list(dict.fromkeys(tabs)) or None
    rows = schema_rows(resolve_schemas(target, titles), titles)
    if output:
        write_records(output, SCHEMA_COLUMNS, rows, escape_formulas=escape_formulas)
        print(f"Wrote {len(rows)} column(s) to {output}", file=sys.stderr)
    else:
        print(format_schema(rows, escape_formulas=escape_formulas), end="")
    return 0


@_noticed
def run_links(
    source: str,
    *,
    config: str | None = None,
    tabs: Sequence[str] = (),
    color: str | None = None,
    apply: bool = False,
) -> int:
    """Check the URL cells of a spreadsheet's tabs, and with ``apply`` fix them.

    ``source`` is a spreadsheet (URL, file ID, or Drive path) or a config
    target: see :func:`_links_target` for how the two are told apart. A
    spreadsheet needs ``color`` (``#rrggbb``); a target's tabs take their
    ``link_urls`` colour, or ``color`` when given, which applies to every tab
    swept. A target's tab with a ``sheet_id`` is found by it, under whatever
    title it has now, as a sync finds it. No hooks or schema of the config are
    imported. Previews on the
    read-only scope unless ``apply``. Prints the report and returns its exit
    code: 0 when every URL cell follows the rule or was fixed, 1 for an
    error, 2 when a preview found cells to fix.
    """
    from gdrives.auth import (
        SHEETS_WRITE_SCOPES,
        announce_credentials,
        build_sheets_service,
    )

    target = _links_target(source, config)
    titles, colors = _links_plan(target, tabs, color)
    scopes = SHEETS_WRITE_SCOPES if apply else None
    if apply:
        announce_credentials(scopes, always=True)
    spreadsheet_id = _resolve_and_report(
        source if target is None else target.spreadsheet
    )
    service = build_sheets_service(scopes)
    listing: TabListing | None = None
    if target is not None and titles is not None:
        # The colours are checked before the listing, the first request.
        _colors(colors, titles)
        listing = tab_listing(service, spreadsheet_id)
        titles, colors = _links_on_sheet(target, titles, colors, listing)
    sweep = sweep_url_links(
        service, spreadsheet_id, titles, color=colors, apply=apply, listing=listing
    )
    print(format_sweep(sweep))
    _hint_pending(sweep)
    return sweep.exit_code
