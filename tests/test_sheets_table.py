"""Tests for gdrives.sheets.table: reading a tab as header-named, keyed records.

The tab's grid is served by ``FakeSheetsService`` in the shape the API returns
it: rows truncated at their last non-empty cell, and numbers and booleans as
JSON values under the unformatted render ``read_tab`` asks for.
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest
from helpers import FakeSheetGrid, FakeSheetsService

from gdrives.sheets import EmptyTabError, Table, parse_tab, pull_serials, read_tab


def tab_of(*rows):
    """A fake whose one ``values.get`` returns ``rows`` as the tab's grid."""
    return FakeSheetsService(get={"values": [list(row) for row in rows]})


class TestRequest:
    def test_reads_the_whole_tab_once_unformatted(self):
        svc = tab_of(["id"], ["a"])
        read_tab(svc, "sid", "My Tab", ["id"], ["id"])
        assert svc.calls == [
            (
                "values.get",
                {
                    "spreadsheetId": "sid",
                    "range": "'My Tab'",
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "FORMATTED_STRING",
                },
            )
        ]

    def test_repeated_wanted_column_is_refused_without_a_request(self):
        svc = tab_of(["id"])
        with pytest.raises(ValueError, match=r"column\(s\) \['id'\] asked for twice"):
            read_tab(svc, "sid", "T", ["id", "name", "id"], ["id"])
        assert svc.calls == []

    def test_blank_wanted_column_is_refused_without_a_request(self):
        # A blank name would otherwise match an unnamed gap in the header.
        svc = tab_of(["id", "", "name"])
        with pytest.raises(ValueError, match="blank column name"):
            read_tab(svc, "sid", "T", ["id", ""], ["id"])
        assert svc.calls == []

    def test_key_outside_columns_is_refused_without_a_request(self):
        svc = tab_of(["id"])
        with pytest.raises(
            ValueError, match=r"key column\(s\) \['id'\] not in columns"
        ):
            read_tab(svc, "sid", "T", ["name"], ["id"])
        assert svc.calls == []


class TestHeader:
    def test_columns_are_found_by_name_not_position(self):
        svc = tab_of(["name", "id", "paid"], ["Alex", "a1", True])
        table = read_tab(svc, "sid", "T", ["id", "paid", "name"], ["id"])
        # Each record holds the asked-for columns, in the asked-for order.
        assert table.rows == [{"id": "a1", "paid": "TRUE", "name": "Alex"}]
        assert list(table.rows[0]) == ["id", "paid", "name"]
        assert table.columns == ["id", "paid", "name"]
        assert table.header == ["name", "id", "paid"]

    def test_header_cells_are_stripped(self):
        svc = tab_of([" id ", "name\n"], ["a", "Alex"])
        table = read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert table.header == ["id", "name"]
        assert table.rows == [{"id": "a", "name": "Alex"}]

    def test_numeric_header_cell_is_its_canonical_string(self):
        svc = tab_of(["id", 2026.0], ["a", 5])
        table = read_tab(svc, "sid", "T", ["id", "2026"], ["id"])
        assert table.rows == [{"id": "a", "2026": "5"}]

    def test_missing_column_raises_naming_it(self):
        svc = tab_of(["id", "name"], ["a", "Alex"])
        with pytest.raises(ValueError) as raised:
            read_tab(svc, "sid", "T", ["id", "status", "paid"], ["id"])
        assert str(raised.value) == (
            "tab 'T' has no column(s) ['status', 'paid']; header: ['id', 'name']"
        )

    def test_repeated_header_raises_even_for_an_unwanted_column(self):
        svc = tab_of(["id", "note", "name", "note"], ["a"])
        with pytest.raises(ValueError, match=r"tab 'T': header repeats \['note'\]"):
            read_tab(svc, "sid", "T", ["id"], ["id"])

    def test_names_that_differ_only_in_padding_repeat(self):
        svc = tab_of(["id", "name", "name "])
        with pytest.raises(ValueError, match="header repeats"):
            read_tab(svc, "sid", "T", ["id"], ["id"])

    def test_repeat_is_checked_before_missing_columns(self):
        svc = tab_of(["id", "id"])
        with pytest.raises(ValueError, match="header repeats"):
            read_tab(svc, "sid", "T", ["nope"], [])

    @pytest.mark.parametrize("grid", [[], [[]], [["", " "]], [[], ["a", "b"]]])
    def test_no_header_row_raises(self, grid):
        svc = FakeSheetsService(get={"values": grid} if grid else {})
        with pytest.raises(EmptyTabError, match="tab 'T' has no header row"):
            read_tab(svc, "sid", "T", ["id"], ["id"])

    def test_empty_tab_error_is_a_value_error(self):
        assert issubclass(EmptyTabError, ValueError)

    def test_extra_columns_are_recorded_and_not_read(self):
        svc = tab_of(["id", "internal", "name", "", "notes"], ["a", "x", "Alex"])
        table = read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert table.extra_columns == ["internal", "notes"]
        assert table.rows == [{"id": "a", "name": "Alex"}]

    def test_columns_none_reads_every_named_column(self):
        svc = tab_of(["id", "", "name"], ["a", "gap", "Alex"])
        table = read_tab(svc, "sid", "T", None, ["id"])
        assert table.columns == ["id", "name"]
        assert table.rows == [{"id": "a", "name": "Alex"}]
        assert table.extra_columns == []

    def test_columns_none_with_a_key_the_header_lacks(self):
        svc = tab_of(["id", "name"], ["a", "Alex"])
        with pytest.raises(ValueError, match=r"has no column\(s\) \['code'\]"):
            read_tab(svc, "sid", "T", None, ["code"])


