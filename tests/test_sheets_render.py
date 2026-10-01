"""Tests for the ``render`` tab setting: reading a tab as the sheet displays it.

``unformatted``, the default, reads numbers and booleans as values, and
``formatted`` reads every cell as the sheet displays it. The fake service
returns a cell's displayed text for a formatted read, including the text of a
number format a test gives the cell (``FakeSheetGrid.display``).
"""

from datetime import date

import pytest
from default_requests import (
    DEFAULT_CREATED_CALLS,
    DEFAULT_PULL_CALLS,
    DEFAULT_PUSH_CALLS,
    DEFAULT_SYNC_CALLS,
    default_created,
    default_pull,
    default_push,
    default_sync,
)

from gdrives.sheets import (
    CONFIG_NAME,
    FORMATTED_VALUE,
    RENDERS,
    UNFORMATTED_VALUE,
    ConfigError,
    SheetChangedError,
    TabConfig,
    parse_config,
    parse_tab,
    pull_tab,
    pull_values,
    push_rows,
    push_tab,
    read_records,
    read_tab,
    sync_tab,
    write_values_csv,
)
from gdrives.testing import FakeSheetGrid


class TestDefaultRequests:
    """A run that does not set ``render`` sends the requests it sent before."""

    @pytest.mark.parametrize(
        ("scenario", "expected"),
        [
            (default_sync, DEFAULT_SYNC_CALLS),
            (default_created, DEFAULT_CREATED_CALLS),
            (default_pull, DEFAULT_PULL_CALLS),
            (default_push, DEFAULT_PUSH_CALLS),
        ],
    )
    def test_a_default_run_sends_the_recorded_requests(
        self, tmp_path, scenario, expected
    ):
        assert scenario(tmp_path).calls == expected


HEADER = ["id", "name", "pct"]
FORMATTED = {"valueRenderOption": "FORMATTED_VALUE"}


def make_tab(tmp_path, mode="sync", **fields):
    """The one tab ``T`` of a target, over local.csv."""
    tab = {"mode": mode, "local": "local.csv"} | fields
    if mode == "sync":
        tab.setdefault("key", ["id"])
    data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def shown_grid(**tabs):
    """Tab ``T`` whose ``pct`` cells hold fractions displayed as percentages."""
    grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada"], ["b", "Bo"]], **tabs})
    grid.display("T", 2, 3, 0.5, "50%")
    grid.display("T", 3, 3, 0.25, "25%")
    return grid


def write_local(tab, *rows, header=HEADER):
    write_values_csv(str(tab.local), [header, *rows])


