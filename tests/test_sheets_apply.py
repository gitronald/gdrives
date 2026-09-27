"""Tests for gdrives.sheets.apply: writing a merge plan's sheet side to a tab.

Every test runs against ``FakeSheetGrid``, which holds the tab's cells and
applies writes to them, so the tests assert the sheet a run leaves behind and
the order of the calls that built it, not only what ``apply_plan`` returns.
The table a plan was computed from is read from a separate fake holding the
same cells, so the working fake's call log starts at the apply.
"""

from datetime import date

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error

from gdrives.sheets import (
    ApplyError,
    ApplyResult,
    Cell,
    MergePlan,
    NewRow,
    ReadBackError,
    SheetChangedError,
    apply_plan,
    insert_point,
    read_tab,
    verify,
)

# The projection skips "note" and the unnamed gap, so new-row writes must
# step around both.
HEADER = ["id", "note", "name", "", "amt"]
PROJECTION = ["id", "name", "amt"]
ROWS = [["a", "n1", "Ada", "", "1"], ["b", "", "Bo", "", "2"]]
READ = "values.get"
PUSH = "values.batchUpdate"
GRID = "spreadsheets.get"
STRUCTURE = "spreadsheets.batchUpdate"


def sheet(*rows, project=PROJECTION, header=HEADER, **size):
    """A working fake holding ``header`` and ``rows``, and the table read from it.

    ``size`` (``rows=``, ``columns=``) sets the grid's size.
    """
    tabs = {"T": [header, *rows]}
    table = read_tab(FakeSheetGrid(tabs, **size), "S", "T", project, ["id"])
    return FakeSheetGrid(tabs, **size), table


def push(key, column, local):
    return Cell(key=(key,), column=column, base="", local=local, sheet="")


def new(key, **values):
    return NewRow(key=(key,), values={"id": key, "name": "", "amt": ""} | values)


def plan(pushes=(), appends=()):
    return MergePlan(pushes=list(pushes), appends=list(appends))


def requests_of(grid):
    """The one structural batch the apply sent, as (kind, body) pairs."""
    (body,) = [kwargs["body"] for method, kwargs in grid.calls if method == STRUCTURE]
    return [next(iter(request.items())) for request in body["requests"]]


@pytest.fixture
def no_sleep(monkeypatch):
    """Retries back off without waiting."""
    monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)


class TestNothingToDo:
    def test_an_empty_plan_makes_no_request(self):
        grid, table = sheet(*ROWS)
        result = apply_plan(grid, "S", table, plan(), insert_above={"id": "a"})
        assert result == ApplyResult(0, 0, [], [])
        assert grid.calls == []


class TestRefusals:
    def test_an_unkeyed_table_is_refused(self):
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        table = read_tab(grid, "S", "T", PROJECTION)
        grid.calls.clear()
        with pytest.raises(ValueError, match="a plan applies to a table read by key"):
            apply_plan(grid, "S", table, plan([push("a", "name", "x")]))
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            (plan([push("a", "note", "x")]), "column 'note' is not a data column"),
            (plan([push("a", "id", "z")]), "column 'id' is not a data column"),
            (plan([push("zz", "name", "x")]), r"row \('zz',\) is not in the table"),
            (
                plan([push("a", "name", "x"), push("a", "name", "y")]),
                r"cell \('a',\), 'name' is pushed twice",
            ),
            (plan(appends=[new("a")]), r"new row \('a',\) is already in the tab"),
            (plan(appends=[new("c"), new("c")]), r"new row \('c',\) is already"),
            (
                plan(appends=[new("c", note="x")]),
                r"new row \('c',\) has columns \['note'\] not read",
            ),
        ],
    )
    def test_a_plan_that_does_not_fit_is_refused_before_any_request(self, bad, message):
        grid, table = sheet(*ROWS)
        with pytest.raises(ValueError, match=message):
            apply_plan(grid, "S", table, bad)
        assert grid.calls == []

    def test_every_misfit_is_listed_at_once(self):
        grid, table = sheet(*ROWS)
        with pytest.raises(ValueError) as raised:
            apply_plan(grid, "S", table, plan([push("zz", "note", "x")], [new("b")]))
        assert str(raised.value) == (
            "tab 'T': the plan does not fit the table: "
            "push to column 'note' is not a data column; "
            "push to row ('zz',) is not in the table; "
            "new row ('b',) is already in the tab or the plan"
        )

    @pytest.mark.parametrize(
        ("insert_above", "message"),
        [
            ({"id": "a", "name": "Bo"}, "names one column, not 2"),
            ({}, "names one column, not 0"),
            ({"status": "x"}, "column 'status' is not in the header"),
            ({"": "x"}, "column '' is not in the header"),
            ({"id": []}, "column 'id' lists no values"),
        ],
    )
    def test_a_bad_insert_above_is_refused_before_any_request(
        self, insert_above, message
    ):
        grid, table = sheet(*ROWS)
        with pytest.raises(ValueError, match=message):
            apply_plan(
                grid, "S", table, plan(appends=[new("c")]), insert_above=insert_above
            )
        assert grid.calls == []

    @pytest.mark.parametrize(
        "the_plan", [plan(), plan([push("a", "name", "x")])], ids=["empty", "pushes"]
    )
    def test_a_bad_insert_above_is_refused_without_appends(self, the_plan):
        grid, table = sheet(*ROWS)
        with pytest.raises(ValueError, match="column 'status' is not in the header"):
            apply_plan(grid, "S", table, the_plan, insert_above={"status": "x"})
        assert grid.calls == []