class TestRows:
    def test_cells_are_canonical_strings(self):
        svc = tab_of(
            ["id", "n", "x", "flag", "when", "text"],
            ["a", 3, 3.0, False, "2026-01-15", "007"],
            ["b", 2.5, -1, True, "", " padded "],
        )
        table = read_tab(svc, "sid", "T", ["id", "n", "x", "flag", "when", "text"])
        assert table.rows == [
            {"id": "a", "n": "3", "x": "3", "flag": "FALSE", "when": "2026-01-15"}
            | {"text": "007"},
            {"id": "b", "n": "2.5", "x": "-1", "flag": "TRUE", "when": ""}
            | {"text": " padded "},
        ]

    def test_short_rows_are_padded(self):
        svc = tab_of(["id", "name", "note"], ["a"], ["b", "Sam"])
        table = read_tab(svc, "sid", "T", ["id", "name", "note"], ["id"])
        assert table.rows == [
            {"id": "a", "name": "", "note": ""},
            {"id": "b", "name": "Sam", "note": ""},
        ]

    def test_blank_rows_are_skipped_and_row_numbers_stay_true(self):
        svc = tab_of(["id", "name"], [], ["a", "Alex"], ["", ""], [], ["b", "Sam"])
        table = read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert [row["id"] for row in table.rows] == ["a", "b"]
        assert table.row_numbers == {("a",): 3, ("b",): 6}

    def test_wide_rows_are_reported_and_their_overflow_not_read(self):
        svc = tab_of(["id", "name"], ["a", "Alex"], ["b", "Sam", "stray"])
        table = read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert table.wide_rows == [3]
        assert table.rows[1] == {"id": "b", "name": "Sam"}

    def test_empty_tab_body(self):
        table = read_tab(tab_of(["id"]), "sid", "T", ["id"], ["id"])
        assert (table.rows, table.row_numbers, table.wide_rows) == ([], {}, [])

    def test_row_numbers_use_normalized_keys(self):
        svc = tab_of(["id", "name"], ["C300 ", "Alex"], ["Jane  Doe", "Sam"])
        table = read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert table.row_numbers == {("C300",): 2, ("Jane Doe",): 3}
        # The stored text is never rewritten.
        assert table.rows[0]["id"] == "C300 "

    def test_composite_key(self):
        svc = tab_of(["year", "id", "v"], [2026, "a", "x"], [2025, "a", "y"])
        table = read_tab(svc, "sid", "T", ["year", "id", "v"], ["year", "id"])
        assert table.key == ("year", "id")
        assert table.row_numbers == {("2026", "a"): 2, ("2025", "a"): 3}

    def test_blank_key_with_data_is_refused_listing_rows(self):
        svc = tab_of(
            ["id", "name", "note"],
            ["a", "Alex"],
            ["", "Sam"],
            ["b"],
            ["  ", "", "just a note"],
        )
        with pytest.raises(ValueError) as raised:
            read_tab(svc, "sid", "T", ["id", "name"], ["id"])
        assert str(raised.value) == "tab 'T': blank key ['id'] in rows [3, 5]"

    def test_data_only_in_an_extra_column_still_needs_a_key(self):
        svc = tab_of(["id", "note"], ["a"], ["", "orphan note"])
        with pytest.raises(ValueError, match=r"blank key \['id'\] in rows \[3\]"):
            read_tab(svc, "sid", "T", ["id"], ["id"])

    def test_data_only_past_the_header_still_needs_a_key(self):
        svc = tab_of(["id", "name"], ["", "", "stray"])
        with pytest.raises(ValueError, match=r"blank key \['id'\] in rows \[2\]"):
            read_tab(svc, "sid", "T", ["id", "name"], ["id"])

    def test_duplicate_key_names_the_tab_and_rows(self):
        svc = tab_of(["id"], ["a"], ["b"], ["a "])
        with pytest.raises(
            ValueError, match=r"^tab 'T': duplicate key \('a',\) in rows \[2, 4\]$"
        ):
            read_tab(svc, "sid", "T", ["id"], ["id"])

    def test_without_a_key_rows_are_read_unindexed(self):
        svc = tab_of(["id"], ["a"], ["a"], [""], ["b"])
        table = read_tab(svc, "sid", "T", ["id"])
        assert table.key == ()
        assert table.rows == [{"id": "a"}, {"id": "a"}, {"id": "b"}]
        assert table.row_numbers == {}


