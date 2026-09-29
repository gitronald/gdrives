"""Tests for the native-spreadsheet check on the path source of a sheets command.

Each test invokes the real Typer app with ``CliRunner``. Path resolution runs
for real over a fake folder listing, so what is pinned is that every command
that takes a source goes through the check, and that a path costs no request
beyond the listings it walked.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import http_error, make_file, mock_list_response
from typer.testing import CliRunner

from gdrives.cli import app
from gdrives.files import SPREADSHEET_MIME
from gdrives.sheets import CONFIG_NAME

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRIVES = [{"id": "drive_id", "type": "personal", "name": "My Drive", "url": ""}]


class Recorder:
    """The Drive listings a path walked, and the sheets services built."""

    def __init__(self) -> None:
        self.listings = 0
        self.sheets_services = 0


@pytest.fixture
def drive(tmp_path, monkeypatch, mock_service):
    """A drive holding ``Roster.xlsx`` and a native ``Roster``, and a project dir."""
    rec = Recorder()
    entries = [
        make_file("Roster.xlsx", id="book_id", mime=XLSX),
        make_file("Roster", id="SHEET", mime=SPREADSHEET_MIME),
    ]

    def execute() -> Any:
        rec.listings += 1
        return mock_list_response(entries)

    mock_service.files().list().execute.side_effect = execute
    mock_service.reset_mock()
    monkeypatch.setattr("gdrives.resolve.load", lambda: DRIVES)
    monkeypatch.setattr("gdrives.resolve.build_drive_service", lambda: mock_service)

    def refuse(scopes: Any = None) -> Any:
        rec.sheets_services += 1
        raise http_error(404, "not reached")

    monkeypatch.setattr("gdrives.auth.build_sheets_service", refuse)
    monkeypatch.chdir(tmp_path)
    return rec


def write_config(spreadsheet: str) -> None:
    config = {
        "roster": {
            "spreadsheet": spreadsheet,
            "tabs": {
                "Members": {"local": "members.csv", "key": ["id"]},
                "Dues": {"mode": "pull", "local": "dues.csv"},
                "Summary": {"mode": "push", "local": "members.csv"},
            },
        }
    }
    Path(CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
    Path("members.csv").write_text("id,name\n", encoding="utf-8")


BOOK = "My Drive/Roster.xlsx"

SOURCE_COMMANDS = [
    ["sheets-get", BOOK],
    ["sheets-get", BOOK, "Members!A1:B2"],
    ["sheets-update", BOOK, "A1", "--values-file", "members.csv"],
    ["sheets-append", BOOK, "A1", "--values-file", "members.csv"],
    ["sheets-clear", BOOK, "A1:B2", "-y"],
    ["sheets-set", BOOK, "-m", "id=1", "-s", "name=Ada"],
    ["sheets-rules", BOOK],
    ["sheets-widths", BOOK],
    ["sheets-add-rule", BOOK, "--range", "A2:B", "--formula", "=1", "--bold"],
    ["sheets-delete-rule", BOOK, "--tab", "Members", "--index", "0", "-y"],
    ["sheets-pull", BOOK, "--all-tabs", "-o", "out"],
    ["sheets-links", BOOK, "--color", "#1155cc"],
]


@pytest.mark.parametrize("args", SOURCE_COMMANDS, ids=lambda a: a[0])
def test_a_path_to_a_workbook_is_refused(drive, args):
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "Error: 'Roster.xlsx' is an Excel workbook (" + XLSX + ")" in result.stderr
    assert "not a Google spreadsheet" in result.stderr
    assert "sheets-create --from" in result.stderr
    assert drive.listings == 1
    assert drive.sheets_services == 0


@pytest.mark.parametrize("command", ["sheets-sync", "sheets-pull", "sheets-push"])
def test_a_target_whose_spreadsheet_is_a_path_to_a_workbook_is_refused(drive, command):
    write_config(BOOK)
    result = CliRunner().invoke(app, [command, "roster"])
    assert result.exit_code == 1
    assert "is an Excel workbook" in result.stderr
    assert drive.sheets_services == 0


def test_links_refuses_a_target_whose_spreadsheet_is_a_workbook_path(drive):
    write_config(BOOK)
    result = CliRunner().invoke(app, ["sheets-links", "roster", "--color", "#1155cc"])
    assert result.exit_code == 1
    assert "is an Excel workbook" in result.stderr
    assert drive.sheets_services == 0


def test_a_native_spreadsheet_path_goes_on_to_the_sheets_service(drive):
    result = CliRunner().invoke(app, ["sheets-get", "My Drive/Roster"])
    # The sheets service is the fake that raises, so the run got past the check.
    assert drive.sheets_services == 1
    assert drive.listings == 1
    assert "Spreadsheet ID: SHEET" in result.stderr
    assert "not a Google spreadsheet" not in result.stderr


def test_a_url_and_an_id_make_no_drive_request(drive):
    for source in ("https://docs.google.com/spreadsheets/d/SHEET/edit", "SHEET"):
        CliRunner().invoke(app, ["sheets-get", source])
    assert drive.listings == 0
    assert drive.sheets_services == 2