class TestGuard:
    """The tab is read again first; any change aborts with nothing written."""

    @staticmethod
    def refused(grid, table, the_plan, message, **options):
        before = grid.values("T")
        with pytest.raises(SheetChangedError, match=message) as raised:
            apply_plan(grid, "S", table, the_plan, **options)
        assert grid.methods == [READ]
        assert grid.values("T") == before
        assert str(raised.value).startswith(
            "tab 'T' changed since it was read, so nothing was written: "
        )

    def test_an_edited_projection_cell(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [["b", "", "Bea"]], row=3)
        self.refused(
            grid, table, plan([push("a", "name", "x")]), r"rows edited: \[\('b',\)\]$"
        )

    def test_a_row_inserted_above_moves_the_rows_below(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [HEADER, ["c", "", "Cy"], *ROWS])
        self.refused(
            grid,
            table,
            plan([push("b", "amt", "9")]),
            r"rows added: \[\('c',\)\]; rows moved: \[\('a',\), \('b',\)\]$",
        )

    def test_a_row_cleared(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [[""] * 5], row=2)
        self.refused(
            grid, table, plan(appends=[new("c")]), r"rows removed: \[\('a',\)\]$"
        )

    def test_a_changed_header(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [["id", "note", "name", "status", "amt"]])
        self.refused(
            grid,
            table,
            plan([push("a", "name", "x")]),
            r"header was \['id', 'note', 'name', '', 'amt'\], "
            r"now \['id', 'note', 'name', 'status', 'amt'\]$",
        )

    def test_a_tab_that_no_longer_reads_cleanly(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [["a", "", "Dup"]], row=4)
        self.refused(
            grid,
            table,
            plan([push("a", "name", "x")]),
            r"duplicate key \('a',\) in rows \[2, 4\]",
        )

    def test_an_edit_just_before_the_reread_is_caught(self):
        grid, table = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["a", "n1", "Ada", "", "1.5"]], row=2),
            before=READ,
            occurrence=1,
        )
        with pytest.raises(SheetChangedError, match=r"rows edited: \[\('a',\)\]"):
            apply_plan(grid, "S", table, plan([push("b", "name", "x")]))
        assert grid.methods == [READ]

    def test_is_an_apply_error_and_a_value_error(self):
        assert issubclass(SheetChangedError, ApplyError)
        assert issubclass(ReadBackError, ApplyError)
        assert issubclass(ApplyError, ValueError)

    def test_edits_outside_the_projection_do_not_block(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [["a", "a note", "Ada", "gap text"]], row=2)
        apply_plan(grid, "S", table, plan([push("a", "name", "Ada L")]))
        assert grid.values("T")[1] == ["a", "a note", "Ada L", "gap text", "1"]


class TestPushes:
    def test_one_raw_batch_addressed_by_header_position(self):
        # The projection order differs from the header order: the column
        # letter comes from the header.
        grid, table = sheet(*ROWS, project=["amt", "id", "name"])
        result = apply_plan(
            grid, "S", table, plan([push("b", "amt", "01"), push("a", "name", "Al")])
        )
        ((_, kwargs),) = [call for call in grid.calls if call[0] == PUSH]
        assert kwargs == {
            "spreadsheetId": "S",
            "body": {
                "valueInputOption": "RAW",
                "data": [
                    {"range": "'T'!E3", "values": [["01"]]},
                    {"range": "'T'!C2", "values": [["Al"]]},
                ],
            },
        }
        assert grid.values("T")[1:] == [
            ["a", "n1", "Al", "", "1"],
            ["b", "", "Bo", "", "01"],  # the literal string, not the number 1
        ]
        # The cells are in the plan's order, not the sheet's.
        assert result == ApplyResult(2, 0, [2, 3], [], [(3, "amt"), (2, "name")], [])
        assert grid.methods == [READ, PUSH, READ]

    def test_columns_past_z(self):
        header = ["id", *(f"c{i}" for i in range(26)), "far"]  # "far" is AB
        grid, table = sheet(["a"], project=["id", "far"], header=header, columns=28)
        apply_plan(grid, "S", table, plan([push("a", "far", "x")]))
        ((_, kwargs),) = [call for call in grid.calls if call[0] == PUSH]
        assert kwargs["body"]["data"][0]["range"] == "'T'!AB2"
        assert grid.values("T")[1] == ["a", *[""] * 26, "x"]

    def test_rows_below_a_blank_gap_use_their_true_row_numbers(self):
        grid, table = sheet(ROWS[0], [], [], ROWS[1])
        apply_plan(grid, "S", table, plan([push("b", "name", "Bea")]))
        assert grid.values("T")[4] == ["b", "", "Bea", "", "2"]

    def test_a_503_on_the_push_is_retried(self, no_sleep):
        grid, table = sheet(*ROWS)
        grid.fail(PUSH, http_error(503, "unavailable"))
        apply_plan(grid, "S", table, plan([push("a", "name", "Al")]))
        assert grid.methods == [READ, PUSH, PUSH, READ]
        assert grid.values("T")[1][2] == "Al"


