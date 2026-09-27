"""Live integration tests for gdrives.sheets against the real Sheets API v4.

These exercise every core value operation (list_tabs, pull/update/append/clear),
the read layer (read_tab, pull_many and its render options), and the
conditional format rule round trip (add/list/delete) against a real
spreadsheet, so they catch anything the fake-service unit tests in
test_sheets.py and test_sheets_rules.py can't — request-shape mismatches, scope
problems, and how the API actually renders formulas, empty ranges, and stored
rule ranges.

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
from gdrives.sheets.retry import RATE_LIMIT_STATUSES, with_retry

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


def _patiently(requests, service, sid):
    """Send structural ``requests``, waiting out the per-minute write quota.

    The fixture's own calls must not give up while the quota is exhausted: a
    ``deleteSheet`` that fails in teardown leaves its temporary tab behind on
    the shared spreadsheet. The quota resets each minute, so the waits here
    (5, 10, 20, 32, and 32 seconds) outlast it.
    """
    return with_retry(
        lambda: (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=sid, body={"requests": requests})
            .execute()
        ),
        statuses=RATE_LIMIT_STATUSES,
        attempts=6,
        base_delay=5.0,
    )


@pytest.fixture
def tab(live_service):
    """Yield (service, spreadsheet_id, tab_name) for a fresh, empty tab.

    Creates a uniquely-named tab and deletes it afterward, so each test starts
    from a blank slate and leaves nothing behind even if it writes.
    """
    service, sid = live_service
    name = "itest_" + uuid.uuid4().hex[:8]
    added = _patiently([{"addSheet": {"properties": {"title": name}}}], service, sid)
    sheet_id = added["replies"][0]["addSheet"]["properties"]["sheetId"]
    try:
        yield service, sid, name
    finally:
        _patiently([{"deleteSheet": {"sheetId": sheet_id}}], service, sid)


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


def test_append_inserts_rows_instead_of_overwriting_the_next_block(tab):
    service, sid, name = tab
    # A table in A1:B2, a blank row 3, and a second block from row 4. The API
    # appends at row 3; OVERWRITE would write the second row over "other".
    sheets.update_values(service, sid, f"'{name}'!A1:B2", [["h1", "h2"], ["a", "b"]])
    sheets.update_values(
        service, sid, f"'{name}'!A4:B5", [["other", "block"], ["keep", "me"]]
    )
    sheets.append_values(service, sid, f"'{name}'!A1:B2", [["x", "y"], ["z", "w"]])
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["h1", "h2"],
        ["a", "b"],
        ["x", "y"],
        ["z", "w"],
        [],
        ["other", "block"],
        ["keep", "me"],
    ]


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


def _seed(service, sid, name, rows):
    """Write a header + data table into the fresh tab and return it."""
    end = sheets.column_letter(len(rows[0]) - 1)
    sheets.update_values(service, sid, f"'{name}'!A1:{end}{len(rows)}", rows)
    return rows


def test_set_by_match_composite_key_multi_column(tab):
    service, sid, name = tab
    _seed(
        service,
        sid,
        name,
        [
            ["year", "id", "status", "amount"],
            ["2025", "C300", "old", "0"],
            ["2026", "C300", "pending", "0"],
            ["2026", "D400", "pending", "0"],
        ],
    )
    result = sheets.set_by_match(
        service,
        sid,
        name,
        {"year": "2026", "id": "C300"},
        {"status": "paid", "amount": "250"},
    )
    assert result["rows"] == [3]  # only the 2026/C300 row
    grid = sheets.pull_values(service, sid, f"'{name}'")
    assert grid[2] == ["2026", "C300", "paid", "250"]  # updated
    assert grid[1] == ["2025", "C300", "old", "0"]  # 2025/C300 untouched
    assert grid[3] == ["2026", "D400", "pending", "0"]  # other id untouched


def test_set_by_match_all_updates_every_match(tab):
    service, sid, name = tab
    _seed(
        service,
        sid,
        name,
        [["id", "status"], ["A", "pending"], ["B", "pending"], ["A", "pending"]],
    )
    result = sheets.set_by_match(
        service, sid, name, {"id": "A"}, {"status": "done"}, allow_multiple=True
    )
    assert result["rows"] == [2, 4]
    statuses = [r[1] for r in sheets.pull_values(service, sid, f"'{name}'")[1:]]
    assert statuses == ["done", "pending", "done"]  # both A rows, B untouched


def test_set_by_match_multiple_rows_refused_without_all(tab):
    service, sid, name = tab
    _seed(service, sid, name, [["id", "v"], ["A", "1"], ["A", "2"]])
    with pytest.raises(ValueError, match="matches rows"):
        sheets.set_by_match(service, sid, name, {"id": "A"}, {"v": "9"})
    # nothing was written
    assert sheets.pull_values(service, sid, f"'{name}'!B2:B3") == [["1"], ["2"]]


def _rules_on(service, sid, name):
    """The conditional format rules on the fresh tab only."""
    return [r for r in sheets.list_conditional_rules(service, sid) if r["tab"] == name]


def _row_count(service, sid, name):
    """The fresh tab's current grid height."""
    result = (
        service.spreadsheets()
        .get(spreadsheetId=sid, fields="sheets.properties(title,gridProperties)")
        .execute()
    )
    (props,) = [
        s["properties"] for s in result["sheets"] if s["properties"]["title"] == name
    ]
    return props["gridProperties"]["rowCount"]


