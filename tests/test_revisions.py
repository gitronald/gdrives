"""Tests for gdrives.revisions — listing and downloading Drive revisions."""

from pathlib import Path
from typing import Any

import pytest
from helpers import make_file, make_gdoc

from gdrives.revisions import (
    EXPORT_EXTENSIONS,
    Revision,
    download_revision,
    list_revisions,
)
from gdrives.testing import http_error

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
PDF_MIME = "application/pdf"

XLSX_MIME = EXPORT_EXTENSIONS["xlsx"][0]
CSV_MIME = EXPORT_EXTENSIONS["csv"][0]
DOCX_MIME = EXPORT_EXTENSIONS["docx"][0]


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "reason"


def _revision_data(
    id_: str,
    *,
    modified: str = "2026-01-01T00:00:00.000Z",
    display: str = "",
    email: str = "",
    mime: str = "",
    size: str | None = None,
    keep_forever: bool | None = None,
    export_links: dict[str, str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"id": id_, "modifiedTime": modified}
    if display or email:
        user: dict[str, Any] = {}
        if display:
            user["displayName"] = display
        if email:
            user["emailAddress"] = email
        data["lastModifyingUser"] = user
    if mime:
        data["mimeType"] = mime
    if size is not None:
        data["size"] = size
    if keep_forever is not None:
        data["keepForever"] = keep_forever
    if export_links is not None:
        data["exportLinks"] = export_links
    return data


# -- list_revisions --


class TestListRevisions:
    def test_single_page(self, mock_service):
        mock_service.revisions().list().execute.return_value = {
            "revisions": [
                _revision_data("1", display="Jane Doe", email="jane@example.com"),
                _revision_data("2", size="1024"),
            ]
        }
        result = list_revisions(mock_service, "FID")
        assert [r.id for r in result] == ["1", "2"]
        assert result[0].modified_by == "Jane Doe (jane@example.com)"
        assert result[1].size == 1024

    def test_pages_through_next_page_token(self, mock_service):
        mock_service.revisions.return_value.list.return_value.execute.side_effect = [
            {"revisions": [_revision_data("1")], "nextPageToken": "TOK"},
            {"revisions": [_revision_data("2")]},
        ]
        result = list_revisions(mock_service, "FID")
        assert [r.id for r in result] == ["1", "2"]
        calls = mock_service.revisions().list.call_args_list
        assert "pageToken" not in calls[0].kwargs
        assert calls[1].kwargs["pageToken"] == "TOK"
        assert calls[0].kwargs["fileId"] == "FID"
        assert calls[0].kwargs["pageSize"] == 1000

    def test_service_account_display_name_equals_email(self, mock_service):
        # A service account's displayName is its own address; showing it twice
        # would be redundant.
        mock_service.revisions().list().execute.return_value = {
            "revisions": [
                _revision_data(
                    "1",
                    display="svc@example.iam.gserviceaccount.com",
                    email="svc@example.iam.gserviceaccount.com",
                )
            ]
        }
        result = list_revisions(mock_service, "FID")
        assert result[0].modified_by == "svc@example.iam.gserviceaccount.com"

    def test_no_modifying_user_is_blank(self, mock_service):
        mock_service.revisions().list().execute.return_value = {
            "revisions": [_revision_data("1")]
        }
        result = list_revisions(mock_service, "FID")
        assert result[0].modified_by == ""

    def test_retries_a_transient_500(self, mock_service, monkeypatch):
        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda *_: None)
        monkeypatch.setattr("gdrives.sheets.retry.random.random", lambda: 0)
        mock_service.revisions.return_value.list.return_value.execute.side_effect = [
            http_error(500, "Internal Error"),
            {"revisions": [_revision_data("1")]},
        ]
        result = list_revisions(mock_service, "FID")
        assert [r.id for r in result] == ["1"]

    def test_native_revision_has_no_size_or_keep_forever(self, mock_service):
        mock_service.revisions().list().execute.return_value = {
            "revisions": [
                _revision_data("1", mime=SHEET_MIME, export_links={CSV_MIME: "link"})
            ]
        }
        result = list_revisions(mock_service, "FID")
        assert result[0].size is None
        assert result[0].keep_forever is None
        assert result[0].export_links == {CSV_MIME: "link"}


# -- download_revision: binary --


