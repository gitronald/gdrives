"""Tests for gdrives.listing — DriveEntry, collection, formatters, and ls()."""

from unittest.mock import patch

import pytest
from helpers import make_file, make_folder, make_gdoc, mock_list_response

from gdrives.listing import (
    DriveEntry,
    collect,
    format_csv,
    format_markdown,
    format_table,
    ls,
)

# -- DriveEntry --


class TestDriveEntry:
    def test_frozen(self):
        entry = DriveEntry("url", "path", "name", "txt", "2026-01-15", "a@b.com")
        with __import__("pytest").raises(AttributeError):
            entry.name = "other"  # pyrefly: ignore[read-only]  # frozen by design

    def test_is_folder_property(self):
        folder = DriveEntry("url", "dir/", "dir", "folder", "2026-01-15", "a@b.com")
        file = DriveEntry("url", "f.txt", "f.txt", "txt", "2026-01-15", "a@b.com")
        assert folder.is_folder is True
        assert file.is_folder is False


# -- collect --


class TestCollect:
    def test_single_level(self, mock_service):
        items = [
            make_file("a.txt", id="a_id", owner_email="alice@x.com"),
            make_folder("sub", id="sub_id"),
        ]
        mock_service.files().list().execute.return_value = mock_list_response(items)
        rows = collect("root_id", depth=1, _service=mock_service)
        assert len(rows) == 2
        # Folders sorted first by list_children
        assert rows[0].name == "sub"
        assert rows[0].is_folder is True
        assert rows[0].path == "sub/"
        assert rows[1].name == "a.txt"
        assert rows[1].owner == "alice@x.com"

    def test_recursive_depth(self, mock_service):
        parent = make_folder("parent", id="parent_id")
        child = make_file("child.txt", id="child_id")
        mock_service.files().list().execute.side_effect = [
            mock_list_response([parent]),
            mock_list_response([child]),
        ]
        rows = collect("root_id", depth=2, _service=mock_service)
        assert len(rows) == 2
        assert rows[1].path == "parent/child.txt"

    def test_depth_field_increments_with_nesting(self, mock_service):
        parent = make_folder("parent", id="parent_id")
        child = make_file("child.txt", id="child_id")
        mock_service.files().list().execute.side_effect = [
            mock_list_response([parent]),
            mock_list_response([child]),
        ]
        rows = collect("root_id", depth=2, _service=mock_service)
        assert rows[0].depth == 0  # top-level
        assert rows[1].depth == 1  # nested one level

    def test_name_with_slash_kept_raw_in_path(self, mock_service):
        # A folder named 'a/b' keeps its raw '/' in the listing path (unlike the
        # download path, which sanitizes it), and depth comes from nesting, not
        # from counting slashes.
        parent = make_folder("a/b", id="ab_id")
        child = make_file("c.txt", id="c_id")
        mock_service.files().list().execute.side_effect = [
            mock_list_response([parent]),
            mock_list_response([child]),
        ]
        rows = collect("root_id", depth=2, _service=mock_service)
        assert rows[0].path == "a/b/"
        assert rows[1].path == "a/b/c.txt"
        assert rows[1].depth == 1

    def test_depth_1_no_recursion(self, mock_service):
        folder = make_folder("sub", id="sub_id")
        mock_service.files().list().execute.return_value = mock_list_response([folder])
        rows = collect("root_id", depth=1, _service=mock_service)
        assert len(rows) == 1
        assert mock_service.files().list().execute.call_count == 1

    def test_empty_folder(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        rows = collect("root_id", _service=mock_service)
        assert rows == []

    def test_found_log_counts_root_children_only(self, mock_service, caplog):
        # The "Found N entries" log counts the root's *direct* children, not the
        # whole tree — here 1 (parent), though the walk yields 2 rows.
        import logging

        parent = make_folder("parent", id="parent_id")
        child = make_file("child.txt", id="child_id")
        mock_service.files().list().execute.side_effect = [
            mock_list_response([parent]),
            mock_list_response([child]),
        ]
        with caplog.at_level(logging.INFO, logger="gdrives.listing"):
            rows = collect("root_id", depth=2, _service=mock_service)
        assert len(rows) == 2
        assert "Found 1 entries" in caplog.text

    def test_gdoc_entry(self, mock_service):
        doc = make_gdoc("Report", id="doc_id")
        mock_service.files().list().execute.return_value = mock_list_response([doc])
        rows = collect("root_id", depth=1, _service=mock_service)
        assert rows[0].file_type == "gdoc"
        assert "docs.google.com" in rows[0].url


# -- format_table --


class TestFormatTable:
    def test_basic_output(self):
        rows = [
            DriveEntry(
                "https://url1", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
            DriveEntry(
                "https://url2", "dir/", "dir", "folder", "2026-01-14", "c@d.com"
            ),
        ]
        result = format_table(rows)
        assert "file.txt" in result
        assert "dir/" in result
        assert result.endswith("\n")

    def test_empty_rows(self):
        assert format_table([]) == ""


# -- format_markdown --


class TestFormatMarkdown:
    def test_nested_output(self):
        rows = [
            DriveEntry(
                "https://url", "dir/", "dir", "folder", "2026-01-15", "a@b.com", depth=0
            ),
            DriveEntry(
                "https://url2",
                "dir/file.txt",
                "file.txt",
                "txt",
                "2026-01-15",
                "a@b.com",
                depth=1,
            ),
        ]
        result = format_markdown(rows)
        lines = result.strip().split("\n")
        assert lines[0] == "- [dir](https://url)"
        assert lines[1] == "  - [file.txt](https://url2)"

    def test_depth_from_field_not_path_slashes(self):
        # A name containing '/' must not inflate the rendered indent: depth is
        # carried explicitly, not counted from slashes in the path string.
        rows = [
            DriveEntry(
                "https://u", "a/b", "a/b", "txt", "2026-01-15", "a@b.com", depth=0
            ),
        ]
        assert format_markdown(rows) == "- [a/b](https://u)\n"

    def test_no_url_no_link(self):
        rows = [DriveEntry("", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com")]
        result = format_markdown(rows)
        assert "- file.txt" in result
        assert "[" not in result


# -- format_csv --


class TestFormatCsv:
    def test_headers_and_rows(self):
        rows = [
            DriveEntry(
                "https://url",
                "dir/file.txt",
                "file.txt",
                "txt",
                "2026-01-15",
                "a@b.com",
            ),
        ]
        result = format_csv(rows)
        lines = result.strip().splitlines()
        assert lines[0] == "path,name,type,modified,owner,shared_by,url"
        assert "dir/file.txt" in lines[1]

    def test_strips_trailing_slash_from_path(self):
        rows = [
            DriveEntry("https://url", "dir/", "dir", "folder", "2026-01-15", "a@b.com"),
        ]
        result = format_csv(rows)
        assert "dir," in result
        assert "dir/," not in result


# -- ls --


class TestLs:
    @patch("gdrives.listing.collect")
    def test_prints_table(self, mock_collect, capsys):
        mock_collect.return_value = [
            DriveEntry(
                "https://url", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
        ]
        ls("folder_id")
        out = capsys.readouterr().out
        assert "file.txt" in out

    @patch("gdrives.listing.collect")
    def test_save_as_csv(self, mock_collect, tmp_path):
        mock_collect.return_value = [
            DriveEntry(
                "https://url", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
        ]
        out_path = tmp_path / "out.csv"
        ls("folder_id", save_as=[str(out_path)])
        assert out_path.exists()
        content = out_path.read_text()
        assert "file.txt" in content

    @patch("gdrives.listing.collect")
    def test_save_as_md(self, mock_collect, tmp_path):
        mock_collect.return_value = [
            DriveEntry(
                "https://url", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
        ]
        out_path = tmp_path / "out.md"
        ls("folder_id", save_as=[str(out_path)])
        assert out_path.exists()
        content = out_path.read_text()
        assert "[file.txt]" in content

    @patch("gdrives.listing.collect")
    def test_save_as_both_writes_each_from_one_traversal(self, mock_collect, tmp_path):
        mock_collect.return_value = [
            DriveEntry(
                "https://url", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
        ]
        md_path = tmp_path / "out.md"
        csv_path = tmp_path / "out.csv"
        ls("folder_id", save_as=[str(md_path), str(csv_path)])
        assert "[file.txt]" in md_path.read_text()
        assert "file.txt" in csv_path.read_text()
        assert mock_collect.call_count == 1  # single API traversal feeds both files

    @patch("gdrives.listing.collect")
    def test_save_as_rejects_unknown_suffix(self, mock_collect, tmp_path):
        # The domain rejects an unexpected extension rather than silently writing
        # CSV, so callers other than the pre-validating CLI get the same guard.
        mock_collect.return_value = [
            DriveEntry(
                "https://url", "file.txt", "file.txt", "txt", "2026-01-15", "a@b.com"
            ),
        ]
        with pytest.raises(ValueError, match="unsupported --save-as extension"):
            ls("folder_id", save_as=[str(tmp_path / "out.txt")])

    @patch("gdrives.listing.collect")
    def test_save_as_empty_warns_and_writes_nothing(
        self, mock_collect, tmp_path, capsys
    ):
        mock_collect.return_value = []
        out_path = tmp_path / "out.md"
        ls("folder_id", save_as=[str(out_path)])
        assert not out_path.exists()
        assert "No files found" in capsys.readouterr().err

    @patch("gdrives.listing.build_drive_service")
    @patch("gdrives.listing.list_shared_with_me")
    def test_shared_with_me(self, mock_shared, mock_build, capsys):
        mock_shared.return_value = [
            make_file("shared.txt", sharing_user={"emailAddress": "bob@x.com"}),
        ]
        ls(shared_with_me=True)
        out = capsys.readouterr().out
        assert "shared.txt" in out
        assert "bob@x.com" in out

    @patch("gdrives.listing.build_drive_service")
    @patch("gdrives.listing.list_shared_with_me")
    def test_shared_with_me_empty(self, mock_shared, mock_build, capsys):
        mock_shared.return_value = []
        ls(shared_with_me=True)
        out = capsys.readouterr().out
        assert out == ""
