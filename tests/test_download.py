"""Tests for gdrives.download — single-file and folder downloads."""

import pytest
from helpers import make_file, make_folder, make_gdoc, make_gslides

from gdrives.download import (
    classify_entry,
    download_entry,
    download_file,
    download_single,
    download_walk,
    format_bytes,
    get_file_metadata,
    print_summary,
    run,
    safe_filename,
    summarize,
    unique_path,
)
from gdrives.files import WalkItem

PDF_MIME = "application/pdf"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SLIDES_MIME = "application/vnd.google-apps.presentation"
FORM_MIME = "application/vnd.google-apps.form"


def _item(f, *, ancestors=(), depth=0, descended=False):
    """Build a WalkItem for a flat list, mirroring what walk_tree would yield."""
    return WalkItem(file=f, ancestors=ancestors, depth=depth, descended=descended)


# -- safe_filename --


class TestSafeFilename:
    def test_replaces_slash(self):
        assert safe_filename("a/b") == "a_b"

    def test_replaces_null_byte(self):
        assert safe_filename("a\x00b") == "a_b"

    def test_strips_surrounding_whitespace(self):
        assert safe_filename("  name  ") == "name"

    def test_empty_becomes_file(self):
        assert safe_filename("") == "file"

    def test_blank_becomes_file(self):
        assert safe_filename("   ") == "file"

    def test_dotdot_neutralized(self):
        # '..' must not survive — out / '..' would escape the target directory.
        assert safe_filename("..") == "__"

    def test_single_dot_neutralized(self):
        assert safe_filename(".") == "_"

    def test_dotdot_with_whitespace_neutralized(self):
        assert safe_filename("  ..  ") == "__"

    def test_dotfile_preserved(self):
        assert safe_filename(".env") == ".env"

    def test_replaces_backslash(self):
        # On Windows '\' is a path separator; neutralize it like '/'.
        assert safe_filename("a\\b") == "a_b"

    def test_backslash_traversal_neutralized(self):
        # '..\\..\\evil' must not survive as a Windows path traversal.
        assert safe_filename("..\\..\\evil") == ".._.._evil"


# -- format_bytes --


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(512) == "512.0 B"

    def test_kilobytes(self):
        assert format_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert format_bytes(5 * 1024 * 1024) == "5.0 MB"

    def test_petabytes_fallthrough(self):
        assert format_bytes(3 * 1024**5) == "3.0 PB"


# -- unique_path --


class TestUniquePath:
    def test_nonexistent_unchanged(self, tmp_path):
        p = tmp_path / "a.txt"
        assert unique_path(p) == p

    def test_existing_gets_suffix(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("x")
        assert unique_path(p) == tmp_path / "a (1).txt"

    def test_multiple_collisions_increment(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "a (1).txt").write_text("x")
        assert unique_path(tmp_path / "a.txt") == tmp_path / "a (2).txt"


# -- get_file_metadata --


class TestGetFileMetadata:
    def test_requests_id_name_mime_and_all_drives(self, mock_service):
        mock_service.files().get().execute.return_value = make_file("a.pdf", id="X")
        meta = get_file_metadata(mock_service, "X")
        mock_service.files().get.assert_called_with(
            fileId="X", fields="id, name, mimeType", supportsAllDrives=True
        )
        assert meta["id"] == "X"


# -- download_file --


class TestDownloadFile:
    def test_passes_supports_all_drives_and_writes_bytes(
        self, mock_service, tmp_path, monkeypatch
    ):
        out = tmp_path / "x.bin"

        class FakeDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"chunk-bytes")
                return (None, True)

        monkeypatch.setattr("gdrives.download.MediaIoBaseDownload", FakeDownloader)
        n = download_file(mock_service, "FID", str(out))

        mock_service.files().get_media.assert_called_with(
            fileId="FID", supportsAllDrives=True
        )
        assert out.read_bytes() == b"chunk-bytes"
        assert n == len(b"chunk-bytes")

    def test_failure_leaves_no_part_or_final_file(
        self, mock_service, tmp_path, monkeypatch
    ):
        # A mid-stream failure must drop the partial .part and leave nothing
        # at the final path — no orphaned half-written download.
        out = tmp_path / "x.bin"

        class FailingDownloader:
            def __init__(self, fd, request):
                self.fd = fd

            def next_chunk(self):
                self.fd.write(b"partial")
                raise OSError("connection dropped")

        monkeypatch.setattr("gdrives.download.MediaIoBaseDownload", FailingDownloader)
        with pytest.raises(OSError, match="connection dropped"):
            download_file(mock_service, "FID", str(out))
        assert not out.exists()
        assert not (tmp_path / "x.bin.part").exists()