class TestDownloadBinary:
    def test_downloads_via_get_media(self, mock_service, tmp_path, monkeypatch):
        mock_service.files().get().execute.return_value = make_file(
            "report.pdf", id="FID", mime=PDF_MIME
        )

        class FakeDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"bytes")
                return (None, True)

        monkeypatch.setattr("gdrives.revisions.MediaIoBaseDownload", FakeDownloader)
        out = tmp_path / "out.pdf"
        target = download_revision(mock_service, "FID", "R1", str(out))
        assert target == out
        assert out.read_bytes() == b"bytes"
        mock_service.revisions().get_media.assert_called_with(
            fileId="FID", revisionId="R1"
        )

    def test_format_refused_for_binary_file(self, mock_service, tmp_path):
        mock_service.files().get().execute.return_value = make_file(
            "report.pdf", id="FID", mime=PDF_MIME
        )
        with pytest.raises(ValueError, match="only applies to a native Google file"):
            download_revision(mock_service, "FID", "R1", str(tmp_path), mime_type="pdf")

    def test_failure_leaves_no_partial_or_final_file(
        self, mock_service, tmp_path, monkeypatch
    ):
        mock_service.files().get().execute.return_value = make_file(
            "report.pdf", id="FID", mime=PDF_MIME
        )

        class FailingDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"partial")
                raise OSError("connection dropped")

        monkeypatch.setattr("gdrives.revisions.MediaIoBaseDownload", FailingDownloader)
        out = tmp_path / "out.pdf"
        with pytest.raises(OSError, match="connection dropped"):
            download_revision(mock_service, "FID", "R1", str(out))
        assert not out.exists()

    def test_directory_output_names_file_by_stem_revision_and_extension(
        self, mock_service, tmp_path, monkeypatch
    ):
        mock_service.files().get().execute.return_value = make_file(
            "report.pdf", id="FID", mime=PDF_MIME
        )

        class FakeDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"bytes")
                return (None, True)

        monkeypatch.setattr("gdrives.revisions.MediaIoBaseDownload", FakeDownloader)
        target = download_revision(mock_service, "FID", "R7", str(tmp_path))
        assert target == tmp_path / "report-R7.pdf"
        assert target.read_bytes() == b"bytes"

    def test_a_revision_id_with_separators_stays_in_the_directory(
        self, mock_service, tmp_path, monkeypatch
    ):
        mock_service.files().get().execute.return_value = make_file(
            "report.pdf", id="FID", mime=PDF_MIME
        )

        class FakeDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"bytes")
                return (None, True)

        monkeypatch.setattr("gdrives.revisions.MediaIoBaseDownload", FakeDownloader)
        out = tmp_path / "out"
        out.mkdir()
        target = download_revision(mock_service, "FID", "../../R7", str(out))
        assert target == out / "report-.._.._R7.pdf"
        assert target.read_bytes() == b"bytes"
        assert [path.name for path in tmp_path.iterdir()] == ["out"]


# -- download_revision: native export --


