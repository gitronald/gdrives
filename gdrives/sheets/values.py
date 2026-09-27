"""Sheets API v4 wrappers: cell values (``spreadsheets.values.*``) and tab lookups.

The core helpers take a Sheets ``service``, a ``spreadsheet_id``, and an A1
``range_`` (e.g. ``"Sheet1!A1:C10"``). Values are plain ``list[list[str]]`` —
what the API returns with the default ``FORMATTED_VALUE`` render and what it
accepts on write. The Sheets API returns rows truncated at the last non-empty
cell, so display padding lives in ``format_values``; writes send rows as-is
(Sheets pads short rows with blanks).

Tab titles and ``sheetId`` values come from ``spreadsheets.get``, and structural
edits go through ``spreadsheets.batchUpdate``; both wrappers live here beside
the value operations. Grid data (formats, links, column sizes) is read by
:func:`pull_grid`, always under a ``fields`` mask.

Every call goes through :func:`~gdrives.sheets.retry.with_retry`. Reads and
range overwrites (``update``, ``clear``, ``batchUpdate`` of values) retry on a
rate limit or a 5xx; ``append`` and the structural ``batchUpdate`` add rows,
columns, or rules, so they retry on a rate limit only.
"""

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gdrives.files import Service
from gdrives.sheets.retry import RATE_LIMIT_STATUSES, with_retry

# The two valueInputOption modes. USER_ENTERED parses "=SUM(...)", dates, and
# numbers like the Sheets UI; RAW stores the literal string in each cell.
USER_ENTERED = "USER_ENTERED"
RAW = "RAW"

# The valueRenderOption modes for reads. FORMATTED_VALUE (the API default)
# returns each cell as displayed; UNFORMATTED_VALUE returns numbers and booleans
# as JSON numbers and booleans; FORMULA returns the formula text itself.
FORMATTED_VALUE = "FORMATTED_VALUE"
UNFORMATTED_VALUE = "UNFORMATTED_VALUE"
FORMULA = "FORMULA"

# The dateTimeRenderOption modes, which apply only when the render is not
# FORMATTED_VALUE. SERIAL_NUMBER (the API default) returns a date as a day count
# from 1899-12-30; FORMATTED_STRING returns it as the cell's number format shows it.
SERIAL_NUMBER = "SERIAL_NUMBER"
FORMATTED_STRING = "FORMATTED_STRING"


# -- core value operations --


def _render_options(render: str | None, date_time_render: str | None) -> dict[str, str]:
    """The render keyword arguments for a read, leaving out the ones not given.

    Only options the caller names are sent, so a plain read makes exactly the
    request it made before render options existed.
    """
    options: dict[str, str] = {}
    if render is not None:
        options["valueRenderOption"] = render
    if date_time_render is not None:
        options["dateTimeRenderOption"] = date_time_render
    return options


def pull_values(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    *,
    render: str | None = None,
    date_time_render: str | None = None,
) -> list[list[Any]]:
    """Read an A1 range, returning its rows (empty list for an empty range).

    Cells are strings under the default render (``FORMATTED_VALUE``). With
    ``render=UNFORMATTED_VALUE`` numbers and booleans come back as Python
    numbers and booleans. ``date_time_render`` picks how dates render when the
    render is not ``FORMATTED_VALUE``.
    """
    options = _render_options(render, date_time_render)
    result = with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_, **options)
            .execute()
        )
    )
    # Sheets omits "values" entirely for an empty range.
    return result.get("values", [])


def pull_many(
    service: Service,
    spreadsheet_id: str,
    ranges: list[str],
    *,
    render: str | None = None,
    date_time_render: str | None = None,
) -> list[list[list[Any]]]:
    """Read several A1 ranges in one ``values.batchGet`` request.

    Returns one grid per range, in the order the ranges were given (an empty
    range gives an empty grid). One request for several tabs spends one unit
    of the per-minute read quota instead of one per tab. The render options
    are as for :func:`pull_values`. No ranges means no request.
    """
    if not ranges:
        return []
    options = _render_options(render, date_time_render)
    result = with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges, **options)
            .execute()
        )
    )
    # valueRanges follows the request order; an empty range omits "values".
    return [block.get("values", []) for block in result.get("valueRanges", [])]


