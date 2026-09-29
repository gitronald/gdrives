"""Tests for gdrives.sheets.create and the ``sheets-create`` entry point.

The Drive fake holds the file that is created, and the Sheets fake records
the one structural request that names its tabs.
"""

from pathlib import Path
from typing import Any

import pytest
from helpers import (
    FOLDER_MIME,
    SHEET_MIME,
    FakeDriveFiles,
    FakeSheetsService,
    http_error,
    patch_drive_service,
    patch_sheets_service,
)

from gdrives.sheets import create_spreadsheet, name_tabs, run_create
from gdrives.sheets.create import check_source, check_tabs, spreadsheet_url
from gdrives.upload import UPLOAD_RETRIES, UploadError

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def refusing() -> FakeDriveFiles:
    """A Drive that stores an upload as it came, refusing the conversion."""
    drive = FakeDriveFiles([folder()])
    drive.converts = False
    return drive


def workbook(tmp_path: Path, name: str = "Roster.xlsx") -> Path:
    path = tmp_path / name
    path.write_bytes(b"PK workbook bytes")
    return path


def folder(id: str = "D", name: str = "reports") -> dict[str, Any]:
    return {"id": id, "name": name, "mimeType": FOLDER_MIME, "parents": ["R"]}


ROOT = {"id": "R", "name": "My Drive", "mimeType": FOLDER_MIME}


def fresh(title: str = "Sheet1") -> FakeSheetsService:
    """The Sheets service of a spreadsheet just created: one tab, sheetId 0."""
    return FakeSheetsService(meta={"sheets": [{"properties": {"title": title}}]})


def requests(svc: FakeSheetsService) -> list[dict[str, Any]]:
    """The requests of the one structural batchUpdate sent."""
    (call,) = [k for m, k in svc.calls if m == "spreadsheets.batchUpdate"]
    return call["body"]["requests"]


def rename(title: str, sheet_id: int = 0) -> dict[str, Any]:
    properties = {"sheetId": sheet_id, "title": title}
    return {"updateSheetProperties": {"properties": properties, "fields": "title"}}


def add(title: str) -> dict[str, Any]:
    return {"addSheet": {"properties": {"title": title}}}


class TestCheckTabs:
    def test_each_title_once_in_order(self):
        assert check_tabs(["b", "a", "b"]) == ["b", "a"]

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_blank_title_is_refused(self, blank):
        with pytest.raises(ValueError, match="blank tab title"):
            check_tabs(["a", blank])


class TestNameTabs:
    def test_first_tab_is_renamed_and_the_others_added(self):
        svc = fresh()
        name_tabs(svc, "S", ["Members", "Dues", "Log"])
        assert requests(svc) == [rename("Members"), add("Dues"), add("Log")]

    def test_first_tab_is_found_by_reading_the_spreadsheet(self):
        svc = FakeSheetsService(
            meta={"sheets": [{"properties": {"title": "Hoja 1", "sheetId": 7}}]}
        )
        name_tabs(svc, "S", ["Members"])
        assert requests(svc) == [rename("Members", sheet_id=7)]

    def test_a_first_tab_already_so_named_is_not_renamed(self):
        svc = fresh()
        name_tabs(svc, "S", ["Sheet1", "Dues"])
        assert requests(svc) == [add("Dues")]

    def test_nothing_to_change_sends_nothing(self):
        svc = fresh()
        name_tabs(svc, "S", ["Sheet1"])
        assert [m for m, _ in svc.calls] == ["spreadsheets.get"]

    def test_no_tabs_named_reads_nothing(self):
        svc = fresh()
        name_tabs(svc, "S", [])
        assert svc.calls == []

    def test_the_default_title_can_be_a_later_tab(self):
        # The requests of a batch apply in order, so the name is free by then.
        svc = fresh()
        name_tabs(svc, "S", ["Members", "Sheet1"])
        assert requests(svc) == [rename("Members"), add("Sheet1")]

    def test_a_spreadsheet_with_no_tabs_is_refused(self):
        svc = FakeSheetsService(meta={"sheets": []})
        with pytest.raises(ValueError, match="spreadsheet has no tabs"):
            name_tabs(svc, "S", ["Members"])


