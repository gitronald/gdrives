"""Tests for gdrives.export — file ID extraction and MIME selection."""

import csv
import io
import random

import pytest

from gdrives.export import (
    EXPORT_MIME_TYPES,
    check_newline,
    export_file,
    mime_for_output,
    run,
    set_line_endings,
)

DOCX_MIME = EXPORT_MIME_TYPES[".docx"]
XLSX_MIME = EXPORT_MIME_TYPES[".xlsx"]
PPTX_MIME = EXPORT_MIME_TYPES[".pptx"]
CSV_MIME = EXPORT_MIME_TYPES[".csv"]
TXT_MIME = EXPORT_MIME_TYPES[".txt"]
MD_MIME = EXPORT_MIME_TYPES[".md"]


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

    def test_txt_is_plain_text(self):
        assert mime_for_output("file.txt") == "text/plain"

    def test_md_is_markdown(self):
        assert mime_for_output("file.md") == "text/markdown"

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

    def test_md_uses_markdown_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.md"
        mock_service.files().export().execute.return_value = b"# Title\n"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=MD_MIME)
        assert out.read_bytes() == b"# Title\n"

    def test_txt_uses_plain_text_mime(self, mock_service, tmp_path):
        out = tmp_path / "out.txt"
        mock_service.files().export().execute.return_value = b"Title\n"
        export_file(mock_service, "fid", str(out))
        mock_service.files().export.assert_called_with(fileId="fid", mimeType=TXT_MIME)
        assert out.read_bytes() == b"Title\n"

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


# -- line endings --

# Rows of cells as Drive's CSV export shapes them: CRLF row endings, a cell's own
# line break as an LF (or CRLF) inside quotes.
CELL_CASES = {
    "plain": b"a,b\r\n1,2\r\n",
    "lf in a cell": b'a,"x\ny"\r\n1,2\r\n',
    "crlf in a cell": b'a,"x\r\ny"\r\n1,2\r\n',
    "doubled quote": b'a,"say ""hi""\nbye"\r\n1,2\r\n',
    "quote at the cell's end": b'"a""",b\r\n',
    "empty trailing cell": b"a,b,\r\n1,2,\r\n",
    "empty quoted cell": b'a,"",c\r\n',
    "no final ending": b"a,b\r\n1,2",
    "no final ending, quoted": b'a,"x\ny"',
    "one row": b"a,b\r\n",
    "empty file": b"",
    "only an ending": b"\r\n",
    "blank row": b"a\r\n\r\nb\r\n",
    "bom": b'\xef\xbb\xbfa,b\r\n1,"x\ny"\r\n',
    "quote inside an unquoted cell": b'5" pipe,b\r\n"x\ny",2\r\n',
    "quote after a comma": b'a,"b\nc"\r\n',
    "lone cr outside quotes": b"a,b\rc,d\r\n",
    "lone cr inside quotes": b'a,"b\rc"\r\n',
    "unterminated quote": b'a,"b\r\nc\r\n',
    "not utf-8": b"a,\xff\xfe\r\n\xe9,2\r\n",
    "mixed endings": b"a,b\n1,2\r\n3,4\n",
    "wide": b'"a,b","c\nd",e\r\n',
}


def cells(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("latin-1"), newline="")))


def random_csv(rng: random.Random) -> bytes:
    """A CSV written by ``csv.writer`` from cells of tricky characters."""
    alphabet = ["a", "b", ",", '"', "\n", "\r\n", " ", "\r", "\u00e9", ""]
    rows = [
        ["".join(rng.choices(alphabet, k=rng.randint(0, 5))) for _ in range(3)]
        for _ in range(rng.randint(0, 4))
    ]
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=rng.choice(["\r\n", "\n"])).writerows(rows)
    return buf.getvalue().encode()