def update_values(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    values: list[list[str]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Overwrite an A1 range with ``values``; return the API update summary."""
    return with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption=input_option,
                body={"values": values},
            )
            .execute()
        )
    )


def append_values(
    service: Service,
    spreadsheet_id: str,
    range_: str,
    values: list[list[str]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Append rows after the table in ``range_``; return the API append summary.

    The rows are inserted (``INSERT_ROWS``), shifting anything below the table
    down. The API's default, ``OVERWRITE``, writes into whatever follows the
    table, so a second block of data one blank row below would lose its first
    rows.

    Retried on a rate limit only: an append that failed with a 5xx may still
    have landed, and repeating it would add the rows twice.
    """
    return with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption=input_option,
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute()
        ),
        statuses=RATE_LIMIT_STATUSES,
    )


def clear_values(service: Service, spreadsheet_id: str, range_: str) -> dict[str, Any]:
    """Clear the values in an A1 range (keeps formatting); return the summary."""
    return with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_, body={})
            .execute()
        )
    )


def batch_update_values(
    service: Service,
    spreadsheet_id: str,
    data: list[tuple[str, list[list[str]]]],
    *,
    input_option: str = USER_ENTERED,
) -> dict[str, Any]:
    """Write several ``(range, values)`` pairs in one API round-trip.

    Wraps ``spreadsheets.values.batchUpdate`` — the efficient path for scattered,
    non-contiguous writes (e.g. one cell each across many rows/columns), so a
    conditional update touches the API once regardless of how many cells change.
    """
    body = {
        "valueInputOption": input_option,
        "data": [{"range": range_, "values": values} for range_, values in data],
    }
    return with_retry(
        lambda: (
            service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )
    )


def list_tabs(service: Service, spreadsheet_id: str) -> list[str]:
    """Return the spreadsheet's tab (sheet) titles in order.

    The friendly path for discovering a valid range: Sheets errors on an unknown
    tab name, so a range-less ``sheets-get`` uses the first tab from here.
    """
    result = with_retry(
        lambda: (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
            .execute()
        )
    )
    return [s["properties"]["title"] for s in result.get("sheets", [])]


def first_tab(service: Service, spreadsheet_id: str) -> str:
    """Return the spreadsheet's first tab title, raising if it has none.

    The shared default for range-less reads and keyed updates: both target the
    first tab when the caller names none.
    """
    tabs = list_tabs(service, spreadsheet_id)
    if not tabs:
        raise ValueError("spreadsheet has no tabs")
    return tabs[0]


# -- tab lookups and structural updates --


def _tab_ids(tabs: list[dict[str, Any]]) -> dict[str, int]:
    """Map each tab title to its ``sheetId`` from a ``spreadsheets.get`` sheets list."""
    # The API omits zero-valued fields, so the first tab's sheetId 0 may be absent.
    return {s["properties"]["title"]: s["properties"].get("sheetId", 0) for s in tabs}


def tab_sheet_ids(service: Service, spreadsheet_id: str) -> dict[str, int]:
    """Map each tab title to its numeric ``sheetId``, in tab order."""
    result = with_retry(
        lambda: (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)"
            )
            .execute()
        )
    )
    return _tab_ids(result.get("sheets", []))


@dataclass(frozen=True)
class TabGrid:
    """A tab's ``sheetId`` and grid size, for structural requests.

    ``row_count`` and ``column_count`` are the grid's size, not its data: a
    fresh tab is 1000 rows by 26 columns whatever it holds. A write outside the
    grid fails, so a request that adds rows or columns past it grows it first.
    """

    sheet_id: int
    row_count: int
    column_count: int


@dataclass(frozen=True)
class TabListing:
    """The tabs of a spreadsheet as one read found them: each title with its grid.

    ``grids`` maps each tab's title to its :class:`TabGrid`, in tab order. A
    run that reads it once knows which tabs exist, and which title a
    ``sheetId`` has now, for every tab it goes on to. The grid sizes are as
    they were when it was read: a write that depends on one reads it again
    (:func:`tab_grid`).
    """

    grids: Mapping[str, TabGrid]

    @property
    def titles(self) -> list[str]:
        """The tab titles, in tab order."""
        return list(self.grids)

    def title_of(self, sheet_id: int) -> str | None:
        """The title of the tab with ``sheet_id``, or None when there is none."""
        for title, grid in self.grids.items():
            if grid.sheet_id == sheet_id:
                return title
        return None


