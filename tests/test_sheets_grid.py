"""Tests for ``FakeSheetGrid``, the stateful Sheets fake in tests/helpers.py.

The apply and structure tests trust this fake to behave like the Sheets API
where the code depends on it: how reads truncate, what a write outside the grid
does, and how dimension and cell requests move cells. These pin that behavior,
so a fake that drifted would fail here rather than let a wrong write pass. The
live tests in test_sheets_integration.py pin the same points against the API.
"""

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error


def batch(grid, *requests):
    """Send ``requests`` in one structural batchUpdate."""
    return (
        grid.spreadsheets()
        .batchUpdate(spreadsheetId="S", body={"requests": list(requests)})
        .execute()
    )


def get(grid, range_, render=None):
    options = {"valueRenderOption": render} if render else {}
    return (
        grid.spreadsheets()
        .values()
        .get(spreadsheetId="S", range=range_, **options)
        .execute()
    )


def rows_dim(start, end, sheet_id=0):
    return {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start} | {
        "endIndex": end
    }


def cols_dim(start, end, sheet_id=0):
    return {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start} | {
        "endIndex": end
    }


def status(raised):
    return raised.value.resp.status


class TestReads:
    def test_rows_are_truncated_and_trailing_empty_rows_dropped(self):
        grid = FakeSheetGrid({"T": [["a", "", "c", ""], [], ["", "b"], [], [""]]})
        assert get(grid, "'T'")["values"] == [["a", "", "c"], [], ["", "b"]]

    def test_a_sub_range_starts_at_its_top_left(self):
        grid = FakeSheetGrid({"T": [["a", "b", "c"], ["d", "e", "f"]]})
        assert get(grid, "'T'!B2:C2")["values"] == [["e", "f"]]
        assert get(grid, "'T'!1:1")["values"] == [["a", "b", "c"]]
        assert get(grid, "'T'!C1")["values"] == [["c"]]
        assert get(grid, "'T'!B")["values"] == [["b"], ["e"]]

    def test_an_empty_range_omits_values(self):
        grid = FakeSheetGrid({"T": [["a"]]})
        assert "values" not in get(grid, "'T'!C3:D4")

    def test_unformatted_returns_what_was_stored(self):
        grid = FakeSheetGrid({"T": [[3, 2.5, True, 4.0, "007"]]})
        assert get(grid, "'T'", "UNFORMATTED_VALUE")["values"] == [
            [3, 2.5, True, 4.0, "007"]
        ]
        assert get(grid, "'T'")["values"] == [["3", "2.5", "TRUE", "4", "007"]]

    def test_batch_get_reads_each_range(self):
        grid = FakeSheetGrid({"T": [["a", "b"]], "U": []})
        result = (
            grid.spreadsheets()
            .values()
            .batchGet(spreadsheetId="S", ranges=["'T'!B1", "U"])
            .execute()
        )
        assert [block.get("values") for block in result["valueRanges"]] == [
            [["b"]],
            None,
        ]

    def test_quoted_titles_unescape(self):
        grid = FakeSheetGrid({"O'Brien! Q3": [["x"]]})
        assert get(grid, "'O''Brien! Q3'!A1")["values"] == [["x"]]

    @pytest.mark.parametrize("range_", ["'T'!A5", "'T'!D1", "'T'!A1:A9", "'Nope'"])
    def test_outside_the_grid_or_an_unknown_tab_is_a_400(self, range_):
        grid = FakeSheetGrid({"T": []}, rows=4, columns=3)
        with pytest.raises(HttpError) as raised:
            get(grid, range_)
        assert status(raised) == 400

    def test_a_malformed_range_is_a_400(self):
        with pytest.raises(HttpError):
            get(FakeSheetGrid({"T": []}), "'T'!1A")


