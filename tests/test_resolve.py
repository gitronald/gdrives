"""Tests for gdrives.resolve — path resolution."""

from unittest.mock import patch

import pytest
from helpers import make_file, make_folder, mock_list_response

from gdrives.resolve import (
    DrivePathError,
    resolve_path,
    resolve_shared_path,
    walk_segments,
)

# -- walk_segments --


class TestWalkSegments:
    def test_single_segment_folder(self, mock_service):
        child = make_folder("projects", id="proj_id")
        mock_service.files().list().execute.return_value = mock_list_response([child])
        result = walk_segments(mock_service, "root_id", ["projects"])
        assert result == "proj_id"

    def test_multi_segment_path(self, mock_service):
        folder_a = make_folder("a", id="a_id")
        folder_b = make_folder("b", id="b_id")
        mock_service.files().list().execute.side_effect = [
            mock_list_response([folder_a]),
            mock_list_response([folder_b]),
        ]
        result = walk_segments(mock_service, "root_id", ["a", "b"])
        assert result == "b_id"

    def test_missing_segment_raises(self, mock_service):
        mock_service.files().list().execute.return_value = mock_list_response([])
        with pytest.raises(DrivePathError, match="folder 'missing'"):
            walk_segments(mock_service, "root_id", ["missing"])

    def test_file_at_non_last_segment_raises(self, mock_service):
        file = make_file("readme.txt", id="file_id")
        mock_service.files().list().execute.return_value = mock_list_response([file])
        with pytest.raises(DrivePathError, match="folder 'readme.txt'"):
            walk_segments(mock_service, "root_id", ["readme.txt", "sub"])

    def test_allow_files_on_last_segment(self, mock_service):
        file = make_file("readme.txt", id="file_id")
        mock_service.files().list().execute.return_value = mock_list_response([file])
        result = walk_segments(
            mock_service, "root_id", ["readme.txt"], allow_files=True
        )
        assert result == "file_id"

    def test_case_insensitive_match(self, mock_service):
        folder = make_folder("Projects", id="proj_id")
        mock_service.files().list().execute.return_value = mock_list_response([folder])
        result = walk_segments(mock_service, "root_id", ["projects"])
        assert result == "proj_id"

    def test_empty_segments_returns_folder_id(self, mock_service):
        result = walk_segments(mock_service, "root_id", [])
        assert result == "root_id"

    def test_corpora_passed_through(self, mock_service):
        folder = make_folder("sub", id="sub_id")
        mock_service.files().list().execute.return_value = mock_list_response([folder])
        walk_segments(mock_service, "root_id", ["sub"], corpora="user")
        call_kwargs = mock_service.files().list.call_args[1]
        assert call_kwargs["corpora"] == "user"

    def test_skips_non_matching_children(self, mock_service):
        other = make_folder("other", id="other_id")
        target = make_folder("target", id="target_id")
        mock_service.files().list().execute.return_value = mock_list_response(
            [other, target]
        )
        result = walk_segments(mock_service, "root_id", ["target"])
        assert result == "target_id"

    def test_duplicate_named_folders_raise(self, mock_service):
        # Drive permits duplicate names; resolution must refuse to guess, not
        # silently pick the first match.
        dup1 = make_folder("reports", id="r1", owner_email="a@x.com")
        dup2 = make_folder("reports", id="r2", owner_email="b@x.com")
        mock_service.files().list().execute.return_value = mock_list_response(
            [dup1, dup2]
        )
        with pytest.raises(DrivePathError, match="multiple items named 'reports'"):
            walk_segments(mock_service, "root_id", ["reports"])


# -- resolve_path --


class TestResolvePath:
    @patch("gdrives.resolve.load")
    def test_single_segment_cache_hit(self, mock_load, mock_service):
        mock_load.return_value = [
            {"id": "drive_id", "type": "personal", "name": "My Drive", "url": ""}
        ]
        result = resolve_path("My Drive", service=mock_service)
        assert result == "drive_id"

    @patch("gdrives.resolve.load")
    def test_cache_miss_raises(self, mock_load, mock_service):
        mock_load.return_value = []
        with pytest.raises(DrivePathError, match="No drive matching"):
            resolve_path("Unknown Drive", service=mock_service)

    @patch("gdrives.resolve.load")
    def test_walks_remaining_segments(self, mock_load, mock_service):
        mock_load.return_value = [
            {"id": "drive_id", "type": "personal", "name": "My Drive", "url": ""}
        ]
        folder = make_folder("projects", id="proj_id")
        mock_service.files().list().execute.return_value = mock_list_response([folder])
        result = resolve_path("My Drive/projects", service=mock_service)
        assert result == "proj_id"


# -- resolve_shared_path --


class TestResolveSharedPath:
    @patch("gdrives.resolve.list_shared_with_me")
    def test_not_found_raises(self, mock_shared, mock_service):
        mock_shared.return_value = []
        with pytest.raises(DrivePathError, match="not found in Shared with me"):
            resolve_shared_path("missing", service=mock_service)

    @patch("gdrives.resolve.list_shared_with_me")
    def test_multiple_matches_raises(self, mock_shared, mock_service):
        items = [
            make_folder("dup", id="id1", owner_email="a@x.com"),
            make_folder("dup", id="id2", owner_email="b@x.com"),
        ]
        mock_shared.return_value = items
        with pytest.raises(DrivePathError, match="multiple items"):
            resolve_shared_path("dup", service=mock_service)

    @patch("gdrives.resolve.list_shared_with_me")
    def test_folder_no_remaining_returns_id(self, mock_shared, mock_service):
        folder = make_folder("shared_dir", id="shared_id")
        mock_shared.return_value = [folder]
        result = resolve_shared_path("shared_dir", service=mock_service)
        assert result == "shared_id"

    @patch("gdrives.resolve.list_shared_with_me")
    def test_file_no_remaining_without_allow_raises(self, mock_shared, mock_service):
        file = make_file("doc.txt", id="file_id")
        mock_shared.return_value = [file]
        with pytest.raises(DrivePathError, match="is a file, not a folder"):
            resolve_shared_path("doc.txt", service=mock_service)

    @patch("gdrives.resolve.list_shared_with_me")
    def test_file_no_remaining_with_allow(self, mock_shared, mock_service):
        file = make_file("doc.txt", id="file_id")
        mock_shared.return_value = [file]
        result = resolve_shared_path("doc.txt", service=mock_service, allow_files=True)
        assert result == "file_id"

    @patch("gdrives.resolve.list_shared_with_me")
    def test_file_with_remaining_raises(self, mock_shared, mock_service):
        file = make_file("doc.txt", id="file_id")
        mock_shared.return_value = [file]
        with pytest.raises(DrivePathError, match="is a file, not a folder"):
            resolve_shared_path("doc.txt/sub", service=mock_service)

    @patch("gdrives.resolve.list_shared_with_me")
    def test_walks_remaining_with_user_corpora(self, mock_shared, mock_service):
        folder = make_folder("shared_dir", id="shared_id")
        mock_shared.return_value = [folder]
        subfolder = make_folder("sub", id="sub_id")
        mock_service.files().list().execute.return_value = mock_list_response(
            [subfolder]
        )
        result = resolve_shared_path("shared_dir/sub", service=mock_service)
        assert result == "sub_id"
        call_kwargs = mock_service.files().list.call_args[1]
        assert call_kwargs["corpora"] == "user"