class TestNewRows:
    def test_go_after_the_last_row_holding_anything(self):
        # Row 4 is blank (not the end), and row 5 holds only a note outside the
        # projection, keyed: new rows go after row 5, not into the gap.
        grid, table = sheet(ROWS[0], [], ["b", "a note"], columns=5)
        result = apply_plan(
            grid, "S", table, plan(appends=[new("c", name="Cy"), new("d", amt="4")])
        )
        assert grid.values("T") == [
            HEADER,
            ROWS[0],
            [],
            ["b", "a note"],
            ["c", "", "Cy"],
            ["d", "", "", "", "4"],
        ]
        # The columns written in each new row, in header order.
        assert result == ApplyResult(0, 2, [], [5, 6], [], ["id", "name", "amt"])
        assert grid.methods == [READ, GRID, STRUCTURE, READ]

    def test_only_projection_columns_are_written(self):
        # "note" (B) and the unnamed gap (D) sit between projection columns:
        # each run of adjacent projection columns gets its own updateCells.
        grid, table = sheet(*ROWS)
        apply_plan(grid, "S", table, plan(appends=[new("c", name="Cy", amt="3")]))
        cells = [body for kind, body in requests_of(grid) if kind == "updateCells"]
        assert [
            (c["start"]["columnIndex"], len(c["rows"][0]["values"])) for c in cells
        ] == [
            (0, 1),
            (2, 1),
            (4, 1),
        ]
        assert all(c["fields"] == "userEnteredValue" for c in cells)
        assert all(c["start"]["rowIndex"] == 3 for c in cells)

    def test_adjacent_columns_share_one_request(self):
        header = ["id", "name", "amt", "note"]
        grid, table = sheet(["a", "Ada", "1", "x"], header=header)
        apply_plan(grid, "S", table, plan(appends=[new("c", name="Cy")]))
        (cells,) = [body for kind, body in requests_of(grid) if kind == "updateCells"]
        assert cells["rows"] == [
            {
                "values": [
                    {"userEnteredValue": {"stringValue": "c"}},
                    {"userEnteredValue": {"stringValue": "Cy"}},
                    {},  # blank: the cell is left empty
                ]
            }
        ]
        assert grid.values("T")[2] == ["c", "Cy"]

    def test_values_are_written_as_literal_strings(self):
        grid, table = sheet(*ROWS)
        apply_plan(grid, "S", table, plan(appends=[new("c", name="=1+2", amt="007")]))
        assert grid.values("T")[3] == ["c", "", "=1+2", "", "007"]

    def test_fit_without_growing_the_grid(self):
        grid, table = sheet(*ROWS, rows=5)
        apply_plan(grid, "S", table, plan(appends=[new("c"), new("d")]))
        assert [kind for kind, _ in requests_of(grid)] == ["updateCells"] * 3
        assert grid.tab("T").row_count == 5
        assert [row[0] for row in grid.values("T")] == ["id", "a", "b", "c", "d"]

    def test_grow_the_grid_in_the_same_request_when_they_do_not_fit(self):
        grid, table = sheet(*ROWS, rows=4)
        result = apply_plan(
            grid, "S", table, plan(appends=[new("c"), new("d"), new("e")])
        )
        kinds = requests_of(grid)
        assert kinds[0] == (
            "appendDimension",
            {"sheetId": 0, "dimension": "ROWS", "length": 2},
        )
        assert grid.tab("T").row_count == 6
        assert [row[0] for row in grid.values("T")] == ["id", "a", "b", "c", "d", "e"]
        assert result.appended_rows == [4, 5, 6]

    def test_the_tab_is_found_by_title_among_several(self):
        tabs = {"Other": [["x"]], "T": [HEADER, *ROWS]}
        table = read_tab(FakeSheetGrid(tabs), "S", "T", PROJECTION, ["id"])
        grid = FakeSheetGrid(tabs, rows=3)
        apply_plan(grid, "S", table, plan(appends=[new("c")]))
        (_, grow), *cells = requests_of(grid)
        assert grow["sheetId"] == 1
        assert {body["start"]["sheetId"] for _, body in cells} == {1}
        assert grid.values("Other") == [["x"]]
        assert grid.values("T")[3] == ["c"]

    def test_a_503_on_the_row_write_is_not_retried(self, no_sleep):
        # The rows may have landed; writing them again would add them twice.
        grid, table = sheet(*ROWS)
        grid.fail(STRUCTURE, http_error(503, "unavailable"))
        with pytest.raises(HttpError):
            apply_plan(grid, "S", table, plan(appends=[new("c")]))
        assert grid.methods == [READ, GRID, STRUCTURE]

    def test_a_429_on_the_row_write_is_retried(self, no_sleep):
        grid, table = sheet(*ROWS)
        grid.fail(STRUCTURE, http_error(429, "rate limited"))
        apply_plan(grid, "S", table, plan(appends=[new("c")]))
        assert grid.methods == [READ, GRID, STRUCTURE, STRUCTURE, READ]
        assert grid.values("T")[3] == ["c"]