class TestValueWrites:
    def test_batch_update_stores_values_and_blank_clears(self):
        grid = FakeSheetGrid({"T": [["a", "b"]]}, columns=27)
        body = {
            "valueInputOption": "RAW",
            "data": [
                {"range": "'T'!B1", "values": [[""]]},
                {"range": "'T'!AA2", "values": [["far"]]},
            ],
        }
        grid.spreadsheets().values().batchUpdate(spreadsheetId="S", body=body).execute()
        assert grid.values("T") == [["a"], [""] * 26 + ["far"]]

    def test_a_batch_with_one_cell_outside_the_grid_writes_nothing(self):
        grid = FakeSheetGrid({"T": [["a"]]}, rows=2, columns=2)
        body = {
            "valueInputOption": "RAW",
            "data": [
                {"range": "'T'!A1", "values": [["changed"]]},
                {"range": "'T'!A3", "values": [["x"]]},
            ],
        }
        request = grid.spreadsheets().values().batchUpdate(spreadsheetId="S", body=body)
        with pytest.raises(HttpError) as raised:
            request.execute()
        assert status(raised) == 400
        assert grid.values("T") == [["a"]]

    def test_update_refuses_values_past_its_range(self):
        grid = FakeSheetGrid({"T": []})
        request = (
            grid.spreadsheets()
            .values()
            .update(
                spreadsheetId="S",
                range="'T'!A1:A1",
                valueInputOption="RAW",
                body={"values": [["a", "b"]]},
            )
        )
        with pytest.raises(HttpError):
            request.execute()
        assert grid.values("T") == []

    def test_a_single_cell_range_past_the_grid_is_a_400(self):
        grid = FakeSheetGrid({"T": []}, rows=2)
        request = (
            grid.spreadsheets()
            .values()
            .update(
                spreadsheetId="S",
                range="'T'!A2",
                valueInputOption="RAW",
                body={"values": [["a"], ["b"]]},
            )
        )
        with pytest.raises(HttpError):
            request.execute()

    def test_update_writes_from_its_start(self):
        grid = FakeSheetGrid({"T": []})
        result = (
            grid.spreadsheets()
            .values()
            .update(
                spreadsheetId="S",
                range="'T'!B2",
                valueInputOption="RAW",
                body={"values": [["x", "y"]]},
            )
            .execute()
        )
        assert result["updatedCells"] == 2
        assert grid.values("T") == [[], ["", "x", "y"]]


class TestDimensions:
    def test_insert_rows_shifts_rows_down_and_grows_the_grid(self):
        grid = FakeSheetGrid({"T": [["h"], ["a"], ["b"]]}, rows=3)
        batch(grid, {"insertDimension": {"range": rows_dim(1, 3)}})
        assert grid.values("T") == [["h"], [], [], ["a"], ["b"]]
        assert grid.tab("T").row_count == 5

    def test_insert_columns_shifts_cells_right_and_copies_the_width(self):
        grid = FakeSheetGrid({"T": [["a", "b"]]}, columns=2)
        batch(
            grid,
            {
                "updateDimensionProperties": {
                    "range": cols_dim(0, 1),
                    "properties": {"pixelSize": 40},
                    "fields": "pixelSize",
                }
            },
            {"insertDimension": {"range": cols_dim(1, 2), "inheritFromBefore": True}},
        )
        assert grid.values("T") == [["a", "", "b"]]
        assert grid.tab("T").widths == [40, 40, 100]

    @pytest.mark.parametrize(
        "request_",
        [
            {"range": rows_dim(0, 1), "inheritFromBefore": True},
            {"range": rows_dim(3, 4)},  # at the grid's end
            {"range": rows_dim(2, 2)},
        ],
    )
    def test_bad_inserts_are_a_400(self, request_):
        grid = FakeSheetGrid({"T": []}, rows=3)
        with pytest.raises(HttpError):
            batch(grid, {"insertDimension": request_})
        assert grid.tab("T").row_count == 3

    def test_append_rows_and_columns(self):
        grid = FakeSheetGrid({"T": [["a"]]}, rows=1, columns=1)
        batch(
            grid,
            {"appendDimension": {"sheetId": 0, "dimension": "ROWS", "length": 2}},
            {"appendDimension": {"dimension": "COLUMNS", "length": 1}},
        )
        tab = grid.tab("T")
        assert (tab.row_count, tab.column_count, tab.widths) == (3, 2, [100, 100])

    def test_append_nothing_is_a_400(self):
        grid = FakeSheetGrid({"T": []})
        with pytest.raises(HttpError):
            batch(grid, {"appendDimension": {"dimension": "ROWS", "length": 0}})

    def test_delete_rows_and_columns(self):
        grid = FakeSheetGrid({"T": [["a", "b", "c"], ["d", "e", "f"]]}, columns=3)
        batch(
            grid,
            {"deleteDimension": {"range": cols_dim(1, 2)}},
            {"deleteDimension": {"range": rows_dim(0, 1)}},
        )
        assert grid.values("T") == [["d", "f"]]
        assert grid.tab("T").widths == [100, 100]

    @pytest.mark.parametrize("span", [cols_dim(0, 2), cols_dim(1, 3), rows_dim(0, 4)])
    def test_bad_deletes_are_a_400(self, span):
        grid = FakeSheetGrid({"T": [["a", "b"]]}, rows=4, columns=2)
        with pytest.raises(HttpError):
            batch(grid, {"deleteDimension": {"range": span}})
        assert grid.values("T") == [["a", "b"]]

    def test_widths_for_rows_are_not_modelled(self):
        grid = FakeSheetGrid({"T": []})
        request = {
            "range": rows_dim(0, 1),
            "properties": {"pixelSize": 5},
            "fields": "pixelSize",
        }
        with pytest.raises(HttpError):
            batch(grid, {"updateDimensionProperties": request})

    def test_widths_outside_the_grid_are_a_400(self):
        grid = FakeSheetGrid({"T": []}, columns=2)
        request = {
            "range": cols_dim(1, 3),
            "properties": {"pixelSize": 5},
            "fields": "pixelSize",
        }
        with pytest.raises(HttpError):
            batch(grid, {"updateDimensionProperties": request})