class TestParseTab:
    """``parse_tab`` is ``read_tab`` without the request, for a grid already read."""

    def test_parses_a_grid_as_read_tab_would(self):
        grid = [["id", "n"], ["a", 3], [], ["b", True]]
        assert parse_tab("T", grid, None, ["id"]) == read_tab(
            tab_of(*grid), "sid", "T", None, ["id"]
        )

    def test_refuses_a_malformed_request(self):
        with pytest.raises(ValueError, match="asked for twice"):
            parse_tab("T", [["id"]], ["id", "id"])

    def test_an_empty_grid_has_no_header(self):
        with pytest.raises(EmptyTabError):
            parse_tab("T", [], None)


class TestSerials:
    """A declared date column is read from the serial numbers of a second read."""

    TYPES = {"on": "date", "at": "datetime", "n": "int"}
    # As an unformatted read with formatted dates returns the tab ...
    GRID = [
        ["id", "on", "note", "at"],
        ["a", "9/27/2026", "x", "9/27/2026 10:30:15"],
        ["b", "2026-09-27", "", "2026-09-27T10:30:15"],
        [],
        ["c", "9/28/2026", "", "9/28/2026 0:00:00"],
    ]
    # ... and as a serial read returns its two declared columns.
    SERIALS = {
        "on": [["on"], [46292], ["2026-09-27"], [], [46293]],
        "at": [
            ["at"],
            [46292.43767361111],
            ["2026-09-27T10:30:15"],
            [],
            [46292.999999988424],
        ],
    }

    def parse(self, grid=None, serials=None, types=None, columns=None):
        return parse_tab(
            "T",
            grid if grid is not None else self.GRID,
            columns,
            ["id"],
            types=types if types is not None else self.TYPES,
            serials=serials if serials is not None else self.SERIALS,
        )

    def test_serials_and_text_both_arrive_as_iso(self):
        table = self.parse()
        assert table.rows == [
            {"id": "a", "on": "2026-09-27", "note": "x", "at": "2026-09-27 10:30:15"},
            {"id": "b", "on": "2026-09-27", "note": "", "at": "2026-09-27T10:30:15"},
            # The display text named the next day; the serial has the moment.
            {
                "id": "c",
                "on": "2026-09-28",
                "note": "",
                "at": "2026-09-27 23:59:59.999000",
            },
        ]
        assert table.row_numbers == {("a",): 2, ("b",): 3, ("c",): 5}

    def test_the_table_records_the_types_of_the_columns_read(self):
        assert self.parse().types == {"on": "date", "at": "datetime"}
        assert self.parse(types={"on": date, "at": datetime}).types == {
            "on": "date",
            "at": "datetime",
        }
        assert self.parse(columns=["id", "on"]).types == {"on": "date"}
        assert parse_tab("T", self.GRID, None).types == {}

    def test_a_boolean_is_not_a_serial_and_a_plain_number_is(self):
        grid = [["id", "on"], ["a", True], ["b", 45000]]
        serials = {"on": [["on"], [True], [45000]]}
        assert self.parse(grid, serials).rows == [
            {"id": "a", "on": "TRUE"},
            {"id": "b", "on": "2023-03-15"},
        ]

    def test_text_from_the_serial_read_never_replaces_the_first_read(self):
        grid = [["id", "on"], ["a", "first"]]
        assert self.parse(grid, {"on": [["on"], ["second"]]}).rows == [
            {"id": "a", "on": "first"}
        ]

    def test_a_serial_that_does_not_fit_its_type_keeps_the_first_read(self):
        # A time of day in a date column: the schema check reports the text.
        grid = [["id", "on"], ["a", "9/27/2026 12:00:00"], ["b", "far"]]
        serials = {"on": [["on"], [46292.5], [1e12]]}
        assert self.parse(grid, serials).rows == [
            {"id": "a", "on": "9/27/2026 12:00:00"},
            {"id": "b", "on": "far"},
        ]

    def test_a_serials_grid_shorter_than_the_tab(self):
        grid = [["id", "on"], ["a", "9/27/2026"], ["b", "9/28/2026"], ["c", ""]]
        serials = {"on": [["on"], [46292]]}
        assert self.parse(grid, serials).rows == [
            {"id": "a", "on": "2026-09-27"},
            {"id": "b", "on": "9/28/2026"},
            {"id": "c", "on": ""},
        ]

    def test_a_blank_serial_row_keeps_the_first_read(self):
        grid = [["id", "on"], ["a", "9/27/2026"]]
        assert self.parse(grid, {"on": [["on"], []]}).rows == [
            {"id": "a", "on": "9/27/2026"}
        ]

    def test_a_typed_column_with_no_serials_is_read_as_before(self):
        table = self.parse(serials={"on": self.SERIALS["on"]})
        assert [row["at"] for row in table.rows] == [
            "9/27/2026 10:30:15",
            "2026-09-27T10:30:15",
            "9/28/2026 0:00:00",
        ]
        assert parse_tab("T", self.GRID, None, types=self.TYPES).rows == (
            parse_tab("T", self.GRID, None).rows
        )

    def test_serials_for_a_column_not_declared_a_date_are_ignored(self):
        grid = [["id", "n"], ["a", 46292]]
        serials = {"n": [["n"], [46292]], "gone": [["gone"], [1]]}
        assert self.parse(grid, serials).rows == [{"id": "a", "n": "46292"}]

    def test_a_typed_column_the_tab_lacks(self):
        grid = [["id", "note"], ["a", "x"]]
        assert self.parse(grid, {}).types == {}
        with pytest.raises(ValueError, match=r"has no column\(s\) \['on'\]"):
            self.parse(grid, {}, columns=["id", "on"])

    def test_a_typed_key_column_is_keyed_by_its_iso_date(self):
        grid = [["on", "v"], ["9/27/2026", "x"]]
        table = parse_tab(
            "T",
            grid,
            None,
            ["on"],
            types={"on": "date"},
            serials={"on": [["on"], [46292]]},
        )
        assert table.row_numbers == {("2026-09-27",): 2}

    def test_an_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type 'day'"):
            self.parse(types={"on": "day"})