class TestCreateSpreadsheet:
    def test_created_in_the_folder_through_the_drive_api(self):
        drive, sheets = FakeDriveFiles([folder()]), fresh()
        spreadsheet_id = create_spreadsheet(drive, sheets, "Roster", folder_id="D")

        assert spreadsheet_id == "new1"
        assert drive.calls == [
            (
                "create",
                {
                    "body": {"name": "Roster", "mimeType": SHEET_MIME}
                    | {"parents": ["D"]},
                    "fields": "id",
                    "supportsAllDrives": True,
                },
            )
        ]
        # Without tabs, the default tab is left as it is.
        assert sheets.calls == []

    def test_no_folder_names_no_parent(self):
        drive = FakeDriveFiles([])
        create_spreadsheet(drive, fresh(), "Roster")
        (call,) = drive.named("create")
        assert call["body"] == {"name": "Roster", "mimeType": SHEET_MIME}

    def test_tabs_are_named_on_the_new_file(self):
        drive, sheets = FakeDriveFiles([folder()]), fresh()
        create_spreadsheet(drive, sheets, "Roster", folder_id="D", tabs=["A", "B"])
        assert [k["spreadsheetId"] for _, k in sheets.calls] == ["new1", "new1"]
        assert requests(sheets) == [rename("A"), add("B")]

    @pytest.mark.parametrize(
        "title, tabs, message",
        [(" ", (), "title must not be empty"), ("Roster", ("",), "blank tab title")],
    )
    def test_bad_arguments_create_nothing(self, title, tabs, message):
        drive = FakeDriveFiles([folder()])
        with pytest.raises(ValueError, match=message):
            create_spreadsheet(drive, fresh(), title, folder_id="D", tabs=tabs)
        assert drive.calls == []


def test_spreadsheet_url():
    assert spreadsheet_url("S") == "https://docs.google.com/spreadsheets/d/S/edit"


