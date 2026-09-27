"""Shared test helpers for gdrives tests.

Drive API response shapes based on docs/drive-api.md.
"""

import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


def local_file(tab: Any) -> Path:
    """A tab's local file, which a tab loaded from a config always has."""
    assert tab.local is not None
    return tab.local


def plain(text: str) -> str:
    """``text`` without terminal styling, which Typer forces on GitHub Actions."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def plant_scratch_symlink(
    monkeypatch: pytest.MonkeyPatch, directory: Path, victim: Path
) -> Path:
    """Plant a symlink to ``victim`` at the scratch name atomic_output tries first.

    ``atomic_output`` names its scratch file ``.gdrives-<random>``. Pinning
    tempfile's candidate names makes the first try land on the planted link, so
    a writer that followed symlinks would write into ``victim``; a safe one
    (``O_EXCL``, as ``NamedTemporaryFile`` opens) skips to the second name.
    """
    monkeypatch.setattr(
        tempfile, "_get_candidate_names", lambda: iter(["planted", "fresh"])
    )
    link = directory / ".gdrives-planted"
    link.symlink_to(victim)
    return link


def make_file(
    name: str,
    *,
    id: str = "file_id",
    mime: str = "text/plain",
    url: str | None = None,
    modified: str = "2026-01-15T10:30:00.000Z",
    owner_email: str = "user@example.com",
    owner_display: str = "Test User",
    sharing_user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a realistic Drive API file response dict."""
    result: dict[str, Any] = {
        "id": id,
        "name": name,
        "mimeType": mime,
        "modifiedTime": modified,
        "owners": [{"displayName": owner_display, "emailAddress": owner_email}],
    }
    if url is not None:
        result["webViewLink"] = url
    if sharing_user is not None:
        result["sharingUser"] = sharing_user
    return result


def make_folder(
    name: str,
    *,
    id: str = "folder_id",
    modified: str = "2026-01-15T10:30:00.000Z",
    owner_email: str = "user@example.com",
) -> dict[str, Any]:
    """Build a realistic Drive API folder response dict."""
    return make_file(
        name,
        id=id,
        mime="application/vnd.google-apps.folder",
        modified=modified,
        owner_email=owner_email,
    )


def make_gdoc(
    name: str,
    *,
    id: str = "doc_id",
    url: str | None = None,
    modified: str = "2026-01-15T10:30:00.000Z",
    owner_email: str = "user@example.com",
) -> dict[str, Any]:
    """Build a realistic Drive API Google Doc response dict."""
    return make_file(
        name,
        id=id,
        mime="application/vnd.google-apps.document",
        url=url or f"https://docs.google.com/document/d/{id}/edit",
        modified=modified,
        owner_email=owner_email,
    )


def make_gslides(
    name: str,
    *,
    id: str = "slides_id",
    url: str | None = None,
    modified: str = "2026-01-15T10:30:00.000Z",
    owner_email: str = "user@example.com",
) -> dict[str, Any]:
    """Build a realistic Drive API Google Slides response dict."""
    return make_file(
        name,
        id=id,
        mime="application/vnd.google-apps.presentation",
        url=url or f"https://docs.google.com/presentation/d/{id}/edit",
        modified=modified,
        owner_email=owner_email,
    )


def mock_list_response(
    files: list[dict[str, Any]], next_page_token: str | None = None
) -> dict[str, Any]:
    """Build a files.list API response."""
    result: dict[str, Any] = {"files": files}
    if next_page_token:
        result["nextPageToken"] = next_page_token
    return result


# -- Sheets API fake --


class _Executable:
    """Stand-in for a Sheets API request whose ``execute()`` returns a fixed dict.

    A preset exception is raised instead, so a test can model an API failure.
    """

    def __init__(self, result: dict[str, Any] | BaseException) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _FakeValues:
    def __init__(self, service: "FakeSheetsService") -> None:
        self._service = service

    def get(self, **kwargs: Any) -> _Executable:
        return self._service._record("values.get", kwargs, "get")

    def batchGet(self, **kwargs: Any) -> _Executable:  # camelCase: Sheets API name
        return self._service._record("values.batchGet", kwargs, "batchGet")

    def update(self, **kwargs: Any) -> _Executable:
        return self._service._record("values.update", kwargs, "update")

    def append(self, **kwargs: Any) -> _Executable:
        return self._service._record("values.append", kwargs, "append")

    def clear(self, **kwargs: Any) -> _Executable:
        return self._service._record("values.clear", kwargs, "clear")

    def batchUpdate(self, **kwargs: Any) -> _Executable:  # camelCase: Sheets API name
        return self._service._record("values.batchUpdate", kwargs, "batchUpdate")


