"""Live integration tests for gdrives.sheets against the real Sheets API v4.

These exercise every core value operation (list_tabs, pull/update/append/clear)
against a real spreadsheet, so they catch anything the fake-service unit tests
in test_sheets.py can't — request-shape mismatches, scope problems, and how the
API actually renders formulas and empty ranges.

They run **only** when a service account is available and
``GDRIVES_TEST_SPREADSHEET_ID`` points at a spreadsheet shared with it as
Editor; otherwise every test skips, so CI without credentials stays green. The
spreadsheet ID is read from the environment (or a gitignored ``.env``, which
``gdrives.auth`` loads on import) rather than hard-coded, since this is a public
repo. Set it to a throwaway sheet:

    export GDRIVES_TEST_SPREADSHEET_ID=<id of a sheet shared with the SA>

Each test gets a fresh, uniquely-named tab that is created before and deleted
after it, so runs never collide with each other or leave state behind — the
spreadsheet's other tabs are never read or modified. Select or skip the suite
with ``-m integration`` / ``-m "not integration"``.
"""

import os
import uuid

import pytest

import gdrives.auth  # import loads .env (python-dotenv), so a .env-set id is visible
from gdrives import sheets

pytestmark = pytest.mark.integration

SPREADSHEET_ID_ENV = "GDRIVES_TEST_SPREADSHEET_ID"


@pytest.fixture(scope="session")
def live_service():
    """A (service, spreadsheet_id) pair for the shared test sheet, or skip.

    Skips — never fails — when the sheet id is unset, no service account is
    configured, or the sheet can't be reached (offline, not shared, Sheets API
    disabled). The service is built straight from the service account so the test
    path is deterministic, bypassing the OAuth-first precedence in authenticate().
    """
    sid = os.environ.get(SPREADSHEET_ID_ENV)
    if not sid:
        pytest.skip(
            f"set {SPREADSHEET_ID_ENV} to a sheet shared with the service account"
        )
    creds = gdrives.auth.authenticate_service_account(gdrives.auth.SHEETS_WRITE_SCOPES)
    if creds is None:
        pytest.skip("no service account configured")
    from googleapiclient.discovery import build

    service = build("sheets", "v4", credentials=creds)
    try:
        sheets.list_tabs(service, sid)  # sanity: the SA can actually reach the sheet
    except Exception as exc:  # network down, not shared, API disabled, bad id, ...
        pytest.skip(f"test sheet not reachable via service account: {exc}")
    return service, sid


@pytest.fixture
def tab(live_service):
    """Yield (service, spreadsheet_id, tab_name) for a fresh, empty tab.

    Creates a uniquely-named tab and deletes it afterward, so each test starts
    from a blank slate and leaves nothing behind even if it writes.
    """
    service, sid = live_service
    name = "itest_" + uuid.uuid4().hex[:8]
    added = (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": name}}}]},
        )
        .execute()
    )
    sheet_id = added["replies"][0]["addSheet"]["properties"]["sheetId"]
    try:
        yield service, sid, name
    finally:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
        ).execute()


def test_list_tabs_includes_new_tab(tab):
    service, sid, name = tab
    assert name in sheets.list_tabs(service, sid)


def test_pull_values_empty_tab_returns_empty(tab):
    service, sid, name = tab
    assert sheets.pull_values(service, sid, f"'{name}'!A1:C3") == []


def test_update_then_pull_round_trips(tab):
    service, sid, name = tab
    rows = [["id", "name"], ["A1", "Ada"]]
    result = sheets.update_values(service, sid, f"'{name}'!A1:B2", rows)
    assert result.get("updatedCells") == 4
    assert sheets.pull_values(service, sid, f"'{name}'!A1:B2") == rows


def test_append_adds_rows_after_table(tab):
    service, sid, name = tab
    sheets.update_values(service, sid, f"'{name}'!A1:B1", [["h1", "h2"]])
    result = sheets.append_values(service, sid, f"'{name}'!A1", [["x", "y"]])
    assert result.get("updates", {}).get("updatedRows") == 1
    assert sheets.pull_values(service, sid, f"'{name}'") == [["h1", "h2"], ["x", "y"]]


def test_clear_empties_range(tab):
    service, sid, name = tab
    sheets.update_values(service, sid, f"'{name}'!A1:B2", [["1", "2"], ["3", "4"]])
    result = sheets.clear_values(service, sid, f"'{name}'!A1:B2")
    assert name in result.get("clearedRange", "")
    assert sheets.pull_values(service, sid, f"'{name}'!A1:B2") == []


def test_user_entered_evaluates_formula(tab):
    service, sid, name = tab
    sheets.update_values(service, sid, f"'{name}'!A1", [["=1+2"]])
    assert sheets.pull_values(service, sid, f"'{name}'!A1") == [["3"]]


def test_raw_stores_formula_literally(tab):
    service, sid, name = tab
    sheets.update_values(
        service, sid, f"'{name}'!A1", [["=1+2"]], input_option=sheets.RAW
    )
    assert sheets.pull_values(service, sid, f"'{name}'!A1") == [["=1+2"]]