class TestCheckSource:
    @pytest.mark.parametrize(
        "name, mime",
        [("a.xlsx", XLSX_MIME), ("a.XLSX", XLSX_MIME), ("b.csv", "text/csv")],
    )
    def test_the_extension_decides_the_type(self, tmp_path, name, mime):
        path = workbook(tmp_path, name)
        assert check_source(path) == (path, mime)

    @pytest.mark.parametrize("name", ["a.xls", "a.ods", "a", "a.xlsx.bak"])
    def test_other_extensions_are_refused_naming_those_taken(self, tmp_path, name):
        with pytest.raises(ValueError, match=r"use \.xlsx, \.csv"):
            check_source(workbook(tmp_path, name))

    def test_a_missing_file_and_a_folder_are_refused(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            check_source(tmp_path / "gone.xlsx")
        (tmp_path / "dir.xlsx").mkdir()
        with pytest.raises(ValueError, match="is not a file"):
            check_source(tmp_path / "dir.xlsx")


class TestCreateFromWorkbook:
    def test_the_workbook_is_the_media_of_a_spreadsheet_create(self, tmp_path):
        drive, sheets = FakeDriveFiles([folder()]), fresh()
        path = workbook(tmp_path)

        spreadsheet_id = create_spreadsheet(
            drive, sheets, "Roster", folder_id="D", source=path
        )

        assert spreadsheet_id == "new1"
        (call,) = drive.named("create")
        assert call["body"] == {
            "name": "Roster",
            "mimeType": SHEET_MIME,
            "parents": ["D"],
        }
        assert call["fields"] == "id, mimeType"
        assert call["supportsAllDrives"] is True
        assert call["media_body"].mimetype() == XLSX_MIME
        assert drive.items["new1"]["content"] == b"PK workbook bytes"
        # Resumable, in two chunks here, each retried.
        assert drive.requests[0].retries == [UPLOAD_RETRIES, UPLOAD_RETRIES]
        assert sheets.calls == []

    def test_a_csv_is_sent_as_text_csv(self, tmp_path):
        drive = FakeDriveFiles([folder()])
        path = workbook(tmp_path, "members.CSV")
        create_spreadsheet(drive, fresh(), "Members", folder_id="D", source=str(path))
        (call,) = drive.named("create")
        assert call["media_body"].mimetype() == "text/csv"

    def test_tabs_are_refused_with_a_workbook(self, tmp_path):
        drive = FakeDriveFiles([folder()])
        with pytest.raises(ValueError, match="names its own tabs"):
            create_spreadsheet(
                drive, fresh(), "Roster", source=workbook(tmp_path), tabs=["A"]
            )
        assert drive.calls == []

    def test_a_bad_source_creates_nothing(self, tmp_path):
        drive = FakeDriveFiles([folder()])
        with pytest.raises(ValueError, match="use .xlsx"):
            create_spreadsheet(
                drive, fresh(), "Roster", source=workbook(tmp_path, "a.xls")
            )
        assert drive.calls == []

    def test_a_file_left_unconverted_is_an_error_naming_it(self, tmp_path):
        drive = refusing()
        with pytest.raises(UploadError) as exc:
            create_spreadsheet(
                drive, fresh(), "Roster", folder_id="D", source=workbook(tmp_path)
            )
        assert "'Roster' (new1) was created but not converted" in str(exc.value)
        assert XLSX_MIME in str(exc.value)

    def test_an_api_error_surfaces(self, tmp_path, monkeypatch):
        drive = FakeDriveFiles([folder()])

        def refuse(kwargs: dict[str, Any]) -> dict[str, Any]:
            raise http_error(400, "Bad Request")

        monkeypatch.setattr(drive, "_create", refuse)
        with pytest.raises(Exception, match="Bad Request"):
            create_spreadsheet(drive, fresh(), "Roster", source=workbook(tmp_path))


def patch_paths(monkeypatch: pytest.MonkeyPatch, paths: dict[str, str]) -> None:
    monkeypatch.setattr(
        "gdrives.resolve.resolve_path", lambda path, service: paths[path]
    )


class TestRunCreate:
    def test_in_a_folder_by_path_with_tabs(self, monkeypatch, capsys):
        drive, sheets = FakeDriveFiles([folder()]), fresh()
        drive_rec = patch_drive_service(monkeypatch, drive)
        sheets_rec = patch_sheets_service(monkeypatch, sheets)
        patch_paths(monkeypatch, {"My Drive/reports": "D"})

        run_create("Roster", folder="My Drive/reports", tabs=["Members", "Dues"])

        assert drive_rec["scopes"] == sheets_rec["scopes"] == DRIVE_SCOPE
        (call,) = drive.named("create")
        assert call["body"]["parents"] == ["D"]
        assert requests(sheets) == [rename("Members"), add("Dues")]
        out = capsys.readouterr()
        assert out.out == "https://docs.google.com/spreadsheets/d/new1/edit\n"
        assert out.err == (
            "Spreadsheet ID: new1\n"
            "Done: create spreadsheet 'Roster' in 'reports' (D) "
            "with tabs: Members, Dues\n"
        )

    def test_with_no_folder_it_goes_in_the_root(self, monkeypatch, capsys):
        drive, sheets = FakeDriveFiles([ROOT], root="R"), fresh()
        patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, sheets)

        run_create("Roster")

        (call,) = drive.named("create")
        assert call["body"]["parents"] == ["R"]
        assert sheets.calls == []
        assert "in 'My Drive' (R)\n" in capsys.readouterr().err

    def test_in_a_folder_by_id(self, monkeypatch):
        drive = FakeDriveFiles([folder()])
        patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, fresh())

        run_create("Roster", folder_id="D")

        (call,) = drive.named("create")
        assert call["body"]["parents"] == ["D"]

    def test_a_file_of_the_same_name_is_noted_not_refused(self, monkeypatch, capsys):
        same = {"id": "X", "name": "roster", "mimeType": SHEET_MIME, "parents": ["D"]}
        drive = FakeDriveFiles([folder(), same])
        patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, fresh())

        run_create("Roster", folder_id="D")

        assert len(drive.named("create")) == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "Note: 'reports' already holds 1 file(s) named 'Roster': X\n"
        )

    def test_dry_run_creates_nothing_and_stays_read_only(self, monkeypatch, capsys):
        same = {"id": "X", "name": "Roster", "mimeType": SHEET_MIME, "parents": ["D"]}
        drive = FakeDriveFiles([folder(), same])
        rec = patch_drive_service(monkeypatch, drive)

        def build(scopes=None):
            raise AssertionError("built a Sheets service")

        monkeypatch.setattr("gdrives.auth.build_sheets_service", build)

        run_create("Roster", folder_id="D", tabs=["Members"], dry_run=True)

        assert rec["scopes"] is None
        assert drive.named("create") == []
        out = capsys.readouterr()
        assert out.out == (
            "Would create spreadsheet 'Roster' in 'reports' (D) with tabs: Members\n"
        )
        assert "already holds 1 file(s) named 'Roster': X" in out.err

    def test_a_destination_that_is_not_a_folder_is_refused(self, monkeypatch):
        held = {"id": "F", "name": "a.pdf", "mimeType": "application/pdf"}
        drive = FakeDriveFiles([held])
        patch_drive_service(monkeypatch, drive)
        with pytest.raises(ValueError, match="'a.pdf' is not a folder"):
            run_create("Roster", folder_id="F")
        assert drive.named("create") == []

    def test_a_folder_in_the_trash_is_refused(self, monkeypatch):
        drive = FakeDriveFiles([{**folder(), "trashed": True}])
        patch_drive_service(monkeypatch, drive)
        with pytest.raises(ValueError, match=r"'reports' \(D\) is in the trash"):
            run_create("Roster", folder_id="D")
        assert drive.named("create") == []

    def test_a_failure_naming_the_tabs_still_names_the_file(self, monkeypatch, capsys):
        drive = FakeDriveFiles([folder()])
        sheets = FakeSheetsService(
            meta={"sheets": [{"properties": {"title": "Sheet1"}}]},
            spreadsheetBatchUpdate=http_error(400, "Bad Request"),
        )
        patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, sheets)

        with pytest.raises(Exception, match="Bad Request"):
            run_create("Roster", folder_id="D", tabs=["Members"])

        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == "Spreadsheet ID: new1\n"

    def test_from_a_workbook(self, monkeypatch, capsys, tmp_path):
        drive, sheets = FakeDriveFiles([folder()]), fresh()
        rec = patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, sheets)
        path = workbook(tmp_path)

        run_create(folder_id="D", source=str(path))

        assert rec["scopes"] == DRIVE_SCOPE
        (call,) = drive.named("create")
        # The title is the file's stem.
        assert call["body"]["name"] == "Roster"
        assert call["media_body"].mimetype() == XLSX_MIME
        assert sheets.calls == []
        out = capsys.readouterr()
        assert out.out == "https://docs.google.com/spreadsheets/d/new1/edit\n"
        assert out.err == (
            "Spreadsheet ID: new1\n"
            f"Done: create spreadsheet 'Roster' in 'reports' (D) "
            f"from '{path}' (17 bytes)\n"
        )

    def test_a_title_given_wins_over_the_stem(self, monkeypatch, tmp_path):
        drive = FakeDriveFiles([folder()])
        patch_drive_service(monkeypatch, drive)
        patch_sheets_service(monkeypatch, fresh())
        run_create("Members", folder_id="D", source=str(workbook(tmp_path)))
        (call,) = drive.named("create")
        assert call["body"]["name"] == "Members"

    def test_dry_run_from_a_workbook_uploads_nothing(
        self, monkeypatch, capsys, tmp_path
    ):
        drive = FakeDriveFiles([folder()])
        rec = patch_drive_service(monkeypatch, drive)
        path = workbook(tmp_path)

        run_create(folder_id="D", source=str(path), dry_run=True)

        assert rec["scopes"] is None
        assert drive.named("create") == []
        assert capsys.readouterr().out == (
            f"Would create spreadsheet 'Roster' in 'reports' (D) "
            f"from '{path}' (17 bytes)\n"
        )

    def test_an_unconverted_file_names_its_id(self, monkeypatch, tmp_path):
        patch_drive_service(monkeypatch, refusing())
        patch_sheets_service(monkeypatch, fresh())
        with pytest.raises(UploadError, match=r"\(new1\) was created but not"):
            run_create(folder_id="D", source=str(workbook(tmp_path)))

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({}, "--title is required unless --from is given"),
            ({"title": " "}, "--title must not be empty"),
            ({"title": "R", "source": "gone.xlsx"}, "does not exist"),
            ({"title": "R", "source": "a.xls"}, "use .xlsx, .csv"),
            (
                {"title": "R", "source": "a.xlsx", "tabs": ["A"]},
                "--from names its own tabs",
            ),
            (
                {"title": "R", "folder": "My Drive/a", "folder_id": "D"},
                "at most one of --folder or --folder-id",
            ),
            ({"title": "R", "folder": " "}, "--folder must not be empty"),
            ({"title": "R", "folder_id": ""}, "--folder-id must not be empty"),
            ({"title": "R", "tabs": ["a", ""]}, "blank tab title"),
        ],
    )
    def test_bad_arguments_are_refused_before_authenticating(
        self, monkeypatch, kwargs, message
    ):
        def build(scopes=None):
            raise AssertionError("authenticated")

        monkeypatch.setattr("gdrives.auth.build_drive_service", build)
        with pytest.raises(ValueError, match=message):
            run_create(**kwargs)

    def test_names_are_escaped_for_the_terminal(self, monkeypatch, capsys):
        drive = FakeDriveFiles([folder(name="re\x1b[2Jports")])
        patch_drive_service(monkeypatch, drive)
        run_create("Ro\x07ster", folder_id="D", tabs=["a\x1bb"], dry_run=True)
        out = capsys.readouterr().out
        assert "\x1b" not in out and "\x07" not in out