class _FakeSpreadsheets:
    def __init__(self, service: "FakeSheetsService") -> None:
        self._service = service

    def values(self) -> _FakeValues:
        return _FakeValues(self._service)

    def get(self, **kwargs: Any) -> _Executable:
        return self._service._record("spreadsheets.get", kwargs, "meta")

    def batchUpdate(self, **kwargs: Any) -> _Executable:  # camelCase: Sheets API name
        return self._service._record(
            "spreadsheets.batchUpdate", kwargs, "spreadsheetBatchUpdate"
        )


class FakeSheetsService:
    """A minimal fake of the Sheets v4 discovery service.

    Records every ``(method, kwargs)`` call in ``calls`` and returns the preset
    response for that method, so tests can assert both the request shape and the
    parsed result. Register responses by key: ``get``/``batchGet``/``update``/
    ``append``/``clear``/``batchUpdate`` (values ops), ``meta``
    (``spreadsheets.get``, used by ``list_tabs``, ``tab_sheet_ids``, and
    ``list_conditional_rules``), and ``spreadsheetBatchUpdate``
    (``spreadsheets.batchUpdate``, the structural one). Any unregistered key
    returns ``{}``. A list registers successive responses, one per call, with
    the last repeating: ``get=[before, after]`` models a sheet that a
    collaborator edits between two reads. A response that is an exception is
    raised by ``execute()``: ``get=[http_error(503, ...), {...}]`` fails once,
    then succeeds.
    """

    def __init__(self, **responses: Any) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(
        self, method: str, kwargs: dict[str, Any], response_key: str
    ) -> _Executable:
        self.calls.append((method, kwargs))
        response = self.responses.get(response_key, {})
        if isinstance(response, list):
            response = response.pop(0) if len(response) > 1 else response[0]
        return _Executable(response)

    def spreadsheets(self) -> _FakeSpreadsheets:
        return _FakeSpreadsheets(self)


def patch_sheets_service(
    monkeypatch: pytest.MonkeyPatch, svc: FakeSheetsService
) -> dict[str, Any]:
    """Make ``build_sheets_service`` return ``svc``; the dict records its ``scopes``.

    Patched at its source (``gdrives.auth``), since the ``run_*`` entry points
    import it lazily.
    """
    rec: dict[str, Any] = {}
    monkeypatch.setattr(
        "gdrives.auth.build_sheets_service",
        lambda scopes=None: rec.update(scopes=scopes) or svc,
    )
    return rec


# -- Docs API fake --


def raw_text(doc: dict[str, Any], tab_id: str | None = None) -> str:
    """A tab's searchable text, all segments joined: what a replace can match.

    ``count_occurrences`` searches each segment separately; tests use this
    joined view to assert exactly what a replacement changed. Non-text elements
    show as the U+FFFC placeholder the search uses.
    """
    from gdrives.docs import _text_runs, tab_segments

    return "".join(
        "".join(_text_runs(segment.get("content", [])))
        for segment in tab_segments(doc, tab_id)
    )


def http_error(status: int, reason: str) -> Any:
    """Build the ``HttpError`` the discovery client raises for an API failure."""
    from googleapiclient.errors import HttpError

    class _Resp:
        def __init__(self) -> None:
            self.status = status
            self.reason = reason

    return HttpError(_Resp(), reason.encode())


def render_content(text: str) -> list[dict[str, Any]]:
    """Render plain text as Docs body content with real ``startIndex``/``endIndex``.

    Index 0 is the leading section break; each line (trailing newline kept)
    becomes one paragraph holding a single text run. ``text`` must end with a
    newline, like every Docs body. Indices count code points, which equals the
    API's UTF-16 units for the BMP-only text used in tests.
    """
    content: list[dict[str, Any]] = [
        {"startIndex": 0, "endIndex": 1, "sectionBreak": {}}
    ]
    index = 1
    for line in text.splitlines(keepends=True):
        end = index + len(line)
        content.append(
            {
                "startIndex": index,
                "endIndex": end,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": index,
                            "endIndex": end,
                            "textRun": {"content": line},
                        }
                    ]
                },
            }
        )
        index = end
    return content


