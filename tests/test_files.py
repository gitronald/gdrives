"""Tests for gdrives.files — helpers and API functions."""

import pytest
from helpers import make_file, make_folder, make_gdoc, mock_list_response

from gdrives.files import (
    extract_drive_id,
    file_type,
    file_url,
    is_folder,
    is_native,
    list_children,
    list_shared_with_me,
    modified_date,
    owner_email,
    paginate_files,
    shared_by,
    strip_url_suffix,
    walk_tree,
)

# -- strip_url_suffix --


class TestStripUrlSuffix:
    def test_removes_edit_suffix(self):
        url = "https://docs.google.com/document/d/abc123/edit"
        assert strip_url_suffix(url) == "https://docs.google.com/document/d/abc123"

    def test_removes_view_suffix(self):
        url = "https://drive.google.com/file/d/abc123/view"
        assert strip_url_suffix(url) == "https://drive.google.com/file/d/abc123"

    def test_removes_query_params_from_drive_url(self):
        url = "https://docs.google.com/document/d/abc123?usp=sharing"
        assert strip_url_suffix(url) == "https://docs.google.com/document/d/abc123"

    def test_preserves_query_params_for_non_drive_url(self):
        url = "https://maps.google.com/map?mid=abc123"
        assert strip_url_suffix(url) == "https://maps.google.com/map?mid=abc123"

    def test_no_suffix_unchanged(self):
        url = "https://drive.google.com/drive/folders/abc123"
        assert strip_url_suffix(url) == url


# -- extract_drive_id --


class TestExtractDriveId:
    def test_file_d_url(self):
        url = "https://docs.google.com/document/d/abc123/edit"
        assert extract_drive_id(url) == "abc123"

    def test_sheet_url_with_fragment(self):
        url = "https://docs.google.com/spreadsheets/d/sheet_xyz/edit#gid=0"
        assert extract_drive_id(url) == "sheet_xyz"

    def test_folders_url(self):
        url = "https://drive.google.com/drive/folders/ABC123"
        assert extract_drive_id(url) == "ABC123"

    def test_bare_id_passthrough(self):
        assert extract_drive_id("BARE_ID-123") == "BARE_ID-123"

    def test_open_id_query_form(self):
        assert extract_drive_id("https://drive.google.com/open?id=ABC123") == "ABC123"

    def test_uc_id_query_form(self):
        assert extract_drive_id("https://drive.google.com/uc?id=XYZ_789") == "XYZ_789"

    def test_unparseable_url_raises(self):
        with pytest.raises(ValueError, match="could not parse a Drive ID"):
            extract_drive_id("https://drive.google.com/drive/my-drive")


# -- is_folder --


class TestIsFolder:
    def test_folder_returns_true(self):
        assert is_folder(make_folder("test")) is True

    def test_file_returns_false(self):
        assert is_folder(make_file("test.txt")) is False

    def test_gdoc_returns_false(self):
        assert is_folder(make_gdoc("test")) is False


# -- is_native --


class TestIsNative:
    def test_gdoc_is_native(self):
        assert is_native(make_gdoc("doc")) is True

    def test_folder_is_native(self):
        assert is_native(make_folder("dir")) is True

    def test_binary_file_is_not_native(self):
        assert is_native(make_file("report.pdf", mime="application/pdf")) is False


# -- modified_date --


class TestModifiedDate:
    def test_extracts_date(self):
        f = make_file("test.txt", modified="2026-03-15T14:30:00.000Z")
        assert modified_date(f) == "2026-03-15"

    def test_missing_modified_time(self):
        f = {"name": "test.txt", "mimeType": "text/plain"}
        assert modified_date(f) == ""


# -- file_type --


class TestFileType:
    def test_folder(self):
        assert file_type(make_folder("dir")) == "folder"

    def test_gdoc(self):
        assert file_type(make_gdoc("doc")) == "gdoc"

    def test_gsheet(self):
        f = make_file("sheet", mime="application/vnd.google-apps.spreadsheet")
        assert file_type(f) == "gsheet"

    def test_colab(self):
        f = make_file("nb", mime="application/vnd.google.colaboratory")
        assert file_type(f) == "colab"

    def test_pdf_by_extension(self):
        f = make_file("report.pdf", mime="application/pdf")
        assert file_type(f) == "pdf"

    def test_no_extension_unknown_mime(self):
        f = make_file("noext", mime="application/octet-stream")
        assert file_type(f) == "unknown"