def tab_listing(service: Service, spreadsheet_id: str) -> TabListing:
    """Read every tab's title, ``sheetId``, and grid size, in one request."""
    result = with_retry(
        lambda: (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties(sheetId,title,gridProperties)",
            )
            .execute()
        )
    )
    grids: dict[str, TabGrid] = {}
    for sheet in result.get("sheets", []):
        props = sheet["properties"]
        # The API omits zero-valued fields, as for sheetId in _tab_ids.
        grid = props.get("gridProperties", {})
        grids[props["title"]] = TabGrid(
            sheet_id=props.get("sheetId", 0),
            row_count=grid.get("rowCount", 0),
            column_count=grid.get("columnCount", 0),
        )
    return TabListing(grids)


def tab_grid(service: Service, spreadsheet_id: str, tab: str) -> TabGrid:
    """Return ``tab``'s ``sheetId`` and grid size, in one ``spreadsheets.get``.

    Raises ValueError when the spreadsheet has no tab named ``tab``.
    """
    listing = tab_listing(service, spreadsheet_id)
    _lookup_tab({t: grid.sheet_id for t, grid in listing.grids.items()}, tab)
    return listing.grids[tab]


def _lookup_tab(tab_ids: dict[str, int], tab: str | None) -> str:
    """Return ``tab`` (or the first tab when None), raising if it does not exist."""
    if not tab_ids:
        raise ValueError("spreadsheet has no tabs")
    if tab is None:
        return next(iter(tab_ids))
    if tab not in tab_ids:
        raise ValueError(f"no tab named {tab!r}; tabs: {list(tab_ids)}")
    return tab


# -- grid data --


class GridTooLargeError(ValueError):
    """A grid read came back too large for the HTTP client to decode.

    A ValueError, so a run reports it for its tab and goes on to the next.
    """


def decode_errors(module: str = "httplib2.decode") -> tuple[type[Exception], ...]:
    """The errors ``httplib2`` raises for a response too large to decode.

    ``DecodeRatioError`` and ``DecodeLimitError`` exist only in recent
    releases. An older one that lacks them has nothing to catch, and the
    result is empty.
    """
    try:
        found = importlib.import_module(module)
    except ImportError:
        return ()
    names = ("DecodeRatioError", "DecodeLimitError")
    return tuple(getattr(found, name) for name in names if hasattr(found, name))


# Looked up once, at import. They are plain Exceptions, not HttpErrors.
_DECODE_ERRORS = decode_errors()


def pull_grid(
    service: Service, spreadsheet_id: str, range_: str, fields: str
) -> dict[str, Any]:
    """Read the grid data of one range, under a ``fields`` mask.

    One ``spreadsheets.get`` with ``includeGridData``. ``range_`` is an A1
    range of one tab, and ``fields`` the mask of what to return, such as
    ``sheets(data(rowData(values(hyperlink))))``. Returns the range's
    ``GridData`` as the API gives it (``rowData``, ``columnMetadata``, and so
    on, as the mask names them), and an empty dict for a range that holds
    none of it.

    The mask is required. An unmasked read returns every property of every
    cell the tab has formatted, which for a tab of 26,000 formatted cells was
    18.6 MB, where the read of its links was 382 bytes. A response too large
    for ``httplib2`` to decode raises :class:`GridTooLargeError`.
    """
    if not fields.strip():
        raise ValueError(
            "pull_grid: a fields mask is required; an unmasked grid read "
            "returns every property of every cell"
        )
    try:
        result = with_retry(
            lambda: (
                service.spreadsheets()
                .get(
                    spreadsheetId=spreadsheet_id,
                    ranges=[range_],
                    includeGridData=True,
                    fields=fields,
                )
                .execute()
            )
        )
    except _DECODE_ERRORS as e:
        raise GridTooLargeError(
            f"the grid read of {range_!r} came back too large to decode ({e}); "
            "narrow the range or the fields mask"
        ) from e
    blocks = [
        block for sheet in result.get("sheets", []) for block in sheet.get("data", [])
    ]
    return blocks[0] if blocks else {}


def batch_update_spreadsheet(
    service: Service, spreadsheet_id: str, requests: list[dict[str, Any]]
) -> dict[str, Any]:
    """Send structural ``requests`` via ``spreadsheets.batchUpdate``.

    Not :func:`batch_update_values` (``spreadsheets.values.batchUpdate``), which
    writes cell values; this one edits the spreadsheet resource itself. Its
    requests add or remove rows, columns, and rules, so it retries on a rate
    limit only.
    """
    return with_retry(
        lambda: (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
            .execute()
        ),
        statuses=RATE_LIMIT_STATUSES,
    )