# -- download_entry --


class TestDownloadEntry:
    def test_binary_downloads(self, mock_service, tmp_path, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.download_file",
            lambda s, fid, path: rec.update(fid=fid, path=path) or 5,
        )
        monkeypatch.setattr(
            "gdrives.download.export_file",
            lambda *a: pytest.fail("export_file must not run for a binary file"),
        )
        f = make_file("report.pdf", id="P1", mime=PDF_MIME)
        download_entry(mock_service, f, tmp_path)
        assert rec["fid"] == "P1"
        assert rec["path"] == str(tmp_path / "report.pdf")

    def test_gdoc_exports_docx(self, mock_service, tmp_path, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.export_file",
            lambda s, fid, path: rec.update(fid=fid, path=path),
        )
        monkeypatch.setattr(
            "gdrives.download.download_file",
            lambda *a: pytest.fail("download_file should not be called for a Doc"),
        )
        f = make_gdoc("My Doc", id="D1")
        download_entry(mock_service, f, tmp_path)
        assert rec["fid"] == "D1"
        assert rec["path"] == str(tmp_path / "My Doc.docx")

    def test_gsheet_exports_xlsx(self, mock_service, tmp_path, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.export_file",
            lambda s, fid, path: rec.update(path=path),
        )
        f = make_file("Budget", id="S1", mime=SHEET_MIME)
        download_entry(mock_service, f, tmp_path)
        assert rec["path"] == str(tmp_path / "Budget.xlsx")

    def test_gslides_exports_pptx(self, mock_service, tmp_path, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.export_file",
            lambda s, fid, path: rec.update(fid=fid, path=path),
        )
        monkeypatch.setattr(
            "gdrives.download.download_file",
            lambda *a: pytest.fail("download_file should not be called for Slides"),
        )
        f = make_gslides("Deck", id="P1")
        download_entry(mock_service, f, tmp_path)
        assert rec["fid"] == "P1"
        assert rec["path"] == str(tmp_path / "Deck.pptx")

    def test_native_without_export_format_skipped(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gdrives.download.export_file",
            lambda *a: pytest.fail("export_file should not be called for a Form"),
        )
        monkeypatch.setattr(
            "gdrives.download.download_file",
            lambda *a: pytest.fail("download_file should not be called for a Form"),
        )
        f = make_file("Survey", id="F1", mime=FORM_MIME)
        download_entry(mock_service, f, tmp_path)  # no-op, no exception

    def test_local_collision_gets_suffix(self, mock_service, tmp_path, monkeypatch):
        (tmp_path / "report.pdf").write_text("existing")
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.download_file",
            lambda s, fid, path: rec.update(path=path) or 1,
        )
        f = make_file("report.pdf", id="P1", mime=PDF_MIME)
        download_entry(mock_service, f, tmp_path)
        assert rec["path"] == str(tmp_path / "report (1).pdf")


# -- download_single --


class TestDownloadSingle:
    def test_creates_dir_and_delegates_to_entry(
        self, mock_service, tmp_path, monkeypatch
    ):
        dest = tmp_path / "new" / "sub"
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.download_entry",
            lambda s, f, out: rec.update(out=out, f=f),
        )
        meta = make_file("a.pdf", id="X", mime=PDF_MIME)
        download_single(mock_service, meta, str(dest))
        assert dest.is_dir()
        assert rec["out"] == dest
        assert rec["f"] is meta


# -- classify_entry --


class TestClassifyEntry:
    def test_binary(self):
        assert classify_entry(make_file("a.pdf", mime=PDF_MIME)) == "binary"

    def test_native_with_export_format(self):
        assert classify_entry(make_gdoc("doc")) == "export"
        assert classify_entry(make_gslides("deck")) == "export"
        assert classify_entry(make_file("s", mime=SHEET_MIME)) == "export"

    def test_native_without_export_format(self):
        assert classify_entry(make_file("survey", mime=FORM_MIME)) == "skip"


# -- summarize --