# -- file_url --


class TestFileUrl:
    def test_folder_uses_drive_folders_url(self):
        f = make_folder("dir", id="folder123")
        assert file_url(f) == "https://drive.google.com/drive/folders/folder123"

    def test_file_uses_web_view_link(self):
        f = make_file(
            "test.txt", id="file123", url="https://drive.google.com/file/d/file123/view"
        )
        assert file_url(f) == "https://drive.google.com/file/d/file123"

    def test_file_without_web_view_link(self):
        f = make_file("test.txt", id="file123")
        assert file_url(f) == "https://drive.google.com/file/d/file123"

    def test_gdoc_strips_edit(self):
        f = make_gdoc("doc", id="doc123")
        assert file_url(f) == "https://docs.google.com/document/d/doc123"


# -- owner_email --


class TestOwnerEmail:
    def test_extracts_email(self):
        f = make_file("test.txt", owner_email="alice@example.com")
        assert owner_email(f) == "alice@example.com"

    def test_missing_owners(self):
        f = {"name": "test.txt", "mimeType": "text/plain"}
        assert owner_email(f) == ""

    def test_empty_owners_list(self):
        f = {"name": "test.txt", "mimeType": "text/plain", "owners": []}
        assert owner_email(f) == ""


# -- shared_by --


class TestSharedBy:
    def test_extracts_sharing_user_email(self):
        f = make_file("s.txt", sharing_user={"emailAddress": "bob@x.com"})
        assert shared_by(f) == "bob@x.com"

    def test_missing_sharing_user(self):
        assert shared_by(make_file("s.txt")) == ""

    def test_sharing_user_explicit_none_returns_empty(self):
        # The API may return an explicit null; must not raise (mirrors owner_email).
        assert shared_by({"name": "s.txt", "sharingUser": None}) == ""


# -- paginate_files --


