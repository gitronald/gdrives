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