class TestDownloadNativeExport:
    def _serve(self, mock_service, *, name="Budget", export_links, status=200):
        mock_service.files().get().execute.return_value = make_file(
            name, id="FID", mime=SHEET_MIME
        )
        mock_service.revisions().get().execute.return_value = _revision_data(
            "R1", mime=SHEET_MIME, export_links=export_links
        )
        mock_service._http.request.return_value = (_Resp(status), b"exported-bytes")

    def test_default_format_uses_native_extension(self, mock_service, tmp_path):
        self._serve(mock_service, export_links={XLSX_MIME: "https://x/export.xlsx"})
        out = tmp_path / "out.bin"
        target = download_revision(mock_service, "FID", "R1", str(out))
        assert target == out
        assert out.read_bytes() == b"exported-bytes"
        mock_service._http.request.assert_called_with("https://x/export.xlsx", "GET")

    def test_explicit_format_selects_that_export_link(self, mock_service, tmp_path):
        self._serve(
            mock_service,
            export_links={
                XLSX_MIME: "https://x/export.xlsx",
                CSV_MIME: "https://x/export.csv",
            },
        )
        out = tmp_path / "out.csv"
        download_revision(mock_service, "FID", "R1", str(out), mime_type="csv")
        mock_service._http.request.assert_called_with("https://x/export.csv", "GET")

    @pytest.mark.parametrize("wanted", [CSV_MIME, "csv", ".CSV"])
    def test_a_format_is_a_mime_type_or_an_extension(
        self, mock_service, tmp_path, wanted
    ):
        self._serve(
            mock_service,
            export_links={
                XLSX_MIME: "https://x/export.xlsx",
                CSV_MIME: "https://x/export.csv",
            },
        )
        target = download_revision(
            mock_service, "FID", "R1", str(tmp_path), mime_type=wanted
        )
        assert target.suffix == ".csv"
        mock_service._http.request.assert_called_with("https://x/export.csv", "GET")

    def test_an_unknown_mime_type_is_refused(self, mock_service, tmp_path):
        self._serve(mock_service, export_links={XLSX_MIME: "https://x/export.xlsx"})
        with pytest.raises(ValueError, match="no export format for MIME type"):
            download_revision(
                mock_service, "FID", "R1", str(tmp_path), mime_type="image/png"
            )
        assert list(tmp_path.iterdir()) == []

    def test_two_mime_spellings_of_one_format_both_match(self, mock_service, tmp_path):
        alt_ods_mime = "application/vnd.oasis.opendocument.spreadsheet"
        self._serve(mock_service, export_links={alt_ods_mime: "https://x/export.ods"})
        out = tmp_path / "out.ods"
        download_revision(mock_service, "FID", "R1", str(out), mime_type="ods")
        mock_service._http.request.assert_called_with("https://x/export.ods", "GET")

    def test_unknown_format_lists_offered_extensions(self, mock_service, tmp_path):
        self._serve(
            mock_service,
            export_links={
                XLSX_MIME: "https://x/export.xlsx",
                CSV_MIME: "https://x/export.csv",
            },
        )
        with pytest.raises(ValueError, match="csv, xlsx"):
            download_revision(mock_service, "FID", "R1", str(tmp_path), mime_type="rtf")

    def test_directory_output(self, mock_service, tmp_path):
        self._serve(
            mock_service, name="Budget", export_links={XLSX_MIME: "https://x/e.xlsx"}
        )
        target = download_revision(mock_service, "FID", "R9", str(tmp_path))
        assert target == tmp_path / "Budget-R9.xlsx"
        assert target.read_bytes() == b"exported-bytes"

    def test_native_type_with_no_export_format_is_refused(self, mock_service, tmp_path):
        mock_service.files().get().execute.return_value = make_file(
            "Survey", id="FID", mime="application/vnd.google-apps.form"
        )
        with pytest.raises(ValueError, match="no export format"):
            download_revision(mock_service, "FID", "R1", str(tmp_path))

    def test_export_link_401_raises_named_status(self, mock_service, tmp_path):
        self._serve(
            mock_service, export_links={XLSX_MIME: "https://x/e.xlsx"}, status=401
        )
        from googleapiclient.errors import HttpError

        with pytest.raises(HttpError):
            download_revision(mock_service, "FID", "R1", str(tmp_path))
        assert not (tmp_path / "Budget-R1.xlsx").exists()

    def test_export_link_429_is_retried_then_succeeds(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda *_: None)
        monkeypatch.setattr("gdrives.sheets.retry.random.random", lambda: 0)
        mock_service.files().get().execute.return_value = make_file(
            "Budget", id="FID", mime=SHEET_MIME
        )
        mock_service.revisions().get().execute.return_value = _revision_data(
            "R1", mime=SHEET_MIME, export_links={XLSX_MIME: "https://x/e.xlsx"}
        )
        mock_service._http.request.side_effect = [
            (_Resp(429), b"<html>rate limited</html>"),
            (_Resp(200), b"exported-bytes"),
        ]
        target = download_revision(mock_service, "FID", "R1", str(tmp_path))
        assert target.read_bytes() == b"exported-bytes"


class TestDownloadNativeExportAtomicFailure:
    def test_write_failure_leaves_no_file(self, mock_service, tmp_path, monkeypatch):
        mock_service.files().get().execute.return_value = make_file(
            "Budget", id="FID", mime=SHEET_MIME
        )
        mock_service.revisions().get().execute.return_value = _revision_data(
            "R1", mime=SHEET_MIME, export_links={XLSX_MIME: "https://x/e.xlsx"}
        )
        mock_service._http.request.return_value = (_Resp(200), b"exported-bytes")

        import gdrives.revisions as revisions_module

        class _BoomFile:
            def write(self, _data: bytes) -> int:
                raise OSError("disk full")

        from contextlib import contextmanager

        @contextmanager
        def boom_atomic_output(_target: Path, **_kwargs: Any):
            yield _BoomFile()

        monkeypatch.setattr(revisions_module, "atomic_output", boom_atomic_output)
        out = tmp_path / "out.bin"
        with pytest.raises(OSError, match="disk full"):
            download_revision(mock_service, "FID", "R1", str(out))
        assert not out.exists()