class TestPaginateFiles:
    def test_single_page(self, mock_service):
        files = [make_file("a.txt"), make_file("b.txt")]
        mock_service.files().list().execute.return_value = mock_list_response(files)
        result = paginate_files(mock_service, "q", "fields", "user")
        assert len(result) == 2

    def test_multiple_pages(self, mock_service):
        page1 = [make_file("a.txt")]
        page2 = [make_file("b.txt")]
        mock_service.files().list().execute.side_effect = [
            mock_list_response(page1, next_page_token="token2"),
            mock_list_response(page2),
        ]
        result = paginate_files(mock_service, "q", "fields", "user")
        assert len(result) == 2

    def test_empty_results(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        result = paginate_files(mock_service, "q", "fields", "user")
        assert result == []

    def test_all_drives_includes_flags(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        paginate_files(mock_service, "q", "fields", "allDrives")
        call_kwargs = mock_service.files().list.call_args[1]
        assert call_kwargs["includeItemsFromAllDrives"] is True
        assert call_kwargs["supportsAllDrives"] is True

    def test_user_corpora_omits_all_drives_flags(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        paginate_files(mock_service, "q", "fields", "user")
        call_kwargs = mock_service.files().list.call_args[1]
        assert "includeItemsFromAllDrives" not in call_kwargs
        assert "supportsAllDrives" not in call_kwargs


# -- list_children --


class TestListChildren:
    def test_sorts_folders_first(self, mock_service):
        items = [make_file("b.txt"), make_folder("a_dir"), make_file("a.txt")]
        mock_service.files().list().execute.return_value = mock_list_response(items)
        result = list_children(mock_service, "parent_id")
        assert result[0]["name"] == "a_dir"
        assert result[1]["name"] == "a.txt"
        assert result[2]["name"] == "b.txt"

    def test_empty_folder(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        result = list_children(mock_service, "parent_id")
        assert result == []

    def test_escapes_quote_in_folder_id(self, mock_service):
        # A quote in folder_id (e.g. an injected --drive-id) must be escaped in q.
        mock_service.files().list().execute.return_value = mock_list_response([])
        list_children(mock_service, "O'Brien")
        q = mock_service.files().list.call_args[1]["q"]
        assert "O\\'Brien" in q


# -- list_shared_with_me --


class TestListSharedWithMe:
    def test_name_filter_escapes_quotes(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        list_shared_with_me(mock_service, name="O'Brien")
        call_kwargs = mock_service.files().list.call_args[1]
        assert "O\\'Brien" in call_kwargs["q"]

    def test_no_name_filter(self, mock_service):
        items = [make_file("shared.txt")]
        mock_service.files().list().execute.return_value = mock_list_response(items)
        result = list_shared_with_me(mock_service)
        assert len(result) == 1


# -- walk_tree --


def _walk_children(s, fid):
    """list_children fake: root has folder SUB + file A; SUB has file B."""
    if fid == "root":
        return [make_folder("sub", id="SUB"), make_file("a.pdf", id="A")]
    if fid == "SUB":
        return [make_file("b.pdf", id="B")]
    return []


class TestWalkTree:
    def test_yields_all_descendants_depth_first(self, mock_service, monkeypatch):
        # Folders-first within a level (SUB before A) and depth-first overall:
        # SUB's child B is yielded before sibling A.
        monkeypatch.setattr("gdrives.files.list_children", _walk_children)
        items = list(walk_tree(mock_service, "root"))
        assert [it.file["id"] for it in items] == ["SUB", "B", "A"]

    def test_ancestors_depth_and_descended(self, mock_service, monkeypatch):
        monkeypatch.setattr("gdrives.files.list_children", _walk_children)
        by_id = {it.file["id"]: it for it in walk_tree(mock_service, "root")}
        assert by_id["SUB"].ancestors == ()
        assert by_id["SUB"].depth == 0
        assert by_id["SUB"].descended is True
        assert by_id["B"].ancestors == ("sub",)  # raw parent-folder name
        assert by_id["B"].depth == 1
        assert by_id["A"].ancestors == ()
        assert by_id["A"].depth == 0

    def test_depth_1_is_flat(self, mock_service, monkeypatch):
        monkeypatch.setattr("gdrives.files.list_children", _walk_children)
        items = list(walk_tree(mock_service, "root", depth=1))
        assert [it.file["id"] for it in items] == ["SUB", "A"]  # no descent
        sub = next(it for it in items if it.file["id"] == "SUB")
        assert sub.descended is False

    def test_depth_2_descends_one_level(self, mock_service, monkeypatch):
        def children(s, fid):
            if fid == "root":
                return [make_folder("L1", id="L1")]
            if fid == "L1":
                return [make_folder("L2", id="L2")]
            if fid == "L2":
                return [make_file("deep.pdf", id="D")]
            return []

        monkeypatch.setattr("gdrives.files.list_children", children)
        items = list(walk_tree(mock_service, "root", depth=2))
        assert [it.file["id"] for it in items] == ["L1", "L2"]
        by_id = {it.file["id"]: it for it in items}
        assert by_id["L1"].descended is True
        assert by_id["L2"].descended is False  # depth-limited, no descent

    def test_ancestor_name_with_slash_kept_raw(self, mock_service, monkeypatch):
        # walk_tree must not sanitize names: the '/' is preserved so each call
        # site (listing raw, download sanitized) can handle it its own way.
        def children(s, fid):
            if fid == "root":
                return [make_folder("a/b", id="AB")]
            if fid == "AB":
                return [make_file("c.pdf", id="C")]
            return []

        monkeypatch.setattr("gdrives.files.list_children", children)
        c = next(it for it in walk_tree(mock_service, "root") if it.file["id"] == "C")
        assert c.ancestors == ("a/b",)

    def test_empty_folder_yields_nothing(self, mock_service, monkeypatch):
        monkeypatch.setattr("gdrives.files.list_children", lambda s, fid: [])
        assert list(walk_tree(mock_service, "root")) == []