class TestReadTabTypes:
    ROWS = [
        ["id", "note", "on", "", "at"],
        ["a", "x", date(2026, 9, 27), "", datetime(2026, 9, 27, 23, 59, 59, 999000)],
        ["b", "", "2026-09-28", "", "2026-09-28T01:02:03"],
    ]
    TYPES = {"on": "date", "at": "datetime"}

    def test_the_declared_date_columns_cost_one_second_read(self):
        grid = FakeSheetGrid({"My Tab": self.ROWS})
        table = read_tab(grid, "S", "My Tab", None, ["id"], types=self.TYPES)
        assert table.rows == [
            {
                "id": "a",
                "note": "x",
                "on": "2026-09-27",
                "at": "2026-09-27 23:59:59.999000",
            },
            {"id": "b", "note": "", "on": "2026-09-28", "at": "2026-09-28T01:02:03"},
        ]
        assert table.types == self.TYPES
        assert grid.calls == [
            (
                "values.get",
                {
                    "spreadsheetId": "S",
                    "range": "'My Tab'",
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "FORMATTED_STRING",
                },
            ),
            (
                "values.batchGet",
                {
                    "spreadsheetId": "S",
                    "ranges": ["'My Tab'!C:C", "'My Tab'!E:E"],
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "SERIAL_NUMBER",
                },
            ),
        ]

    def test_without_types_the_tab_reads_as_displayed_in_one_request(self):
        grid = FakeSheetGrid({"T": self.ROWS})
        table = read_tab(grid, "S", "T", ["id", "on", "at"], ["id"])
        assert table.rows[0] == {
            "id": "a",
            "on": "9/27/2026",
            "at": "9/28/2026 0:00:00",
        }
        assert grid.methods == ["values.get"]

    @pytest.mark.parametrize(
        "types",
        [{}, {"note": "str", "id": "int"}, {"gone": "date"}],
    )
    def test_no_declared_date_column_on_the_tab_makes_one_read(self, types):
        grid = FakeSheetGrid({"T": self.ROWS})
        read_tab(grid, "S", "T", ["id", "note"], ["id"], types=types)
        assert grid.methods == ["values.get"]

    def test_only_the_columns_read_are_read_again(self):
        grid = FakeSheetGrid({"T": self.ROWS})
        table = read_tab(grid, "S", "T", ["id", "on"], ["id"], types=self.TYPES)
        assert table.types == {"on": "date"}
        (_, kwargs) = grid.calls[1]
        assert kwargs["ranges"] == ["'T'!C:C"]

    def test_an_unknown_type_is_refused_without_a_request(self):
        grid = FakeSheetGrid({"T": self.ROWS})
        with pytest.raises(ValueError, match="unknown column type"):
            read_tab(grid, "S", "T", None, ["id"], types={"on": "day"})
        assert grid.calls == []

    def test_pull_serials_returns_each_declared_column_as_read(self):
        grid = FakeSheetGrid({"T": self.ROWS})
        first = read_tab(grid, "S", "T", None).header
        serials = pull_serials(grid, "S", "T", [first], {"on": date, "note": "str"})
        assert serials == {"on": [["on"], [46292], ["2026-09-28"]]}

    @pytest.mark.parametrize("rows", [[], [[]], [["", ""]], [["id", "on", "on"]]])
    def test_pull_serials_of_a_tab_with_no_such_column_asks_nothing(self, rows):
        grid = FakeSheetGrid({"T": []})
        assert pull_serials(grid, "S", "T", rows, {"at": "date"}) == {}
        assert grid.calls == []