# -- Revision dataclass sanity --


def test_revision_is_frozen():
    r = Revision(
        id="1",
        modified_time="t",
        modified_by="m",
        mime_type="x",
        size=None,
        keep_forever=None,
        export_links=None,
    )
    with pytest.raises(AttributeError):
        setattr(r, "id", "2")


# -- forbidden-method guard --
#
# The hard rule: the module calls revisions.list, revisions.get,
# revisions.get_media, files.get, and a GET of an export link, and nothing
# else. This fake raises on anything else, and every public function is run
# against it.


class _Executable:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


def _forbidden(label: str, name: str) -> Any:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"forbidden call: {label}.{name}")

    return _raise


class _FakeFiles:
    def __init__(self, meta: dict[str, Any]) -> None:
        self._meta = meta

    def get(self, **_kwargs: Any) -> _Executable:
        return _Executable(self._meta)

    def __getattr__(self, name: str) -> Any:
        return _forbidden("files", name)


class _FakeRevisions:
    def __init__(
        self, *, list_response: dict[str, Any], get_response: dict[str, Any]
    ) -> None:
        self._list_response = list_response
        self._get_response = get_response
        self.get_media_calls: list[dict[str, Any]] = []

    def list(self, **_kwargs: Any) -> _Executable:
        return _Executable(self._list_response)

    def get(self, **_kwargs: Any) -> _Executable:
        return _Executable(self._get_response)

    def get_media(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.get_media_calls.append(kwargs)
        return ("media-request", kwargs)

    def __getattr__(self, name: str) -> Any:
        return _forbidden("revisions", name)


class _FakeHttp:
    def __init__(self, response: tuple[Any, bytes]) -> None:
        self._response = response
        self.requests: list[tuple[str, str]] = []

    def request(self, uri: str, method: str) -> tuple[Any, bytes]:
        self.requests.append((uri, method))
        return self._response

    def __getattr__(self, name: str) -> Any:
        return _forbidden("_http", name)


class FakeDriveService:
    """Raises on any call outside the hard rule's five allowed operations."""

    def __init__(
        self,
        *,
        meta: dict[str, Any],
        list_response: dict[str, Any],
        get_response: dict[str, Any],
        http_response: tuple[Any, bytes],
    ) -> None:
        self._files = _FakeFiles(meta)
        self._revisions = _FakeRevisions(
            list_response=list_response, get_response=get_response
        )
        self._http = _FakeHttp(http_response)

    def files(self) -> _FakeFiles:
        return self._files

    def revisions(self) -> _FakeRevisions:
        return self._revisions

    def __getattr__(self, name: str) -> Any:
        return _forbidden("service", name)


class TestForbiddenMethodGuard:
    def test_list_revisions_calls_only_revisions_list(self):
        service = FakeDriveService(
            meta=make_file("x"),
            list_response={"revisions": [_revision_data("1")]},
            get_response={},
            http_response=(_Resp(200), b""),
        )
        result = list_revisions(service, "FID")
        assert [r.id for r in result] == ["1"]

    def test_download_revision_binary_stays_within_the_guard(
        self, tmp_path, monkeypatch
    ):
        service = FakeDriveService(
            meta=make_file("bin.dat", id="FID", mime="application/octet-stream"),
            list_response={},
            get_response={},
            http_response=(_Resp(200), b""),
        )

        class FakeDownloader:
            def __init__(self, fd: Any, request: Any) -> None:
                self.fd = fd

            def next_chunk(self) -> tuple[None, bool]:
                self.fd.write(b"bytes")
                return (None, True)

        monkeypatch.setattr("gdrives.revisions.MediaIoBaseDownload", FakeDownloader)
        target = download_revision(service, "FID", "R1", str(tmp_path / "out.dat"))
        assert target.read_bytes() == b"bytes"

    def test_download_revision_native_stays_within_the_guard(self, tmp_path):
        service = FakeDriveService(
            meta=make_gdoc("Notes", id="FID"),
            list_response={},
            get_response=_revision_data(
                "R1",
                mime="application/vnd.google-apps.document",
                export_links={DOCX_MIME: "https://x/e.docx"},
            ),
            http_response=(_Resp(200), b"docx-bytes"),
        )
        target = download_revision(service, "FID", "R1", str(tmp_path))
        assert target.read_bytes() == b"docx-bytes"
        assert service._http.requests == [("https://x/e.docx", "GET")]