def write_base(target, *rows, header=HEADER):
    path = target.base_path(target.tabs[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_values_csv(str(path), [header, *rows])


def value_reads(grid):
    """The keyword arguments of every ``values.get``, less the spreadsheet ID."""
    return [
        {k: v for k, v in kwargs.items() if k != "spreadsheetId"}
        for method, kwargs in grid.calls
        if method == "values.get"
    ]


def render_options(reads):
    """The render options of each read, without its range."""
    return [{k: v for k, v in read.items() if k != "range"} for read in reads]


SHOWN = [["a", "Ada", "50%"], ["b", "Bo", "25%"]]


def rows_of(path):
    return [list(row.values()) for row in read_records(path).rows]


class TestFakeDisplay:
    def test_a_displayed_cell_reads_as_its_value_or_its_text(self):
        grid = shown_grid()
        assert pull_values(grid, "S", "T!C2", render=UNFORMATTED_VALUE) == [[0.5]]
        assert pull_values(grid, "S", "T!C2", render=FORMATTED_VALUE) == [["50%"]]

    def test_a_string_written_over_it_is_displayed_as_written(self):
        grid = shown_grid()
        grid.write("T", [["a", "Ada", "75%"]], row=2)
        assert grid.values("T")[1] == ["a", "Ada", "75%"]
        assert pull_values(grid, "S", "T!C2", render=FORMATTED_VALUE) == [["75%"]]


class TestConfig:
    @pytest.mark.parametrize("mode", ["sync", "pull", "push"])
    def test_render_is_accepted_on_every_mode(self, tmp_path, mode):
        tab = make_tab(tmp_path, mode, render="formatted").tabs[0]
        assert tab.render == "formatted"

    def test_unformatted_is_the_default(self, tmp_path):
        assert make_tab(tmp_path).tabs[0].render == "unformatted"
        assert TabConfig("T", local=tmp_path / "t.csv").render == "unformatted"

    @pytest.mark.parametrize("render", ["displayed", "FORMATTED_VALUE", 1, None])
    def test_any_other_value_is_refused(self, tmp_path, render):
        with pytest.raises(ConfigError) as caught:
            make_tab(tmp_path, render=render)
        assert caught.value.problems == [
            "target 't', tab 'T': 'render' must be one of "
            f"['formatted', 'unformatted'], not {render!r}"
        ]

    def test_renders_names_the_two(self):
        assert RENDERS == {"unformatted", "formatted"}


class TestReadTab:
    def test_formatted_sends_formatted_value_and_the_table_records_it(self):
        grid = shown_grid()
        table = read_tab(grid, "S", "T", None, ["id"], render="formatted")
        assert value_reads(grid) == [{"range": "'T'"} | FORMATTED]
        assert table.render == "formatted"
        assert [row["pct"] for row in table.rows] == ["50%", "25%"]

    def test_the_default_reads_values_and_records_unformatted(self):
        grid = shown_grid()
        table = read_tab(grid, "S", "T", None, ["id"])
        assert render_options(value_reads(grid)) == [
            {
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            }
        ]
        assert table.render == "unformatted"
        assert [row["pct"] for row in table.rows] == ["0.5", "0.25"]

    def test_a_declared_date_column_arrives_as_iso_8601(self):
        grid = FakeSheetGrid({"T": [[*HEADER, "when"]]})
        grid.write("T", [["a", "Ada", 0.5, date(2024, 1, 2)]], row=2)
        grid.write("T", [["b", "Bo", 0.25, date(2024, 2, 3)]], row=3)
        grid.display("T", 2, 3, 0.5, "50%")
        grid.display("T", 3, 3, 0.25, "25%")
        table = read_tab(
            grid, "S", "T", None, ["id"], types={"when": "date"}, render="formatted"
        )
        assert [(row["pct"], row["when"]) for row in table.rows] == [
            ("50%", "2024-01-02"),
            ("25%", "2024-02-03"),
        ]
        # The serial read sends its own render options.
        assert grid.calls[-1] == (
            "values.batchGet",
            {
                "spreadsheetId": "S",
                "ranges": ["'T'!D:D"],
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "SERIAL_NUMBER",
            },
        )

    def test_another_render_is_refused_before_any_request(self):
        grid = shown_grid()
        with pytest.raises(ValueError, match="render must be one of"):
            read_tab(grid, "S", "T", None, render="displayed")
        assert grid.calls == []

    def test_parse_tab_records_the_render_and_refuses_another(self):
        grid = [HEADER, *SHOWN]
        assert parse_tab("T", grid, None, render="formatted").render == "formatted"
        with pytest.raises(ValueError, match="render must be one of"):
            parse_tab("T", grid, None, render="FORMATTED_VALUE")


def all_formatted(grid):
    """Every ``values.get`` of the run was a formatted read."""
    reads = value_reads(grid)
    return bool(reads) and render_options(reads) == [FORMATTED] * len(reads)


class TestFirstRun:
    """A local file holding what the sheet displays is in step with the tab."""

    def test_a_sync_bootstrap_reports_no_edit(self, tmp_path):
        target = make_tab(tmp_path, render="formatted")
        write_local(target.tabs[0], *SHOWN)
        report = sync_tab(shown_grid(), "S", target, target.tabs[0])
        assert report.bootstrapped and report.error is None
        plan = report.plan
        assert plan is not None and not plan.has_writes
        assert not plan.needs_attention

    def test_the_default_reads_each_formatted_cell_as_a_sheet_edit(self, tmp_path):
        target = make_tab(tmp_path)
        write_local(target.tabs[0], *SHOWN)
        report = sync_tab(shown_grid(), "S", target, target.tabs[0])
        assert report.plan is not None
        assert [(c.local, c.sheet) for c in report.plan.fold_cells] == [
            ("50%", "0.5"),
            ("25%", "0.25"),
        ]

    def test_a_pull_reports_no_change(self, tmp_path):
        tab = make_tab(tmp_path, "pull", key=["id"], render="formatted").tabs[0]
        write_local(tab, *SHOWN)
        report = pull_tab(shown_grid(), "S", tab, apply=True)
        assert report.replacement is not None and report.replacement.unchanged
        assert not report.wrote_local

    def test_a_push_reports_no_change_and_writes_nothing(self, tmp_path):
        tab = make_tab(tmp_path, "push", render="formatted").tabs[0]
        write_local(tab, *SHOWN)
        grid = shown_grid()
        report = push_tab(grid, "S", tab, apply=True)
        assert report.replacement is not None and report.replacement.unchanged
        assert not report.wrote_sheet and grid.methods == [
            "spreadsheets.get",
            "values.get",
        ]
        assert all_formatted(grid)


class TestEveryRead:
    """Every read a ``formatted`` run makes sends ``FORMATTED_VALUE``."""

    def test_the_guard_and_the_read_back_of_a_sync(self, tmp_path):
        target = make_tab(tmp_path, render="formatted")
        write_local(target.tabs[0], ["a", "Ada", "75%"], SHOWN[1], ["c", "Cy", "5%"])
        write_base(target, *SHOWN)
        grid = shown_grid()
        report = sync_tab(grid, "S", target, target.tabs[0], apply=True)
        assert report.error is None and report.wrote_sheet
        assert report.applied is not None and report.applied.pushed == 1
        # Preview, guard, and read-back: the cell written reads back as written.
        assert len(value_reads(grid)) == 3 and all_formatted(grid)
        assert grid.values("T") == [
            HEADER,
            ["a", "Ada", "75%"],
            ["b", "Bo", 0.25],
            ["c", "Cy", "5%"],
        ]

    def test_the_guard_compares_displayed_text(self, tmp_path):
        target = make_tab(tmp_path, render="formatted")
        write_local(target.tabs[0], ["a", "Ada", "75%"], SHOWN[1])
        write_base(target, *SHOWN)
        grid = shown_grid()
        # A new number format changes what the sheet displays, and so the read.
        grid.edit_externally(
            lambda g: g.display("T", 3, 3, 0.25, "0.25"),
            before="values.get",
            occurrence=2,
        )
        with pytest.raises(SheetChangedError, match="rows edited"):
            sync_tab(grid, "S", target, target.tabs[0], apply=True)
        assert grid.values("T")[1] == ["a", "Ada", 0.5]

    def test_the_header_reads_of_a_restructure_and_of_widths(self, tmp_path):
        target = make_tab(tmp_path, render="formatted", widths={"name": 120})
        header = [*HEADER, "note"]
        write_local(target.tabs[0], [*SHOWN[0], "x"], [*SHOWN[1], ""], header=header)
        write_base(target, [*SHOWN[0], ""], [*SHOWN[1], ""], header=header)
        grid = shown_grid()
        grid.write("T", [[*HEADER, "old"]])
        report = sync_tab(
            grid,
            "S",
            target,
            target.tabs[0],
            apply=True,
            add_missing=True,
            drop_extra=True,
        )
        assert report.error is None and report.wrote_widths
        ranges = [read["range"] for read in value_reads(grid)]
        assert ranges.count("'T'!1:1") == 3  # place, delete, and widths
        assert all_formatted(grid)
        assert grid.values("T")[0] == header

    def test_the_header_read_of_a_created_tab(self, tmp_path):
        target = make_tab(tmp_path, render="formatted")
        write_local(target.tabs[0], *SHOWN)
        grid = FakeSheetGrid({"Other": []})
        report = sync_tab(grid, "S", target, target.tabs[0], apply=True)
        assert report.error is None and report.wrote_sheet
        assert "'T'!1:1" in [read["range"] for read in value_reads(grid)]
        assert all_formatted(grid)
        assert grid.values("T") == [HEADER, *SHOWN]

    def test_a_push_writes_raw_and_reads_back_what_it_wrote(self, tmp_path):
        tab = make_tab(tmp_path, "push", render="formatted", widths={"name": 120}).tabs[
            0
        ]
        write_local(tab, ["a", "Ada", "75%"], SHOWN[1])
        grid = shown_grid()
        report = push_tab(grid, "S", tab, apply=True)
        assert report.wrote_sheet and report.wrote_widths
        update = next(kw for m, kw in grid.calls if m == "values.update")
        assert update["valueInputOption"] == "RAW"
        # The read of the tab, the read before the write, the read-back, and
        # the header read of the widths.
        assert len(value_reads(grid)) == 4 and all_formatted(grid)
        assert grid.values("T") == [HEADER, ["a", "Ada", "75%"], ["b", "Bo", "25%"]]

    def test_push_rows_takes_the_render(self):
        grid = shown_grid()
        report = push_rows(
            grid,
            "S",
            "T",
            HEADER,
            [dict(zip(HEADER, row)) for row in SHOWN],
            render="formatted",
        )
        assert report.replacement is not None and report.replacement.unchanged
        assert all_formatted(grid)

    def test_push_rows_refuses_another_render_before_any_request(self):
        grid = shown_grid()
        with pytest.raises(ValueError, match="render must be one of"):
            push_rows(grid, "S", "T", HEADER, [{"id": "a"}], render="displayed")
        assert grid.calls == []


class TestDeclaredTypes:
    SCHEMA = {"amt": {"type": "float"}}
    HEADER = ["id", "amt"]

    def grid(self):
        grid = FakeSheetGrid({"T": [self.HEADER, ["a"], ["b", 2]]})
        grid.display("T", 2, 2, 1234.5, "1,234.50")
        return grid

    def test_a_pull_of_a_date_column_reads_it_as_iso_8601(self, tmp_path):
        tab = make_tab(
            tmp_path, "pull", render="formatted", schema={"when": {"type": "date"}}
        ).tabs[0]
        grid = FakeSheetGrid({"T": [["id", "pct", "when"]]})
        grid.write("T", [["a", 0.5, date(2024, 1, 2)]], row=2)
        grid.display("T", 2, 2, 0.5, "50%")
        pull_tab(grid, "S", tab, apply=True)
        assert rows_of(tab.local) == [["a", "50%", "2024-01-02"]]

    def test_a_displayed_number_that_does_not_parse_is_a_problem(self, tmp_path):
        tab = make_tab(tmp_path, "pull", render="formatted", schema=self.SCHEMA).tabs[0]
        report = pull_tab(self.grid(), "S", tab, apply=True)
        assert report.problems == [
            "T (sheet): row 1, column 'amt': '1,234.50' is not a valid float"
        ]
        assert not report.wrote_local

    def test_a_sync_refuses_it_by_default(self, tmp_path):
        target = make_tab(tmp_path, render="formatted", schema=self.SCHEMA)
        rows = [["a", "1234.5"], ["b", "2"]]
        write_local(target.tabs[0], *rows, header=self.HEADER)
        write_base(target, *rows, header=self.HEADER)
        report = sync_tab(self.grid(), "S", target, target.tabs[0], apply=True)
        assert report.problems == [
            "T (merged): key ('a',), column 'amt': '1,234.50' is not a valid float"
        ]

    def test_a_sync_holds_it_under_hold(self, tmp_path):
        target = make_tab(
            tmp_path, render="formatted", schema=self.SCHEMA, on_invalid="hold"
        )
        rows = [["a", "1234.5"], ["b", "2"]]
        write_local(target.tabs[0], *rows, header=self.HEADER)
        write_base(target, *rows, header=self.HEADER)
        report = sync_tab(self.grid(), "S", target, target.tabs[0], apply=True)
        assert report.problems == [] and report.plan is not None
        assert [(h.key, h.sheet) for h in report.plan.held] == [(("a",), "1,234.50")]
        assert rows_of(target.tabs[0].local) == rows
