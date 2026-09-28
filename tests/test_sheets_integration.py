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
from helpers import local_file

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


def test_read_tab_reads_declared_dates_from_their_serials(seeded):
    # Row a holds a date and a date-time, entered as a person types them; row
    # b holds ISO text, as a sync writes it. Both arrive as ISO 8601.
    service, sid, name = seeded(
        [["id", "on", "at"], ["a", "9/27/2026", "9/27/2026 10:30:15"], ["b", "", ""]]
    )
    sheets.update_values(
        service,
        sid,
        f"'{name}'!B3:C3",
        [["2026-09-28", "2026-09-28T01:02:03"]],
        input_option=sheets.RAW,
    )
    table = sheets.read_tab(
        service, sid, name, None, ["id"], types={"on": "date", "at": "datetime"}
    )
    assert table.rows == [
        {"id": "a", "on": "2026-09-27", "at": "2026-09-27 10:30:15.000"},
        {"id": "b", "on": "2026-09-28", "at": "2026-09-28T01:02:03"},
    ]


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
    assert result == sheets.ApplyResult(
        1, 2, [3], [4, 5], [(3, "code")], ["id", "name", "code"]
    )
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
    local = local_file(target.tabs[0])
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


def test_sync_of_a_tab_whose_keys_have_a_blank_component(seeded, tmp_path):
    header = ["year", "id", "v"]
    service, sid, name = seeded([header, ["2026", "", "a"], ["", "1", "b"]])
    target = _target(
        tmp_path,
        sid,
        name,
        {"local": "rows.csv", "key": ["year", "id"], "blank_keys": "partial"},
    )
    tab = target.tabs[0]
    rows = [
        {"year": "2026", "id": "", "v": "a"},
        {"year": "", "id": "1", "v": "b"},
    ]
    sheets.write_records(target.base_path(tab), header, rows)
    sheets.write_records(
        local_file(tab),
        header,
        [rows[0] | {"v": "A"}, rows[1], {"year": "2026", "id": "1", "v": "c"}],
    )
    report = sheets.run_target(service, sid, target, "sync", apply=True)
    assert report.exit_code == 0, sheets.format_report(report)
    (done,) = report.tabs
    assert done.applied == sheets.ApplyResult(
        1, 1, [2], [4], [(2, "v")], ["year", "id", "v"]
    )
    assert sheets.pull_values(service, sid, f"'{name}'") == [
        header,
        ["2026", "", "A"],
        ["", "1", "b"],
        ["2026", "1", "c"],
    ]


GREY = {"red": 0.8, "green": 0.8, "blue": 0.8}


def _values_and_fills(service, sid, name, span):
    """The displayed values of ``span``, and column A's fills, in one read.

    Each row of values is cut at its last non-blank cell, as a values read
    returns it. A fill is None where none is set.
    """
    response = (
        service.spreadsheets()
        .get(
            spreadsheetId=sid,
            ranges=[f"'{name}'!{span}"],
            fields="sheets(data(rowData(values("
            "formattedValue,userEnteredFormat(backgroundColor)))))",
        )
        .execute()
    )
    ((data,),) = [sheet["data"] for sheet in response["sheets"]]
    values, fills = [], []
    for row in data.get("rowData", []):
        cells = row.get("values", [])
        shown = [cell.get("formattedValue", "") for cell in cells]
        while shown and shown[-1] == "":
            shown.pop()
        values.append(shown)
        first = cells[0] if cells else {}
        fills.append(first.get("userEnteredFormat", {}).get("backgroundColor"))
    return values, fills