class TestSetLineEndingsCsv:
    @pytest.mark.parametrize("name", CELL_CASES)
    @pytest.mark.parametrize("newline", ["lf", "crlf"])
    def test_only_row_endings_change(self, name, newline):
        data = CELL_CASES[name]
        out = set_line_endings(data, ".csv", newline)
        # The cells come out as they went in.
        assert cells(out) == cells(data)
        # The bytes differ from the input at row endings alone.
        ending = b"\n" if newline == "lf" else b"\r\n"
        assert out.replace(ending, b"").count(b'"') == data.count(b'"')
        assert out.endswith(ending) == data.rstrip(b"\r").endswith(b"\n") or not data

    def test_expected_bytes(self):
        data = b'a,"x\ny"\r\n1,"p\r\nq"\r\n'
        assert set_line_endings(data, ".csv", "lf") == b'a,"x\ny"\n1,"p\r\nq"\n'
        assert set_line_endings(b'a,"x\ny"\n1,2', ".csv", "crlf") == b'a,"x\ny"\r\n1,2'

    def test_a_quoted_first_cell_after_a_byte_order_mark_keeps_its_line_break(self):
        data = b'\xef\xbb\xbf"a\nb",c\nd\n'
        out = set_line_endings(data, ".csv", "crlf")
        assert out == b'\xef\xbb\xbf"a\nb",c\r\nd\r\n'
        assert list(csv.reader(io.StringIO(out.decode("utf-8-sig"), newline=""))) == [
            ["a\nb", "c"],
            ["d"],
        ]
        assert set_line_endings(out, ".csv", "lf") == data
        # A mark anywhere else is a cell's own bytes.
        assert set_line_endings(b'a\r\n\xef\xbb\xbf"b\r\n', ".csv", "lf") == (
            b'a\n\xef\xbb\xbf"b\n'
        )

    def test_a_quote_inside_an_unquoted_cell_does_not_open_a_cell(self):
        data = b'5" pipe,b\r\n"x\ny",2\r\n'
        assert set_line_endings(data, ".csv", "lf") == b'5" pipe,b\n"x\ny",2\n'

    def test_an_unterminated_cell_runs_to_the_end_as_csv_reads_it(self):
        data = b'a,"b\r\nc\r\n'
        assert set_line_endings(data, ".csv", "lf") == data
        assert cells(data) == [["a", "b\r\nc\r\n"]]

    def test_a_lone_cr_outside_quotes_is_a_row_ending(self):
        assert set_line_endings(b"a\rb\r\n", ".csv", "lf") == b"a\nb\n"

    def test_a_lone_cr_inside_quotes_is_the_cell_s(self):
        assert set_line_endings(b'"a\rb"\r\n', ".csv", "lf") == b'"a\rb"\n'

    def test_extension_is_matched_in_any_case(self):
        assert set_line_endings(b'"a\nb"\r\n', ".CSV", "lf") == b'"a\nb"\n'

    @pytest.mark.parametrize("name", CELL_CASES)
    def test_round_trips_agree_with_a_direct_conversion(self, name):
        data = CELL_CASES[name]
        assert_round_trips(data)

    def test_round_trips_on_random_csv(self):
        rng = random.Random(20260929)
        for _ in range(500):
            assert_round_trips(random_csv(rng))


def assert_round_trips(data: bytes) -> None:
    lf = set_line_endings(data, ".csv", "lf")
    crlf = set_line_endings(data, ".csv", "crlf")
    # Through the other ending and back is the same as going direct.
    assert set_line_endings(crlf, ".csv", "lf") == lf
    assert set_line_endings(lf, ".csv", "crlf") == crlf
    # Converting twice changes nothing.
    assert set_line_endings(lf, ".csv", "lf") == lf
    assert set_line_endings(crlf, ".csv", "crlf") == crlf
    # A reader sees the same cells before and after.
    assert cells(lf) == cells(crlf) == cells(data)


class TestSetLineEndingsText:
    @pytest.mark.parametrize("extension", [".txt", ".md", ".TXT"])
    def test_every_line_ending_is_rewritten(self, extension):
        data = b"one\r\ntwo\nthree\r\n\r\nfour"
        assert set_line_endings(data, extension, "lf") == b"one\ntwo\nthree\n\nfour"
        assert (
            set_line_endings(data, extension, "crlf")
            == b"one\r\ntwo\r\nthree\r\n\r\nfour"
        )

    def test_quotes_mean_nothing_in_text(self):
        assert set_line_endings(b'say "hi\r\nbye', ".md", "lf") == b'say "hi\nbye'

    def test_a_lone_cr_is_a_line_ending(self):
        assert set_line_endings(b"a\rb\r\n", ".txt", "lf") == b"a\nb\n"
        assert set_line_endings(b"a\r\rb", ".txt", "crlf") == b"a\r\n\r\nb"

    def test_bytes_that_are_not_utf8_pass_through(self):
        data = b"caf\xe9\xff\r\n\x00\xfe\n"
        assert set_line_endings(data, ".txt", "lf") == b"caf\xe9\xff\n\x00\xfe\n"

    def test_empty(self):
        assert set_line_endings(b"", ".txt", "crlf") == b""

    def test_round_trips_agree_with_a_direct_conversion(self):
        rng = random.Random(7)
        pieces = [b"a", b"\r\n", b"\n", b"\r", b"\xff", b" "]
        for _ in range(500):
            data = b"".join(rng.choices(pieces, k=rng.randint(0, 8)))
            lf = set_line_endings(data, ".txt", "lf")
            crlf = set_line_endings(data, ".txt", "crlf")
            assert set_line_endings(crlf, ".txt", "lf") == lf
            assert set_line_endings(lf, ".txt", "crlf") == crlf