class _Deferred:
    """Stand-in for a Docs API request; ``execute()`` runs the fake's handler."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def execute(self) -> dict[str, Any]:
        return self._fn()


class _FakeDocuments:
    def __init__(self, service: "FakeDocsService") -> None:
        self._service = service

    def get(self, **kwargs: Any) -> _Deferred:
        self._service.calls.append(("documents.get", kwargs))
        return _Deferred(self._service.document)

    def batchUpdate(self, **kwargs: Any) -> _Deferred:  # camelCase: Docs API name
        self._service.calls.append(("documents.batchUpdate", kwargs))
        return _Deferred(lambda: self._service._batch_update(kwargs["body"]))

    def create(self, **kwargs: Any) -> _Deferred:
        self._service.calls.append(("documents.create", kwargs))
        return _Deferred(lambda: self._service._create(kwargs["body"]))


class FakeDocsService:
    """A minimal, stateful fake of the Docs v1 discovery service.

    Holds one document as plain text per tab (``tabs`` maps tab ID -> text;
    every tab ends with the newline Docs requires) and applies ``insertText``,
    ``deleteContentRange``, and ``replaceAllText`` requests to that text with
    real index arithmetic, so shifted-index and stale-revision paths are
    testable without the API. The formatting requests (``deleteParagraphBullets``,
    ``updateParagraphStyle``, ``updateTextStyle``) change no text but have their
    ranges checked against it. ``headers`` / ``footers`` / ``footnotes`` (each
    segment ID -> text) render as the first tab's extra segments; only
    ``replaceAllText`` touches them, as in the API. Every successful batch
    bumps ``revision``; a batch whose ``writeControl.requiredRevisionId`` is
    stale raises the 400 HttpError the real API returns, and a failing batch
    leaves the text untouched. Every ``(method, kwargs)`` call is recorded in
    ``calls``.
    """

    def __init__(
        self,
        text: str = "",
        *,
        tabs: dict[str, str] | None = None,
        document_id: str = "DOC",
        title: str = "Untitled",
        headers: dict[str, str] | None = None,
        footers: dict[str, str] | None = None,
        footnotes: dict[str, str] | None = None,
    ) -> None:
        source = tabs if tabs is not None else {"t.0": text}
        self.tabs: dict[str, str] = {
            tab_id: self._terminated(body) for tab_id, body in source.items()
        }
        # Extra segments of the first tab: kind -> segment ID -> text.
        self.segments: dict[str, dict[str, str]] = {
            kind: {sid: self._terminated(body) for sid, body in (given or {}).items()}
            for kind, given in (
                ("headers", headers),
                ("footers", footers),
                ("footnotes", footnotes),
            )
        }
        self.titles: dict[str, str] = {
            tab_id: f"Tab {i + 1}" for i, tab_id in enumerate(self.tabs)
        }
        self.document_id = document_id
        self.title = title
        self.revision = "rev-1"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def _terminated(text: str) -> str:
        return text if text.endswith("\n") else text + "\n"

    @property
    def text(self) -> str:
        """The first tab's text (the common single-tab case)."""
        return next(iter(self.tabs.values()))

    def _first_tab(self) -> str:
        return next(iter(self.tabs))

    def _bump(self) -> None:
        self.revision = f"rev-{int(self.revision.split('-')[1]) + 1}"

    def edit_externally(self, text: str, tab_id: str | None = None) -> None:
        """Replace a tab's text as a collaborator would, bumping the revision."""
        self.tabs[tab_id or self._first_tab()] = self._terminated(text)
        self._bump()

    def documents(self) -> _FakeDocuments:
        return _FakeDocuments(self)

    def document(self) -> dict[str, Any]:
        """Render the current state the way ``documents.get`` returns it."""
        return {
            "documentId": self.document_id,
            "title": self.title,
            "revisionId": self.revision,
            "tabs": [
                {
                    "tabProperties": {
                        "tabId": tab_id,
                        "title": self.titles[tab_id],
                        "index": i,
                    },
                    "documentTab": self._document_tab(i, body),
                }
                for i, (tab_id, body) in enumerate(self.tabs.items())
            ],
        }

    def _document_tab(self, index: int, body: str) -> dict[str, Any]:
        """Render one tab's body, plus the extra segments on the first tab."""
        tab: dict[str, Any] = {"body": {"content": render_content(body)}}
        if index == 0:
            for kind, segments in self.segments.items():
                if segments:
                    tab[kind] = {
                        sid: {"content": render_content(text)}
                        for sid, text in segments.items()
                    }
        return tab

    def _create(self, body: dict[str, Any]) -> dict[str, Any]:
        """Become a fresh, empty, single-tab document titled per ``body``."""
        self.document_id = "NEW_DOC"
        self.title = body["title"]
        self.tabs = {"t.0": "\n"}
        self.titles = {"t.0": "Tab 1"}
        self.revision = "rev-1"
        return self.document()

    def _batch_update(self, body: dict[str, Any]) -> dict[str, Any]:
        required = body.get("writeControl", {}).get("requiredRevisionId")
        if required is not None and required != self.revision:
            raise http_error(400, "The provided revision ID is not the latest")
        if not body.get("requests"):
            raise http_error(400, "requests must not be empty")
        snapshot = dict(self.tabs)
        segments_snapshot = {k: dict(v) for k, v in self.segments.items()}
        try:
            replies = [self._apply(request) for request in body["requests"]]
        except Exception:
            self.tabs, self.segments = snapshot, segments_snapshot
            raise
        self._bump()
        return {
            "documentId": self.document_id,
            "replies": replies,
            "writeControl": {"requiredRevisionId": self.revision},
        }

    def _apply(self, request: dict[str, Any]) -> dict[str, Any]:
        """Apply one batchUpdate request to the text model; return its reply."""
        if "insertText" in request:
            req = request["insertText"]
            if "endOfSegmentLocation" in req:
                tab_id = req["endOfSegmentLocation"].get("tabId") or self._first_tab()
                current = self.tabs[tab_id]
                pos = len(current) - 1  # immediately before the final newline
            else:
                tab_id = req["location"].get("tabId") or self._first_tab()
                current = self.tabs[tab_id]
                pos = req["location"]["index"] - 1
            if not req["text"]:
                raise http_error(400, "insertText: text must not be empty")
            if pos < 0 or pos > len(current) - 1:
                raise http_error(400, f"insertText: index {pos + 1} out of range")
            self.tabs[tab_id] = current[:pos] + req["text"] + current[pos:]
            return {}
        if "deleteContentRange" in request:
            span = request["deleteContentRange"]["range"]
            tab_id = span.get("tabId") or self._first_tab()
            current = self.tabs[tab_id]
            start, end = span["startIndex"] - 1, span["endIndex"] - 1
            if start < 0 or start >= end:
                raise http_error(400, "deleteContentRange: range must not be empty")
            if end > len(current) - 1:
                raise http_error(
                    400, "deleteContentRange: cannot delete the final newline"
                )
            self.tabs[tab_id] = current[:start] + current[end:]
            return {}
        style = next(
            (
                request[kind]
                for kind in (
                    "deleteParagraphBullets",
                    "updateParagraphStyle",
                    "updateTextStyle",
                )
                if kind in request
            ),
            None,
        )
        if style is not None:
            # Formatting is not modelled, but the range is checked like the
            # API does: inside the tab, final newline included.
            span = style["range"]
            current = self.tabs[span.get("tabId") or self._first_tab()]
            if not 1 <= span["startIndex"] < span["endIndex"] <= len(current) + 1:
                raise http_error(400, f"style range {span} out of bounds")
            return {}
        if "replaceAllText" in request:
            req = request["replaceAllText"]
            find = req["containsText"]["text"]
            match_case = req["containsText"].get("matchCase", False)
            tab_ids = req.get("tabsCriteria", {}).get("tabIds") or list(self.tabs)

            def substitute(current: str) -> tuple[str, int]:
                if match_case:
                    return current.replace(find, req["replaceText"]), current.count(
                        find
                    )
                import re

                pattern = re.compile(re.escape(find), re.IGNORECASE)
                return pattern.subn(lambda match: req["replaceText"], current)

            changed = 0
            for tab_id in tab_ids:
                self.tabs[tab_id], n = substitute(self.tabs[tab_id])
                changed += n
            # Headers, footers, and footnotes are edited too (first tab only).
            if self._first_tab() in tab_ids:
                for segments in self.segments.values():
                    for sid, text in segments.items():
                        segments[sid], n = substitute(text)
                        changed += n
            return {"replaceAllText": {"occurrencesChanged": changed}}
        raise http_error(400, f"unsupported request: {sorted(request)}")