def test_sync_places_a_new_row_and_a_new_column(seeded, shared_tab, tmp_path):
    # One run closes row b, adds row n, and adds the column "email". The new
    # row belongs above b, the first closed row once the run is done, with
    # the formatting of the open row above it, and the column after "id".
    service, sid, name = seeded(
        [
            ["id", "status", "note"],
            ["a", "open", "keep"],
            ["b", "open"],
            ["c", "closed"],
            ["d", "closed"],
        ]
    )
    grey = {
        "repeatCell": {
            "range": {
                "sheetId": shared_tab.sheet_id,
                "startRowIndex": 3,
                "endRowIndex": 5,
            },
            "cell": {"userEnteredFormat": {"backgroundColor": GREY}},
            "fields": "userEnteredFormat.backgroundColor",
        }
    }
    _patiently(service, sid, {"requests": [grey]})
    target = _target(
        tmp_path,
        sid,
        name,
        {
            "local": "cases.csv",
            "key": ["id"],
            "insert_above": {"status": ["closed"]},
        },
    )
    tab = target.tabs[0]
    sheets.write_records(
        target.base_path(tab),
        ["id", "status"],
        [
            {"id": "a", "status": "open"},
            {"id": "b", "status": "open"},
            {"id": "c", "status": "closed"},
            {"id": "d", "status": "closed"},
        ],
    )
    sheets.write_values_csv(
        str(tab.local),
        [
            ["id", "email", "status"],
            ["a", "a@example.com", "open"],
            ["b", "", "closed"],
            ["c", "", "closed"],
            ["d", "", "closed"],
            ["n", "n@example.com", "open"],
        ],
    )

    report = sheets.run_target(
        service, sid, target, "sync", apply=True, add_missing=True
    )
    assert report.exit_code == 0, sheets.format_report(report)
    (done,) = report.tabs
    assert done.add_columns == ["email"]
    assert done.applied is not None and done.applied.appended_rows == [3]
    values, fills = _values_and_fills(service, sid, name, "A1:D6")
    assert values == [
        ["id", "email", "status", "note"],
        ["a", "a@example.com", "open", "keep"],
        ["n", "n@example.com", "open"],
        ["b", "", "closed"],
        ["c", "", "closed"],
        ["d", "", "closed"],
    ]
    # Only c and d were grey: the new row n did not take it from the row it
    # sits above, nor did b, which was closed by a push.
    assert fills == [None, None, None, None, GREY, GREY]


def test_sync_finds_a_renamed_tab_by_its_sheet_id(seeded, shared_tab, tmp_path):
    header = ["id", "v"]
    service, sid, name = seeded([header, ["a", "1"]])
    renamed = f"{name}_renamed"
    rename = {
        "updateSheetProperties": {
            "properties": {"sheetId": shared_tab.sheet_id, "title": renamed},
            "fields": "title",
        }
    }
    _patiently(service, sid, {"requests": [rename]})
    # The tests after this one reach the tab under the title it has now.
    shared_tab.name = renamed

    target = _target(
        tmp_path,
        sid,
        name,
        {"local": "rows.csv", "key": ["id"], "sheet_id": shared_tab.sheet_id},
    )
    tab = target.tabs[0]
    sheets.write_records(target.base_path(tab), header, [{"id": "a", "v": "1"}])
    sheets.write_records(
        local_file(tab), header, [{"id": "a", "v": "2"}, {"id": "b", "v": "3"}]
    )
    report = sheets.run_target(service, sid, target, "sync", apply=True)
    assert report.exit_code == 0, sheets.format_report(report)
    (done,) = report.tabs
    assert done.tab == name
    assert done.notes == [f"renamed on the sheet: {name!r} is now {renamed!r}"]
    assert target.base_path(tab).name == f"{name}.csv"
    assert sheets.pull_values(service, sid, f"'{renamed}'") == [
        header,
        ["a", "2"],
        ["b", "3"],
    ]