class TestUpdateCells:
    def cells(self, row, col, *rows, fields="userEnteredValue", sheet_id=0):
        return {
            "updateCells": {
                "start": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
                "rows": [{"values": list(values)} for values in rows],
                "fields": fields,
            }
        }

    def test_writes_from_its_start_and_an_empty_cell_clears(self):
        grid = FakeSheetGrid({"T": [["a", "b", "c"]]})
        s = {"userEnteredValue": {"stringValue": "x"}}
        n = {"userEnteredValue": {"numberValue": 3}}
        batch(grid, self.cells(0, 1, [s, {}], [n]))
        assert grid.values("T") == [["a", "x"], ["", 3]]

    def test_star_mask_writes_too(self):
        grid = FakeSheetGrid({"T": []})
        b = {"userEnteredValue": {"boolValue": True}}
        batch(grid, self.cells(0, 0, [b], fields="*"))
        assert grid.values("T") == [[True]]

    def test_an_empty_string_value_leaves_the_cell_empty(self):
        grid = FakeSheetGrid({"T": [["a"]]})
        batch(grid, self.cells(0, 0, [{"userEnteredValue": {"stringValue": ""}}]))
        assert grid.values("T") == []

    def test_omitted_start_indices_are_zero(self):
        grid = FakeSheetGrid({"T": []})
        request = {
            "updateCells": {
                "start": {},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": "x"}}]}],
                "fields": "userEnteredValue",
            }
        }
        batch(grid, request)
        assert grid.values("T") == [["x"]]

    @pytest.mark.parametrize(
        "request_",
        [
            {"start": {"rowIndex": 2}},  # past the last row
            {"start": {"columnIndex": 1}, "wide": True},  # past the last column
            {"start": {"rowIndex": 0}, "fields": "note"},
            {"start": {}, "value": {"formulaValue": "=1"}},
            {"range": {"sheetId": 0}},
        ],
    )
    def test_unmodelled_or_out_of_grid_writes_are_a_400(self, request_):
        grid = FakeSheetGrid({"T": [["keep"]]}, rows=2, columns=2)
        value = {"userEnteredValue": request_.pop("value", {"stringValue": "x"})}
        width = 2 if request_.pop("wide", False) else 1
        body = {
            "rows": [{"values": [value] * width}],
            "fields": request_.pop("fields", "userEnteredValue"),
            **request_,
        }
        with pytest.raises(HttpError):
            batch(grid, {"updateCells": body})
        assert grid.values("T") == [["keep"]]

    def test_a_failing_request_rolls_back_the_whole_batch(self):
        grid = FakeSheetGrid({"T": [["a"]]}, rows=2)
        with pytest.raises(HttpError):
            batch(
                grid,
                {"insertDimension": {"range": rows_dim(0, 1)}},
                self.cells(9, 0, [{"userEnteredValue": {"stringValue": "x"}}]),
            )
        assert (grid.values("T"), grid.tab("T").row_count) == ([["a"]], 2)


