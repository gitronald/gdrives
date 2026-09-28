"""Tests for gdrives.files: find_named and get_folder, on the Drive files fake."""

from typing import Any

import pytest
from helpers import FOLDER_MIME, FakeDriveFiles

from gdrives.files import find_named, get_folder


def folder(id: str = "D", name: str = "reports") -> dict[str, Any]:
    return {"id": id, "name": name, "mimeType": FOLDER_MIME, "parents": ["root"]}


def held(name: str, *, id: str = "F", parent: str = "D") -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "mimeType": "application/pdf",
        "parents": [parent],
        "content": b"old",
    }


def test_matches_without_regard_to_case_and_skips_folders():
    svc = FakeDriveFiles(
        [
            folder(),
            held("Report.pdf", id="B"),
            held("report.pdf", id="A"),
            held("other.pdf", id="C"),
            {**folder("E", "report.pdf"), "parents": ["D"]},
            held("report.pdf", id="Z", parent="elsewhere"),
        ]
    )
    assert [f["id"] for f in find_named(svc, "D", "report.pdf")] == ["A", "B"]


def test_query_escapes_the_name_and_reaches_shared_drives():
    svc = FakeDriveFiles([folder(), held("it's.pdf")])
    (found,) = find_named(svc, "D", "it's.pdf")
    assert found["id"] == "F"
    (call,) = svc.named("list")
    assert call["q"] == "'D' in parents and name = 'it\\'s.pdf' and trashed = false"
    assert call["fields"] == (
        "nextPageToken, incompleteSearch, files(id, name, mimeType)"
    )
    assert call["supportsAllDrives"] is True
    assert call["includeItemsFromAllDrives"] is True


def test_fields_name_what_is_asked_of_each_file():
    svc = FakeDriveFiles([folder(), held("report.pdf")])
    find_named(svc, "D", "report.pdf", fields="id, name, mimeType, size")
    (call,) = svc.named("list")
    assert call["fields"] == (
        "nextPageToken, incompleteSearch, files(id, name, mimeType, size)"
    )


def test_every_page_is_read():
    files = [held("report.pdf", id=f"F{i}") for i in range(3)]
    svc = FakeDriveFiles([folder(), *files], pages=2)
    assert len(find_named(svc, "D", "report.pdf")) == 3
    assert len(svc.named("list")) == 2


class TestGetFolder:
    def test_returns_the_folder(self):
        svc = FakeDriveFiles([folder()])
        assert get_folder(svc, "D")["name"] == "reports"
        (call,) = svc.named("get")
        assert call == {
            "fileId": "D",
            "fields": "id, name, mimeType, trashed",
            "supportsAllDrives": True,
        }

    def test_a_file_is_refused(self):
        svc = FakeDriveFiles([held("report.pdf")])
        with pytest.raises(ValueError, match="'report.pdf' is not a folder"):
            get_folder(svc, "F")

    def test_a_folder_in_the_trash_is_refused(self):
        svc = FakeDriveFiles([{**folder(), "trashed": True}])
        with pytest.raises(ValueError, match=r"'reports' \(D\) is in the trash"):
            get_folder(svc, "D")
