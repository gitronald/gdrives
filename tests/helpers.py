"""Shared test helpers for gdrives tests.

Drive API response shapes based on docs/drive-api.md.
"""

from typing import Any


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
    """Stand-in for a Sheets API request whose ``execute()`` returns a fixed dict."""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _FakeValues:
    def __init__(self, service: "FakeSheetsService") -> None:
        self._service = service

    def get(self, **kwargs: Any) -> _Executable:
        return self._service._record("values.get", kwargs, "get")

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


class FakeSheetsService:
    """A minimal fake of the Sheets v4 discovery service.

    Records every ``(method, kwargs)`` call in ``calls`` and returns the preset
    response for that method, so tests can assert both the request shape and the
    parsed result. Register responses by key: ``get``/``update``/``append``/
    ``clear`` (values ops) and ``meta`` (``spreadsheets.get``, used by
    ``list_tabs``). Any unregistered key returns ``{}``.
    """

    def __init__(self, **responses: dict[str, Any]) -> None:
        self.responses: dict[str, dict[str, Any]] = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(
        self, method: str, kwargs: dict[str, Any], response_key: str
    ) -> _Executable:
        self.calls.append((method, kwargs))
        return _Executable(self.responses.get(response_key, {}))

    def spreadsheets(self) -> _FakeSpreadsheets:
        return _FakeSpreadsheets(self)


# -- Docs API fake --


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
    testable without the API. ``headers`` / ``footers`` / ``footnotes`` (each
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
                return pattern.subn(req["replaceText"], current)

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
