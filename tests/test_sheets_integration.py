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

The module shares one uniquely-named temporary tab, created before its first
test and deleted after its last, and emptied between tests, so every test
starts from a blank tab, or from the table it seeds the tab with, and runs
never collide with each other or leave state behind — the spreadsheet's other
tabs are never read or modified. One tab for the module, in place of one per
test, keeps the suite's write requests under the API's quota of 60 per minute.
So does counting them: the tab is not emptied after a test that wrote nothing,
and a test finds the table it seeds already in place when the test before it
seeded the same one and wrote nothing else. The suite's read requests are over
their own quota of 60 per minute, so the service waits and sends a request
again when the API refuses it for that, and a run takes from half a minute to
a few minutes. Select or skip the suite with ``-m integration`` /
``-m "not integration"``.
"""

import copy
import os
import uuid
from dataclasses import dataclass

import pytest
from googleapiclient.http import HttpRequest

import gdrives.auth  # import loads .env (python-dotenv), so a .env-set id is visible
from gdrives import sheets
from gdrives.sheets.retry import RATE_LIMIT_STATUSES, with_retry

pytestmark = pytest.mark.integration

SPREADSHEET_ID_ENV = "GDRIVES_TEST_SPREADSHEET_ID"


@dataclass
class _Writes:
    """How many write requests the live service has sent."""

    count: int = 0


@pytest.fixture(scope="session")
def writes():
    """The count of write requests :func:`live_service` has sent."""
    return _Writes()


class _Patient:
    """A request that waits out the per-minute quotas when it is sent.

    The API allows a user 60 reads and 60 writes a minute, and the library's
    calls give up on a rate limit after about 15 seconds. The fixtures' calls
    must not give up either: a ``deleteSheet`` that fails in teardown leaves
    the temporary tab behind on the shared spreadsheet. The limit is not kept
    to the minute: a run straight after another had its first read refused for
    about 100 seconds. The waits here (5, 10, and 20 seconds, then 32 seconds
    four times) come to 163 seconds.
    """

    def __init__(self, request):
        self._request = request

    def execute(self, http=None, num_retries=0):
        return with_retry(
            lambda: self._request.execute(http=http, num_retries=num_retries),
            statuses=RATE_LIMIT_STATUSES,
            attempts=8,
            base_delay=5.0,
        )


@pytest.fixture(scope="session")
def live_service(writes):
    """A (service, spreadsheet_id) pair for the shared test sheet, or skip.

    Skips — never fails — when the sheet id is unset, no service account is
    configured, or the sheet can't be reached (offline, not shared, Sheets API
    disabled). The service is built straight from the service account so the test
    path is deterministic, bypassing the OAuth-first precedence in authenticate().

    Every request the service builds that is not a GET is counted in ``writes``,
    whether or not it succeeds, and every request is a :class:`_Patient` one.
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

    def request(http, postproc, uri, method="GET", **kwargs):
        if method != "GET":
            writes.count += 1
        return _Patient(HttpRequest(http, postproc, uri, method=method, **kwargs))

    service = build("sheets", "v4", credentials=creds, requestBuilder=request)
    try:
        sheets.list_tabs(service, sid)  # sanity: the SA can actually reach the sheet
    except Exception as exc:  # network down, not shared, API disabled, bad id, ...
        pytest.skip(f"test sheet not reachable via service account: {exc}")
    return service, sid


#: The grid a tab is created with, which the reset restores.
DEFAULT_ROWS = 1000
DEFAULT_COLUMNS = 26


def _patiently(service, sid, body, fields=None):
    """Send a structural ``batchUpdate``, waiting out the per-minute write quota.

    The fixtures' own calls must not give up while the quota is exhausted: a
    ``deleteSheet`` that fails in teardown leaves the temporary tab behind on
    the shared spreadsheet. The waiting is the request's own
    (:class:`_Patient`), so this refuses a service whose requests would not
    wait.
    """
    request = service.spreadsheets().batchUpdate(
        spreadsheetId=sid, body=body, fields=fields
    )
    assert isinstance(request, _Patient), "the service's requests do not wait"
    return request.execute()


@dataclass
class _SharedTab:
    """The module's temporary tab, and what it holds.

    ``blank_at`` is the count of writes sent when the tab was last blank, and
    ``seeded_at`` the count when ``seed`` was written to it. While the count
    stands at one of them, the tab still holds what it held then.
    """

    name: str
    sheet_id: int
    blank_at: int
    seed: list[list[str]] | None = None
    seeded_at: int = 0


@pytest.fixture(scope="module")
def shared_tab(live_service, writes):
    """Add the module's temporary tab, and delete it after the last test."""
    service, sid = live_service
    name = "itest_" + uuid.uuid4().hex[:8]
    added = _patiently(
        service, sid, {"requests": [{"addSheet": {"properties": {"title": name}}}]}
    )
    sheet_id = added["replies"][0]["addSheet"]["properties"]["sheetId"]
    try:
        yield _SharedTab(name, sheet_id, blank_at=writes.count)
    finally:
        _patiently(service, sid, {"requests": [{"deleteSheet": {"sheetId": sheet_id}}]})


