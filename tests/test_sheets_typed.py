"""Tests for typed writes: declared columns written to the sheet as values.

``cell_data`` and ``to_serial`` are pure. The writes run against
``FakeSheetGrid``, which stores a typed ``userEnteredValue`` as the number or
boolean it is and holds each cell's number format, so the tests assert the
values and formats a run leaves on the sheet and the requests that made them.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from helpers import FakeSheetGrid

from gdrives.sheets import (
    CONFIG_NAME,
    DATE_FORMATS,
    NUMBER_FORMAT_FIELD,
    Cell,
    ColumnSchema,
    ConfigError,
    EmptyTabError,
    MergePlan,
    NewRow,
    ReadBackError,
    SheetChangedError,
    TabConfig,
    apply_plan,
    cell_data,
    dated_cells,
    format_requests,
    parse_config,
    push_rows,
    read_records,
    read_tab,
    retype_columns,
    run_target,
    serial_to_cell,
    to_serial,
    typed_columns,
    verify,
    write_values_csv,
)

STRUCTURE = "spreadsheets.batchUpdate"
PUSH = "values.batchUpdate"
DATE = {"type": "DATE", "pattern": "yyyy-mm-dd"}
DATE_TIME = {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
HEADER = ["id", "amt", "paid", "due", "note"]
TYPES = {"id": "int", "amt": "float", "paid": "bool", "due": "date", "note": "str"}
SERIAL = 46292  # 2026-09-27


def sheet(*rows, header=HEADER, types=TYPES, **size):
    """A working fake holding ``header`` and ``rows``, and its table by ``id``."""
    tabs = {"T": [header, *rows]}
    table = read_tab(FakeSheetGrid(tabs, **size), "S", "T", header, ["id"], types=types)
    return FakeSheetGrid(tabs, **size), table


def push(key, column, local):
    return Cell(key=(key,), column=column, base="", local=local, sheet="")


def new(key, **values):
    blank = {"id": key, "amt": "", "paid": "", "due": "", "note": ""}
    return NewRow(key=(key,), values=blank | values)


def plan(pushes=(), appends=()):
    return MergePlan(pushes=list(pushes), appends=list(appends))


def kinds(grid):
    """The requests of the one structural batch sent, by kind."""
    (body,) = [kwargs["body"] for method, kwargs in grid.calls if method == STRUCTURE]
    return [next(iter(request)) for request in body["requests"]]


def number(grid, row, column, title="T"):
    """The number format of a cell, at spreadsheet ``row`` and 1-based ``column``."""
    return grid.format(title, row, column).get("number")


class TestCellData:
    @pytest.mark.parametrize("type_", ["str", "int", "float", "bool", "date"])
    def test_a_blank_is_an_empty_cell(self, type_):
        assert cell_data("", type_) == {}

    def test_str_is_a_string_and_a_formula_stays_text(self):
        assert cell_data("007") == {"userEnteredValue": {"stringValue": "007"}}
        assert cell_data("=1+2", "str") == {"userEnteredValue": {"stringValue": "=1+2"}}

    @pytest.mark.parametrize(
        ("text", "type_", "value"),
        [
            ("7", "int", {"numberValue": 7}),
            ("-3", int, {"numberValue": -3}),
            ("3.0", "float", {"numberValue": 3.0}),
            ("1e-07", "float", {"numberValue": 1e-07}),
            ("true", "bool", {"boolValue": True}),
            ("FALSE", bool, {"boolValue": False}),
            ("2026-09-27", "date", {"numberValue": SERIAL}),
            ("2026-09-27 06:00:00", "datetime", {"numberValue": SERIAL + 0.25}),
        ],
    )
    def test_a_typed_cell_is_its_value(self, text, type_, value):
        assert cell_data(text, type_) == {"userEnteredValue": value}

    def test_an_integer_at_2_to_53_is_exact_and_past_it_is_refused(self):
        assert cell_data(str(2**53), "int") == {
            "userEnteredValue": {"numberValue": 2**53}
        }
        with pytest.raises(ValueError, match="past 2\\*\\*53"):
            cell_data(str(-(2**53) - 1), "int")

    @pytest.mark.parametrize("text", ["nan", "inf", "-inf"])
    def test_a_float_that_is_not_finite_is_refused(self, text):
        with pytest.raises(ValueError, match="not a finite number"):
            cell_data(text, "float")

    def test_text_that_does_not_parse_is_refused(self):
        with pytest.raises(ValueError, match="not a valid int"):
            cell_data("3.5", "int")

    def test_a_datetime_with_a_time_zone_or_below_a_millisecond_is_refused(self):
        with pytest.raises(ValueError, match="time zone"):
            cell_data("2026-09-27 06:00:00+00:00", "datetime")
        with pytest.raises(ValueError, match="finer than a millisecond"):
            cell_data("2026-09-27 06:00:00.000500", "datetime")


class TestToSerial:
    def test_a_date_is_whole(self):
        assert to_serial(date(2026, 9, 27)) == SERIAL
        assert to_serial(date(1899, 12, 30)) == 0

    def test_a_datetime_round_trips_through_serial_to_cell(self):
        moment = datetime(2026, 9, 27, 9, 5, 7, 123000)
        assert serial_to_cell(to_serial(moment), "datetime") == (
            "2026-09-27 09:05:07.123"
        )

    def test_a_date_round_trips_through_serial_to_cell(self):
        assert serial_to_cell(to_serial(date(2031, 2, 28)), "date") == "2031-02-28"

    def test_an_aware_datetime_is_refused(self):
        with pytest.raises(ValueError, match="time zone"):
            to_serial(datetime(2026, 9, 27, tzinfo=timezone(timedelta(hours=2))))


class TestTypedColumns:
    def test_str_and_the_key_are_left_out(self):
        assert typed_columns(TYPES, ["id"]) == {
            "amt": "float",
            "paid": "bool",
            "due": "date",
        }

    def test_a_type_given_by_class_is_named(self):
        assert typed_columns({"n": int, "d": date}) == {"n": "int", "d": "date"}

    def test_no_types_is_none(self):
        assert typed_columns(None) == {}


class TestFormatRequests:
    def test_adjacent_rows_of_one_column_share_a_request(self):
        requests = format_requests(3, [(4, 2, "date"), (1, 2, "date"), (2, 2, "date")])
        spans = [
            (
                r["repeatCell"]["range"]["startRowIndex"],
                r["repeatCell"]["range"]["endRowIndex"],
            )
            for r in requests
        ]
        assert spans == [(1, 3), (4, 5)]
        (first, _) = requests
        assert first["repeatCell"]["fields"] == NUMBER_FORMAT_FIELD
        assert first["repeatCell"]["range"]["sheetId"] == 3
        assert first["repeatCell"]["cell"] == {
            "userEnteredFormat": {"numberFormat": DATE}
        }

    def test_each_column_and_type_gets_its_own(self):
        requests = format_requests(0, [(1, 3, "datetime"), (1, 2, "date")])
        formats = [
            (
                r["repeatCell"]["range"]["startColumnIndex"],
                r["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"],
            )
            for r in requests
        ]
        assert formats == [(2, DATE), (3, DATE_TIME)]
        assert dict(DATE_FORMATS["datetime"]) == DATE_TIME

    def test_no_cells_is_no_request(self):
        assert format_requests(0, []) == []


class TestDatedCells:
    def test_no_columns_makes_no_request(self):
        grid = FakeSheetGrid({"T": [HEADER]})
        assert dated_cells(grid, "S", "T", HEADER, []) == set()
        assert grid.calls == []

    def test_date_and_time_formats_count_and_a_number_format_does_not(self):
        grid = FakeSheetGrid({"T": [HEADER, [1], [2], [3], [4]]})
        grid.format("T", 2, 4)["number"] = {"type": "DATE", "pattern": "d mmm"}
        grid.format("T", 3, 4)["number"] = {"type": "TIME"}
        grid.format("T", 4, 4)["number"] = {"type": "NUMBER"}
        grid.format("T", 5, 2)["number"] = {"type": "DATE_TIME"}
        found = dated_cells(grid, "S", "T", HEADER, ["amt", "due"])
        assert found == {(2, "due"), (3, "due"), (5, "amt")}
        assert grid.methods == ["spreadsheets.get"]
        assert grid.calls[0][1]["ranges"] == ["'T'!B:D"]


class TestApplyTyped:
    def test_pushes_are_one_batch_of_values_with_a_date_format(self):
        grid, table = sheet(["1", "1", "FALSE", "", "x"], ["2", "2", "", "", ""])
        pushes = [
            push("1", "amt", "3.0"),
            push("1", "paid", "true"),
            push("2", "due", "2026-09-27"),
            push("2", "note", "=1+2"),
        ]
        result = apply_plan(grid, "S", table, plan(pushes), typed_writes=True)
        assert PUSH not in grid.methods
        assert grid.methods.count(STRUCTURE) == 1
        assert kinds(grid) == ["updateCells"] * 4 + ["repeatCell"]
        assert grid.values("T")[1:] == [
            ["1", 3.0, True, "", "x"],
            ["2", "2", "", SERIAL, "=1+2"],
        ]
        assert number(grid, 3, 4) == DATE
        assert number(grid, 2, 2) is None
        assert result.pushed == 4

    def test_a_date_cell_that_has_a_date_format_keeps_it(self):
        grid, table = sheet(["1", "", "", "", ""])
        own = {"type": "DATE", "pattern": "d mmm yyyy"}
        grid.format("T", 2, 4)["number"] = own
        apply_plan(
            grid, "S", table, plan([push("1", "due", "2026-09-27")]), typed_writes=True
        )
        assert kinds(grid) == ["updateCells"]
        assert number(grid, 2, 4) == own

    def test_a_blank_date_push_sets_no_format(self):
        grid, table = sheet(["1", "", "", "2026-09-27", ""])
        apply_plan(grid, "S", table, plan([push("1", "due", "")]), typed_writes=True)
        assert kinds(grid) == ["updateCells"]
        assert "spreadsheets.get" not in [
            m for m, kw in grid.calls if kw.get("includeGridData")
        ]
        assert grid.values("T") == [HEADER, ["1"]]

    def test_new_rows_are_values_and_an_int_key_stays_text(self):
        grid, table = sheet(["1", "1", "", "", ""])
        rows = [new("007", amt="2.5", paid="FALSE", due="2026-09-27")]
        result = apply_plan(grid, "S", table, plan(appends=rows), typed_writes=True)
        assert grid.values("T")[2] == ["007", 2.5, False, SERIAL]
        assert number(grid, 3, 4) == DATE
        assert result.appended_rows == [3]
        assert grid.methods.count(STRUCTURE) == 1

    def test_new_rows_written_in_place_keep_a_format_the_rows_have(self):
        grid, table = sheet(["1", "", "", "", ""])
        own = {"type": "DATE", "pattern": "d/m"}
        grid.format("T", 3, 4)["number"] = own
        rows = [new("2", due="2026-09-27"), new("3", due="2026-09-28")]
        apply_plan(grid, "S", table, plan(appends=rows), typed_writes=True)
        assert number(grid, 3, 4) == own
        assert number(grid, 4, 4) == DATE

    def test_new_rows_past_the_grid_are_given_the_format(self):
        grid, table = sheet(["1", "", "", "", ""], rows=2)
        apply_plan(
            grid,
            "S",
            table,
            plan(appends=[new("2", due="2026-09-27")]),
            typed_writes=True,
        )
        assert kinds(grid) == ["appendDimension", "updateCells", "repeatCell"]
        assert number(grid, 3, 4) == DATE

    def test_inserted_rows_take_the_format_of_the_row_above(self):
        grid, table = sheet(["1", "", "", "2026-01-01", "a"], ["2", "", "", "", "b"])
        own = {"type": "DATE", "pattern": "mmm d"}
        grid.format("T", 2, 4)["number"] = own
        rows = [new("3", due="2026-09-27", note="b")]
        result = apply_plan(
            grid,
            "S",
            table,
            plan([push("2", "amt", "4")], rows),
            insert_above={"note": "b"},
            typed_writes=True,
        )
        # Inserted above row 3, so the push to that row lands on row 4.
        assert result.appended_rows == [3]
        assert result.pushed_cells == [(4, "amt")]
        assert grid.values("T")[2:] == [
            ["3", "", "", SERIAL, "b"],
            ["2", 4, "", "", "b"],
        ]
        assert number(grid, 3, 4) == own
        assert kinds(grid) == ["insertDimension", "updateCells", "updateCells"]

    def test_rows_inserted_below_the_header_inherit_from_below(self):
        grid, table = sheet(["1", "", "", "", "b"])
        rows = [new("0", due="2026-09-27", note="a")]
        apply_plan(
            grid,
            "S",
            table,
            plan(appends=rows),
            insert_above={"note": "b"},
            typed_writes=True,
        )
        assert grid.values("T")[1] == ["0", "", "", SERIAL, "a"]
        assert number(grid, 2, 4) == DATE

    def test_clear_links_joins_the_mask_of_a_typed_push(self):
        grid, table = sheet(["1", "", "", "", "old"])
        apply_plan(
            grid,
            "S",
            table,
            plan([push("1", "note", "example.com")]),
            clear_links=True,
            typed_writes=True,
        )
        assert grid.links("T") == {}
        assert kinds(grid) == ["updateCells"]

    def test_a_value_that_cannot_be_written_is_refused_before_any_request(self):
        grid, table = sheet(["1", "", "", "", ""])
        with pytest.raises(ValueError, match="column 'amt': 'nan' is not a finite"):
            apply_plan(
                grid, "S", table, plan([push("1", "amt", "nan")]), typed_writes=True
            )
        with pytest.raises(ValueError, match="new row \\('2',\\), column 'amt'"):
            apply_plan(
                grid, "S", table, plan(appends=[new("2", amt="inf")]), typed_writes=True
            )
        assert grid.calls == []

    def test_a_formatted_table_is_refused(self):
        tabs = {"T": [HEADER, ["1"]]}
        grid = FakeSheetGrid(tabs)
        table = read_tab(grid, "S", "T", HEADER, ["id"], render="formatted")
        grid.calls.clear()
        with pytest.raises(ValueError, match="need a table read unformatted"):
            apply_plan(
                grid, "S", table, plan([push("1", "amt", "3")]), typed_writes=True
            )
        assert grid.calls == []

    def test_without_the_field_pushes_are_raw_text(self):
        grid, table = sheet(["1", "", "", "", ""])
        apply_plan(grid, "S", table, plan([push("1", "amt", "3.0")]))
        assert grid.methods.count(PUSH) == 1
        assert grid.values("T")[1] == ["1", "3.0"]


class TestVerifyTyped:
    def test_a_typed_column_compares_by_value(self):
        grid, table = sheet(["1", 3, "", "", ""])
        three = plan([push("1", "amt", "3.0")])
        verify(grid, "S", table, three, typed_writes=True)
        with pytest.raises(ReadBackError, match="wrote '3.0', read '3'"):
            verify(grid, "S", table, three)

    def test_a_value_that_differs_is_a_mismatch(self):
        grid, table = sheet(["1", 4, "", "", ""])
        with pytest.raises(ReadBackError, match="wrote '3.0', read '4'"):
            verify(grid, "S", table, plan([push("1", "amt", "3.0")]), typed_writes=True)

    def test_a_new_row_compares_by_value_and_its_key_as_text(self):
        grid, table = sheet(["007", 2.5, "", "", ""])
        verify(
            grid, "S", table, plan(appends=[new("007", amt="2.50")]), typed_writes=True
        )
        with pytest.raises(ReadBackError, match="rows \\[\\('7',\\)\\] not found"):
            verify(grid, "S", table, plan(appends=[new("7")]), typed_writes=True)


class TestPushRowsTyped:
    COLUMNS = ["id", "amt", "paid", "due", "at"]
    SCHEMA = {
        "id": ColumnSchema("int"),
        "amt": ColumnSchema("float"),
        "paid": ColumnSchema("bool"),
        "due": ColumnSchema("date"),
        "at": ColumnSchema("datetime"),
    }
    ROWS = [
        {
            "id": "007",
            "amt": "3.0",
            "paid": "true",
            "due": "2026-09-27",
            "at": "2026-09-27 09:05",
        },
        {"id": "8", "amt": "", "paid": "", "due": "", "at": ""},
    ]

    def run(self, grid, rows=None, **options):
        return push_rows(
            grid,
            "S",
            "T",
            self.COLUMNS,
            rows if rows is not None else self.ROWS,
            key=["id"],
            schema=self.SCHEMA,
            typed_writes=True,
            **options,
        )

    def test_the_tab_is_written_as_values_in_one_request(self):
        grid = FakeSheetGrid({"T": [["old"], ["x"], ["y"], ["z"]]})
        report = self.run(grid, apply=True)
        assert report.wrote_sheet
        assert "values.update" not in grid.methods
        assert grid.values("T") == [
            self.COLUMNS,
            ["007", 3.0, True, SERIAL, SERIAL + (9 * 60 + 5) / 1440],
            ["8"],
        ]
        assert number(grid, 2, 4) == DATE
        assert number(grid, 2, 5) == DATE_TIME
        assert number(grid, 3, 4) is None
        writes = [kw for m, kw in grid.calls if m == STRUCTURE]
        assert [next(iter(r)) for r in writes[-1]["body"]["requests"]] == [
            "updateCells",
            "repeatCell",
            "repeatCell",
        ]

    def test_a_second_push_of_the_same_rows_finds_the_tab_unchanged(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        self.run(grid, apply=True)
        grid.calls.clear()
        report = self.run(grid, apply=True)
        assert report.replacement is not None
        assert report.replacement.unchanged
        assert not report.wrote_sheet
        assert STRUCTURE not in grid.methods

    def test_a_date_column_with_its_own_format_keeps_it(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        own = {"type": "DATE", "pattern": "d mmm"}
        grid.format("T", 2, 4)["number"] = own
        self.run(grid, apply=True)
        assert number(grid, 2, 4) == own

    def test_a_missing_tab_is_created_and_written(self):
        grid = FakeSheetGrid({"Other": [["a"]]})
        report = self.run(grid, apply=True)
        assert report.tab_state == "missing"
        assert grid.values("T")[1][:2] == ["007", 3.0]

    def test_clear_links_joins_the_mask(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        push_rows(
            grid,
            "S",
            "T",
            ["id", "site"],
            [{"id": "1", "site": "example.com"}],
            schema={"id": ColumnSchema("int")},
            clear_links=True,
            typed_writes=True,
            apply=True,
        )
        assert grid.links("T") == {}
        assert grid.values("T") == [["id", "site"], [1, "example.com"]]

    def test_a_read_back_that_differs_raises(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["9"]], row=3), before="values.get", occurrence=3
        )
        with pytest.raises(ReadBackError, match="rows \\[3\\] differ"):
            self.run(grid, apply=True)

    def test_refused_with_user_entered(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        with pytest.raises(ValueError, match="input_option USER_ENTERED contradicts"):
            self.run(grid, input_option="USER_ENTERED")
        assert grid.calls == []

    def test_refused_with_a_formatted_render(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        with pytest.raises(ValueError, match="need the tab read unformatted"):
            self.run(grid, render="formatted")
        assert grid.calls == []

    def test_a_value_that_cannot_be_written_is_refused_before_any_request(self):
        grid = FakeSheetGrid({"T": [["old"]]})
        rows = [{"id": "1", "amt": "inf"}]
        with pytest.raises(ValueError, match="row 2, column 'amt'"):
            self.run(grid, rows=rows, apply=True)
        assert grid.calls == []


class TestConfig:
    def target(self, tmp_path, input_option="RAW", **fields):
        tab = {"local": "t.csv", "key": ["id"]} | fields
        data = {
            "t": {"spreadsheet": "S", "input_option": input_option, "tabs": {"T": tab}}
        }
        return parse_config(data, tmp_path / CONFIG_NAME).target("t")

    def test_a_sync_and_a_push_tab_take_it(self, tmp_path):
        assert self.target(tmp_path, typed_writes=True).tabs[0].typed_writes
        assert (
            self.target(tmp_path, mode="push", typed_writes=True).tabs[0].typed_writes
        )
        assert not self.target(tmp_path).tabs[0].typed_writes

    @pytest.mark.parametrize(
        ("fields", "input_option", "problem"),
        [
            ({"typed_writes": "yes"}, "RAW", "must be true or false"),
            ({"mode": "pull", "typed_writes": True}, "RAW", "does not apply to a pull"),
            ({"typed_writes": True, "render": "formatted"}, "RAW", "needs 'render'"),
            (
                {"mode": "push", "typed_writes": True},
                "USER_ENTERED",
                "USER_ENTERED contradicts it",
            ),
        ],
    )
    def test_refusals(self, tmp_path, fields, input_option, problem):
        with pytest.raises(ConfigError, match=problem):
            self.target(tmp_path, input_option, **fields)

    def test_a_tab_built_in_code_refuses_a_formatted_render(self, tmp_path):
        with pytest.raises(ValueError, match="needs 'render' unformatted"):
            TabConfig(
                title="T",
                local=tmp_path / "t.csv",
                typed_writes=True,
                render="formatted",
            )


class TestRunTarget:
    """The field reaches the sheet writes of a sync and of a push."""

    SCHEMA = {"amt": {"type": "float"}, "due": {"type": "date"}}

    def config(self, tmp_path, mode):
        tab = {
            "local": "t.csv",
            "key": ["id"],
            "mode": mode,
            "typed_writes": True,
            "schema": self.SCHEMA,
        }
        data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}}}
        target = parse_config(data, tmp_path / CONFIG_NAME).target("t")
        write_values_csv(
            str(tmp_path / "t.csv"),
            [["id", "amt", "due"], ["a", "2.5", "2026-09-27"]],
        )
        return target

    def test_a_sync_writes_values(self, tmp_path):
        target = self.config(tmp_path, "sync")
        grid = FakeSheetGrid({"T": [["id", "amt", "due"]]})
        report = run_target(grid, "S", target, "sync", apply=True, adopt=True)
        assert report.exit_code == 0
        assert grid.values("T")[1] == ["a", 2.5, SERIAL]
        assert number(grid, 2, 3) == DATE
        assert PUSH not in grid.methods
        # The base keeps the local text, and the next run is in sync.
        again = run_target(grid, "S", target, "sync")
        assert again.exit_code == 0
        plan_again = again.tabs[0].plan
        assert plan_again is not None
        assert not plan_again.has_writes
        base = read_records(target.base_path(target.tabs[0])).rows
        assert base == [{"id": "a", "amt": "2.5", "due": "2026-09-27"}]

    def test_a_push_writes_values(self, tmp_path):
        target = self.config(tmp_path, "push")
        grid = FakeSheetGrid({"T": [["old"]]})
        report = run_target(grid, "S", target, "push", apply=True)
        assert report.exit_code == 0
        assert grid.values("T") == [["id", "amt", "due"], ["a", 2.5, SERIAL]]


class TestRetypeColumns:
    SCHEMA = {
        "id": ColumnSchema("int"),
        "amt": ColumnSchema("float"),
        "paid": ColumnSchema("bool"),
        "due": ColumnSchema("date"),
        "note": ColumnSchema("str"),
    }

    def grid(self):
        return FakeSheetGrid(
            {
                "T": [
                    HEADER,
                    ["1", "3.0", "true", "2026-09-27", "7"],
                    ["2", 4, True, date(2026, 1, 2), ""],
                    ["3", "x", "", "2026-13-01", "y"],
                ]
            }
        )

    def test_a_preview_lists_the_changes_and_writes_nothing(self):
        grid = self.grid()
        report = retype_columns(grid, "S", "T", self.SCHEMA, key=["id"])
        assert report.types == {"amt": "float", "paid": "bool", "due": "date"}
        assert [(c.row, c.column, c.text) for c in report.changes] == [
            (2, "amt", "3.0"),
            (2, "paid", "true"),
            (2, "due", "2026-09-27"),
        ]
        assert [(c.row, c.column) for c in report.unparsed] == [
            (4, "amt"),
            (4, "due"),
        ]
        assert "not a valid float" in report.unparsed[0].problem
        assert not report.applied
        assert grid.methods == ["values.get"]

    def test_apply_rewrites_the_changes_and_nothing_else(self):
        grid = self.grid()
        grid.format("T", 3, 4)["number"] = {"type": "DATE", "pattern": "d mmm"}
        report = retype_columns(grid, "S", "T", self.SCHEMA, key=["id"], apply=True)
        assert report.applied
        assert grid.values("T")[1:] == [
            ["1", 3.0, True, SERIAL, "7"],
            ["2", 4, True, date(2026, 1, 2)],
            ["3", "x", "", "2026-13-01", "y"],
        ]
        assert number(grid, 2, 4) == DATE
        assert kinds(grid) == ["updateCells"] * 3 + ["repeatCell"]

    def test_a_key_column_is_left_as_text_and_without_key_it_is_retyped(self):
        grid = self.grid()
        report = retype_columns(grid, "S", "T", {"id": ColumnSchema("int")})
        assert [c.row for c in report.changes] == [2, 3, 4]
        assert (
            retype_columns(
                grid, "S", "T", {"id": ColumnSchema("int")}, key=["id"]
            ).changes
            == []
        )

    def test_a_date_cell_with_its_own_format_keeps_it(self):
        grid = self.grid()
        own = {"type": "DATE", "pattern": "d mmm"}
        grid.format("T", 2, 4)["number"] = own
        retype_columns(grid, "S", "T", {"due": ColumnSchema("date")}, apply=True)
        assert number(grid, 2, 4) == own
        assert kinds(grid) == ["updateCells"]

    def test_nothing_to_change_writes_nothing(self):
        grid = FakeSheetGrid({"T": [["amt"], [3]]})
        report = retype_columns(
            grid, "S", "T", {"amt": ColumnSchema("float")}, apply=True
        )
        assert report.changes == [] and not report.applied
        assert grid.methods == ["values.get"]

    def test_a_tab_that_changed_is_refused_with_nothing_written(self):
        grid = self.grid()
        grid.edit_externally(
            lambda g: g.write("T", [["9"]], row=5), before="values.get", occurrence=2
        )
        with pytest.raises(SheetChangedError, match="nothing was written"):
            retype_columns(grid, "S", "T", self.SCHEMA, apply=True)
        assert STRUCTURE not in grid.methods

    @pytest.mark.parametrize(
        ("column", "value", "read"),
        [(2, "3.0", "'3.0'"), (4, SERIAL + 0.5, str(SERIAL + 0.5)), (2, 5, "5")],
    )
    def test_a_read_back_that_differs_raises(self, column, value, read):
        grid = self.grid()

        def edit(g):
            g.tab("T").cells[1][column - 1] = value

        grid.edit_externally(edit, before="values.get", occurrence=3)
        with pytest.raises(ReadBackError, match=f"read {read}"):
            retype_columns(grid, "S", "T", self.SCHEMA, key=["id"], apply=True)

    def test_a_tab_with_no_header_is_refused(self):
        with pytest.raises(EmptyTabError):
            retype_columns(FakeSheetGrid({"T": []}), "S", "T", self.SCHEMA)

    def test_a_typed_column_the_header_lacks_is_refused(self):
        grid = FakeSheetGrid({"T": [["id"]]})
        with pytest.raises(ValueError, match="no column\\(s\\) \\['amt'\\]"):
            retype_columns(grid, "S", "T", {"amt": ColumnSchema("float")})


class TestFakeNumberFormats:
    def test_an_inserted_row_takes_the_number_format_and_bold_above(self):
        grid = FakeSheetGrid({"T": [["a"], ["b"]]})
        grid.format("T", 2, 1).update(number=DATE, bold=True)
        grid.spreadsheets().batchUpdate(
            spreadsheetId="S",
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": 0,
                                "dimension": "ROWS",
                                "startIndex": 2,
                                "endIndex": 3,
                            },
                            "inheritFromBefore": True,
                        }
                    }
                ]
            },
        ).execute()
        assert grid.format("T", 3, 1) == {"number": DATE, "bold": True}

    def test_a_number_is_stored_as_written(self):
        grid = FakeSheetGrid({"T": [["a"]]})
        grid.spreadsheets().batchUpdate(
            spreadsheetId="S",
            body={
                "requests": [
                    {
                        "updateCells": {
                            "start": {"sheetId": 0, "rowIndex": 1, "columnIndex": 0},
                            "rows": [{"values": [cell_data("0.1", "float")]}],
                            "fields": "userEnteredValue",
                        }
                    }
                ]
            },
        ).execute()
        assert grid.values("T")[1] == [0.1]
