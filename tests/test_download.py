"""Tests for gdrives.download — single-file and folder downloads."""

from pathlib import Path

import pytest
from helpers import http_error, make_file, make_folder, make_gdoc, make_gslides

from gdrives.download import (
    DownloadError,
    classify_entry,
    download_entry,
    download_file,
    download_single,
    download_walk,
    format_bytes,
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

    def test_control_characters_replaced(self):
        # ESC would reach the terminal in every progress line that prints the
        # local path; newlines and tabs make awkward local names too.
        assert safe_filename("\x1b]0;x\x07a\nb\tc\x9bd.pdf") == "_]0;x_a_b_c_d.pdf"

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
            lambda s, f, out, names=None: rec.update(out=out, f=f),
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

    def test_counts_a_cycle_apart_from_the_depth_limit(self, capsys):
        items = [
            _item(make_folder("A", id="A"), descended=True),
            WalkItem(
                file=make_folder("A", id="A"),
                ancestors=("A",),
                depth=1,
                descended=False,
                cycle=True,
            ),
        ]
        summary = summarize(items)
        assert summary["cyclic_subfolders"] == 1
        assert summary["skipped_subfolders"] == 0
        print_summary(summary, "out")
        err = capsys.readouterr().err
        assert "1 subfolder(s) skipped (contains itself)" in err
        assert "depth limit" not in err

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
            lambda s, meta, od, **k: rec.update(meta=meta, od=od, **k),
        )
        monkeypatch.setattr(
            "gdrives.download.download_walk",
            lambda *a, **k: pytest.fail("folder flow should not run for a file"),
        )
        run("https://drive.google.com/file/d/FID/view", str(tmp_path))
        assert rec["meta"]["id"] == "FID"
        assert rec["od"] == str(tmp_path)
        assert rec["skip_existing"] is False

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
        monkeypatch.setattr("gdrives.download.download_single", lambda *a, **k: None)
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
            "gdrives.download.download_entry",
            lambda s, f, out, names=None: got.append(f["id"]),
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
            "cyclic_subfolders": 0,
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

        def rec(s, f, out, names=None):
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

    def test_cyclic_folder_is_named_as_such(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr("gdrives.download.download_entry", lambda *a: None)
        item = WalkItem(
            file=make_folder("A", id="A"),
            ancestors=(),
            depth=0,
            descended=False,
            cycle=True,
        )
        download_walk(mock_service, [item], str(tmp_path))
        err = capsys.readouterr().err
        assert "skip subfolder (contains itself): A/" in err
        assert "depth limit" not in err

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

        def rec(s, f, out, names=None):
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


def test_download_preserves_existing_part_file(mock_service, tmp_path, monkeypatch):
    target = tmp_path / "report"
    scratch = tmp_path / "report.part"
    scratch.write_bytes(b"unrelated download")

    class Downloader:
        def __init__(self, stream, request):
            self.stream = stream
            self.chunks = iter([False, True])

        def next_chunk(self):
            self.stream.write(b"chunk")
            return None, next(self.chunks)

    monkeypatch.setattr("gdrives.download.MediaIoBaseDownload", Downloader)
    assert download_file(mock_service, "ID", str(target)) == 10
    assert target.read_bytes() == b"chunkchunk"
    assert scratch.read_bytes() == b"unrelated download"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report", "report.part"]


@pytest.mark.parametrize("names", [("dup", "dup"), ("a/b", "a_b")])
def test_colliding_folders_keep_separate_descendants(
    mock_service, tmp_path, monkeypatch, names
):
    first, second = names
    items = [
        _item(make_folder(first), descended=True),
        _item(make_folder("nested"), depth=1, descended=True),
        _item(make_file("a"), depth=2),
        _item(make_folder(second), descended=True),
        _item(make_file("b"), depth=1),
        _item(make_file("root")),
    ]
    destinations = {}
    monkeypatch.setattr(
        "gdrives.download.download_entry",
        lambda s, f, out, names=None: destinations.update({f["name"]: out}),
    )
    download_walk(mock_service, items, str(tmp_path))
    name = safe_filename(first)
    assert destinations == {
        "a": tmp_path / name / "nested",
        "b": tmp_path / f"{name} (1)",
        "root": tmp_path,
    }


@pytest.mark.parametrize("existing_kind", ["file", "directory", "symlink"])
def test_folder_download_avoids_existing_local_entries(
    mock_service, tmp_path, existing_kind
):
    occupied = tmp_path / "sub"
    if existing_kind == "file":
        occupied.write_text("keep")
    elif existing_kind == "directory":
        occupied.mkdir()
    else:
        occupied.symlink_to(tmp_path / "missing", target_is_directory=True)
    items = [_item(make_folder("sub"), descended=True)]
    download_walk(mock_service, items, str(tmp_path))
    assert (tmp_path / "sub (1)").is_dir()
    assert not (tmp_path / "missing").exists()


def test_unique_path_accepts_a_taken_predicate(tmp_path):
    (tmp_path / "a.txt").write_text("on disk, but not taken")
    used = {tmp_path / "a.txt", tmp_path / "a (1).txt"}
    assert unique_path(tmp_path / "a.txt", used.__contains__) == (
        tmp_path / "a (2).txt"
    )
    assert unique_path(tmp_path / "b.txt", used.__contains__) == tmp_path / "b.txt"


def test_unique_path_avoids_dangling_symlinks(tmp_path):
    (tmp_path / "a").symlink_to(tmp_path / "missing")
    (tmp_path / "a (1)").symlink_to(tmp_path / "also-missing")
    assert unique_path(tmp_path / "a") == tmp_path / "a (2)"


def test_folder_run_preserves_empty_subfolders(mock_service, tmp_path, monkeypatch):
    monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
    mock_service.files().get().execute.return_value = make_folder("root", id="root")
    monkeypatch.setattr(
        "gdrives.files.list_children",
        lambda s, fid: [make_folder("empty", id="E")] if fid == "root" else [],
    )
    run("https://drive.google.com/drive/folders/root", str(tmp_path), yes=True)
    assert (tmp_path / "empty").is_dir()


def test_unknown_native_does_not_export_based_on_extension():
    f = make_file("x.gdoc", mime="application/vnd.google-apps.x")
    assert classify_entry(f) == "skip"


# -- failures and reruns --


def _fake_io(monkeypatch, fetched, fail=()):
    """Stand-ins for download_file/export_file that write real local files.

    Each records the file ID it was asked for; IDs in ``fail`` raise the 403 the
    API returns for a download-restricted file or an oversized export.
    """

    def download_file(s, fid, path):
        fetched.append(fid)
        if fid in fail:
            raise http_error(403, "cannotDownloadFile")
        Path(path).write_bytes(b"x")
        return 1

    def export_file(s, fid, path):
        fetched.append(fid)
        if fid in fail:
            raise http_error(403, "exportSizeLimitExceeded")
        Path(path).write_bytes(b"doc")

    monkeypatch.setattr("gdrives.download.download_file", download_file)
    monkeypatch.setattr("gdrives.download.export_file", export_file)


def _tree():
    return [
        _item(make_folder("sub", id="SUB"), descended=True),
        _item(make_file("b.pdf", id="B", mime=PDF_MIME), ancestors=("sub",), depth=1),
        _item(make_file("a.pdf", id="A", mime=PDF_MIME)),
        _item(make_gdoc("Big", id="BIG")),
        _item(make_file("c.pdf", id="C", mime=PDF_MIME)),
    ]


def _local_files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class TestFailuresAndReruns:
    def test_one_failure_does_not_stop_the_rest(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        fetched = []
        _fake_io(monkeypatch, fetched, fail={"BIG"})
        failures = download_walk(mock_service, _tree(), str(tmp_path))
        assert fetched == ["B", "A", "BIG", "C"]  # C still tried after BIG failed
        assert _local_files(tmp_path) == ["a.pdf", "c.pdf", "sub/b.pdf"]
        (line,) = failures
        assert line.startswith("Big: <HttpError 403")
        assert "failed: Big: <HttpError 403" in capsys.readouterr().err

    def test_rerun_with_skip_existing_fetches_only_what_is_missing(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        _fake_io(monkeypatch, [], fail={"BIG"})
        download_walk(mock_service, _tree(), str(tmp_path))
        fetched = []
        _fake_io(monkeypatch, fetched)
        failures = download_walk(
            mock_service, _tree(), str(tmp_path), skip_existing=True
        )
        assert failures == []
        assert fetched == ["BIG"]
        # no "a (1).pdf"-style duplicates of what the first run saved
        assert _local_files(tmp_path) == ["Big.docx", "a.pdf", "c.pdf", "sub/b.pdf"]
        assert f"skip (already there): {tmp_path / 'a.pdf'}" in capsys.readouterr().err

    def test_rerun_maps_duplicate_names_to_the_same_paths(
        self, mock_service, tmp_path, monkeypatch
    ):
        # Two Drive files share a name; the second one failed the first time.
        items = [
            _item(make_file("dup.pdf", id="D1", mime=PDF_MIME)),
            _item(make_file("dup.pdf", id="D2", mime=PDF_MIME)),
            _item(make_folder("f", id="F1"), descended=True),
            _item(make_file("x.pdf", id="X1", mime=PDF_MIME), depth=1),
            _item(make_folder("f", id="F2"), descended=True),
            _item(make_file("x.pdf", id="X2", mime=PDF_MIME), depth=1),
        ]
        _fake_io(monkeypatch, [], fail={"D2", "X2"})
        download_walk(mock_service, items, str(tmp_path))
        fetched = []
        _fake_io(monkeypatch, fetched)
        download_walk(mock_service, items, str(tmp_path), skip_existing=True)
        assert fetched == ["D2", "X2"]
        assert _local_files(tmp_path) == [
            "dup (1).pdf",
            "dup.pdf",
            "f (1)/x.pdf",
            "f/x.pdf",
        ]

    def test_folder_that_cannot_be_created_skips_its_contents(
        self, mock_service, tmp_path, monkeypatch
    ):
        long_name = "x" * 300  # past the usual 255-byte file name limit
        items = [
            _item(make_folder(long_name, id="L"), descended=True),
            _item(make_folder("inner", id="I"), depth=1, descended=True),
            _item(make_file("deep.pdf", id="D", mime=PDF_MIME), depth=2),
            _item(make_file("a.pdf", id="A", mime=PDF_MIME)),
        ]
        fetched = []
        _fake_io(monkeypatch, fetched)
        failures = download_walk(mock_service, items, str(tmp_path))
        assert fetched == ["A"]  # the long folder's subtree was never attempted
        (line,) = failures
        assert line.startswith(long_name + ": ")
        assert line.endswith("(its contents were not downloaded)")

    def test_file_name_the_filesystem_rejects_is_a_failure(
        self, mock_service, tmp_path, monkeypatch
    ):
        items = [
            _item(make_file("y" * 300 + ".pdf", id="Y", mime=PDF_MIME)),
            _item(make_file("a.pdf", id="A", mime=PDF_MIME)),
        ]
        fetched = []
        _fake_io(monkeypatch, fetched)
        failures = download_walk(mock_service, items, str(tmp_path))
        assert fetched == ["Y", "A"]
        assert len(failures) == 1 and "y" * 300 in failures[0]
        assert _local_files(tmp_path) == ["a.pdf"]

    def test_run_reports_failures_after_downloading_the_rest(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_folder("refs", id="R")
        monkeypatch.setattr(
            "gdrives.files.list_children",
            lambda s, fid: [
                make_file("a.pdf", id="A", mime=PDF_MIME),
                make_file("b.pdf", id="B", mime=PDF_MIME),
            ],
        )
        fetched = []
        _fake_io(monkeypatch, fetched, fail={"A"})
        with pytest.raises(DownloadError) as exc:
            run("https://drive.google.com/drive/folders/R", str(tmp_path), yes=True)
        assert fetched == ["A", "B"]
        message = str(exc.value)
        assert message.startswith("1 item(s) failed to download:\n  a.pdf: ")
        assert message.endswith(
            "Rerun with --skip-existing to fetch only what is missing."
        )

    def test_run_passes_skip_existing_to_the_walk(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        mock_service.files().get().execute.return_value = make_folder("refs", id="R")
        monkeypatch.setattr(
            "gdrives.files.list_children",
            lambda s, fid: [make_file("a.pdf", id="A", mime=PDF_MIME)],
        )
        (tmp_path / "a.pdf").write_bytes(b"from last time")
        fetched = []
        _fake_io(monkeypatch, fetched)
        run(
            "https://drive.google.com/drive/folders/R",
            str(tmp_path),
            yes=True,
            skip_existing=True,
        )
        assert fetched == []
        assert (tmp_path / "a.pdf").read_bytes() == b"from last time"

    def test_single_file_skip_existing_leaves_it_alone(
        self, mock_service, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "a.pdf").write_bytes(b"keep")
        fetched = []
        _fake_io(monkeypatch, fetched)
        meta = make_file("a.pdf", id="A", mime=PDF_MIME)
        download_single(mock_service, meta, str(tmp_path), skip_existing=True)
        assert fetched == []
        assert "skip (already there)" in capsys.readouterr().err
        download_single(mock_service, meta, str(tmp_path))  # default: a copy
        assert _local_files(tmp_path) == ["a (1).pdf", "a.pdf"]


# -- source forms --


class TestRunSources:
    def _run(self, monkeypatch, mock_service, tmp_path, source, drives=()):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        monkeypatch.setattr("gdrives.drives.load", lambda: list(drives))
        mock_service.files().get().execute.return_value = make_file(
            "a.pdf", id="X", mime=PDF_MIME
        )
        monkeypatch.setattr("gdrives.download.download_single", lambda *a, **k: None)
        run(source, str(tmp_path))
        return mock_service.files().get.call_args.kwargs["fileId"]

    def test_bare_file_id_is_fetched_directly(
        self, mock_service, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda *a, **k: pytest.fail("a bare ID is not a path"),
        )
        file_id = self._run(monkeypatch, mock_service, tmp_path, "1AbC_xyz-9")
        assert file_id == "1AbC_xyz-9"

    def test_bare_drive_name_downloads_that_drive(
        self, mock_service, tmp_path, monkeypatch
    ):
        drives = [{"id": "DRV", "type": "shared", "name": "Team Drive", "url": "u"}]
        file_id = self._run(monkeypatch, mock_service, tmp_path, "team drive", drives)
        assert file_id == "DRV"