class TestInsertAbove:
    def test_inserts_above_the_first_matching_row(self):
        rows = [["a", "", "Ada"], ["b", "old", "Bo"], ["c", "old", "Cy"]]
        grid, table = sheet(*rows)
        result = apply_plan(
            grid,
            "S",
            table,
            plan(appends=[new("d", name="Di"), new("e")]),
            insert_above={"note": "old"},
        )
        assert grid.values("T") == [
            HEADER,
            ["a", "", "Ada"],
            ["d", "", "Di"],
            ["e"],
            ["b", "old", "Bo"],
            ["c", "old", "Cy"],
        ]
        assert result.appended_rows == [3, 4]
        kinds = requests_of(grid)
        assert kinds[0] == (
            "insertDimension",
            {
                "range": {
                    "sheetId": 0,
                    "dimension": "ROWS",
                    "startIndex": 2,
                    "endIndex": 4,
                },
                # The new rows take the formatting of the row above them, not
                # of the block they are kept out of.
                "inheritFromBefore": True,
            },
        )
        assert {body["start"]["rowIndex"] for _, body in kinds[1:]} == {2}
        assert grid.methods == [READ, GRID, STRUCTURE, READ]

    def test_above_the_first_data_row(self):
        grid, table = sheet(*ROWS)
        apply_plan(grid, "S", table, plan(appends=[new("c")]), insert_above={"id": "a"})
        assert [row[0] for row in grid.values("T")] == ["id", "c", "a", "b"]
        kind, body = requests_of(grid)[0]
        assert kind == "insertDimension" and body["range"]["startIndex"] == 1
        # The row above is the header, so the rows inherit from below.
        assert body["inheritFromBefore"] is False

    def test_a_push_to_a_match_moves_the_insert_point_up(self):
        # Rows b and c are open and row d is closed; the run closes row b and
        # adds a row, which belongs above b, the first closed row once it is
        # done. 0.11.0 placed it above d, below a closed row.
        header = ["id", "status"]
        rows = [["a", "open"], ["b", "open"], ["c", "open"], ["d", "closed"]]
        grid, table = sheet(*rows, header=header, project=header)
        result = apply_plan(
            grid,
            "S",
            table,
            plan(
                [Cell(("b",), "status", "open", "closed", "open")],
                [NewRow(("n",), {"id": "n", "status": "open"})],
            ),
            insert_above={"status": "closed"},
        )
        assert grid.values("T") == [
            header,
            ["a", "open"],
            ["n", "open"],
            ["b", "closed"],
            ["c", "open"],
            ["d", "closed"],
        ]
        assert result == ApplyResult(1, 1, [4], [3], [(4, "status")], ["id", "status"])

    def test_a_push_away_from_a_match_moves_the_insert_point_down(self):
        header = ["id", "status"]
        rows = [["a", "closed"], ["b", "open"], ["c", "closed"]]
        grid, table = sheet(*rows, header=header, project=header)
        apply_plan(
            grid,
            "S",
            table,
            plan(
                [Cell(("a",), "status", "closed", "open", "closed")],
                [NewRow(("n",), {"id": "n", "status": "open"})],
            ),
            insert_above={"status": "closed"},
        )
        assert [row[0] for row in grid.values("T")] == ["id", "a", "b", "n", "c"]

    def test_any_of_several_values_compared_as_canonical_strings(self):
        header = ["id", "year"]
        grid, table = sheet(
            ["a", 2024], ["b", 2025], ["c", 2026], header=header, project=["id"]
        )
        apply_plan(
            grid,
            "S",
            table,
            plan(appends=[NewRow(key=("n",), values={"id": "n"})]),
            insert_above={"year": (2026, 2025.0)},
        )
        assert [row[0] for row in grid.values("T")] == ["id", "a", "n", "b", "c"]

    def test_a_scalar_value(self):
        grid, table = sheet(*ROWS)
        apply_plan(
            grid, "S", table, plan(appends=[new("c")]), insert_above={"name": "Bo"}
        )
        assert [row[0] for row in grid.values("T")] == ["id", "a", "c", "b"]

    def test_no_match_goes_after_the_last_row(self):
        grid, table = sheet(*ROWS, rows=3)
        result = apply_plan(
            grid, "S", table, plan(appends=[new("c")]), insert_above={"note": "none"}
        )
        kinds = [kind for kind, _ in requests_of(grid)]
        assert kinds[0] == "appendDimension" and "insertDimension" not in kinds
        assert [row[0] for row in grid.values("T")] == ["id", "a", "b", "c"]
        assert result.appended_rows == [4]

    def test_pushes_land_before_the_insert_shifts_rows(self):
        rows = [["a", "", "Ada"], ["b", "old", "Bo"], ["c", "", "Cy"]]
        grid, table = sheet(*rows)
        result = apply_plan(
            grid,
            "S",
            table,
            plan(
                [push("a", "name", "Al"), push("c", "name", "Cyd")],
                [new("d"), new("e")],
            ),
            insert_above={"note": "old"},
        )
        assert grid.methods == [READ, GRID, PUSH, STRUCTURE, READ]
        ((_, kwargs),) = [call for call in grid.calls if call[0] == PUSH]
        # Addressed by the rows before the insert ...
        assert [d["range"] for d in kwargs["body"]["data"]] == ["'T'!C2", "'T'!C4"]
        # ... and reported where they sit after it.
        assert result == ApplyResult(
            2,
            2,
            [2, 6],
            [3, 4],
            # Row c was read in row 4, and sits in row 6 after the insert.
            [(2, "name"), (6, "name")],
            ["id", "name", "amt"],
        )
        assert grid.values("T") == [
            HEADER,
            ["a", "", "Al"],
            ["d"],
            ["e"],
            ["b", "old", "Bo"],
            ["c", "", "Cyd"],
        ]


