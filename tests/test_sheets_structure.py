"""Tests for gdrives.sheets.structure: columns by header name, tabs, and widths.

Each helper runs against ``FakeSheetGrid``, so the tests assert the header and
cells a request leaves behind, and that a refused request sends nothing.
"""

from datetime import date

import pytest
from googleapiclient.errors import HttpError
from helpers import LINK_BLUE, FakeSheetGrid, http_error

from gdrives.sheets import (
    URL_LINK_REASONS,
    LinkedCell,
    ReadBackError,
    UrlLinkProblem,
    add_columns,
    clear_link_format,
    delete_columns,
    ensure_tabs,
    get_column_widths,
    linked_cells,
    place_columns,
    set_column_widths,
    set_url_links,
    strip_links,
    url_link_problems,
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


LINK = "userEnteredFormat.textFormat.link"
BOLD = "userEnteredFormat.textFormat.bold"


def run_link(uri, start=0):
    return {"startIndex": start, "format": {"link": {"uri": uri}}}


def linked_grid():
    """A tab with a link of each kind, and cells that only look like one.

    Column ``site`` holds a URL, a bare domain, and a URL inside a sentence;
    ``note`` holds a link on part of its text in row 2, a bold run in row 3,
    and a link on part of a URL's own text in row 4.
    """
    grid = FakeSheetGrid(
        {
            "T": [
                ["id", "site", "", "note", "mail"],
                ["a", "https://example.com/a", "gap.io", "see the docs", "a@x.io"],
                ["b", "example.com", "", "bold words", ""],
                ["c", "see https://x.io", "", "https://both.io", "plain"],
            ]
        }
    )
    formats = grid.tab("T").formats
    formats[(1, 3)] = {"runs": [run_link("https://docs.example", 4)]}
    formats[(2, 3)] = {"runs": [{"startIndex": 0, "format": {"bold": True}}]}
    formats[(3, 3)]["runs"] = [run_link("https://part.io", 8)]
    formats[(3, 1)] = {"bold": True}
    return grid


class TestLinkedCells:
    def test_every_cell_holding_a_link_with_its_targets(self):
        grid = linked_grid()
        assert linked_cells(grid, "S", "T") == [
            LinkedCell(2, "site", ("https://example.com/a",), in_runs=False),
            LinkedCell(2, "note", ("https://docs.example",), in_runs=True),
            # A bare domain's target is not its text.
            LinkedCell(3, "site", ("http://example.com",), in_runs=False),
            LinkedCell(4, "note", ("https://both.io", "https://part.io"), in_runs=True),
        ]
        assert grid.methods == [READ, GRID]
        (_, kwargs) = grid.calls[-1]
        assert kwargs == {
            "spreadsheetId": "S",
            "ranges": ["'T'!A:E"],
            "includeGridData": True,
            "fields": (
                "sheets(data(rowData(values(hyperlink,textFormatRuns(format(link))))))"
            ),
        }

    def test_the_columns_named_bound_the_read(self):
        grid = linked_grid()
        found = linked_cells(grid, "S", "T", columns=["note", "site"])
        assert [(cell.row, cell.column) for cell in found] == [
            (2, "site"),
            (2, "note"),
            (3, "site"),
            (4, "note"),
        ]
        assert grid.calls[-1][1]["ranges"] == ["'T'!B:D"]
        assert linked_cells(grid, "S", "T", columns=["mail"]) == []
        assert grid.calls[-1][1]["ranges"] == ["'T'!E:E"]

    def test_a_header_given_saves_its_read(self):
        grid = linked_grid()
        header = ["id", "site", "", "note", "mail"]
        found = linked_cells(grid, "S", "T", columns=["site"], header=header)
        assert [cell.row for cell in found] == [2, 3]
        assert grid.methods == [GRID]

    def test_a_link_in_the_header_row_is_a_link(self):
        grid = FakeSheetGrid({"T": [["example.com", "b"], ["x", "y"]]})
        assert linked_cells(grid, "S", "T") == [
            LinkedCell(1, "example.com", ("http://example.com",), in_runs=False)
        ]

    def test_no_columns_asks_nothing(self):
        grid = FakeSheetGrid({"T": []})
        assert linked_cells(grid, "S", "T") == []
        assert linked_cells(grid, "S", "T", columns=[], header=["id"]) == []
        assert grid.methods == [READ]

    @pytest.mark.parametrize(
        ("columns", "message"),
        [
            (["nope"], r"has no column\(s\) \['nope'\]"),
            (["site", "site"], r"column\(s\) \['site'\] named twice"),
            ([""], "blank column name"),
        ],
    )
    def test_refusals_read_no_grid(self, columns, message):
        grid = linked_grid()
        with pytest.raises(ValueError, match=message):
            linked_cells(grid, "S", "T", columns=columns)
        assert GRID not in grid.methods


class TestClearLinkFormat:
    def test_every_named_column_in_one_request_per_run_of_columns(self):
        grid = linked_grid()
        clear_link_format(grid, "S", "T", runs=False)
        assert grid.methods == [READ, GRID, STRUCTURE]
        assert requests_of(grid) == [
            (
                "repeatCell",
                {
                    "range": {
                        "sheetId": 0,
                        "startColumnIndex": start,
                        "endColumnIndex": end,
                    },
                    "cell": {},
                    "fields": LINK,
                },
            )
            for start, end in [(0, 2), (3, 5)]
        ]
        # The unnamed column between them keeps its link, and the run links stay.
        assert grid.links("T") == {(2, 3): "http://gap.io"}
        assert grid.format("T", 4, 2) == {"bold": True}
        assert [cell.targets for cell in linked_cells(grid, "S", "T")] == [
            ("https://docs.example",),
            ("https://part.io",),
        ]

    def test_runs_that_hold_a_link_are_cleared_whole_and_others_kept(self):
        grid = linked_grid()
        clear_link_format(grid, "S", "T")
        assert grid.methods == [READ, GRID, GRID, STRUCTURE]
        assert linked_cells(grid, "S", "T") == []
        assert grid.format("T", 2, 4) == {}
        assert grid.format("T", 3, 4) == {
            "runs": [{"startIndex": 0, "format": {"bold": True}}]
        }
        cleared = [
            (body["range"], body["fields"])
            for kind, body in requests_of(grid)
            if body["fields"] == "textFormatRuns"
        ]
        assert cleared == [
            (
                {
                    "sheetId": 0,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 3,
                    "endColumnIndex": 4,
                },
                "textFormatRuns",
            )
            for row in (2, 4)
        ]

    def test_given_columns_and_rows(self):
        grid = linked_grid()
        clear_link_format(grid, "S", "T", columns=["site", "note"], rows=[4, 2, 3, 7])
        spans = [body["range"] for _, body in requests_of(grid)]
        assert spans[:4] == [
            {
                "sheetId": 0,
                "startRowIndex": first,
                "endRowIndex": last,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            }
            for column in (1, 3)
            for first, last in [(1, 4), (6, 7)]
        ]
        assert linked_cells(grid, "S", "T") == []

    def test_rows_bound_the_runs_that_are_cleared(self):
        grid = linked_grid()
        clear_link_format(grid, "S", "T", columns=["note"], rows=[2])
        assert [(cell.row, cell.targets) for cell in linked_cells(grid, "S", "T")][
            -1
        ] == (4, ("https://both.io", "https://part.io"))
        assert grid.format("T", 2, 4) == {}

    def test_a_header_and_a_sheet_id_given_save_their_reads(self):
        grid = linked_grid()
        header = ["id", "site", "", "note", "mail"]
        clear_link_format(grid, "S", "T", header=header, sheet_id=0, runs=False)
        assert grid.methods == [STRUCTURE]
        clear_link_format(grid, "S", "T", header=header, sheet_id=0)
        assert grid.methods == [STRUCTURE, GRID, STRUCTURE]

    @pytest.mark.parametrize("options", [{"columns": []}, {"rows": []}])
    def test_nothing_to_clear_sends_nothing(self, options):
        grid = linked_grid()
        clear_link_format(grid, "S", "T", header=["id", "site"], **options)
        assert grid.calls == []

    def test_a_tab_with_no_header_sends_no_write(self):
        grid = FakeSheetGrid({"T": []})
        clear_link_format(grid, "S", "T")
        assert grid.methods == [READ]

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"columns": ["nope"]}, r"has no column\(s\) \['nope'\]"),
            ({"rows": [0]}, r"rows are spreadsheet rows, from 1: \[0\]"),
        ],
    )
    def test_refusals_write_nothing(self, options, message):
        grid = linked_grid()
        with pytest.raises(ValueError, match=message):
            clear_link_format(grid, "S", "T", **options)
        assert STRUCTURE not in grid.methods


