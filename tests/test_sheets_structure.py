"""Tests for gdrives.sheets.structure: columns by header name, tabs, and widths.

Each helper runs against ``FakeSheetGrid``, so the tests assert the header and
cells a request leaves behind, and that a refused request sends nothing.
"""

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error

from gdrives.sheets import (
    add_columns,
    delete_columns,
    ensure_tabs,
    place_columns,
    set_column_widths,
)

READ = "values.get"
GRID = "spreadsheets.get"
STRUCTURE = "spreadsheets.batchUpdate"


def requests_of(grid):
    """The one structural batch sent, as (kind, body) pairs."""
    (body,) = [kwargs["body"] for method, kwargs in grid.calls if method == STRUCTURE]
    return [next(iter(request.items())) for request in body["requests"]]


class TestAddColumns:
    def test_after_the_last_header_column(self):
        # The stray cell past the header moves right: it never lands under a
        # new header.
        grid = FakeSheetGrid({"T": [["id", "", "name"], ["a", "x", "Ada", "stray"]]})
        add_columns(grid, "S", "T", ["email", "phone"])
        assert grid.values("T") == [
            ["id", "", "name", "email", "phone"],
            ["a", "x", "Ada", "", "", "stray"],
        ]
        assert grid.methods == [READ, GRID, STRUCTURE]
        assert requests_of(grid)[0] == (
            "insertDimension",
            {
                "range": {
                    "sheetId": 0,
                    "dimension": "COLUMNS",
                    "startIndex": 3,
                    "endIndex": 5,
                },
                "inheritFromBefore": True,
            },
        )

    def test_the_header_is_read_as_canonical_stripped_names(self):
        grid = FakeSheetGrid({"T": [[" id ", 2026.0]]})
        with pytest.raises(ValueError, match=r"already has column\(s\) \['2026'\]"):
            add_columns(grid, "S", "T", ["2026"])
        (_, kwargs), *_ = grid.calls
        assert kwargs == {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        }

    def test_before_a_named_column(self):
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        add_columns(grid, "S", "T", ["x", "y"], before="name")
        assert grid.values("T") == [["id", "x", "y", "name"], ["a", "", "", "Ada"]]
        (_, insert), _ = requests_of(grid)
        assert insert["inheritFromBefore"] is False

    def test_after_a_named_column(self):
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        add_columns(grid, "S", "T", ["x", "y"], after="id")
        assert grid.values("T") == [["id", "x", "y", "name"], ["a", "", "", "Ada"]]
        (_, insert), _ = requests_of(grid)
        assert insert["inheritFromBefore"] is True

    def test_after_the_grid_s_last_column(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]}, columns=2)
        add_columns(grid, "S", "T", ["x"], after="name")
        assert requests_of(grid)[0] == (
            "appendDimension",
            {"sheetId": 0, "dimension": "COLUMNS", "length": 1},
        )
        assert grid.values("T") == [["id", "name", "x"]]

    def test_before_and_after_together_are_refused(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        with pytest.raises(ValueError, match="before or after, not both"):
            add_columns(grid, "S", "T", ["x"], before="name", after="id")
        assert grid.calls == []

    def test_before_the_first_column(self):
        grid = FakeSheetGrid({"T": [["id"], ["a"]]})
        add_columns(grid, "S", "T", ["n"], before="id")
        assert grid.values("T") == [["n", "id"], ["", "a"]]

    def test_grows_the_grid_when_the_header_fills_it(self):
        grid = FakeSheetGrid({"T": [["a", "b"]]}, columns=2)
        add_columns(grid, "S", "T", ["c", "d"])
        assert requests_of(grid)[0] == (
            "appendDimension",
            {"sheetId": 0, "dimension": "COLUMNS", "length": 2},
        )
        assert grid.values("T") == [["a", "b", "c", "d"]]
        assert grid.tab("T").column_count == 4

    def test_on_an_empty_tab(self):
        grid = FakeSheetGrid({"T": []})
        add_columns(grid, "S", "T", ["id", "name"])
        assert grid.values("T") == [["id", "name"]]
        assert requests_of(grid)[0][1]["inheritFromBefore"] is False

    def test_names_are_written_as_literal_strings(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        add_columns(grid, "S", "T", ["=SUM(A:A)", "01"])
        assert grid.values("T") == [["id", "=SUM(A:A)", "01"]]

    def test_past_z(self):
        header = [f"c{i}" for i in range(27)]
        grid = FakeSheetGrid({"T": [header]}, columns=27)
        add_columns(grid, "S", "T", ["new"])
        assert grid.values("T")[0][27] == "new"  # column AB

    def test_nothing_to_add_sends_nothing(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        add_columns(grid, "S", "T", [])
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("names", "before", "message"),
        [
            (["x", ""], None, "blank column name"),
            (["x", "x"], None, r"column\(s\) \['x'\] named twice"),
            (["name", "x"], None, r"already has column\(s\) \['name'\]"),
            (["x"], "nope", r"has no column\(s\) \['nope'\]"),
            (["x"], "", r"has no column\(s\) \[''\]"),
        ],
    )
    def test_refusals_write_nothing(self, names, before, message):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        with pytest.raises(ValueError, match=message):
            add_columns(grid, "S", "T", names, before=before)
        assert STRUCTURE not in grid.methods
        assert grid.values("T") == [["id", "name"]]

    @pytest.mark.parametrize(
        ("after", "message"),
        [
            ("nope", r"has no column\(s\) \['nope'\]"),
            ("", r"has no column\(s\) \[''\]"),
        ],
    )
    def test_an_after_the_header_lacks_is_refused(self, after, message):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        with pytest.raises(ValueError, match=message):
            add_columns(grid, "S", "T", ["x"], after=after)
        assert STRUCTURE not in grid.methods

    def test_a_before_the_header_repeats_is_refused(self):
        grid = FakeSheetGrid({"T": [["id", "dup", "dup"]]})
        with pytest.raises(ValueError, match=r"header repeats \['dup'\]"):
            add_columns(grid, "S", "T", ["x"], before="dup")

    def test_an_unknown_tab_is_refused(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        with pytest.raises(HttpError):
            add_columns(grid, "S", "Nope", ["x"])


class TestPlaceColumns:
    @pytest.mark.parametrize(
        ("header", "columns", "result"),
        [
            (
                ["id", "name", "notes"],
                ["id", "email", "name", "notes"],
                ["id", "email", "name", "notes"],
            ),
            (
                ["id", "name"],
                ["status", "id", "name", "city"],
                ["status", "id", "name", "city"],
            ),
            (
                ["id", "legacy", "name"],
                ["id", "name", "city"],
                ["id", "legacy", "name", "city"],
            ),
            (["name", "id"], ["id", "email", "name"], ["name", "id", "email"]),
            # Several in a row stay in the order wanted, after the same column.
            (
                ["id", "name"],
                ["a", "b", "id", "c", "d", "name", "e"],
                ["a", "b", "id", "c", "d", "name", "e"],
            ),
        ],
    )
    def test_each_column_lands_at_its_place_in_the_wanted_order(
        self, header, columns, result
    ):
        row = [f"v-{name}" for name in header]
        grid = FakeSheetGrid({"T": [header, row]})
        added = place_columns(grid, "S", "T", columns)
        assert grid.values("T")[0] == result
        # Every value is still under its own header.
        cells = grid.values("T")[1]
        cells += [""] * (len(result) - len(cells))
        assert dict(zip(result, cells, strict=True)) == {
            name: f"v-{name}" if name in header else "" for name in result
        }
        assert added == [name for name in columns if name not in header]
        assert grid.methods == [READ, GRID, STRUCTURE]

    def test_inserts_run_right_to_left_each_with_its_header_cells(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        place_columns(grid, "S", "T", ["status", "id", "x", "y", "name", "city"])
        sent = [
            (
                kind,
                body.get("range", {}).get("startIndex"),
                body.get("range", {}).get("endIndex"),
                body.get("inheritFromBefore"),
                body.get("start", {}).get("columnIndex"),
            )
            for kind, body in requests_of(grid)
        ]
        assert sent == [
            ("insertDimension", 2, 3, True, None),
            ("updateCells", None, None, None, 2),
            ("insertDimension", 1, 3, True, None),
            ("updateCells", None, None, None, 1),
            # At the front there is no column to the left to take after.
            ("insertDimension", 0, 1, False, None),
            ("updateCells", None, None, None, 0),
        ]
        assert grid.values("T") == [["status", "id", "x", "y", "name", "city"]]

    def test_widths_are_taken_from_the_column_beside(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        grid.tab("T").widths[:2] = [50, 200]
        place_columns(grid, "S", "T", ["front", "id", "mid", "name", "end"])
        assert grid.tab("T").widths[:5] == [50, 50, 50, 200, 200]

    def test_a_blank_header_gap_is_moved_over_not_filled(self):
        grid = FakeSheetGrid({"T": [["id", "", "name"], ["a", "gap", "Ada", "stray"]]})
        place_columns(grid, "S", "T", ["id", "email", "name", "city"])
        assert grid.values("T") == [
            ["id", "email", "", "name", "city"],
            ["a", "", "gap", "Ada", "", "stray"],
        ]

    def test_a_grid_too_narrow_for_the_new_columns_is_grown(self):
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]}, columns=2)
        place_columns(grid, "S", "T", ["id", "email", "name", "city", "zip"])
        assert [kind for kind, _ in requests_of(grid)] == [
            "appendDimension",
            "updateCells",
            "insertDimension",
            "updateCells",
        ]
        assert requests_of(grid)[0][1] == {
            "sheetId": 0,
            "dimension": "COLUMNS",
            "length": 2,
        }
        assert grid.values("T") == [
            ["id", "email", "name", "city", "zip"],
            ["a", "", "Ada"],
        ]
        assert grid.tab("T").column_count == 5

    def test_on_an_empty_tab(self):
        grid = FakeSheetGrid({"T": []})
        assert place_columns(grid, "S", "T", ["id", "name"]) == ["id", "name"]
        assert grid.values("T") == [["id", "name"]]

    def test_names_are_written_as_literal_strings(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        place_columns(grid, "S", "T", ["id", "=SUM(A:A)", "01"])
        assert grid.values("T") == [["id", "=SUM(A:A)", "01"]]

    def test_the_header_is_read_as_canonical_stripped_names(self):
        grid = FakeSheetGrid({"T": [[" id ", 2026.0]]})
        assert place_columns(grid, "S", "T", ["id", "x", "2026"]) == ["x"]
        assert grid.values("T") == [[" id ", "x", 2026.0]]

    @pytest.mark.parametrize("columns", [[], ["id"], ["name", "id"]])
    def test_nothing_to_add_sends_nothing(self, columns):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        assert place_columns(grid, "S", "T", columns) == []
        assert STRUCTURE not in grid.methods and GRID not in grid.methods

    @pytest.mark.parametrize(
        ("columns", "message"),
        [
            (["id", ""], "blank column name"),
            (["x", "id", "x"], r"column\(s\) \['x'\] named twice"),
            (["dup", "x"], r"header repeats \['dup'\]"),
        ],
    )
    def test_refusals_write_nothing(self, columns, message):
        header = ["id", "dup", "dup"]
        grid = FakeSheetGrid({"T": [header]})
        with pytest.raises(ValueError, match=message):
            place_columns(grid, "S", "T", columns)
        assert STRUCTURE not in grid.methods
        assert grid.values("T") == [header]

    def test_an_unknown_tab_is_refused(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        with pytest.raises(HttpError):
            place_columns(grid, "S", "Nope", ["id", "x"])

    def test_a_failed_request_places_no_column(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        grid.fail(STRUCTURE, http_error(400, "boom"))
        with pytest.raises(HttpError):
            place_columns(grid, "S", "T", ["a", "id", "b", "name"])
        assert grid.values("T") == [["id", "name"]]


class TestDeleteColumns:
    def test_deletes_right_to_left_by_name(self):
        grid = FakeSheetGrid(
            {"T": [["id", "a", "b", "c"], ["1", "x", "y", "z"]]}, columns=4
        )
        delete_columns(grid, "S", "T", ["a", "c"])
        assert grid.values("T") == [["id", "b"], ["1", "y"]]
        starts = [body["range"]["startIndex"] for _, body in requests_of(grid)]
        assert starts == [3, 1]
        assert grid.methods == [READ, GRID, STRUCTURE]

    def test_the_tab_is_found_by_title(self):
        grid = FakeSheetGrid({"Other": [["a"]], "T": [["id", "a"]]})
        delete_columns(grid, "S", "T", ["a"])
        assert grid.values("T") == [["id"]]
        assert grid.values("Other") == [["a"]]

    def test_nothing_to_delete_sends_nothing(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        delete_columns(grid, "S", "T", [])
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("names", "message"),
        [
            (["a", "a"], r"column\(s\) \['a'\] named twice"),
            ([""], "blank column name"),
            (["a", "zz"], r"has no column\(s\) \['zz'\]"),
            (["dup"], r"header repeats \['dup'\]"),
        ],
    )
    def test_refusals_delete_nothing(self, names, message):
        grid = FakeSheetGrid({"T": [["id", "a", "dup", "dup"]]})
        with pytest.raises(ValueError, match=message):
            delete_columns(grid, "S", "T", names)
        assert STRUCTURE not in grid.methods
        assert grid.values("T") == [["id", "a", "dup", "dup"]]

    def test_a_failed_delete_is_not_retried(self, monkeypatch):
        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)
        grid = FakeSheetGrid({"T": [["id", "a"]]})
        grid.fail(STRUCTURE, http_error(503, "unavailable"))
        with pytest.raises(HttpError):
            delete_columns(grid, "S", "T", ["a"])
        assert grid.methods == [READ, GRID, STRUCTURE]


class TestEnsureTabs:
    def test_creates_only_the_missing_tabs_in_order(self):
        grid = FakeSheetGrid({"Keep": [["x"]], "B": []})
        assert ensure_tabs(grid, "S", ["C", "B", "A", "C"]) == ["C", "A"]
        assert [tab.title for tab in grid.tabs] == ["Keep", "B", "C", "A"]
        assert grid.methods == [GRID, STRUCTURE]
        assert grid.values("Keep") == [["x"]]

    def test_nothing_missing_sends_no_write(self):
        grid = FakeSheetGrid({"A": [], "B": []})
        assert ensure_tabs(grid, "S", ["B"]) == []
        assert grid.methods == [GRID]

    def test_no_tabs_sends_nothing(self):
        grid = FakeSheetGrid()
        assert ensure_tabs(grid, "S", []) == []
        assert grid.calls == []

    def test_a_blank_title_is_refused(self):
        grid = FakeSheetGrid()
        with pytest.raises(ValueError, match="blank tab title"):
            ensure_tabs(grid, "S", ["A", ""])
        assert grid.calls == []


class TestSetColumnWidths:
    def test_sets_widths_by_header_name(self):
        grid = FakeSheetGrid({"T": [["id", "", "name"]]}, columns=4)
        set_column_widths(grid, "S", "T", {"name": 240, "id": 1})
        assert grid.tab("T").widths == [1, 100, 240, 100]
        assert grid.methods == [READ, GRID, STRUCTURE]
        (_, first), _ = requests_of(grid)
        assert first == {
            "range": {
                "sheetId": 0,
                "dimension": "COLUMNS",
                "startIndex": 2,
                "endIndex": 3,
            },
            "properties": {"pixelSize": 240},
            "fields": "pixelSize",
        }

    def test_nothing_to_set_sends_nothing(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        set_column_widths(grid, "S", "T", {})
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("widths", "message"),
        [
            ({"id": 0}, r"at least 1 pixel: \{'id': 0\}"),
            ({"id": 10, "nope": 10}, r"has no column\(s\) \['nope'\]"),
            ({"": 10}, "blank column name"),
        ],
    )
    def test_refusals_change_nothing(self, widths, message):
        grid = FakeSheetGrid({"T": [["id", "", "x"]]})
        with pytest.raises(ValueError, match=message):
            set_column_widths(grid, "S", "T", widths)
        assert STRUCTURE not in grid.methods
        assert grid.tab("T").widths == [100] * 26