class TestWrittenCells:
    def test_the_columns_of_new_rows_follow_the_header_not_the_projection(self):
        grid, table = sheet(*ROWS, project=["amt", "id", "name"])
        added = NewRow(("c",), {"amt": "3", "id": "c", "name": "Cy"})
        result = apply_plan(grid, "S", table, plan(appends=[added]))
        assert result.appended_columns == ["id", "name", "amt"]
        assert result.appended_rows == [4] and result.pushed_cells == []

    def test_a_cell_pushed_twice_over_is_listed_once_per_push(self):
        grid, table = sheet(*ROWS)
        result = apply_plan(
            grid,
            "S",
            table,
            plan([push("b", "name", "Bea"), push("b", "amt", "9")]),
        )
        assert result.pushed_cells == [(3, "name"), (3, "amt")]
        assert result.pushed_rows == [3] and result.appended_columns == []

    def test_nothing_written_names_no_cell(self):
        grid, table = sheet(*ROWS)
        result = apply_plan(grid, "S", table, plan())
        assert result == ApplyResult(0, 0, [], [])
        assert (result.pushed_cells, result.appended_columns) == ([], [])


class TestClearLinks:
    HEADER = ["id", "note", "site", "", "name"]
    ROWS = [["a", "n1", "plain", "", "Ada"], ["b", "old", "example.org", "", "Bo"]]
    PROJECT = ["id", "site", "name"]
    LINK = "userEnteredFormat.textFormat.link"

    def sheet(self):
        return sheet(*self.ROWS, header=self.HEADER, project=self.PROJECT)

    def new_row(self, key, site):
        return NewRow((key,), {"id": key, "site": site, "name": "New"})

    def test_new_rows_are_written_with_no_link_in_the_requests_of_today(self):
        grid, table = self.sheet()
        the_plan = plan(appends=[self.new_row("c", "https://example.com/c")])
        apply_plan(grid, "S", table, the_plan, clear_links=True)
        # The one read more is the read-back of the links.
        assert grid.methods == [READ, GRID, STRUCTURE, READ, GRID]
        kinds = requests_of(grid)
        assert [kind for kind, _ in kinds] == ["updateCells"] * 3
        assert {body["fields"] for _, body in kinds} == {
            f"userEnteredValue,{self.LINK}"
        }
        assert grid.values("T")[3] == ["c", "", "https://example.com/c", "", "New"]
        # The link row b held before the run is not the run's to clear.
        assert grid.links("T") == {(3, 3): "http://example.org"}

    def test_without_clear_links_a_new_row_is_linked_as_the_api_links_it(self):
        grid, table = self.sheet()
        the_plan = plan(appends=[self.new_row("c", "https://example.com/c")])
        apply_plan(grid, "S", table, the_plan)
        assert grid.methods == [READ, GRID, STRUCTURE, READ]
        assert grid.links("T")[(4, 3)] == "https://example.com/c"

    def test_pushed_cells_cost_one_request_more(self):
        grid, table = self.sheet()
        the_plan = plan(
            [push("a", "site", "example.com"), push("b", "name", "Bea")],
        )
        apply_plan(grid, "S", table, the_plan, clear_links=True)
        assert grid.methods == [READ, GRID, PUSH, STRUCTURE, READ, GRID]
        assert requests_of(grid) == [
            (
                "repeatCell",
                {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": row,
                        "endRowIndex": row + 1,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "cell": {},
                    "fields": f"{self.LINK},textFormatRuns",
                },
            )
            for row, column in [(1, 2), (2, 4)]
        ]
        assert grid.values("T")[1][2] == "example.com"
        assert grid.links("T") == {(3, 3): "http://example.org"}

    def test_the_clears_use_the_rows_as_they_are_after_the_insert(self):
        grid, table = self.sheet()
        grid.tab("T").formats[(2, 2)]["runs"] = [
            {"startIndex": 0, "format": {"link": {"uri": "https://old.example"}}}
        ]
        the_plan = plan(
            [push("b", "site", "https://example.com/b")],
            [self.new_row("c", "example.com"), self.new_row("d", "plain")],
        )
        result = apply_plan(
            grid, "S", table, the_plan, insert_above={"note": "old"}, clear_links=True
        )
        assert result.pushed_cells == [(5, "site")]
        assert grid.values("T") == [
            self.HEADER,
            self.ROWS[0],
            ["c", "", "example.com", "", "New"],
            ["d", "", "plain", "", "New"],
            ["b", "old", "https://example.com/b", "", "Bo"],
        ]
        assert grid.links("T") == {}
        assert grid.format("T", 5, 3) == {}
        kinds = [kind for kind, _ in requests_of(grid)]
        assert kinds == ["insertDimension", "updateCells", "updateCells", "repeatCell"]
        assert requests_of(grid)[-1][1]["range"]["startRowIndex"] == 4

    def test_a_link_that_remains_fails_the_read_back(self):
        grid, table = self.sheet()
        # The link comes back between the write and the read-back.
        grid.edit_externally(
            lambda g: (
                g.write("T", [["example.com"]], row=2)
                or g.tab("T").formats.update({(1, 2): {"link": "http://example.com"}})
            ),
            before=GRID,
            occurrence=2,
        )
        with pytest.raises(ReadBackError) as raised:
            apply_plan(
                grid,
                "S",
                table,
                plan([push("a", "site", "example.com")]),
                clear_links=True,
            )
        assert str(raised.value) == (
            "tab 'T': the read-back found links the run did not clear: row 2, "
            "column 'site' still holds a link to ['http://example.com']"
        )

    def test_nothing_to_write_asks_nothing(self):
        grid, table = self.sheet()
        apply_plan(grid, "S", table, plan(), clear_links=True)
        assert grid.calls == []


