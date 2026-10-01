"""Shared test helpers for gdrives tests.

Drive API response shapes based on docs/drive-api.md. The Sheets fakes are
the library's, in ``gdrives.testing``.
"""

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from gdrives.testing import http_error


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


# -- Drive files fake --

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

_QUOTED = r"'((?:[^'\\]|\\.)*)'"


def _unescaped(value: str) -> str:
    """Undo ``escape_query_value``."""
    return re.sub(r"\\(.)", r"\1", value)


class _DriveRequest:
    """A Drive request: ``execute()`` runs it, as does a resumable upload's last chunk.

    ``next_chunk()`` answers once with progress and no response, then with the
    response, as a resumable upload of two chunks does.
    """

    def __init__(self, run: Any) -> None:
        self._run = run
        self._chunks = 0
        self.retries: list[int] = []

    def execute(self) -> dict[str, Any]:
        return self._run()

    def next_chunk(self, num_retries: int = 0) -> tuple[Any, dict[str, Any] | None]:
        self.retries.append(num_retries)
        self._chunks += 1
        if self._chunks == 1:
            return object(), None
        return None, self._run()


class FakeDriveFiles:
    """A fake of the Drive v3 ``files`` resource that holds files and their content.

    ``files`` are dicts with ``id``, ``name``, ``mimeType``, and optionally
    ``parents`` and ``content`` (bytes). A response carries ``size`` and
    ``md5Checksum`` computed from the content, for a file that has any, and
    never for a Google-native one. A file in the trash has ``trashed`` set,
    and ``files.list`` leaves it out. ``files.list`` answers the query
    ``'<id>' in parents [and name = '<name>'] and trashed = false``, names
    compared without regard to case, in ``pages`` of that many files.
    ``files.create`` and ``files.update`` take a ``media_body`` and store its
    bytes, and each such request is kept in ``requests``, where ``retries``
    holds the ``num_retries`` of each chunk; with ``corrupt`` set, the stored
    content loses its last byte, so a read-back finds a file that is not the
    one sent; with ``converts`` off, a create that names a native type stores
    the upload as it came instead of converting it. Every call is recorded
    in ``calls``, and a request does nothing until it is executed. ``root``
    is the ID of the file the alias ``root`` names.
    """

    def __init__(
        self,
        files: list[dict[str, Any]],
        *,
        pages: int = 1000,
        root: str | None = None,
    ) -> None:
        self.items = {f["id"]: dict(f) for f in files}
        self.root = root
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.corrupt = False
        self.converts = True
        self.pages = pages
        self.requests: list[_DriveRequest] = []

    def files(self) -> "FakeDriveFiles":
        return self

    def named(self, method: str) -> list[dict[str, Any]]:
        """The kwargs of each call of ``method``, in order."""
        return [kwargs for name, kwargs in self.calls if name == method]

    def _shown(self, item: dict[str, Any]) -> dict[str, Any]:
        shown = {k: v for k, v in item.items() if k != "content"}
        content = item.get("content")
        if content is not None:
            shown["size"] = str(len(content))
            shown["md5Checksum"] = hashlib.md5(content).hexdigest()
        return shown

    def _write(self, run: Any) -> _DriveRequest:
        """A create or an update, kept in ``requests`` for what it was sent with."""
        request = _DriveRequest(run)
        self.requests.append(request)
        return request

    def _content(self, media: Any) -> bytes:
        content = media.getbytes(0, media.size())
        return content[:-1] if self.corrupt else content

    def get(self, **kwargs: Any) -> _DriveRequest:
        self.calls.append(("get", kwargs))
        file_id = kwargs["fileId"]
        if file_id == "root" and self.root is not None:
            file_id = self.root
        return _DriveRequest(lambda: self._shown(self.items[file_id]))

    def list(self, **kwargs: Any) -> _DriveRequest:
        self.calls.append(("list", kwargs))
        return _DriveRequest(lambda: self._list(kwargs))

    def _list(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        query = kwargs["q"]
        parent = re.search(_QUOTED + " in parents", query)
        assert parent is not None, query
        parent_id = _unescaped(parent.group(1))
        name = re.search("name = " + _QUOTED, query)
        found = [
            self._shown(item)
            for item in self.items.values()
            if parent_id in item.get("parents", [])
            and not item.get("trashed")
            and (name is None or item["name"].lower() == _unescaped(name[1]).lower())
        ]
        start = int(kwargs.get("pageToken") or 0)
        page: dict[str, Any] = {"files": found[start : start + self.pages]}
        if start + self.pages < len(found):
            page["nextPageToken"] = str(start + self.pages)
        return page

    def create(self, **kwargs: Any) -> _DriveRequest:
        self.calls.append(("create", kwargs))
        return self._write(lambda: self._create(kwargs))

    def _create(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        item = dict(kwargs["body"])
        item["id"] = f"new{len(self.named('create'))}"
        media = kwargs.get("media_body")
        if media is not None:
            # A native type in the metadata is a conversion: Drive keeps it,
            # unless ``converts`` is off, when the upload is stored as it came.
            if not self.converts:
                item["mimeType"] = media.mimetype()
            item.setdefault("mimeType", media.mimetype())
            item["content"] = self._content(media)
        self.items[item["id"]] = item
        return self._shown(item)

    def update(self, **kwargs: Any) -> _DriveRequest:
        self.calls.append(("update", kwargs))
        return self._write(lambda: self._update(kwargs))

    def _update(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        item = self.items[kwargs["fileId"]]
        item.update(kwargs.get("body") or {})
        media = kwargs["media_body"]
        item["mimeType"] = media.mimetype()
        item["content"] = self._content(media)
        return self._shown(item)


def patch_drive_service(monkeypatch: pytest.MonkeyPatch, svc: Any) -> dict[str, Any]:
    """Make ``build_drive_service`` return ``svc``; the dict records its ``scopes``."""
    rec: dict[str, Any] = {}
    monkeypatch.setattr(
        "gdrives.auth.build_drive_service",
        lambda scopes=None: rec.update(scopes=scopes) or svc,
    )
    return rec