# -- Stateful Sheets fake --


def _letters_to_index(letters: str) -> int:
    """A1 column letters to a 0-based index (A -> 0, AA -> 26).

    The fake parses A1 itself, independently of ``gdrives.sheets.a1``, so a
    bug there cannot cancel out against the same bug here.
    """
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _split_range(range_: str) -> tuple[str, str]:
    """Split ``'Tab'!A1:B2`` (quoted or not) into the tab title and the span."""
    if range_.startswith("'"):
        title: list[str] = []
        i = 1
        while True:
            j = range_.index("'", i)
            if range_[j + 1 : j + 2] == "'":  # a doubled quote is an escaped one
                title.append(range_[i : j + 1])
                i = j + 2
                continue
            title.append(range_[i:j])
            rest = range_[j + 1 :]
            break
        return "".join(title), rest.removeprefix("!")
    tab, _, span = range_.partition("!")
    return tab, span


_SPAN_RE = re.compile(r"([A-Za-z]*)(\d*)(?::([A-Za-z]*)(\d*))?")


def _as_moment(value: date) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day)


def _serial(value: date) -> int | float:
    """A date cell's serial number: days since 1899-12-30, whole for a date.

    Worked out here, independently of ``gdrives.sheets.cells``, for the same
    reason the fake parses A1 itself.
    """
    days = (_as_moment(value) - datetime(1899, 12, 30)) / timedelta(days=1)
    return int(days) if days == int(days) else days