def _reset(service, sid, sheet_id):
    """Return the temporary tab to the state ``addSheet`` left it in.

    One write restores the grid's size and clears every cell's value, format,
    and the rest of its data. Its response carries the tab's conditional format
    rules, which no request removes in bulk, so a second write follows only
    when a test left rules behind. A test that changes anything else about the
    tab (a column width, a merge, a filter) has to be undone here too.
    """
    response = _patiently(
        service,
        sid,
        {
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": DEFAULT_ROWS,
                                "columnCount": DEFAULT_COLUMNS,
                            },
                        },
                        "fields": "gridProperties(rowCount,columnCount)",
                    }
                },
                {"updateCells": {"range": {"sheetId": sheet_id}, "fields": "*"}},
            ],
            "includeSpreadsheetInResponse": True,
        },
        fields="updatedSpreadsheet.sheets(properties.sheetId,conditionalFormats)",
    )
    (sheet,) = [
        s
        for s in response["updatedSpreadsheet"]["sheets"]
        if s["properties"]["sheetId"] == sheet_id
    ]
    rules = sheet.get("conditionalFormats", [])
    if rules:
        delete = {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
        _patiently(service, sid, {"requests": [delete] * len(rules)})


def _empty(service, sid, shared_tab, writes):
    """Reset the tab, unless nothing was written since it was last blank."""
    if writes.count != shared_tab.blank_at:
        _reset(service, sid, shared_tab.sheet_id)
        shared_tab.blank_at = writes.count


@pytest.fixture
def tab(live_service, shared_tab, writes):
    """Yield (service, spreadsheet_id, tab_name) for the module's tab, emptied.

    The tab is reset when anything was written since it was last blank, so a
    test starts from a blank slate whatever the one before it wrote, and
    whether or not that one passed.
    """
    service, sid = live_service
    _empty(service, sid, shared_tab, writes)
    return service, sid, shared_tab.name


def _seed(service, sid, name, rows):
    """Write a header + data table into the emptied tab."""
    end = sheets.column_letter(len(rows[0]) - 1)
    sheets.update_values(service, sid, f"'{name}'!A1:{end}{len(rows)}", rows)


@pytest.fixture
def seeded(live_service, shared_tab, writes):
    """Return a function that puts a table in the module's tab.

    ``seeded(rows)`` empties the tab as :func:`tab` does, writes ``rows`` from
    A1 the way typed data is entered, and returns (service, spreadsheet_id,
    tab_name). When the tab already holds ``rows``, seeded by the test before
    and not written to since, it is left as it is.
    """
    service, sid = live_service

    def seed(rows):
        held = shared_tab.seed == rows and writes.count == shared_tab.seeded_at
        if not held:
            _empty(service, sid, shared_tab, writes)
            _seed(service, sid, shared_tab.name, rows)
            shared_tab.seed, shared_tab.seeded_at = copy.deepcopy(rows), writes.count
        return service, sid, shared_tab.name

    return seed


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
    sheets.update_values(
        service,
        sid,
        f"'{name}'!A1:B5",
        [["h1", "h2"], ["a", "b"], [], ["other", "block"], ["keep", "me"]],
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


def test_set_by_match_composite_key_multi_column(seeded):
    service, sid, name = seeded(
        [
            ["year", "id", "status", "amount"],
            ["2025", "C300", "old", "0"],
            ["2026", "C300", "pending", "0"],
            ["2026", "D400", "pending", "0"],
        ]
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


def test_set_by_match_all_updates_every_match(seeded):
    service, sid, name = seeded(
        [["id", "status"], ["A", "pending"], ["B", "pending"], ["A", "pending"]]
    )
    result = sheets.set_by_match(
        service, sid, name, {"id": "A"}, {"status": "done"}, allow_multiple=True
    )
    assert result["rows"] == [2, 4]
    statuses = [r[1] for r in sheets.pull_values(service, sid, f"'{name}'")[1:]]
    assert statuses == ["done", "pending", "done"]  # both A rows, B untouched


#: The table of the next two tests, which only read it, so one seed serves both.
REPEATED_ID = [["n", "id"], ["1", "A"], ["2.5", "A"]]


def test_set_by_match_multiple_rows_refused_without_all(seeded):
    service, sid, name = seeded(REPEATED_ID)
    with pytest.raises(ValueError, match="matches rows"):
        sheets.set_by_match(service, sid, name, {"id": "A"}, {"n": "9"})
    # nothing was written
    assert sheets.pull_values(service, sid, f"'{name}'!A2:A3") == [["1"], ["2.5"]]


def test_pull_many_reads_ranges_in_order(seeded):
    service, sid, name = seeded(REPEATED_ID)
    grids = sheets.pull_many(
        service,
        sid,
        [f"'{name}'!A2:A3", f"'{name}'!D1:D3", f"'{name}'!B1"],
        render=sheets.UNFORMATTED_VALUE,
    )
    assert grids == [[[1], [2.5]], [], [["id"]]]


def _rules_on(service, sid, name):
    """The conditional format rules on the temporary tab only."""
    return [r for r in sheets.list_conditional_rules(service, sid) if r["tab"] == name]


def _row_count(service, sid, name):
    """The temporary tab's current grid height."""
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
    assert stored["ranges"][0] == {**grid, "endRowIndex": DEFAULT_ROWS}
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
    rules = _rules_on(service, sid, name)
    formulas = [
        r["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
        for r in rules
    ]
    assert formulas == ["=FALSE", "=TRUE"]

    # a listed rule re-adds verbatim (the replay path behind --rule-json)
    listed = rules[1]["rule"]
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


# -- apply and structure: pin FakeSheetGrid's assumptions against the API --


def _table(service, sid, name):
    return sheets.read_tab(service, sid, name, ["id", "name", "code"], ["id"])


def test_apply_pushes_and_appends_past_the_grid_end(seeded, shared_tab):
    service, sid, name = seeded(
        [["id", "note", "name", "code"], ["a", "keep", "Ada", "1"], ["b", "", "Bo"]]
    )
    # Shrink the grid to the rows in use, so the new rows need grid rows added.
    sheets.batch_update_spreadsheet(
        service,
        sid,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": shared_tab.sheet_id,
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


def test_apply_inserts_above_a_matching_row(seeded):
    service, sid, name = seeded(
        [["id", "note", "name", "code"], ["a", "", "Ada"], ["b", "old", "Bo"]]
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


def test_add_then_delete_columns_by_name(seeded):
    service, sid, name = seeded([["id", "name"], ["a", "Ada"]])
    sheets.add_columns(service, sid, name, ["x", "y"], before="name")
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["id", "x", "y", "name"],
        ["a", "", "", "Ada"],
    ]
    sheets.delete_columns(service, sid, name, ["x", "name"])
    assert sheets.pull_values(service, sid, f"'{name}'") == [["id", "y"], ["a"]]


# -- sync and push: a whole run against the API --


def _target(tmp_path, sid, name, tab):
    """A config target ``roster`` with one tab: the temporary one, as ``tab`` says."""
    data = {"roster": {"spreadsheet": sid, "tabs": {name: tab}}}
    return sheets.parse_config(data, tmp_path / sheets.CONFIG_NAME).target("roster")


def _rows(path):
    return [list(row.values()) for row in sheets.read_records(path).rows]


def test_sync_adopts_merges_and_then_writes_nothing(tab, tmp_path):
    service, sid, name = tab
    target = _target(
        tmp_path, sid, name, {"local": "members.csv", "key": ["member_id"]}
    )
    local = target.tabs[0].local
    base = target.base_path(target.tabs[0])
    header = ["member_id", "name", "status"]
    sheets.write_values_csv(
        str(local), [header, ["m1", "Ada", "active"], ["m2", "Bo", "007"]]
    )

    # First sync: the empty tab gets a header and every local row.
    first = sheets.run_target(service, sid, target, "sync", apply=True, adopt=True)
    assert first.exit_code == 0, sheets.format_report(first)
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        header,
        ["m1", "Ada", "active"],
        ["m2", "Bo", "007"],
    ]
    assert base.exists()

    # A local edit and a sheet edit, on different cells, both land.
    sheets.write_values_csv(
        str(local), [header, ["m1", "Ada", "closed"], ["m2", "Bo", "007"]]
    )
    sheets.update_values(
        service, sid, f"'{name}'!B3", [["Bea"]], input_option=sheets.RAW
    )
    second = sheets.run_target(service, sid, target, "sync", apply=True)
    assert second.exit_code == 0, sheets.format_report(second)
    merged = [["m1", "Ada", "closed"], ["m2", "Bea", "007"]]
    assert sheets.pull_values(service, sid, f"'{name}'") == [header, *merged]
    assert _rows(local) == merged
    assert _rows(base) == merged

    # A run with nothing to do writes nothing anywhere.
    stamps = [path.stat().st_mtime_ns for path in (local, base)]
    third = sheets.run_target(service, sid, target, "sync", apply=True)
    (report,) = third.tabs
    assert third.exit_code == 0
    assert not (report.wrote_sheet or report.wrote_local or report.wrote_base)
    assert [path.stat().st_mtime_ns for path in (local, base)] == stamps


def test_push_that_shrinks_the_tab_clears_the_old_cells(seeded, tmp_path):
    service, sid, name = seeded(
        [
            ["total", "count", "extra", "more"],
            ["a", "1", "x", "y"],
            ["b", "2", "x", "y"],
            ["c", "3", "x", "y"],
        ]
    )
    target = _target(tmp_path, sid, name, {"mode": "push", "local": "summary.csv"})
    sheets.write_values_csv(str(target.tabs[0].local), [["total", "count"], ["a", "9"]])
    report = sheets.run_target(service, sid, target, "push", apply=True)
    assert report.exit_code == 0, sheets.format_report(report)
    # The blanks padding the write cleared every cell the new data does not cover.
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        ["total", "count"],
        ["a", "9"],
    ]