class TestTable:
    def test_is_frozen(self):
        table = read_tab(tab_of(["id"], ["a"]), "sid", "T", ["id"], ["id"])
        assert isinstance(table, Table)
        with pytest.raises(FrozenInstanceError):
            setattr(table, "tab", "other")


class TestLastRow:
    def test_header_only_tab_ends_at_row_one(self):
        assert read_tab(tab_of(["id"]), "sid", "T", ["id"], ["id"]).last_row == 1

    def test_trailing_blank_rows_do_not_count(self):
        svc = tab_of(["id"], ["a"], [], ["b"], [""], [])
        assert read_tab(svc, "sid", "T", ["id"], ["id"]).last_row == 4

    def test_a_row_holding_only_an_extra_column_counts(self):
        # "b" is the last keyed row, but row 4 holds a value outside the
        # projection, so new rows must go after it.
        svc = tab_of(["id", "note"], ["a"], ["b"], ["c", "only a note"])
        table = read_tab(svc, "sid", "T", ["id"], ["id"])
        assert table.last_row == 4

    def test_a_cell_past_the_header_counts(self):
        svc = tab_of(["id"], ["a"], [], ["b", "stray"])
        table = read_tab(svc, "sid", "T", ["id"], ["id"])
        assert (table.last_row, table.wide_rows) == (4, [4])

    def test_counts_without_a_key(self):
        svc = tab_of(["id"], ["a"], ["a"], [])
        assert read_tab(svc, "sid", "T", ["id"]).last_row == 3
