"""Tests for gdrives.drives — cache and DriveInfo."""

import json

import pytest

from gdrives.drives import DriveInfo, fetch, find_drive, load, save


class TestDriveInfo:
    def test_is_typed_dict(self):
        info: DriveInfo = {
            "id": "abc",
            "type": "personal",
            "name": "My Drive",
            "url": "https://drive.google.com/drive/my-drive",
        }
        assert info["id"] == "abc"
        assert info["type"] == "personal"


class TestFetch:
    def test_returns_personal_and_shared(self, mock_service):
        mock_service.files().get().execute.return_value = {"id": "root_id"}
        mock_service.drives().list().execute.return_value = {
            "drives": [{"id": "sd_id", "name": "Team Drive"}]
        }
        drives = fetch(mock_service)
        assert len(drives) == 2
        assert drives[0]["type"] == "personal"
        assert drives[0]["id"] == "root_id"
        assert drives[1]["type"] == "shared"
        assert drives[1]["name"] == "Team Drive"

    def test_no_shared_drives(self, mock_service):
        mock_service.files().get().execute.return_value = {"id": "root_id"}
        mock_service.drives().list().execute.return_value = {"drives": []}
        drives = fetch(mock_service)
        assert len(drives) == 1
        assert drives[0]["name"] == "My Drive"

    def test_paginates_shared_drives(self, mock_service):
        # More than one page of shared drives must all be collected, not truncated.
        mock_service.files().get().execute.return_value = {"id": "root_id"}
        mock_service.drives().list().execute.side_effect = [
            {"drives": [{"id": "d1", "name": "One"}], "nextPageToken": "t2"},
            {"drives": [{"id": "d2", "name": "Two"}]},
        ]
        drives = fetch(mock_service)
        assert [d["name"] for d in drives] == ["My Drive", "One", "Two"]


class TestCacheRoundtrip:
    def test_save_and_load(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        drives: list[DriveInfo] = [
            {
                "id": "root_id",
                "type": "personal",
                "name": "My Drive",
                "url": "https://drive.google.com/drive/my-drive",
            },
            {
                "id": "shared_id",
                "type": "shared",
                "name": "Team Drive",
                "url": "https://drive.google.com/drive/folders/shared_id",
            },
        ]
        save(drives, path=cache_path)
        loaded = load(path=cache_path)
        assert loaded == drives

    def test_save_creates_parent_dir(self, tmp_path):
        cache_path = tmp_path / "subdir" / "cache.json"
        save([], path=cache_path)
        assert cache_path.exists()

    def test_load_missing_file(self, tmp_path):
        cache_path = tmp_path / "missing.json"
        assert load(path=cache_path) == []

    def test_save_format(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        drives: list[DriveInfo] = [
            {"id": "x", "type": "personal", "name": "My Drive", "url": "u"}
        ]
        save(drives, path=cache_path)
        text = cache_path.read_text()
        assert text.endswith("\n")
        parsed = json.loads(text)
        assert parsed == drives

    def test_save_and_load_non_ascii(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        drives: list[DriveInfo] = [
            {"id": "x", "type": "shared", "name": "Cafe Munche 日本", "url": "u"}
        ]
        save(drives, path=cache_path)
        assert load(path=cache_path) == drives


class TestFindDrive:
    def test_case_insensitive(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        drives: list[DriveInfo] = [
            {"id": "x", "type": "personal", "name": "My Drive", "url": "u"}
        ]
        save(drives, path=cache_path)
        result = find_drive(load(cache_path), "my drive")
        assert result is not None
        assert result["id"] == "x"

    def test_not_found(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        save([], path=cache_path)
        assert find_drive(load(cache_path), "Missing") is None

    def test_duplicate_drive_names_refuse_to_guess(self, tmp_path):
        drives: list[DriveInfo] = [
            {"id": "a", "type": "shared", "name": "Team", "url": "u"},
            {"id": "b", "type": "shared", "name": "TEAM", "url": "u"},
        ]
        path = tmp_path / "nested" / "cache" / "cache.json"
        save(drives, path)
        with pytest.raises(ValueError, match="multiple drives.*a, b"):
            find_drive(load(path), "team")


class TestCacheValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            "{}",
            '"drives"',
            '[["x"]]',
            '[{"id": "x", "type": "personal", "url": "u"}]',
            '[{"id": "x", "type": "personal", "name": 5, "url": "u"}]',
        ],
        ids=["object", "string", "non-dict-entry", "missing-name", "non-str-name"],
    )
    def test_malformed_cache_names_the_fix(self, tmp_path, payload):
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(payload)
        with pytest.raises(
            ValueError, match="is malformed; rerun 'gdrives show-drives'"
        ):
            load(cache_path)

    def test_invalid_json_names_the_fix(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        cache_path.write_text('[{"id": "x",')
        with pytest.raises(ValueError, match="not valid JSON .*show-drives"):
            load(cache_path)


def test_interrupted_save_keeps_the_previous_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    old: list[DriveInfo] = [
        {"id": "x", "type": "personal", "name": "My Drive", "url": "u"}
    ]
    save(old, path=cache_path)

    def interrupted(self, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr("pathlib.Path.replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        save([], path=cache_path)
    assert load(cache_path) == old
    assert [p.name for p in tmp_path.iterdir()] == ["cache.json"]