class TestGetColumnWidths:
    def test_each_named_column_in_header_order(self):
        grid = FakeSheetGrid({"My Tab": [["id", "", "name", 2026.0], ["a", "x"]]})
        grid.tab("My Tab").widths[:5] = [50, 60, 150, 80, 999]
        assert get_column_widths(grid, "S", "My Tab") == {
            "id": 50,
            "name": 150,
            "2026": 80,
        }
        assert list(get_column_widths(grid, "S", "My Tab")) == ["id", "name", "2026"]
        assert grid.calls[1] == (
            "spreadsheets.get",
            {
                "spreadsheetId": "S",
                "ranges": ["'My Tab'!1:1"],
                "includeGridData": True,
                "fields": "sheets(data(columnMetadata(pixelSize)))",
            },
        )
        assert grid.methods == ["values.get", GRID] * 2

    def test_a_date_in_the_header_is_named_as_every_helper_names_it(self):
        grid = FakeSheetGrid({"T": [["id", date(2026, 9, 27)], ["a", "x"]]})
        widths = get_column_widths(grid, "S", "T")
        assert list(widths) == ["id", "9/27/2026"]
        # The names are the ones set_column_widths finds in the same header.
        set_column_widths(grid, "S", "T", dict.fromkeys(widths, 70))
        assert get_column_widths(grid, "S", "T") == {"id": 70, "9/27/2026": 70}

    def test_what_it_returns_is_what_set_column_widths_takes(self):
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        set_column_widths(grid, "S", "T", {"name": 240, "id": 70})
        assert get_column_widths(grid, "S", "T") == {"id": 70, "name": 240}

    @pytest.mark.parametrize("rows", [[], [[], ["a"]]])
    def test_a_tab_with_no_header_has_no_widths(self, rows):
        grid = FakeSheetGrid({"T": rows})
        assert get_column_widths(grid, "S", "T") == {}
        assert grid.methods == ["values.get"]

    def test_a_header_that_repeats_a_name_is_refused(self):
        grid = FakeSheetGrid({"T": [["id", "dup", " dup "]]})
        with pytest.raises(ValueError, match=r"header repeats \['dup', 'dup'\]"):
            get_column_widths(grid, "S", "T")

    def test_an_unknown_tab_is_refused(self):
        with pytest.raises(HttpError):
            get_column_widths(FakeSheetGrid({"T": [["id"]]}), "S", "Nope")