class TestSummarize:
    def test_counts_binary_native_and_skipped(self):
        items = [
            _item({**make_file("a.pdf", mime=PDF_MIME), "size": "100"}),
            _item(make_gdoc("doc")),
            _item(make_gslides("deck")),
            _item(make_file("survey", mime=FORM_MIME)),
        ]
        summary = summarize(items)
        assert summary["binary_files"] == 1
        assert summary["binary_bytes"] == 100
        assert summary["auto_export"] == 2  # gdoc + gslides
        assert summary["skipped_natives"] == 1

    def test_counts_descended_and_skipped_subfolders(self):
        items = [
            _item(make_folder("in", id="IN"), descended=True),
            _item(make_folder("out", id="OUT"), descended=False),
        ]
        summary = summarize(items)
        assert summary["subfolders"] == 1
        assert summary["skipped_subfolders"] == 1

    def test_non_numeric_size_ignored(self):
        items = [
            _item(
                {
                    **make_file("weird.bin", mime="application/octet-stream"),
                    "size": "NaN",
                }
            )
        ]
        summary = summarize(items)
        assert summary["binary_files"] == 1
        assert summary["binary_bytes"] == 0


# -- shared tree fixture --


def _tree_list_children(s, fid):
    """list_children fake: root has a subfolder + a 5-byte pdf; sub has a 7-byte pdf."""
    if fid == "root":
        return [
            make_folder("sub", id="SUB"),
            {**make_file("a.pdf", id="A", mime=PDF_MIME), "size": "5"},
        ]
    if fid == "SUB":
        return [{**make_file("b.pdf", id="B", mime=PDF_MIME), "size": "7"}]
    return []


# -- run (dispatch + single walk) --