def _shown_date(value: date) -> str:
    """A date cell as displayed: ``m/d/yyyy``, and whole seconds for a date-time.

    The display rounds to what its format shows, as the Sheets UI does, so
    ``23:59:59.999`` shows as midnight of the next day.
    """
    if not isinstance(value, datetime):
        return f"{value.month}/{value.day}/{value.year}"
    shown = (value + timedelta(milliseconds=500)).replace(microsecond=0)
    return f"{shown.month}/{shown.day}/{shown.year} {shown.hour}:{shown:%M:%S}"


def _displayed(value: Any) -> str:
    """How the Sheets UI shows a stored value (the FORMATTED_VALUE render)."""
    if isinstance(value, date):
        return _shown_date(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class _GridTab:
    """One tab of a :class:`FakeSheetGrid`: a dense grid of stored values.

    ``cells[r][c]`` is the value at 0-based row ``r`` and column ``c``, None
    when the cell is empty. ``widths`` holds each column's pixel width.
    """

    def __init__(self, sheet_id: int, title: str, rows: int, columns: int) -> None:
        self.sheet_id = sheet_id
        self.title = title
        self.cells: list[list[Any]] = [[None] * columns for _ in range(rows)]
        self.widths: list[int] = [FakeSheetGrid.DEFAULT_WIDTH] * columns

    @property
    def row_count(self) -> int:
        return len(self.cells)

    @property
    def column_count(self) -> int:
        return len(self.widths)

    def copy(self) -> "_GridTab":
        clone = _GridTab(self.sheet_id, self.title, 0, 0)
        clone.cells = [list(row) for row in self.cells]
        clone.widths = list(self.widths)
        return clone


class _GridRequest:
    """Stand-in for a Sheets API request; ``execute()`` runs the fake's handler.

    The call is recorded when the request is built, as the discovery client
    builds one per attempt; hooks and failures fire when it is executed.
    """

    def __init__(self, grid: "FakeSheetGrid", method: str, handler: Any) -> None:
        self._grid = grid
        self._method = method
        self._handler = handler

    def execute(self) -> dict[str, Any]:
        return self._grid._execute(self._method, self._handler)


class _GridValues:
    def __init__(self, grid: "FakeSheetGrid") -> None:
        self._grid = grid

    def get(self, **kwargs: Any) -> _GridRequest:
        return self._grid._request("values.get", kwargs, self._grid._values_get)

    def batchGet(self, **kwargs: Any) -> _GridRequest:  # camelCase: Sheets API name
        return self._grid._request(
            "values.batchGet", kwargs, self._grid._values_batch_get
        )

    def update(self, **kwargs: Any) -> _GridRequest:
        return self._grid._request("values.update", kwargs, self._grid._values_update)

    def batchUpdate(self, **kwargs: Any) -> _GridRequest:  # camelCase: Sheets API name
        return self._grid._request(
            "values.batchUpdate", kwargs, self._grid._values_batch_update
        )


class _GridSpreadsheets:
    def __init__(self, grid: "FakeSheetGrid") -> None:
        self._grid = grid

    def values(self) -> _GridValues:
        return _GridValues(self._grid)

    def get(self, **kwargs: Any) -> _GridRequest:
        return self._grid._request("spreadsheets.get", kwargs, self._grid._meta)

    def batchUpdate(self, **kwargs: Any) -> _GridRequest:  # camelCase: Sheets API name
        return self._grid._request(
            "spreadsheets.batchUpdate", kwargs, self._grid._batch_update
        )


class FakeSheetGrid:
    """A stateful fake of the Sheets v4 service: a grid of stored values per tab.

    Unlike :class:`FakeSheetsService`, which replays preset responses, this one
    holds each tab's cells and applies writes to them, so a test can assert the
    sheet a sequence of calls leaves behind. It models the API where the code
    depends on it:

    - ``values.get`` / ``values.batchGet`` return rows from the range's top-left
      cell, each truncated at its last non-empty cell, with trailing empty rows
      dropped. ``UNFORMATTED_VALUE`` returns what was stored (a number seeded
      as a number, a RAW-written string as that string); the default
      ``FORMATTED_VALUE`` returns the displayed string.
    - A cell seeded with a ``date`` or a ``datetime`` is a date cell. It reads
      as its display text (``m/d/yyyy``, to the whole second), or under
      ``UNFORMATTED_VALUE`` with ``SERIAL_NUMBER``, which is the API's default
      ``dateTimeRenderOption``, as its serial number. A string that looks like
      a date is a string under every option.
    - ``values.update`` / ``values.batchUpdate`` store each value as given (an
      empty string clears the cell). ``USER_ENTERED`` parsing is not modelled.
      A batch is applied all or nothing.
    - ``spreadsheets.batchUpdate`` applies ``insertDimension``,
      ``appendDimension``, ``deleteDimension``, ``updateCells`` (from its
      ``start``, honouring the ``fields`` mask), ``updateDimensionProperties``
      (``pixelSize``), ``addSheet`` (a 1000 x 26 grid), and ``deleteSheet``,
      all or nothing.
    - Any read or write outside a tab's grid raises the 400 ``HttpError`` the
      API returns; so do an unknown tab, a duplicate tab title, inheriting
      from before row or column 0, and deleting every row or column.

    Every ``(method, kwargs)`` call is recorded in ``calls``. ``edit_externally``
    registers a change a collaborator makes just before a given call runs, and
    ``fail`` makes a given call raise instead of running.
    """

    DEFAULT_ROWS = 1000
    DEFAULT_COLUMNS = 26
    DEFAULT_WIDTH = 100

    def __init__(
        self,
        tabs: dict[str, list[list[Any]]] | None = None,
        *,
        rows: int = DEFAULT_ROWS,
        columns: int = DEFAULT_COLUMNS,
    ) -> None:
        self.tabs: list[_GridTab] = []
        for title, grid in (tabs if tabs is not None else {"Sheet1": []}).items():
            tab = _GridTab(len(self.tabs), title, rows, columns)
            self.tabs.append(tab)
            self.write(title, grid)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counts: dict[str, int] = {}
        self._edits: dict[tuple[str, int], Any] = {}
        self._failures: dict[tuple[str, int], BaseException] = {}

    # -- test-side access (not recorded as calls) --

    def tab(self, title: str) -> _GridTab:
        for tab in self.tabs:
            if tab.title == title:
                return tab
        raise KeyError(title)

    def write(self, title: str, grid: list[list[Any]], row: int = 1) -> None:
        """Store ``grid`` from spreadsheet row ``row``, column A, as typed."""
        cells = self.tab(title).cells
        for r, values in enumerate(grid, start=row - 1):
            for c, value in enumerate(values):
                cells[r][c] = None if value == "" else value

    def values(self, title: str) -> list[list[Any]]:
        """The tab's stored values as the API would return them (truncated)."""
        return self._truncated(self.tab(title).cells)

    def resize(self, title: str, rows: int, columns: int) -> None:
        """Set a tab's grid size, keeping the cells that still fit."""
        tab = self.tab(title)
        tab.cells = [
            (row + [None] * columns)[:columns]
            for row in (tab.cells + [[] for _ in range(rows)])[:rows]
        ]
        tab.widths = (tab.widths + [self.DEFAULT_WIDTH] * columns)[:columns]

    def edit_externally(self, edit: Any, *, before: str, occurrence: int = 1) -> None:
        """Run ``edit(self)`` just before the ``occurrence``-th ``before`` call runs.

        Models a collaborator changing the sheet between two of the code's
        calls: ``before="values.batchUpdate"`` edits after the code's read
        and before its write.
        """
        self._edits[(before, occurrence)] = edit

    def fail(self, method: str, error: BaseException, *, occurrence: int = 1) -> None:
        """Make the ``occurrence``-th ``method`` call raise ``error`` instead."""
        self._failures[(method, occurrence)] = error

    @property
    def methods(self) -> list[str]:
        """The recorded calls' method names, in order."""
        return [method for method, _ in self.calls]

    def spreadsheets(self) -> _GridSpreadsheets:
        return _GridSpreadsheets(self)

    # -- request plumbing --

    def _request(
        self, method: str, kwargs: dict[str, Any], handler: Any
    ) -> _GridRequest:
        self.calls.append((method, kwargs))
        return _GridRequest(self, method, lambda: handler(**kwargs))

    def _execute(self, method: str, handler: Any) -> dict[str, Any]:
        self._counts[method] = self._counts.get(method, 0) + 1
        slot = (method, self._counts[method])
        edit = self._edits.pop(slot, None)
        if edit is not None:
            edit(self)
        failure = self._failures.pop(slot, None)
        if failure is not None:
            raise failure
        return handler()

    def _find(self, title: str) -> _GridTab:
        for tab in self.tabs:
            if tab.title == title:
                return tab
        raise http_error(400, f"Unable to parse range: {title}")

    def _find_id(self, sheet_id: int) -> _GridTab:
        for tab in self.tabs:
            if tab.sheet_id == sheet_id:
                return tab
        raise http_error(400, f"No grid with id: {sheet_id}")

    @staticmethod
    def _truncated(cells: list[list[Any]]) -> list[list[Any]]:
        rows = []
        for row in cells:
            end = max((c + 1 for c, v in enumerate(row) if v is not None), default=0)
            rows.append(["" if v is None else v for v in row[:end]])
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def _span(self, range_: str) -> tuple[_GridTab, int, int, int, int]:
        """Resolve an A1 range to its tab and 0-based, end-exclusive bounds.

        Raises the API's 400 for an unknown tab or a range past the grid.
        """
        title, span = _split_range(range_)
        tab = self._find(title)
        match = _SPAN_RE.fullmatch(span)
        if match is None:
            raise http_error(400, f"Unable to parse range: {range_}")
        c1, r1, c2, r2 = match.groups()
        start_col = _letters_to_index(c1) if c1 else 0
        start_row = int(r1) - 1 if r1 else 0
        if span and c2 is None and r2 is None:  # a single cell
            end_col = start_col + 1 if c1 else tab.column_count
            end_row = start_row + 1 if r1 else tab.row_count
        else:
            end_col = _letters_to_index(c2) + 1 if c2 else tab.column_count
            end_row = int(r2) if r2 else tab.row_count
        if end_row > tab.row_count or end_col > tab.column_count:
            raise http_error(
                400,
                f"Range ({range_}) exceeds grid limits. Max rows: "
                f"{tab.row_count}, max columns: {tab.column_count}",
            )
        return tab, start_row, end_row, start_col, end_col

    def _read(
        self, range_: str, render: str | None, date_time: str | None = None
    ) -> dict[str, Any]:
        tab, r1, r2, c1, c2 = self._span(range_)
        rows = self._truncated([row[c1:c2] for row in tab.cells[r1:r2]])
        if render != "UNFORMATTED_VALUE":
            rows = [[_displayed(v) for v in row] for row in rows]
        else:
            shown = _shown_date if date_time == "FORMATTED_STRING" else _serial
            rows = [
                [shown(v) if isinstance(v, date) else v for v in row] for row in rows
            ]
        result: dict[str, Any] = {"range": range_, "majorDimension": "ROWS"}
        if rows:  # the API omits "values" for an empty range
            result["values"] = rows
        return result

    # -- handlers --

    def _values_get(self, **kwargs: Any) -> dict[str, Any]:
        return self._read(
            kwargs["range"],
            kwargs.get("valueRenderOption"),
            kwargs.get("dateTimeRenderOption"),
        )

    def _values_batch_get(self, **kwargs: Any) -> dict[str, Any]:
        render = kwargs.get("valueRenderOption")
        date_time = kwargs.get("dateTimeRenderOption")
        return {
            "valueRanges": [self._read(r, render, date_time) for r in kwargs["ranges"]]
        }

    def _store(self, range_: str, values: list[list[Any]]) -> int:
        tab, r1, r2, c1, c2 = self._span(range_)
        width = max((len(row) for row in values), default=0)
        if ":" not in range_:
            # A single cell (or bare tab) is only the start: data runs on from
            # it, up to the grid's edge.
            r2, c2 = tab.row_count, tab.column_count
        if r1 + len(values) > r2 or c1 + width > c2:
            raise http_error(
                400, f"Requested writing within range [{range_}], but tried more"
            )
        for r, row in enumerate(values, start=r1):
            for c, value in enumerate(row, start=c1):
                tab.cells[r][c] = None if value == "" else value
        return sum(len(row) for row in values)

    def _atomically(self, apply: Any) -> Any:
        snapshot = [tab.copy() for tab in self.tabs]
        try:
            return apply()
        except Exception:
            self.tabs = snapshot
            raise

    def _values_update(self, **kwargs: Any) -> dict[str, Any]:
        updated = self._atomically(
            lambda: self._store(kwargs["range"], kwargs["body"]["values"])
        )
        return {"updatedRange": kwargs["range"], "updatedCells": updated}

    def _values_batch_update(self, **kwargs: Any) -> dict[str, Any]:
        data = kwargs["body"]["data"]
        updated = self._atomically(
            lambda: sum(self._store(d["range"], d["values"]) for d in data)
        )
        return {"totalUpdatedCells": updated}

    def _meta(self, **kwargs: Any) -> dict[str, Any]:
        sheets = []
        for index, tab in enumerate(self.tabs):
            props: dict[str, Any] = {
                "title": tab.title,
                "gridProperties": {
                    "rowCount": tab.row_count,
                    "columnCount": tab.column_count,
                },
            }
            # The API omits zero-valued fields: sheetId 0 and index 0.
            if tab.sheet_id:
                props["sheetId"] = tab.sheet_id
            if index:
                props["index"] = index
            sheets.append({"properties": props})
        return {"sheets": sheets}

    def _batch_update(self, **kwargs: Any) -> dict[str, Any]:
        requests = kwargs["body"]["requests"]
        if not requests:
            raise http_error(400, "Must specify at least one request.")
        replies = self._atomically(lambda: [self._apply(r) for r in requests])
        return {"spreadsheetId": kwargs["spreadsheetId"], "replies": replies}

    def _apply(self, request: dict[str, Any]) -> dict[str, Any]:
        (kind,) = request
        body = request[kind]
        handler = getattr(self, f"_req_{kind}", None)
        if handler is None:
            raise http_error(400, f"unsupported request: {kind}")
        return handler(body)

    def _dimension(self, span: dict[str, Any]) -> tuple[_GridTab, bool, int, int]:
        """A DimensionRange's tab, whether it spans rows, and its bounds."""
        tab = self._find_id(span.get("sheetId", 0))
        rows = span["dimension"] == "ROWS"
        return tab, rows, span.get("startIndex", 0), span["endIndex"]

    def _req_insertDimension(self, body: dict[str, Any]) -> dict[str, Any]:
        tab, rows, start, end = self._dimension(body["range"])
        size = tab.row_count if rows else tab.column_count
        if not 0 <= start < end or start >= size:
            raise http_error(400, f"insertDimension: bad range {start}:{end}")
        inherit = body.get("inheritFromBefore", False)
        if inherit and start == 0:
            raise http_error(400, "Cannot inherit from before the first index")
        count = end - start
        if rows:
            width = tab.column_count
            tab.cells[start:start] = [[None] * width for _ in range(count)]
        else:
            source = tab.widths[start - 1 if inherit else start]
            for row in tab.cells:
                row[start:start] = [None] * count
            tab.widths[start:start] = [source] * count
        return {}

    def _req_appendDimension(self, body: dict[str, Any]) -> dict[str, Any]:
        tab = self._find_id(body.get("sheetId", 0))
        length = body["length"]
        if length < 1:
            raise http_error(400, "appendDimension: length must be positive")
        if body["dimension"] == "ROWS":
            tab.cells.extend([None] * tab.column_count for _ in range(length))
        else:
            for row in tab.cells:
                row.extend([None] * length)
            tab.widths.extend([self.DEFAULT_WIDTH] * length)
        return {}

    def _req_deleteDimension(self, body: dict[str, Any]) -> dict[str, Any]:
        tab, rows, start, end = self._dimension(body["range"])
        size = tab.row_count if rows else tab.column_count
        if not 0 <= start < end <= size:
            raise http_error(400, f"deleteDimension: bad range {start}:{end}")
        if end - start == size:
            which = "rows" if rows else "columns"
            raise http_error(400, f"You can't delete all the {which} on the sheet.")
        if rows:
            del tab.cells[start:end]
        else:
            for row in tab.cells:
                del row[start:end]
            del tab.widths[start:end]
        return {}

    def _req_updateCells(self, body: dict[str, Any]) -> dict[str, Any]:
        if "start" not in body:
            raise http_error(400, "the fake models updateCells with start only")
        start = body["start"]
        tab = self._find_id(start.get("sheetId", 0))
        r0, c0 = start.get("rowIndex", 0), start.get("columnIndex", 0)
        fields = body["fields"]
        if fields not in ("userEnteredValue", "*"):
            raise http_error(400, f"the fake models no fields mask {fields!r}")
        for r, row in enumerate(body.get("rows", []), start=r0):
            for c, cell in enumerate(row.get("values", []), start=c0):
                if r >= tab.row_count or c >= tab.column_count:
                    raise http_error(
                        400,
                        f"updateCells: cell ({r}, {c}) is outside the grid "
                        f"({tab.row_count} x {tab.column_count})",
                    )
                value = cell.get("userEnteredValue")
                if value is None:
                    tab.cells[r][c] = None  # the mask names it, so it is cleared
                    continue
                (value_kind,) = value
                if value_kind not in ("stringValue", "numberValue", "boolValue"):
                    raise http_error(400, f"the fake models no {value_kind}")
                stored = value[value_kind]
                tab.cells[r][c] = None if stored == "" else stored
        return {}

    def _req_updateDimensionProperties(self, body: dict[str, Any]) -> dict[str, Any]:
        tab, rows, start, end = self._dimension(body["range"])
        if rows or body["fields"] != "pixelSize":
            raise http_error(400, "the fake models column pixelSize only")
        if not 0 <= start < end <= tab.column_count:
            raise http_error(400, f"updateDimensionProperties: bad range {start}:{end}")
        tab.widths[start:end] = [body["properties"]["pixelSize"]] * (end - start)
        return {}

    def _req_addSheet(self, body: dict[str, Any]) -> dict[str, Any]:
        title = body["properties"]["title"]
        if any(tab.title == title for tab in self.tabs):
            raise http_error(
                400,
                f'A sheet with the name "{title}" already exists. '
                "Please enter another name.",
            )
        sheet_id = max((tab.sheet_id for tab in self.tabs), default=-1) + 1
        tab = _GridTab(sheet_id, title, self.DEFAULT_ROWS, self.DEFAULT_COLUMNS)
        self.tabs.append(tab)
        return {"addSheet": {"properties": {"sheetId": sheet_id, "title": title}}}

    def _req_deleteSheet(self, body: dict[str, Any]) -> dict[str, Any]:
        self.tabs.remove(self._find_id(body["sheetId"]))
        return {}