class TestStripLinks:
    HEADER = ["id", "site", "", "note", "mail"]

    def strip(self, grid, columns, **options):
        return strip_links(
            grid, "S", "T", columns, header=self.HEADER, sheet_id=0, **options
        )

    def test_the_links_found_are_cleared_and_none_remains(self):
        grid = linked_grid()
        assert self.strip(grid, ["site", "note"]) == []
        assert grid.methods == [GRID, STRUCTURE, GRID]
        assert grid.links("T") == {(2, 3): "http://gap.io"}
        assert grid.format("T", 4, 2) == {"bold": True}
        assert grid.format("T", 3, 4) == {
            "runs": [{"startIndex": 0, "format": {"bold": True}}]
        }

    def test_columns_with_no_link_cost_one_read_and_no_write(self):
        grid = linked_grid()
        assert self.strip(grid, ["id", "mail"]) == []
        assert grid.methods == [GRID]

    def test_rows_bound_what_is_cleared_and_what_is_reported(self):
        grid = linked_grid()
        assert self.strip(grid, ["site", "note"], rows=[3, 4]) == []
        assert [(cell.row, cell.column) for cell in linked_cells(grid, "S", "T")] == [
            (2, "site"),
            (2, "note"),
        ]
        assert self.strip(grid, ["site", "note"], rows=[7]) == []

    def test_a_link_that_comes_back_is_returned(self):
        grid = linked_grid()
        grid.edit_externally(
            lambda g: g.write("T", [["a", "example.org"]], row=2),
            before=GRID,
            occurrence=2,
        )
        assert self.strip(grid, ["site"]) == [
            LinkedCell(2, "site", ("http://example.org",), in_runs=False)
        ]

    def test_no_columns_asks_nothing(self):
        grid = linked_grid()
        assert self.strip(grid, []) == []
        assert grid.calls == []


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