def test_push_with_clear_links_leaves_no_link(tab, shared_tab):
    # What the fake's link rule rests on: a whole-cell URL or domain is linked
    # when it is written, under RAW input; a link on part of a cell's text is
    # in its runs; and writing a value again puts its link back.
    service, sid, name = tab
    header = ["id", "site", "note"]
    rows = [
        ["a", "https://example.com/a", "see the docs"],
        ["b", "example.com", "see https://example.com"],
        ["c", "a@example.com", "plain"],
    ]
    sheets.update_values(
        service, sid, f"'{name}'!A1:C4", [header, *rows], input_option=sheets.RAW
    )
    part = {"startIndex": 4, "format": {"link": {"uri": "https://docs.example.com"}}}
    seeded_runs = {
        "updateCells": {
            "start": {"sheetId": shared_tab.sheet_id, "rowIndex": 1, "columnIndex": 2},
            "rows": [{"values": [{"textFormatRuns": [{"format": {}}, part]}]}],
            "fields": "textFormatRuns",
        }
    }
    _patiently(service, sid, {"requests": [seeded_runs]})
    assert sheets.linked_cells(service, sid, name, header=header) == [
        sheets.LinkedCell(2, "site", ("https://example.com/a",), in_runs=False),
        sheets.LinkedCell(2, "note", ("https://docs.example.com",), in_runs=True),
        sheets.LinkedCell(3, "site", ("http://example.com",), in_runs=False),
    ]

    records = [dict(zip(header, row, strict=True)) for row in rows]
    records.append({"id": "d", "site": "example.org", "note": ""})
    report = sheets.push_rows(
        service, sid, name, header, records, key=["id"], apply=True, clear_links=True
    )
    assert report.error is None and report.wrote_sheet

    # The push left no link, and a value written again is linked again.
    sheets.update_values(
        service, sid, f"'{name}'!B3", [["example.com"]], input_option=sheets.RAW
    )
    assert sheets.linked_cells(service, sid, name, header=header) == [
        sheets.LinkedCell(3, "site", ("http://example.com",), in_runs=False)
    ]


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


