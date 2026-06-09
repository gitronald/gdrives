"""Tests for gdrives.export — file ID extraction and MIME selection."""

import pytest

from gdrives.export import (
    EXPORT_MIME_TYPES,
    export_file,
    mime_for_output,
    run,
)

DOCX_MIME = EXPORT_MIME_TYPES[".docx"]
XLSX_MIME = EXPORT_MIME_TYPES[".xlsx"]
PPTX_MIME = EXPORT_MIME_TYPES[".pptx"]
CSV_MIME = EXPORT_MIME_TYPES[".csv"]


# -- mime_for_output --


class TestMimeForOutput:
    def test_docx(self):
        assert mime_for_output("file.docx") == DOCX_MIME

    def test_xlsx(self):
        assert mime_for_output("file.xlsx") == XLSX_MIME

    def test_pptx(self):
        assert mime_for_output("file.pptx") == PPTX_MIME

    def test_csv(self):
        assert mime_for_output("file.csv") == CSV_MIME

    def test_uppercase_extension(self):
        assert mime_for_output("FILE.XLSX") == XLSX_MIME

    def test_path_with_directory(self):
        assert mime_for_output("/tmp/out/report.docx") == DOCX_MIME

    def test_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported output extension"):
            mime_for_output("file.pdf")

    def test_no_extension(self):
        with pytest.raises(ValueError, match="Unsupported output extension"):
            mime_for_output("file")


# -- export_file --


class TestExportFile:
    def test_docx_uses_docx_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.docx"
        mock_service.files().export().execute.return_value = b"docx-bytes"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=DOCX_MIME)
        assert out.read_bytes() == b"docx-bytes"

    def test_xlsx_uses_xlsx_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.xlsx"
        mock_service.files().export().execute.return_value = b"xlsx-bytes"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=XLSX_MIME)
        assert out.read_bytes() == b"xlsx-bytes"

    def test_pptx_uses_pptx_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.pptx"
        mock_service.files().export().execute.return_value = b"pptx-bytes"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=PPTX_MIME)
        assert out.read_bytes() == b"pptx-bytes"

    def test_csv_uses_csv_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.csv"
        mock_service.files().export().execute.return_value = b"a,b\n1,2\n"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=CSV_MIME)
        assert out.read_bytes() == b"a,b\n1,2\n"

    def test_unsupported_extension_does_not_call_api(self, mock_service, tmp_path):
        out = tmp_path / "out.pdf"
        with pytest.raises(ValueError):
            export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_not_called()
        assert not out.exists()

    def test_creates_missing_parent_dir(self, mock_service, tmp_path):
        # A nested -o path whose parent doesn't exist must be created, not crash.
        out = tmp_path / "new" / "sub" / "out.docx"
        mock_service.files().export().execute.return_value = b"docx-bytes"
        export_file(mock_service, "fid", str(out))
        assert out.read_bytes() == b"docx-bytes"


# -- run --


class TestRun:
    def test_builds_service_and_exports_extracted_id(
        self, mock_service, monkeypatch, capsys
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.export_file",
            lambda service, file_id, output: rec.update(
                svc=service, fid=file_id, out=output
            ),
        )
        run("https://docs.google.com/document/d/DOCID/edit", "out.docx")
        assert rec["svc"] is mock_service
        assert rec["fid"] == "DOCID"
        assert rec["out"] == "out.docx"
        assert "File ID: DOCID" in capsys.readouterr().err