class TestPartialKeys:
    HEADER = ["y", "id", "v"]
    ROWS = [["2026", "", "a"], ["", "1", "b"]]

    def sheet(self):
        tabs = {"T": [self.HEADER, *self.ROWS]}
        table = read_tab(
            FakeSheetGrid(tabs),
            "S",
            "T",
            self.HEADER,
            ["y", "id"],
            blank_keys="partial",
        )
        return FakeSheetGrid(tabs), table

    def test_the_re_read_and_the_read_back_index_rows_as_the_table_did(self):
        grid, table = self.sheet()
        the_plan = plan(
            [Cell(("2026", ""), "v", "a", "A", "a")],
            [NewRow(("2027", ""), {"y": "2027", "id": "", "v": "c"})],
        )
        result = apply_plan(grid, "S", table, the_plan)
        assert result == ApplyResult(1, 1, [2], [4], [(2, "v")], ["y", "id", "v"])
        assert grid.values("T") == [
            self.HEADER,
            ["2026", "", "A"],
            ["", "1", "b"],
            ["2027", "", "c"],
        ]


class TestTypedDates:
    """The re-read guard and the read-back read a typed table as it was read."""

    HEADER = ["id", "on", "name"]
    ROWS = [["a", date(2026, 9, 27), "Ada"], ["b", "2026-09-28", "Bo"]]
    SERIALS = "values.batchGet"

    def sheet(self):
        tabs = {"T": [self.HEADER, *self.ROWS]}
        table = read_tab(
            FakeSheetGrid(tabs), "S", "T", self.HEADER, ["id"], types={"on": "date"}
        )
        return FakeSheetGrid(tabs), table

    def test_a_date_cell_does_not_read_as_a_change(self):
        grid, table = self.sheet()
        assert [row["on"] for row in table.rows] == ["2026-09-27", "2026-09-28"]
        result = apply_plan(
            grid,
            "S",
            table,
            plan(
                [push("a", "name", "Al"), push("b", "on", "2026-10-01")],
                [NewRow(("c",), {"id": "c", "on": "2026-09-29", "name": "Cy"})],
            ),
        )
        assert result == ApplyResult(
            2, 1, [2, 3], [4], [(2, "name"), (3, "on")], ["id", "on", "name"]
        )
        assert grid.values("T") == [
            self.HEADER,
            ["a", date(2026, 9, 27), "Al"],
            ["b", "2026-10-01", "Bo"],
            ["c", "2026-09-29", "Cy"],
        ]
        assert grid.methods == [
            READ,
            self.SERIALS,
            GRID,
            PUSH,
            STRUCTURE,
            READ,
            self.SERIALS,
        ]

    def test_a_date_changed_on_the_sheet_is_a_change(self):
        grid, table = self.sheet()
        grid.write("T", [[date(2026, 9, 26)]], row=2)
        grid.tab("T").cells[1][:2] = ["a", date(2026, 9, 26)]
        with pytest.raises(SheetChangedError, match=r"rows edited: \[\('a',\)\]"):
            apply_plan(grid, "S", table, plan([push("a", "name", "Al")]))
        assert PUSH not in grid.methods

    def test_verify_reads_the_dates_the_same_way(self):
        grid, table = self.sheet()
        verify(grid, "S", table, plan([push("a", "on", "2026-09-27")]))
        assert grid.methods == [READ, self.SERIALS]