def test_reorder_moves_whole_rows(seeded, shared_tab):
    # What the fake's moveDimension rests on: the destination is counted
    # before the row is taken out, a row takes its format and every column
    # with it (the unnamed column C is never read into a record), and a blank
    # row keeps its place. c crosses the blank row down, a moves up past it.
    service, sid, name = seeded(
        [
            ["id", "name", "", "note"],
            ["c", "Cy", "gap c", "third"],
            ["b", "Bo", "", "second"],
            ["", "", "", ""],
            ["a", "Ada", "gap a", "first"],
        ]
    )
    grey = {
        "repeatCell": {
            "range": {
                "sheetId": shared_tab.sheet_id,
                "startRowIndex": 1,
                "endRowIndex": 2,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            "cell": {"userEnteredFormat": {"backgroundColor": GREY}},
            "fields": "userEnteredFormat.backgroundColor",
        }
    }
    _patiently(service, sid, {"requests": [grey]})

    result = sheets.reorder_rows(
        service, sid, name, ["id"], ["a", "b", "c"], apply=True
    )
    assert result == sheets.ReorderResult(
        moves=2, moved=[("a",), ("c",)], unchanged=False, applied=True
    )
    values, fills = _values_and_fills(service, sid, name, "A1:D5")
    assert values == [
        ["id", "name", "", "note"],
        ["a", "Ada", "gap a", "first"],
        ["b", "Bo", "", "second"],
        [],
        ["c", "Cy", "gap c", "third"],
    ]
    assert fills == [None, None, None, None, GREY]


def test_set_url_links_keeps_the_bold_and_clears_the_runs(tab, shared_tab):
    # What the fake's link formats rest on: a link set as the cell's own
    # format takes, runs cleared in an earlier request of the same batch do
    # not drop it, and link, underline, and colour under one mask leave the
    # bold alone.
    service, sid, name = tab
    header = ["id", "site"]
    rows = [["a", "https://example.com/a"], ["b", "https://example.com/b"]]
    sheets.update_values(
        service, sid, f"'{name}'!A1:B3", [header, *rows], input_option=sheets.RAW
    )

    def cell(row):
        return {"sheetId": shared_tab.sheet_id, "rowIndex": row, "columnIndex": 1}

    part = {"startIndex": 8, "format": {"link": {"uri": "https://part.example"}}}
    bold = {"userEnteredFormat": {"textFormat": {"bold": True}}}
    formats = [
        {
            "updateCells": {
                "start": cell(1),
                "rows": [{"values": [bold]}],
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            "updateCells": {
                "start": cell(2),
                "rows": [{"values": [{"textFormatRuns": [{"format": {}}, part]}]}],
                "fields": "textFormatRuns",
            }
        },
    ]
    _patiently(service, sid, {"requests": formats})

    fixed = sheets.set_url_links(service, sid, name, color="#33aa55")
    assert [(problem.row, problem.column) for problem in fixed] == [
        (2, "site"),
        (3, "site"),
    ]
    assert {"color", "underline"} <= set(fixed[0].reasons)
    assert "runs" in fixed[1].reasons

    data = sheets.pull_grid(
        service,
        sid,
        f"'{name}'!B2:B3",
        "sheets(data(rowData(values(hyperlink,textFormatRuns,"
        "userEnteredFormat(textFormat),effectiveFormat(textFormat)))))",
    )
    got = [row["values"][0] for row in data["rowData"]]
    for read, (_, text) in zip(got, rows, strict=True):
        assert read["hyperlink"] == text
        assert "textFormatRuns" not in read
        shown = read["effectiveFormat"]["textFormat"]
        assert shown["underline"] is False
        rgb = shown["foregroundColorStyle"]["rgbColor"]
        assert [round(rgb.get(c, 0) * 255) for c in ("red", "green", "blue")] == [
            0x33,
            0xAA,
            0x55,
        ]
    assert got[0]["userEnteredFormat"]["textFormat"]["bold"] is True
    assert got[0]["effectiveFormat"]["textFormat"]["bold"] is True


def test_sync_with_typed_writes_writes_values_the_sheet_computes_over(tab, tmp_path):
    # A date, a date-time before 10:00, a float, and a boolean go to the sheet
    # as values, with the date formats the package sets; a formula over the
    # pushed date computes; and the next run finds everything in sync.
    service, sid, name = tab
    schema = {
        "due": {"type": "date"},
        "at": {"type": "datetime"},
        "amt": {"type": "float"},
        "paid": {"type": "bool"},
    }
    tab_config = {
        "local": "dues.csv",
        "key": ["id"],
        "typed_writes": True,
        "schema": schema,
    }
    target = _target(tmp_path, sid, name, tab_config)
    local = local_file(target.tabs[0])
    header = ["id", "due", "at", "amt", "paid"]
    sheets.write_values_csv(
        str(local),
        [header, ["007", "2026-09-27", "2026-09-27 09:05:00", "2.5", "true"]],
    )
    first = sheets.run_target(service, sid, target, "sync", apply=True, adopt=True)
    assert first.exit_code == 0, sheets.format_report(first)

    serials = sheets.pull_values(
        service,
        sid,
        f"'{name}'!A2:E2",
        render=sheets.UNFORMATTED_VALUE,
        date_time_render=sheets.SERIAL_NUMBER,
    )
    assert serials == [["007", 46292, 46292 + (9 * 60 + 5) / 1440, 2.5, True]]
    shown = sheets.pull_values(service, sid, f"'{name}'!B2:C2")
    assert shown == [["2026-09-27", "2026-09-27 09:05:00"]]

    # A formula over the pushed date computes, where over text it would not.
    sheets.update_values(service, sid, f"'{name}'!F1:F2", [["next"], ["=B2+7"]])
    (row,) = sheets.pull_values(
        service,
        sid,
        f"'{name}'!F2",
        render=sheets.UNFORMATTED_VALUE,
        date_time_render=sheets.SERIAL_NUMBER,
    )
    assert row == [46299]

    # A local edit is pushed as a value, and the read-back compares by value.
    sheets.write_values_csv(
        str(local),
        [header, ["007", "2026-10-01", "2026-09-27 09:05:00", "3.0", "FALSE"]],
    )
    second = sheets.run_target(service, sid, target, "sync", apply=True)
    assert second.exit_code == 0, sheets.format_report(second)
    serials = sheets.pull_values(
        service,
        sid,
        f"'{name}'!A2:D2",
        render=sheets.UNFORMATTED_VALUE,
        date_time_render=sheets.SERIAL_NUMBER,
    )
    assert serials == [["007", 46296, 46292 + (9 * 60 + 5) / 1440, 3]]

    third = sheets.run_target(service, sid, target, "sync")
    assert third.exit_code == 0, sheets.format_report(third)
    (report,) = third.tabs
    assert report.plan is not None and not report.plan.has_writes