class TestExportFileNewline:
    def test_csv_is_rewritten_before_it_appears(self, mock_service, tmp_path, capsys):
        out = tmp_path / "out.csv"
        mock_service.files().export().execute.return_value = b'a,"x\ny"\r\n1,2\r\n'
        export_file(mock_service, "fid", str(out), newline="lf")
        assert out.read_bytes() == b'a,"x\ny"\n1,2\n'
        assert "Exported to" in capsys.readouterr().out

    def test_none_leaves_the_bytes_as_drive_sent_them(self, mock_service, tmp_path):
        out = tmp_path / "out.csv"
        mock_service.files().export().execute.return_value = b"a\r\n"
        export_file(mock_service, "fid", str(out))
        assert out.read_bytes() == b"a\r\n"

    def test_a_failed_rewrite_leaves_no_file(self, mock_service, tmp_path, monkeypatch):
        out = tmp_path / "out.csv"
        out.write_bytes(b"old")
        mock_service.files().export().execute.return_value = b"a\r\n"

        def boom(*args):
            raise RuntimeError("rewrite failed")

        monkeypatch.setattr("gdrives.export.set_line_endings", boom)
        with pytest.raises(RuntimeError):
            export_file(mock_service, "fid", str(out), newline="lf")
        assert out.read_bytes() == b"old"
        assert [p.name for p in tmp_path.iterdir()] == ["out.csv"]

    @pytest.mark.parametrize("name", ["out.docx", "out.xlsx", "out.pptx"])
    def test_a_binary_format_is_refused_before_any_request(
        self, mock_service, tmp_path, name
    ):
        out = tmp_path / name
        with pytest.raises(ValueError, match="text export"):
            export_file(mock_service, "fid", str(out), newline="lf")
        mock_service.files().export.assert_not_called()
        assert not out.exists()

    def test_another_value_is_refused_before_any_request(self, mock_service, tmp_path):
        with pytest.raises(ValueError, match="newline must be one of"):
            export_file(mock_service, "fid", str(tmp_path / "o.csv"), newline="cr")
        mock_service.files().export.assert_not_called()

    def test_check_newline_none_is_always_fine(self):
        check_newline("out.docx", None)


# -- run --


class TestRun:
    def test_builds_service_and_exports_extracted_id(
        self, mock_service, monkeypatch, capsys
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.export_file",
            lambda service, file_id, output, newline=None: rec.update(
                svc=service, fid=file_id, out=output
            ),
        )
        run("https://docs.google.com/document/d/DOCID/edit", "out.docx")
        assert rec["svc"] is mock_service
        assert rec["fid"] == "DOCID"
        assert rec["out"] == "out.docx"
        assert "File ID: DOCID" in capsys.readouterr().err

    def test_a_bad_newline_is_refused_before_authenticating(self, monkeypatch):
        def build():
            raise AssertionError("authenticated")

        monkeypatch.setattr("gdrives.auth.build_drive_service", build)
        with pytest.raises(ValueError, match="text export"):
            run("DOCID", "out.docx", "lf")
        with pytest.raises(ValueError, match="newline must be one of"):
            run("DOCID", "out.csv", "cr")

    def test_passes_the_newline_on(self, mock_service, monkeypatch):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.export_file",
            lambda service, file_id, output, newline: rec.update(n=newline),
        )
        run("DOCID", "out.csv", "crlf")
        assert rec == {"n": "crlf"}