class TestInsertPoint:
    HEADER = ["id", "status", "name"]
    ROWS = [
        ["a", "open", "Ada"],
        ["b", "open", "Bo"],
        [],
        ["c", "closed", "Cy"],
        ["d", "closed", "Di"],
    ]

    def table(self, project=("id", "status", "name")):
        tabs = {"T": [self.HEADER, *self.ROWS]}
        return read_tab(FakeSheetGrid(tabs), "S", "T", list(project), ["id"])

    def closes(self, key):
        return Cell((key,), "status", "open", "closed", "open")

    def test_the_first_matching_row_as_read(self):
        # Row 4 is blank, so c sits in spreadsheet row 5.
        assert insert_point(self.table(), plan(), {"status": "closed"}) == 5

    def test_a_push_to_a_match_counts_as_the_value_the_row_will_hold(self):
        the_plan = plan([self.closes("b")], [new("n")])
        assert insert_point(self.table(), the_plan, {"status": "closed"}) == 3

    def test_a_push_away_from_a_match_no_longer_matches(self):
        the_plan = plan(
            [
                Cell(("c",), "status", "closed", "open", "closed"),
                Cell(("d",), "status", "closed", "open", "closed"),
            ]
        )
        assert insert_point(self.table(), the_plan, {"status": "closed"}) is None

    def test_several_pushes_to_the_column(self):
        the_plan = plan(
            [
                self.closes("b"),
                self.closes("a"),
                Cell(("c",), "status", "closed", "open", "closed"),
            ]
        )
        assert insert_point(self.table(), the_plan, {"status": "closed"}) == 2

    def test_a_push_to_another_column_changes_nothing(self):
        the_plan = plan([push("a", "name", "closed")])
        assert insert_point(self.table(), the_plan, {"status": "closed"}) == 5

    def test_any_of_several_values(self):
        the_plan = plan([Cell(("b",), "status", "open", "held", "open")])
        point = insert_point(self.table(), the_plan, {"status": ["closed", "held"]})
        assert point == 3

    def test_no_match(self):
        assert insert_point(self.table(), plan(), {"status": "gone"}) is None

    def test_a_column_outside_the_projection_is_as_read(self):
        # The plan cannot push to a column it does not carry.
        table = self.table(project=("id", "name", "status"))
        the_plan = plan([push("a", "name", "closed")], [new("n")])
        assert insert_point(table, the_plan, {"status": "closed"}) == 5

    def test_a_plan_with_no_new_rows_still_has_a_point(self):
        assert (
            insert_point(self.table(), plan([self.closes("a")]), {"status": "closed"})
            == 2
        )

    def test_a_column_the_table_did_not_read_is_refused(self):
        table = self.table(project=("id", "name"))
        with pytest.raises(
            ValueError, match="insert_above column 'status' was not read"
        ):
            insert_point(table, plan(), {"status": "closed"})

    def test_a_column_the_header_lacks_is_refused(self):
        with pytest.raises(ValueError, match="'nope' is not in the header"):
            insert_point(self.table(), plan(), {"nope": "x"})

    def test_apply_inserts_at_the_point_a_preview_computed(self):
        tabs = {"T": [self.HEADER, *self.ROWS]}
        grid, table = FakeSheetGrid(tabs), self.table()
        added = NewRow(("n",), {"id": "n", "status": "open", "name": ""})
        the_plan = plan([self.closes("b")], [added])
        point = insert_point(table, the_plan, {"status": "closed"})
        result = apply_plan(
            grid, "S", table, the_plan, insert_above={"status": "closed"}
        )
        assert result.appended_rows == [point]