GREEN = "#33aa55"
GREEN_RGB = {"red": 0x33 / 255, "green": 0xAA / 255, "blue": 0x55 / 255}
URL_FIELDS = {
    "userEnteredFormat.textFormat.link",
    "userEnteredFormat.textFormat.underline",
    "userEnteredFormat.textFormat.foregroundColorStyle",
}


def url_grid():
    """A tab of URL cells linked as wanted, and cells that are no URL cell.

    ``site`` and ``home`` hold URLs, each linked to its text, in ``GREEN``,
    and not underlined. ``other`` holds a bare domain, a URL in a sentence,
    an email address, and a number, which the API links or not as it does.
    """
    grid = FakeSheetGrid(
        {
            "T": [
                ["id", "site", "other", "home"],
                ["a", "https://a.example", "example.com", "https://ha.example"],
                ["b", "https://b.example", "see https://x.io", "https://hb.example"],
                ["c", "https://c.example", "a@x.io", ""],
                ["d", "", 42, "https://hd.example"],
            ]
        }
    )
    for (r, c), held in grid.tab("T").formats.items():
        if c in (1, 3) and "link" in held:
            held |= {"underline": False, "color": dict(GREEN_RGB)}
    return grid


def fine(grid):
    return url_link_problems(grid, "S", "T", color=GREEN)