class TestRun:
    def test_url_file_dispatches_to_single(self, mock_service, tmp_path, monkeypatch):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_file(
            "a.pdf", id="FID", mime=PDF_MIME
        )
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.download_single",
            lambda s, meta, od: rec.update(meta=meta, od=od),
        )
        monkeypatch.setattr(
            "gdrives.download.download_walk",
            lambda *a, **k: pytest.fail("folder flow should not run for a file"),
        )
        run("https://drive.google.com/file/d/FID/view", str(tmp_path))
        assert rec["meta"]["id"] == "FID"
        assert rec["od"] == str(tmp_path)

    def test_path_file_resolves_with_allow_files(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        rec = {}

        def fake_resolve(path, service=None, *, allow_files=False):
            rec.update(path=path, allow_files=allow_files)
            return "RID"

        monkeypatch.setattr("gdrives.resolve.resolve_path", fake_resolve)
        mock_service.files().get().execute.return_value = make_file(
            "a.pdf", id="RID", mime=PDF_MIME
        )
        monkeypatch.setattr("gdrives.download.download_single", lambda *a: None)
        run("My Drive/refs/a.pdf", str(tmp_path))
        assert rec["path"] == "My Drive/refs/a.pdf"
        assert rec["allow_files"] is True

    def test_folder_proceed_walks_once_then_downloads(
        self, mock_service, tmp_path, monkeypatch
    ):
        # The proceed path must walk the tree exactly once (one list_children
        # call per folder), then download every entry from that same walk.
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_folder("refs", id="root")
        calls = []

        def spy(s, fid):
            calls.append(fid)
            return _tree_list_children(s, fid)

        monkeypatch.setattr("gdrives.files.list_children", spy)
        got = []
        monkeypatch.setattr(
            "gdrives.download.download_entry", lambda s, f, out: got.append(f["id"])
        )
        monkeypatch.setattr(
            "gdrives.download.download_single",
            lambda *a: pytest.fail("single-file flow should not run for a folder"),
        )
        run("https://drive.google.com/drive/folders/root", str(tmp_path), yes=True)
        assert calls == ["root", "SUB"]  # exactly one walk, not two
        assert set(got) == {"A", "B"}

    def test_folder_abort_walks_once_and_downloads_nothing(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        # Declining the prompt still scans once (the summary), but downloads
        # nothing — and never walks a second time.
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_folder("refs", id="root")
        calls = []

        def spy(s, fid):
            calls.append(fid)
            return _tree_list_children(s, fid)

        monkeypatch.setattr("gdrives.files.list_children", spy)
        monkeypatch.setattr("gdrives.download.typer.confirm", lambda *a, **k: False)
        monkeypatch.setattr(
            "gdrives.download.download_entry",
            lambda *a: pytest.fail("must not download when aborted"),
        )
        run("https://drive.google.com/drive/folders/root", str(tmp_path))
        assert calls == ["root", "SUB"]  # one walk for the scan, no second pass
        assert "Aborted." in capsys.readouterr().err

    def test_folder_with_nothing_to_download_skips(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_folder(
            "empty", id="root"
        )
        monkeypatch.setattr(
            "gdrives.files.list_children",
            lambda s, fid: [make_file("survey", mime=FORM_MIME)],
        )
        monkeypatch.setattr(
            "gdrives.download.download_entry",
            lambda *a: pytest.fail("nothing to download, must not call download"),
        )
        run("https://drive.google.com/drive/folders/root", str(tmp_path))


# -- print_summary --


class TestPrintSummary:
    def test_prints_every_populated_section(self, capsys):
        summary = {
            "binary_files": 3,
            "binary_bytes": 2048,
            "auto_export": 2,
            "skipped_natives": 1,
            "subfolders": 4,
            "skipped_subfolders": 5,
        }
        print_summary(summary, "out")
        err = capsys.readouterr().err
        assert "3 binary file(s)" in err
        assert "auto-export" in err
        assert "native file(s) skipped" in err
        assert "subfolder(s) to create" in err
        assert "subfolder(s) skipped (depth limit)" in err


# -- download_walk --


class TestDownloadWalk:
    def test_downloads_entries_into_their_parent_dirs(
        self, mock_service, tmp_path, monkeypatch
    ):
        items = [
            _item(make_folder("sub", id="SUB"), descended=True),
            _item(
                make_file("b.pdf", id="B", mime=PDF_MIME),
                ancestors=("sub",),
                depth=1,
            ),
            _item(make_file("a.pdf", id="A", mime=PDF_MIME)),
        ]
        got = {}

        def rec(s, f, out):
            got[f["id"]] = str(out)

        monkeypatch.setattr("gdrives.download.download_entry", rec)
        download_walk(mock_service, items, str(tmp_path))
        assert (tmp_path / "sub").is_dir()
        assert got["A"] == str(tmp_path)  # root file -> output dir
        assert got["B"] == str(tmp_path / "sub")  # nested file -> its subdir

    def test_creates_empty_within_depth_subdir(
        self, mock_service, tmp_path, monkeypatch
    ):
        # A descended folder with no children still gets its directory created:
        # the subdir mkdir is unconditional, not "only when writing a file".
        monkeypatch.setattr("gdrives.download.download_entry", lambda *a: None)
        items = [_item(make_folder("empty", id="E"), descended=True)]
        download_walk(mock_service, items, str(tmp_path))
        assert (tmp_path / "empty").is_dir()

    def test_depth_limited_folder_skips_with_message(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr("gdrives.download.download_entry", lambda *a: None)
        items = [_item(make_folder("sub", id="SUB"), descended=False)]
        download_walk(mock_service, items, str(tmp_path))
        assert "skip subfolder (depth limit)" in capsys.readouterr().err
        assert not (tmp_path / "sub").exists()

    def test_folder_name_with_slash_sanitized(
        self, mock_service, tmp_path, monkeypatch
    ):
        # A folder named 'a/b' becomes 'a_b' on disk and its child downloads
        # inside it — each path component is sanitized, unlike the listing path.
        items = [
            _item(make_folder("a/b", id="AB"), descended=True),
            _item(
                make_file("c.pdf", id="C", mime=PDF_MIME),
                ancestors=("a/b",),
                depth=1,
            ),
        ]
        got = {}

        def rec(s, f, out):
            got[f["id"]] = str(out)

        monkeypatch.setattr("gdrives.download.download_entry", rec)
        download_walk(mock_service, items, str(tmp_path))
        assert (tmp_path / "a_b").is_dir()
        assert got["C"] == str(tmp_path / "a_b")

    def test_file_occupied_path_raises(self, mock_service, tmp_path):
        # A local file where the output dir is expected -> clean
        # NotADirectoryError, not the bare FileExistsError mkdir would raise.
        (tmp_path / "blocker").write_text("x")
        with pytest.raises(NotADirectoryError, match="a file with that name exists"):
            download_walk(mock_service, [], str(tmp_path / "blocker"))