class TestFailureOrder:
    def test_a_failed_push_inserts_no_row(self):
        grid, table = sheet(*ROWS)
        before = grid.values("T")
        grid.fail(PUSH, http_error(400, "bad request"))
        with pytest.raises(HttpError):
            apply_plan(
                grid,
                "S",
                table,
                plan([push("a", "name", "Al")], [new("c")]),
                insert_above={"id": "b"},
            )
        assert grid.methods == [READ, GRID, PUSH]
        assert grid.values("T") == before
        assert grid.tab("T").row_count == 1000

    def test_a_failed_grid_read_writes_nothing(self):
        grid, table = sheet(*ROWS)
        before = grid.values("T")
        grid.fail(GRID, http_error(404, "not found"))
        with pytest.raises(HttpError):
            apply_plan(grid, "S", table, plan([push("a", "name", "Al")], [new("c")]))
        assert grid.methods == [READ, GRID]
        assert grid.values("T") == before

    def test_a_failed_row_write_keeps_the_pushes_and_skips_the_read_back(self):
        grid, table = sheet(*ROWS)
        grid.fail(STRUCTURE, http_error(400, "bad request"))
        with pytest.raises(HttpError):
            apply_plan(grid, "S", table, plan([push("a", "name", "Al")], [new("c")]))
        assert grid.methods == [READ, GRID, PUSH, STRUCTURE]
        assert grid.values("T") == [HEADER, ["a", "n1", "Al", "", "1"], ROWS[1]]

    def test_a_failed_read_back_raises_after_the_writes(self):
        grid, table = sheet(*ROWS)
        # A collaborator overwrites the pushed cell between the write and the
        # read-back.
        grid.edit_externally(
            lambda g: g.write("T", [["a", "n1", "Someone"]], row=2),
            before=READ,
            occurrence=2,
        )
        with pytest.raises(ReadBackError) as raised:
            apply_plan(grid, "S", table, plan([push("a", "name", "Al")], [new("c")]))
        assert str(raised.value) == (
            "tab 'T': the read-back does not match the write: "
            "row ('a',), column 'name': wrote 'Al', read 'Someone'"
        )
        assert grid.methods == [READ, GRID, PUSH, STRUCTURE, READ]


class TestVerify:
    def test_passes_when_every_write_landed(self):
        grid, table = sheet(["a", "", "Al", "", "1"], ["c", "", "Cy"])
        verify(grid, "S", table, plan([push("a", "name", "Al")], [new("c", name="Cy")]))
        assert grid.methods == [READ]

    def test_finds_rows_by_key_whatever_their_number(self):
        grid, table = sheet(*ROWS)
        # "a" moves from row 2 to row 4; "c" sits where "a" was.
        grid.write("T", [HEADER, ["c", "", "", "", ""], ["x", "", "", "", ""], ROWS[0]])
        verify(grid, "S", table, plan([push("a", "name", "Ada")], [new("c")]))

    def test_lists_every_mismatch_at_once(self):
        grid, table = sheet(["a", "", "Al", "", "1"], ["c", "", "Cy", "", "9"])
        with pytest.raises(ReadBackError) as raised:
            verify(
                grid,
                "S",
                table,
                plan(
                    [
                        push("a", "name", "Ada"),
                        push("a", "amt", "1"),
                        push("gone", "name", "x"),
                        push("gone", "amt", "y"),
                    ],
                    [new("c", name="Cy"), new("d")],
                ),
            )
        assert str(raised.value) == (
            "tab 'T': the read-back does not match the write: "
            "rows [('gone',), ('d',)] not found; "
            "row ('a',), column 'name': wrote 'Ada', read 'Al'; "
            "new row ('c',), column 'amt': wrote '', read '9'"
        )

    def test_a_tab_that_no_longer_reads_cleanly(self):
        grid, table = sheet(*ROWS)
        grid.write("T", [["", "", "no key"]], row=4)
        with pytest.raises(ReadBackError, match=r"blank key \['id'\] in rows \[4\]"):
            verify(grid, "S", table, plan([push("a", "name", "Ada")]))