class TestUrlLinkProblems:
    def test_cells_linked_as_wanted_have_no_problem(self):
        grid = url_grid()
        assert fine(grid) == []
        assert grid.methods == [READ, GRID]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda held: held.pop("link"), "no_link"),
            (lambda held: held.update(link="https://elsewhere.io"), "target"),
            (lambda held: held.update(color={"red": 1.0}), "color"),
            (lambda held: held.update(underline=True), "underline"),
            (
                lambda held: held.update(runs=[run_link("https://p.io", 8)]),
                "runs",
            ),
        ],
    )
    def test_each_reason_alone(self, change, reason):
        grid = url_grid()
        change(grid.format("T", 3, 2))
        assert fine(grid) == [UrlLinkProblem(3, "site", "https://b.example", (reason,))]
        assert reason in URL_LINK_REASONS

    def test_a_url_as_the_api_writes_it_is_blue_and_underlined(self):
        grid = url_grid()
        grid.write("T", [["e", "https://e.example"]], row=6)
        grid.tab("T").formats[(5, 1)]["runs"] = [run_link("https://p.io", 8)]
        (bare,) = fine(grid)
        assert bare == UrlLinkProblem(
            6, "site", "https://e.example", ("color", "underline", "runs")
        )
        # The link's own blue is a colour like any other.
        assert fine_in(grid, "#1155cc", rows=[6]) == [
            UrlLinkProblem(6, "site", "https://e.example", ("underline", "runs"))
        ]

    def test_a_cell_with_every_reason(self):
        grid = url_grid()
        held = grid.format("T", 2, 4)
        held.pop("link")
        held.update(underline=True, runs=[run_link("https://p.io", 3)], color={})
        (problem,) = fine(grid)
        assert problem.reasons == ("no_link", "color", "underline", "runs")
        held["link"] = "https://elsewhere.io"
        (problem,) = fine(grid)
        assert problem.reasons == ("target", "color", "underline", "runs")

    def test_a_channel_the_api_omits_is_zero_and_fractions_round(self):
        grid = url_grid()
        grid.format("T", 2, 2)["color"] = {"green": 1.0}
        grid.format("T", 3, 2)["color"] = {"green": 0.99999994}
        assert fine_in(grid, "#00ff00", columns=["site"], rows=[2, 3]) == []

    def test_the_text_is_stripped(self):
        grid = url_grid()
        grid.write("T", [["e", "  https://e.example  "]], row=6)
        (problem,) = fine(grid)
        assert (problem.text, problem.reasons[0]) == ("https://e.example", "no_link")

    def test_cells_that_are_no_url_cell_are_never_returned(self):
        grid = url_grid()
        # The bare domain is linked, blue, and underlined, and is left alone.
        assert grid.links("T")[(2, 3)] == "http://example.com"
        assert fine_in(grid, GREEN, columns=["other"]) == []
        assert grid.methods == [READ]

    def test_the_grid_read_is_bounded_to_the_url_cells(self):
        grid = url_grid()
        fine(grid)
        assert grid.calls[-1][1]["ranges"] == ["'T'!B2:D5"]
        fine_in(grid, GREEN, columns=["home"], rows=[3, 4])
        assert grid.calls[-1][1]["ranges"] == ["'T'!D3:D3"]
        assert grid.calls[-1][1]["fields"] == (
            "sheets(data(rowData(values(hyperlink,textFormatRuns,"
            "effectiveFormat(textFormat(underline,foregroundColorStyle))))))"
        )

    def test_columns_and_rows_limit_what_is_returned(self):
        grid = url_grid()
        for row, column in [(2, 2), (3, 2), (2, 4), (5, 4)]:
            grid.format("T", row, column)["underline"] = True
        found = fine_in(grid, GREEN, columns=["home", "site"], rows=[3, 5])
        assert [(p.row, p.column) for p in found] == [(3, "site"), (5, "home")]
        found = fine_in(grid, GREEN, columns=["site"])
        assert [(p.row, p.column) for p in found] == [(2, "site"), (3, "site")]

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"color": "33aa55"}, "a colour is written '#rrggbb', not '33aa55'"),
            ({"color": "#33aa5g"}, "not '#33aa5g'"),
            ({"color": GREEN, "rows": [0, 2]}, r"rows are spreadsheet rows, from 1"),
        ],
    )
    def test_refusals_ask_nothing(self, options, message):
        grid = url_grid()
        with pytest.raises(ValueError, match=message):
            url_link_problems(grid, "S", "T", **options)
        with pytest.raises(ValueError, match=message):
            set_url_links(grid, "S", "T", **options)
        assert grid.calls == []

    def test_an_unknown_column_reads_no_grid(self):
        grid = url_grid()
        with pytest.raises(ValueError, match=r"has no column\(s\) \['nope'\]"):
            fine_in(grid, GREEN, columns=["nope"])
        assert grid.methods == [READ]


def fine_in(grid, color, **options):
    return url_link_problems(grid, "S", "T", color=color, **options)