def test_conditional_rule_add_list_delete_round_trips(tab):
    service, sid, name = tab
    assert _rules_on(service, sid, name) == []
    grid = sheets.a1_to_grid_range(service, sid, f"'{name}'!A2:C")
    rule = sheets.build_formula_rule(
        [grid],
        '=$B2="Rejected"',
        strikethrough=True,
        text_color=sheets.hex_to_color("#999999"),
    )
    sheets.add_conditional_rule(service, sid, rule)

    (entry,) = _rules_on(service, sid, name)
    assert entry["index"] == 0
    stored = entry["rule"]
    # The request's open end is not kept: the API stores the range clamped to the
    # tab's current row count (A2:C -> A2:C1000 on a fresh 1000-row tab).
    assert "endRowIndex" not in grid
    assert stored["ranges"][0] == {
        **grid,
        "endRowIndex": _row_count(service, sid, name),
    }
    assert stored["booleanRule"]["condition"] == rule["booleanRule"]["condition"]
    assert sheets.describe_rule(stored).endswith("[strikethrough, text #999999]")

    sheets.delete_conditional_rule(service, sid, entry["sheet_id"], 0)
    assert _rules_on(service, sid, name) == []


def test_conditional_rule_index_orders_and_json_replays(tab):
    service, sid, name = tab
    grid = sheets.a1_to_grid_range(service, sid, f"'{name}'!A1:A10")
    first = sheets.build_formula_rule([grid], "=TRUE", bold=True)
    second = sheets.build_formula_rule([grid], "=FALSE", italic=True)
    sheets.add_conditional_rule(service, sid, first)
    sheets.add_conditional_rule(service, sid, second, index=0)  # jumps ahead
    formulas = [
        r["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
        for r in _rules_on(service, sid, name)
    ]
    assert formulas == ["=FALSE", "=TRUE"]

    # a listed rule re-adds verbatim (the replay path behind --rule-json)
    listed = _rules_on(service, sid, name)[1]["rule"]
    sheets.add_conditional_rule(service, sid, listed, index=2)
    assert _rules_on(service, sid, name)[2]["rule"] == listed


def test_read_tab_reads_canonical_cells_by_header_name(tab):
    service, sid, name = tab
    # A RAW write stores text as typed; a USER_ENTERED one parses numbers and
    # booleans, which the unformatted read returns as values.
    sheets.update_values(
        service,
        sid,
        f"'{name}'!A1:E2",
        [["note", "id", "text", "n", "flag"], ["", "a ", "007", "3.0", "TRUE"]],
        input_option=sheets.RAW,
    )
    sheets.update_values(service, sid, f"'{name}'!B3:E3", [["b", "007", "3.0", "TRUE"]])
    table = sheets.read_tab(service, sid, name, ["id", "text", "n", "flag"], ["id"])
    assert table.rows == [
        {"id": "a ", "text": "007", "n": "3.0", "flag": "TRUE"},
        {"id": "b", "text": "7", "n": "3", "flag": "TRUE"},
    ]
    assert table.row_numbers == {("a",): 2, ("b",): 3}
    assert table.extra_columns == ["note"]


def test_pull_many_reads_ranges_in_order(tab):
    service, sid, name = tab
    _seed(service, sid, name, [["h1", "h2"], ["1", "x"], ["2.5", "y"]])
    grids = sheets.pull_many(
        service,
        sid,
        [f"'{name}'!A2:A3", f"'{name}'!D1:D3", f"'{name}'!B1"],
        render=sheets.UNFORMATTED_VALUE,
    )
    assert grids == [[[1], [2.5]], [], [["h2"]]]


# -- apply and structure: pin FakeSheetGrid's assumptions against the API --


def _table(service, sid, name):
    return sheets.read_tab(service, sid, name, ["id", "name", "code"], ["id"])


def test_apply_pushes_and_appends_past_the_grid_end(tab):
    service, sid, name = tab
    _seed(
        service,
        sid,
        name,
        [["id", "note", "name", "code"], ["a", "keep", "Ada", "1"], ["b", "", "Bo"]],
    )
    # Shrink the grid to the rows in use, so the new rows need grid rows added.
    sheet_id = sheets.tab_grid(service, sid, name).sheet_id
    sheets.batch_update_spreadsheet(
        service,
        sid,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"rowCount": 3},
                    },
                    "fields": "gridProperties.rowCount",
                }
            }
        ],
    )
    table = _table(service, sid, name)
    plan = sheets.MergePlan(
        pushes=[sheets.Cell(("b",), "code", "", "01", "")],
        appends=[
            sheets.NewRow(("c",), {"id": "c", "name": "TRUE", "code": "007"}),
            sheets.NewRow(("d",), {"id": "d", "name": "=1+2", "code": ""}),
        ],
    )
    # apply_plan reads the tab back itself: literal strings must survive.
    result = sheets.apply_plan(service, sid, table, plan)
    assert result == sheets.ApplyResult(1, 2, [3], [4, 5])
    assert _row_count(service, sid, name) == 5
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["id", "note", "name", "code"],
        ["a", "keep", "Ada", "1"],
        ["b", "", "Bo", "01"],
        ["c", "", "TRUE", "007"],
        ["d", "", "=1+2"],
    ]


def test_apply_inserts_above_a_matching_row(tab):
    service, sid, name = tab
    _seed(
        service,
        sid,
        name,
        [["id", "note", "name", "code"], ["a", "", "Ada"], ["b", "old", "Bo"]],
    )
    table = _table(service, sid, name)
    plan = sheets.MergePlan(
        appends=[sheets.NewRow(("c",), {"id": "c", "name": "Cy", "code": "3"})]
    )
    result = sheets.apply_plan(service, sid, table, plan, insert_above={"note": "old"})
    assert result.appended_rows == [3]
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["id", "note", "name", "code"],
        ["a", "", "Ada"],
        ["c", "", "Cy", "3"],
        ["b", "old", "Bo"],
    ]


def test_add_then_delete_columns_by_name(tab):
    service, sid, name = tab
    _seed(service, sid, name, [["id", "name"], ["a", "Ada"]])
    sheets.add_columns(service, sid, name, ["x", "y"], before="name")
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["id", "x", "y", "name"],
        ["a", "", "", "Ada"],
    ]
    sheets.delete_columns(service, sid, name, ["x", "name"])
    assert sheets.pull_values(service, sid, f"'{name}'") == [["id", "y"], ["a"]]
