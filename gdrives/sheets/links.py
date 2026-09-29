"""Check and fix the links of URL cells over a spreadsheet's tabs.

:func:`sweep_url_links` runs :func:`~gdrives.sheets.structure.url_link_problems`
(or :func:`~gdrives.sheets.structure.set_url_links`, with ``apply``) over the
tabs of one spreadsheet, and :func:`format_sweep` prints what it found. A sync
or a push formats only the URL cells it wrote; this covers the cells a person
typed or pasted.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from gdrives.files import Service
from gdrives.local import printable
from gdrives.sheets.structure import (
    UrlLinkProblem,
    _rgb,
    _url_problems,
    _write_url_links,
)
from gdrives.sheets.values import TabListing, tab_listing

# What one tab's failure is, as in a sync run: the tab is reported and the
# sweep goes on to the next, since tabs are independent. ReadBackError and
# GridTooLargeError are ValueErrors.
_TAB_ERRORS = (ValueError, HttpError, OSError)


@dataclass(frozen=True)
class TabLinks:
    """What a sweep found on one tab, and whether it fixed it.

    ``problems`` are the URL cells whose link is not as wanted, in row then
    column order; ``applied`` is True when the sweep linked them and the
    read-back agreed. ``skipped`` is why the tab was not checked, for a tab
    that cannot hold URL cells in named columns (no header row, or a header
    of blank cells): that is no error, since a notes or chart tab is no fault
    of the spreadsheet's. ``error`` is the message of a failure on the tab: a
    header that repeats a name, a request the API refused, or a read-back
    that found cells still wrong (``problems`` then lists the cells the fix
    was for, and ``applied`` is False).
    """

    tab: str
    color: str
    problems: tuple[UrlLinkProblem, ...] = ()
    applied: bool = False
    skipped: str | None = None
    error: str | None = None

    @property
    def pending(self) -> bool:
        """True when a preview found cells that ``apply`` would fix."""
        return bool(self.problems) and not self.applied and self.error is None

    @property
    def exit_code(self) -> int:
        """1 for an error, 2 for cells left to fix, else 0."""
        if self.error is not None:
            return 1
        return 2 if self.pending else 0


@dataclass(frozen=True)
class LinkSweep:
    """The tabs a sweep covered, in the order it took them.

    ``apply`` is whether the sweep was asked to write.
    """

    tabs: tuple[TabLinks, ...]
    apply: bool = False

    @property
    def exit_code(self) -> int:
        """1 if any tab failed, else 2 if a preview found cells to fix, else 0.

        0 means every URL cell follows the rule, or was fixed. A skipped tab
        counts as neither.
        """
        codes = {tab.exit_code for tab in self.tabs}
        return 1 if 1 in codes else 2 if 2 in codes else 0

    @property
    def pending(self) -> bool:
        """True when any tab is :attr:`TabLinks.pending`."""
        return any(tab.pending for tab in self.tabs)


def _colors(
    color: str | Mapping[str, str], titles: Sequence[str] | None
) -> Mapping[str, str] | str:
    """``color`` checked, and for ``titles`` when given: every tab needs one."""
    if isinstance(color, str):
        _rgb(color)
        return color
    for wanted in color.values():
        _rgb(wanted)
    absent = [title for title in titles or () if title not in color]
    if absent:
        raise ValueError(f"no colour for tab(s) {absent}; colours: {list(color)}")
    return color


def sweep_url_links(
    service: Service,
    spreadsheet_id: str,
    tabs: Sequence[str] | None = None,
    *,
    color: str | Mapping[str, str],
    apply: bool = False,
    listing: TabListing | None = None,
) -> LinkSweep:
    """Check the URL cells of each of ``tabs``; with ``apply``, fix them.

    ``tabs`` defaults to every tab of the spreadsheet, from one listing, and
    a tab it lacks is refused before any write, with the tabs it has.
    ``color`` (``#rrggbb``) is the wanted colour of every tab, or a mapping
    of tab title to colour that has one for each tab swept. A colour that is
    not ``#rrggbb`` raises ValueError before any request. ``listing`` is the
    spreadsheet's tabs when the caller has read them, which saves the listing.

    Each tab is read as :func:`~gdrives.sheets.structure.url_link_problems`
    reads it, over every named column, and with ``apply`` the cells found are
    fixed as :func:`~gdrives.sheets.structure.set_url_links` fixes them, in
    one batch per tab. Tabs are independent: a tab that fails (a header that
    repeats a name, an ``HttpError``, a read-back that finds cells still
    wrong) is reported with its ``error`` and the sweep goes on. A tab with
    no header row, or a header of blank cells, has no named column to hold a
    URL cell and is reported with ``skipped``, not as an error.
    """
    titles = None if tabs is None else list(dict.fromkeys(tabs))
    colors = _colors(color, titles)
    if listing is None:
        listing = tab_listing(service, spreadsheet_id)
    grids = listing.grids
    if titles is None:
        titles = list(grids)
        colors = _colors(color, titles)
    missing = [title for title in titles if title not in grids]
    if missing:
        raise ValueError(f"no tab(s) {missing} in the spreadsheet; tabs: {list(grids)}")
    results = []
    for title in titles:
        wanted = colors if isinstance(colors, str) else colors[title]
        results.append(
            _sweep_tab(
                service, spreadsheet_id, title, grids[title].sheet_id, wanted, apply
            )
        )
    return LinkSweep(tuple(results), apply)


def _sweep_tab(
    service: Service,
    spreadsheet_id: str,
    tab: str,
    sheet_id: int,
    color: str,
    apply: bool,
) -> TabLinks:
    """One tab of a sweep: its problems, fixed when ``apply``."""
    found: tuple[UrlLinkProblem, ...] = ()
    try:
        problems, positions = _url_problems(
            service, spreadsheet_id, tab, _rgb(color), None, None
        )
        found = tuple(problems)
        if not positions:
            return TabLinks(tab, color, skipped="no named columns in the first row")
        if apply and problems:
            _write_url_links(
                service, spreadsheet_id, tab, _rgb(color), problems, positions, sheet_id
            )
    except _TAB_ERRORS as e:
        return TabLinks(tab, color, found, error=str(e))
    return TabLinks(tab, color, found, applied=apply and bool(found))


def format_sweep(sweep: LinkSweep) -> str:
    """Render ``sweep`` as text, one block per tab. Pure: prints nothing.

    Every string that came from the sheet (titles, column names, cell text,
    messages) goes through :func:`~gdrives.local.printable`.
    """
    run = "apply" if sweep.apply else "preview"
    return "\n\n".join(_format_tab(tab, run) for tab in sweep.tabs)


def _format_tab(tab: TabLinks, run: str) -> str:
    lines = [f"tab {printable(repr(tab.tab))} ({run}, colour {tab.color})"]
    if tab.skipped is not None:
        lines.append(f"  skipped: {printable(tab.skipped)}")
    lines.extend(
        f"  row {cell.row}, column {printable(repr(cell.column))}: "
        f"{', '.join(cell.reasons)}: {printable(cell.text)}"
        for cell in tab.problems
    )
    if tab.error is not None:
        lines.append(f"  error: {printable(tab.error)}")
    elif tab.applied:
        lines.append(f"  fixed: {len(tab.problems)} URL cell(s) linked")
    elif tab.pending:
        lines.append(f"  to fix: {len(tab.problems)} URL cell(s)")
    elif tab.skipped is None:
        lines.append("  every URL cell follows the rule")
    return "\n".join(lines)