class TestSetUrlLinks:
    def broken(self):
        """``url_grid`` with a problem of each kind, bold kept on one cell."""
        grid = url_grid()
        grid.format("T", 2, 2).update(bold=True, underline=True)
        grid.format("T", 3, 2)["link"] = "https://elsewhere.io"
        grid.format("T", 4, 2).update(runs=[run_link("https://p.io", 8)])
        grid.format("T", 3, 4).pop("link")
        return grid

    def test_the_cells_are_linked_and_every_other_format_kept(self):
        grid = self.broken()
        fixed = set_url_links(grid, "S", "T", color=GREEN)
        assert [(p.row, p.column, p.reasons) for p in fixed] == [
            (2, "site", ("underline",)),
            (3, "site", ("target",)),
            (3, "home", ("no_link",)),
            (4, "site", ("runs",)),
        ]
        assert grid.methods == [READ, GRID, GRID, STRUCTURE, READ, GRID]
        assert fine(grid) == []
        assert grid.format("T", 2, 2)["bold"] is True
        assert grid.format("T", 3, 2)["link"] == "https://b.example"
        # The text is the authority: the link follows it, never the other way.
        assert grid.values("T")[2][1] == "https://b.example"

    def test_the_requests_name_no_property_but_the_four(self):
        grid = self.broken()
        set_url_links(grid, "S", "T", color=GREEN)
        sent = requests_of(grid)
        # The runs are cleared first, each in a request of its own.
        assert sent[0] == (
            "repeatCell",
            {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": 3,
                    "endRowIndex": 4,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {},
                "fields": "textFormatRuns",
            },
        )
        assert {kind for kind, _ in sent} == {"repeatCell"}
        for _, body in sent[1:]:
            assert set(body["fields"].split(",")) == URL_FIELDS
            text = body["cell"]["userEnteredFormat"]["textFormat"]
            assert set(text) == {"link", "underline", "foregroundColorStyle"}
            assert text["underline"] is False
            assert text["foregroundColorStyle"] == {"rgbColor": GREEN_RGB}
        assert [
            body["cell"]["userEnteredFormat"]["textFormat"]["link"]
            for _, body in sent[1:]
        ] == [
            {"uri": uri}
            for uri in [
                "https://a.example",
                "https://b.example",
                "https://hb.example",
                "https://c.example",
            ]
        ]

    def test_cells_that_are_no_url_cell_are_never_written(self):
        grid = url_grid()
        before = {at: dict(held) for at, held in grid.tab("T").formats.items()}
        grid.write("T", [["e", "example.org", "https://e.example"]], row=6)
        set_url_links(grid, "S", "T", color=GREEN)
        after = grid.tab("T").formats
        assert {at: after[at] for at in before} == before
        assert after[(5, 1)] == {"link": "http://example.org"}
        assert after[(5, 2)] == {
            "link": "https://e.example",
            "underline": False,
            "color": GREEN_RGB,
        }

    def test_columns_and_rows_limit_the_write(self):
        grid = self.broken()
        fixed = set_url_links(grid, "S", "T", color=GREEN, columns=["site"], rows=[3])
        assert [(p.row, p.column) for p in fixed] == [(3, "site")]
        assert [(p.row, p.column) for p in fine(grid)] == [
            (2, "site"),
            (3, "home"),
            (4, "site"),
        ]
        assert len(requests_of(grid)) == 1

    def test_no_problem_no_write(self):
        grid = url_grid()
        assert set_url_links(grid, "S", "T", color=GREEN) == []
        assert grid.methods == [READ, GRID]

    def test_a_cell_the_fix_did_not_link_fails_the_read_back(self):
        grid = self.broken()
        grid.edit_externally(
            lambda g: g.format("T", 3, 4).update(underline=True),
            before=GRID,
            occurrence=3,
        )
        with pytest.raises(ReadBackError) as raised:
            set_url_links(grid, "S", "T", color=GREEN)
        assert str(raised.value) == (
            "tab 'T': the read-back found URL cells the fix did not link: "
            "row 3, column 'home' (underline)"
        )

    def test_the_link_s_blue_is_replaced(self):
        grid = FakeSheetGrid({"T": [["site"], ["https://a.example"]]})
        assert grid.format("T", 2, 1) == {"link": "https://a.example"}
        set_url_links(grid, "S", "T", color=GREEN)
        assert grid.format("T", 2, 1)["color"] == GREEN_RGB
        assert LINK_BLUE != GREEN_RGB