class TestSheets:
    def test_add_sheet_gets_the_default_grid_and_a_new_id(self):
        grid = FakeSheetGrid({"T": []}, rows=5, columns=2)
        reply = batch(grid, {"addSheet": {"properties": {"title": "New"}}})
        assert reply["replies"] == [
            {"addSheet": {"properties": {"sheetId": 1, "title": "New"}}}
        ]
        tab = grid.tab("New")
        assert (tab.row_count, tab.column_count) == (1000, 26)

    def test_a_duplicate_title_is_a_400(self):
        grid = FakeSheetGrid({"T": []})
        with pytest.raises(HttpError, match="already exists"):
            batch(grid, {"addSheet": {"properties": {"title": "T"}}})

    def test_delete_sheet(self):
        grid = FakeSheetGrid({"T": [], "U": []})
        batch(grid, {"deleteSheet": {"sheetId": 1}})
        assert [tab.title for tab in grid.tabs] == ["T"]

    def test_unknown_sheet_id_is_a_400(self):
        with pytest.raises(HttpError):
            batch(FakeSheetGrid({"T": []}), {"deleteSheet": {"sheetId": 9}})

    def test_meta_omits_zero_values_like_the_api(self):
        grid = FakeSheetGrid({"T": [], "U": []}, rows=4, columns=2)
        result = grid.spreadsheets().get(spreadsheetId="S").execute()
        assert result["sheets"] == [
            {
                "properties": {
                    "title": "T",
                    "gridProperties": {"rowCount": 4, "columnCount": 2},
                }
            },
            {
                "properties": {
                    "title": "U",
                    "gridProperties": {"rowCount": 4, "columnCount": 2},
                    "sheetId": 1,
                    "index": 1,
                }
            },
        ]

    def test_an_empty_or_unknown_request_is_a_400(self):
        grid = FakeSheetGrid({"T": []})
        with pytest.raises(HttpError):
            batch(grid)
        with pytest.raises(HttpError, match="unsupported"):
            batch(grid, {"sortRange": {}})


class TestHarness:
    def test_calls_are_recorded_in_order(self):
        grid = FakeSheetGrid({"T": []})
        get(grid, "'T'")
        grid.spreadsheets().get(spreadsheetId="S").execute()
        assert grid.methods == ["values.get", "spreadsheets.get"]
        assert grid.calls[0] == ("values.get", {"spreadsheetId": "S", "range": "'T'"})

    def test_edit_externally_runs_before_the_named_call(self):
        grid = FakeSheetGrid({"T": [["a"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["edited"]]), before="values.get", occurrence=2
        )
        assert get(grid, "'T'")["values"] == [["a"]]
        assert get(grid, "'T'")["values"] == [["edited"]]

    def test_fail_raises_once_and_changes_nothing(self):
        grid = FakeSheetGrid({"T": [["a"]]})
        grid.fail("spreadsheets.batchUpdate", http_error(400, "boom"))
        with pytest.raises(HttpError):
            batch(grid, {"deleteDimension": {"range": rows_dim(0, 1)}})
        assert grid.values("T") == [["a"]]
        batch(grid, {"deleteDimension": {"range": rows_dim(0, 1)}})
        assert grid.values("T") == []

    def test_resize_keeps_the_cells_that_fit(self):
        grid = FakeSheetGrid({"T": [["a", "b"], ["c", "d"]]})
        grid.resize("T", 1, 3)
        assert grid.values("T") == [["a", "b"]]
        assert (grid.tab("T").row_count, grid.tab("T").column_count) == (1, 3)

    def test_default_is_one_empty_tab(self):
        grid = FakeSheetGrid()
        assert [tab.title for tab in grid.tabs] == ["Sheet1"]
        with pytest.raises(KeyError):
            grid.tab("Nope")
