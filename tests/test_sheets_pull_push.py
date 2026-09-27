"""Tests for gdrives.sheets.sync's whole-tab modes, the dump, runs, and reports.

``pull_tab`` replaces a local file with a tab, ``push_tab`` replaces a tab's
values with a local file, ``pull_all_tabs`` dumps every tab with no config,
and ``run_target`` runs the tabs of one mode. Each runs against
``FakeSheetGrid`` and files under ``tmp_path``; the tests assert the sheet,
the file bytes, and the calls a run leaves behind.
"""

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error

from gdrives.sheets import (
    CONFIG_NAME,
    ApplyResult,
    Cell,
    CheckContext,
    HeldCell,
    MergePlan,
    NewRow,
    Override,
    ReadBackError,
    Replacement,
    RowFlag,
    SheetChangedError,
    SyncReport,
    TabReport,
    format_report,
    parse_config,
    pull_all_tabs,
    pull_tab,
    push_tab,
    read_records,
    run_target,
    write_values_csv,
)

HEADER = ["id", "name", "amt"]
ROWS = [["a", "Ada", "1"], ["b", "Bo", "2"]]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}


def make_target(tmp_path, tabs=None, **fields):
    """A target whose tabs default to one push tab ``T`` over local.csv."""
    tabs = tabs or {"T": {"mode": "push", "local": "local.csv"}}
    data = {"t": {"spreadsheet": "S", "tabs": tabs} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def one_tab(tmp_path, mode, **fields):
    tab = {"mode": mode, "local": "local.csv"} | fields
    return make_target(tmp_path, {"T": tab}).tabs[0]


def write_local(tab, *rows, header=HEADER):
    write_values_csv(str(tab.local), [header, *rows])


def rows_of(path):
    return [list(row.values()) for row in read_records(path).rows]


def writes(grid):
    return [method for method in grid.methods if method not in READS]


def replacement_of(report):
    assert report.replacement is not None
    return report.replacement


# -- push --


class TestPushPreview:
    def test_reports_what_the_sheet_loses_by_key(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["id"])
        write_local(tab, ["a", "Ada", "9"], ["b", "Bo", "2"])
        grid = FakeSheetGrid(
            {
                "T": [
                    HEADER + ["notes"],
                    ["a", "Ada", "1", "n"],
                    ["b", "Bo", "2"],
                    ["z", "Z"],
                ]
            }
        )
        report = push_tab(grid, "S", tab)
        assert replacement_of(report) == Replacement(
            before_rows=3,
            after_rows=2,
            before_cells=9,
            keyed=True,
            added=[],
            removed=[("z",)],
            changed=[("a",)],
            dropped_columns={"notes": 1},
            unchanged=False,
        )
        assert replacement_of(report).row_drop == 1
        assert writes(grid) == []
        assert grid.values("T")[3] == ["z", "Z"]

    def test_without_a_key_only_counts(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS, ["c", "", "3"]]})
        change = replacement_of(push_tab(grid, "S", tab))
        assert (
            change.keyed,
            change.removed,
            change.before_rows,
            change.before_cells,
        ) == (
            False,
            [],
            3,
            8,
        )

    def test_a_repeated_sheet_header_gives_counts_only(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["id"])
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [["id", "id"], ["a", "b"]]})
        change = replacement_of(push_tab(grid, "S", tab))
        assert (change.keyed, change.before_rows, change.dropped_columns) == (
            False,
            1,
            {},
        )

    def test_a_missing_tab_is_reported(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        report = push_tab(grid, "S", tab)
        assert report.tab_state == "missing"
        assert grid.methods == ["spreadsheets.get"]


class TestPushApply:
    def test_one_update_over_the_old_extent_replaces_the_tab(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, ["a", "Ada", "9"])
        grid = FakeSheetGrid(
            {"T": [HEADER + ["notes"], ["a", "Ada", "1", "n"], ["b", "Bo", "2"]]}
        )
        report = push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [HEADER, ["a", "Ada", "9"]]
        assert grid.methods == [
            "spreadsheets.get",
            "values.get",
            "values.get",
            "spreadsheets.get",
            "values.update",
            "values.get",
        ]
        (update,) = [
            kwargs for method, kwargs in grid.calls if method == "values.update"
        ]
        assert update["range"] == "'T'!A1:D3"
        assert update["valueInputOption"] == "RAW"
        assert update["body"]["values"] == [
            ["id", "name", "amt", ""],
            ["a", "Ada", "9", ""],
            ["", "", "", ""],
        ]
        assert report.wrote_sheet

    def test_columns_are_written_in_local_file_order(self, tmp_path):
        tab = one_tab(tmp_path, "push", columns=["amt", "id"])
        write_local(tab, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": []})
        push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [["id", "amt"], ["a", "1"]]

    def test_the_grid_grows_first_when_the_data_does_not_fit(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS, ["c", "Cy", "3"])
        grid = FakeSheetGrid({"T": [["x"]]}, rows=2, columns=2)
        push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [HEADER, *ROWS, ["c", "Cy", "3"]]
        assert (grid.tab("T").row_count, grid.tab("T").column_count) == (4, 3)
        grow = [
            kwargs
            for method, kwargs in grid.calls
            if method == "spreadsheets.batchUpdate"
        ]
        assert [next(iter(r)) for r in grow[0]["body"]["requests"]] == [
            "appendDimension",
            "appendDimension",
        ]
        assert writes(grid) == ["spreadsheets.batchUpdate", "values.update"]

    def test_a_missing_tab_is_created(self, tmp_path):
        tab = one_tab(tmp_path, "push", widths={"name": 180})
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        report = push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [HEADER, *ROWS]
        assert grid.tab("T").widths[1] == 180
        assert report.wrote_sheet and report.wrote_widths

    def test_an_unchanged_tab_is_not_written(self, tmp_path):
        tab = one_tab(tmp_path, "push", widths={"name": 180})
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", 1], ["b", "Bo", 2.0]]})
        report = push_tab(grid, "S", tab, apply=True)
        assert replacement_of(report).unchanged
        assert grid.methods == ["spreadsheets.get", "values.get"]
        assert not report.wrote_sheet

    def test_a_tab_changed_since_the_preview_read_is_refused(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, ["a", "Ada", "9"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        grid.edit_externally(
            lambda g: g.write("T", [["c", "Cy"]], row=4),
            before="values.get",
            occurrence=2,
        )
        with pytest.raises(SheetChangedError, match="nothing was written"):
            push_tab(grid, "S", tab, apply=True)
        assert writes(grid) == []

    def test_a_raw_read_back_compares_every_cell(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [["x"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["b", "Bo", "3"]], row=3),
            before="values.get",
            occurrence=3,
        )
        with pytest.raises(ReadBackError, match=r"rows \[3\] differ"):
            push_tab(grid, "S", tab, apply=True)

    def test_a_raw_read_back_catches_an_extra_row(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [["x"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["z"]], row=5), before="values.get", occurrence=3
        )
        with pytest.raises(ReadBackError, match=r"rows \[5\] differ"):
            push_tab(grid, "S", tab, apply=True)

    def test_user_entered_checks_the_header_and_row_count(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [["x"]]})
        report = push_tab(grid, "S", tab, input_option="USER_ENTERED", apply=True)
        (update,) = [
            kwargs for method, kwargs in grid.calls if method == "values.update"
        ]
        assert update["valueInputOption"] == "USER_ENTERED"
        assert report.notes == [
            "USER_ENTERED rewrites values on entry, so the read-back checks the "
            "header and the row count only"
        ]
        # A rewritten value passes; a missing row does not.
        grid = FakeSheetGrid({"T": [["x"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["b", "Bo", 2]], row=3),
            before="values.get",
            occurrence=3,
        )
        push_tab(grid, "S", tab, input_option="USER_ENTERED", apply=True)
        grid = FakeSheetGrid({"T": [["x"]]})
        grid.edit_externally(
            lambda g: g.write("T", [["", "", ""]], row=3),
            before="values.get",
            occurrence=3,
        )
        with pytest.raises(
            ReadBackError,
            match=r"wrote header \['id', 'name', 'amt'\] and 2 rows, read header "
            r"\['id', 'name', 'amt'\] and 1 rows",
        ):
            push_tab(grid, "S", tab, input_option="USER_ENTERED", apply=True)

    def test_user_entered_read_back_of_an_emptied_tab(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [["x"]]})
        grid.edit_externally(
            lambda g: g.write("T", [[""] * 3] * 3), before="values.get", occurrence=3
        )
        with pytest.raises(ReadBackError, match=r"read header \[\] and 0 rows"):
            push_tab(grid, "S", tab, input_option="USER_ENTERED", apply=True)

    def test_a_failed_update_leaves_the_tab_whole(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, ["a", "Ada", "9"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        grid.fail("values.update", http_error(400, "boom"))
        with pytest.raises(HttpError):
            push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [HEADER, *ROWS]


class TestPushRefusals:
    def test_an_empty_local_file(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        with pytest.raises(ValueError, match="has no rows"):
            push_tab(grid, "S", tab, apply=True)
        assert grid.calls == []

    def test_a_missing_local_file(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            push_tab(FakeSheetGrid(), "S", one_tab(tmp_path, "push"))

    def test_a_configured_column_the_file_lacks(self, tmp_path):
        tab = one_tab(tmp_path, "push", columns=["id", "zz"])
        write_local(tab, *ROWS)
        with pytest.raises(ValueError, match=r"lacks column\(s\) \['zz'\]"):
            push_tab(FakeSheetGrid(), "S", tab)

    def test_a_duplicate_local_key(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["id"])
        write_local(tab, ROWS[0], ROWS[0])
        with pytest.raises(ValueError, match="duplicate key"):
            push_tab(FakeSheetGrid(), "S", tab)

    def test_schema_and_validate_problems_write_nothing(self, tmp_path):
        tab = one_tab(tmp_path, "push", schema={"amt": {"type": "int"}})
        write_local(tab, ["a", "Ada", "x"])
        grid = FakeSheetGrid({"T": [HEADER]})
        report = push_tab(grid, "S", tab, apply=True, validate=lambda rows: ["bad"])
        assert report.problems == [
            "T (local): row 1, column 'amt': 'x' is not a valid int",
            "T (local): bad",
        ]
        assert grid.calls == [] and report.exit_code == 1


class TestHooks:
    def test_a_pull_checks_the_rows_the_sheet_holds(self, tmp_path):
        tab = one_tab(tmp_path, "pull", columns=["id", "amt"])
        grid = FakeSheetGrid({"T": [[*HEADER, "", "memo"], *ROWS]})
        seen = []
        report = pull_tab(
            grid,
            "S",
            tab,
            apply=True,
            check=lambda context: seen.append(context) or [],
            warn=lambda context: [f"{len(context.rows)} rows at {context.stage}"],
        )
        assert seen == [
            CheckContext(
                tab="T",
                stage="sheet",
                rows=[{"id": "a", "amt": "1"}, {"id": "b", "amt": "2"}],
                columns=("id", "amt"),
                projection=("id", "amt"),
                sheet_columns=("id", "name", "amt", "memo"),
                adding=(),
                dropping=(),
                plan=None,
            )
        ]
        assert seen[0].extra_columns == ("name", "memo")
        assert report.warnings == ["2 rows at sheet"]
        assert report.wrote_local and report.exit_code == 0

    def test_a_pull_with_a_problem_writes_nothing_and_warns_of_nothing(self, tmp_path):
        tab = one_tab(tmp_path, "pull")
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        warned = []
        report = pull_tab(
            grid,
            "S",
            tab,
            apply=True,
            validate=lambda rows: ["from validate"],
            check=lambda context: ["from check"],
            warn=lambda context: warned.append(context) or ["a warning"],
        )
        assert report.problems == ["T (sheet): from validate", "T (sheet): from check"]
        assert warned == [] and report.warnings == []
        assert not tab.local.exists()

    def test_a_push_checks_the_local_rows_before_any_request(self, tmp_path):
        tab = one_tab(tmp_path, "push", columns=["id", "amt"])
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": []})
        seen = []
        report = push_tab(
            grid,
            "S",
            tab,
            apply=True,
            check=lambda context: seen.append((context, list(grid.calls))) or [],
            warn=lambda context: [f"pushing {list(context.projection)}"],
        )
        ((context, calls),) = seen
        assert calls == []
        assert context == CheckContext(
            tab="T",
            stage="local",
            rows=[dict(zip(HEADER, row, strict=True)) for row in ROWS],
            columns=tuple(HEADER),
            projection=("id", "amt"),
            sheet_columns=None,
            adding=(),
            dropping=(),
            plan=None,
        )
        assert report.warnings == ["pushing ['id', 'amt']"]
        assert grid.values("T") == [["id", "amt"], ["a", "1"], ["b", "2"]]

    def test_a_push_with_a_problem_writes_nothing_and_warns_of_nothing(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": []})
        warned = []
        report = push_tab(
            grid,
            "S",
            tab,
            apply=True,
            check=lambda context: ["from check"],
            warn=lambda context: warned.append(context) or ["a warning"],
        )
        assert report.problems == ["T (local): from check"]
        assert warned == [] and report.warnings == [] and grid.calls == []

    @pytest.mark.parametrize("mode", ["sync", "pull", "push"])
    def test_run_target_passes_the_hooks_to_every_mode(self, tmp_path, mode):
        tab = {"mode": mode, "local": "local.csv", "key": ["id"]}
        target = make_target(tmp_path, {"T": tab})
        write_local(target.tabs[0], *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        stages = []
        report = run_target(
            grid,
            "S",
            target,
            mode,
            validate=lambda rows: stages.append("validate") or [],
            check=lambda context: stages.append(f"check {context.stage}") or [],
            warn=lambda context: [f"warn {context.stage}"],
        )
        last = {"sync": "merged", "pull": "sheet", "push": "local"}[mode]
        assert stages[-2:] == ["validate", f"check {last}"]
        assert report.tabs[0].warnings == [f"warn {last}"]
        assert report.exit_code == 0

    def test_warnings_are_printed_after_problems_and_change_no_exit_code(self):
        report = TabReport(
            tab="T", mode="push", problems=["bad"], warnings=["look\x1b[2J", "again"]
        )
        assert format_report(SyncReport([report])).splitlines() == [
            "push tab 'T' (preview)",
            "  problems (1), so nothing is written:",
            "    bad",
            "  warnings (2):",
            "    look\\x1b[2J",
            "    again",
        ]
        quiet = TabReport(tab="T", mode="sync", plan=MergePlan(), warnings=["look"])
        assert quiet.exit_code == 0 and not quiet.failed
        assert format_report(SyncReport([quiet])).splitlines() == [
            "sync tab 'T' (preview)",
            "  warnings (1):",
            "    look",
            "  in sync: nothing to write",
        ]


class TestPushPartialKeys:
    HEADER = ["y", "id", "v"]
    ROWS = [["2026", "", "a"], ["", "1", "b"]]

    def test_a_blank_component_is_refused_by_default(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["y", "id"])
        write_local(tab, *self.ROWS, header=self.HEADER)
        grid = FakeSheetGrid({"T": []})
        with pytest.raises(
            ValueError, match=r"blank key \['y', 'id'\] in rows \[1, 2\]"
        ):
            push_tab(grid, "S", tab, apply=True)
        assert writes(grid) == []

    def test_partial_pushes_the_rows(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["y", "id"], blank_keys="partial")
        write_local(tab, *self.ROWS, header=self.HEADER)
        grid = FakeSheetGrid({"T": [self.HEADER, ["2026", "", "old"]]})
        report = push_tab(grid, "S", tab, apply=True)
        assert grid.values("T") == [self.HEADER, *self.ROWS]
        assert replacement_of(report).changed == [("2026", "")]
        assert replacement_of(report).added == [("", "1")]


# -- pull --


class TestPull:
    def test_preview_compares_with_the_local_file_by_key(self, tmp_path):
        tab = one_tab(tmp_path, "pull", key=["id"])
        write_local(tab, ["a", "Ada", "1"], ["b", "Bo", "2"], ["c", "Cy", "3"])
        before = tab.local.read_bytes()
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "5"], ["d", "Di", 4]]})
        report = pull_tab(grid, "S", tab)
        assert replacement_of(report) == Replacement(
            before_rows=3,
            after_rows=2,
            before_cells=9,
            keyed=True,
            added=[("d",)],
            removed=[("b",), ("c",)],
            changed=[("a",)],
            dropped_columns={},
            unchanged=False,
        )
        assert replacement_of(report).row_drop == 1
        assert tab.local.read_bytes() == before
        assert writes(grid) == []

    def test_apply_replaces_the_local_file(self, tmp_path):
        tab = one_tab(tmp_path, "pull", bom=True)
        write_local(tab, ["a", "Ada", "1", "memo"], header=HEADER + ["memo"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", 5], ["b", "Bo", True]]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert tab.local.read_bytes() == (
            b"\xef\xbb\xbfid,name,amt\na,Ada,5\nb,Bo,TRUE\n"
        )
        assert replacement_of(report).dropped_columns == {"memo": 1}
        assert report.wrote_local and writes(grid) == []

    def test_configured_columns_are_pulled_in_that_order(self, tmp_path):
        tab = one_tab(tmp_path, "pull", columns=["amt", "id"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        pull_tab(grid, "S", tab, apply=True)
        assert tab.local.read_bytes() == b"amt,id\n1,a\n2,b\n"

    @pytest.mark.parametrize("bom", [False, True])
    def test_newline_crlf_is_written_on_request(self, tmp_path, bom):
        tab = one_tab(tmp_path, "pull", newline="crlf", bom=bom)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        pull_tab(grid, "S", tab, apply=True)
        mark = b"\xef\xbb\xbf" if bom else b""
        assert tab.local.read_bytes() == (
            mark + b"id,name,amt\r\na,Ada,1\r\nb,Bo,2\r\n"
        )

    def test_a_declared_date_column_is_pulled_as_iso(self, tmp_path):
        from datetime import date, datetime

        tab = one_tab(
            tmp_path,
            "pull",
            schema={"on": {"type": "date"}, "at": {"type": "datetime"}},
        )
        rows = [
            ["a", date(2026, 9, 27), datetime(2026, 9, 27, 23, 59, 59, 999000)],
            ["b", "2026-09-28", date(2026, 9, 28)],
        ]
        grid = FakeSheetGrid({"T": [["id", "on", "at"], *rows]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == [] and report.wrote_local
        assert rows_of(tab.local) == [
            ["a", "2026-09-27", "2026-09-27 23:59:59.999000"],
            ["b", "2026-09-28", "2026-09-28 00:00:00"],
        ]
        assert grid.methods == ["spreadsheets.get", "values.get", "values.batchGet"]

    def test_a_pull_with_no_declared_date_makes_the_reads_it_made(self, tmp_path):
        tab = one_tab(tmp_path, "pull", schema={"amt": {"type": "int"}})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        pull_tab(grid, "S", tab, apply=True)
        assert grid.methods == ["spreadsheets.get", "values.get"]

    def test_partial_keys(self, tmp_path):
        header = ["y", "id", "v"]
        rows = [["2026", "", "a"], ["", "1", "b"]]
        grid = FakeSheetGrid({"T": [header, *rows]})
        strict = one_tab(tmp_path, "pull", key=["y", "id"])
        with pytest.raises(
            ValueError, match=r"blank key \['y', 'id'\] in rows \[2, 3\]"
        ):
            pull_tab(grid, "S", strict, apply=True)
        tab = one_tab(tmp_path, "pull", key=["y", "id"], blank_keys="partial")
        pull_tab(grid, "S", tab, apply=True)
        assert rows_of(tab.local) == rows
        grid.write("T", [["2027", "", "c"]], row=4)
        report = pull_tab(grid, "S", tab)
        assert replacement_of(report).added == [("2027", "")]

    def test_a_missing_local_file_is_created(self, tmp_path):
        tab = one_tab(tmp_path, "pull", local="out/deep/t.json")
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert rows_of(tab.local) == ROWS
        assert replacement_of(report).before_rows == 0

    def test_an_unchanged_file_is_not_rewritten(self, tmp_path):
        tab = one_tab(tmp_path, "pull")
        write_local(tab, *ROWS)
        mtime = tab.local.stat().st_mtime_ns
        report = pull_tab(FakeSheetGrid({"T": [HEADER, *ROWS]}), "S", tab, apply=True)
        assert replacement_of(report).unchanged and not report.wrote_local
        assert tab.local.stat().st_mtime_ns == mtime

    @pytest.mark.parametrize(
        ("tabs", "message", "state"),
        [
            ({"Other": []}, "no tab named 'T'", "missing"),
            ({"T": []}, "has no header row", "empty"),
            ({"T": [HEADER]}, "has no rows", "present"),
        ],
    )
    def test_an_empty_or_missing_tab_leaves_the_local_file(
        self, tmp_path, tabs, message, state
    ):
        tab = one_tab(tmp_path, "pull")
        write_local(tab, *ROWS)
        before = tab.local.read_bytes()
        report = TabReport(tab="T", mode="pull")
        with pytest.raises(
            ValueError, match=f"{message}.*the local file is left alone"
        ):
            pull_tab(FakeSheetGrid(tabs), "S", tab, apply=True, report=report)
        assert tab.local.read_bytes() == before
        assert report.tab_state == state

    def test_schema_problems_write_nothing(self, tmp_path):
        tab = one_tab(tmp_path, "pull", schema={"amt": {"allowed": ["1"]}})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (sheet): row 2, column 'amt': '2' is not one of ['1']"
        ]
        assert not tab.local.exists()


# -- the one-off dump --


class TestPullAllTabs:
    def grid(self):
        return FakeSheetGrid(
            {
                "Members": [HEADER, *ROWS],
                "a/b": [["x"], ["1"]],
                "Blank": [],
                "Headless": [[], ["v"]],
                "..": [["k"], ["v"]],
            }
        )

    def test_writes_one_file_per_tab_from_one_read(self, tmp_path):
        grid = self.grid()
        out = tmp_path / "out"
        report = pull_all_tabs(grid, "S", out, apply=True)
        assert grid.methods == ["spreadsheets.get", "values.batchGet"]
        assert sorted(p.name for p in out.iterdir()) == [
            "Members.csv",
            "__.csv",
            "a_b.csv",
        ]
        assert rows_of(out / "Members.csv") == ROWS
        assert (out / "Members.csv").read_bytes() == b"id,name,amt\na,Ada,1\nb,Bo,2\n"
        assert rows_of(out / "a_b.csv") == [["1"]]
        by_tab = {tab.tab: tab for tab in report.tabs}
        assert by_tab["Blank"].skipped and by_tab["Blank"].notes == [
            "no values; skipped"
        ]
        assert by_tab["Headless"].notes == ["no header row; skipped"]
        assert by_tab["Members"].wrote_local
        assert report.exit_code == 0

    def test_a_preview_writes_nothing_and_creates_no_directory(self, tmp_path):
        report = pull_all_tabs(self.grid(), "S", tmp_path / "out")
        assert not (tmp_path / "out").exists()
        assert replacement_of(report.tabs[0]).after_rows == 2

    def test_skip_leaves_a_tab_out(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "Members.csv").write_text("made elsewhere")
        pull_all_tabs(self.grid(), "S", out, skip=["Members"], apply=True)
        assert (out / "Members.csv").read_text() == "made elsewhere"

    def test_an_unknown_skip_is_refused(self, tmp_path):
        grid = self.grid()
        with pytest.raises(ValueError, match=r"no tab\(s\) named \['Member'\] to skip"):
            pull_all_tabs(grid, "S", tmp_path, skip=["Member"])
        assert grid.methods == ["spreadsheets.get"]

    def test_colliding_file_names_are_refused_before_any_read(self, tmp_path):
        grid = FakeSheetGrid({"a/b": [["x"]], "a\\b": [["y"]], "N": [["z"]], "n": []})
        with pytest.raises(ValueError) as raised:
            pull_all_tabs(grid, "S", tmp_path, apply=True)
        assert str(raised.value) == (
            "tabs whose file names collide: ['a/b', 'a\\\\b']; ['N', 'n'] "
            "(skip one of each)"
        )
        assert grid.methods == ["spreadsheets.get"]
        assert list(tmp_path.iterdir()) == []

    def test_json_extension(self, tmp_path):
        pull_all_tabs(
            FakeSheetGrid({"T": [HEADER, *ROWS]}),
            "S",
            tmp_path,
            extension=".json",
            apply=True,
        )
        assert rows_of(tmp_path / "T.json") == ROWS

    def test_an_unknown_extension_is_refused(self, tmp_path):
        grid = FakeSheetGrid()
        with pytest.raises(ValueError, match="extension '.xlsx' must be one of"):
            pull_all_tabs(grid, "S", tmp_path, extension=".xlsx")
        assert grid.calls == []

    def test_a_bad_tab_is_reported_and_the_rest_written(self, tmp_path):
        grid = FakeSheetGrid({"Dup": [["a", "a"]], "Wide": [["h"], ["1", "2"]]})
        report = pull_all_tabs(grid, "S", tmp_path, apply=True)
        dup, wide = report.tabs
        assert dup.error == "tab 'Dup': header repeats ['a']"
        assert wide.notes == [
            "rows [2] hold cells past the header, which are not written"
        ]
        assert rows_of(tmp_path / "Wide.csv") == [["1"]]
        assert report.exit_code == 1

    def test_an_unchanged_file_is_not_rewritten(self, tmp_path):
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        pull_all_tabs(grid, "S", tmp_path, apply=True)
        mtime = (tmp_path / "T.csv").stat().st_mtime_ns
        report = pull_all_tabs(grid, "S", tmp_path, apply=True)
        assert not report.tabs[0].wrote_local
        assert (tmp_path / "T.csv").stat().st_mtime_ns == mtime


# -- whole targets --


class TestRunTarget:
    @staticmethod
    def target(tmp_path, **fields):
        tabs = {
            "Members": {"mode": "sync", "local": "m.csv", "key": ["id"]},
            "Dues": {"mode": "sync", "local": "d.csv", "key": ["id"]},
            "Summary": {"mode": "push", "local": "s.csv"},
            "Rates": {"mode": "pull", "local": "r.csv"},
        }
        target = make_target(tmp_path, tabs, **fields)
        for tab in target.tabs:
            if tab.mode != "pull":
                write_local(tab, *ROWS)
        return target

    def grid(self):
        return FakeSheetGrid(
            {name: [HEADER, *ROWS] for name in ("Members", "Dues", "Summary", "Rates")}
        )

    def test_runs_the_tabs_of_one_mode(self, tmp_path):
        target = self.target(tmp_path)
        report = run_target(self.grid(), "S", target, "sync", apply=True)
        assert report.target == "t"
        assert [(t.tab, t.mode, t.apply) for t in report.tabs] == [
            ("Members", "sync", True),
            ("Dues", "sync", True),
        ]
        assert report.exit_code == 0

    def test_pull_and_push(self, tmp_path):
        tabs = {
            "Summary": {"mode": "push", "local": "s.csv"},
            "Rates": {"mode": "pull", "local": "r.csv"},
        }
        target = make_target(tmp_path, tabs, input_option="USER_ENTERED")
        write_local(target.tab("Summary"), *ROWS)
        grid = self.grid()
        grid.write("Summary", [["c", "Cy", "3"]], row=4)
        pushed = run_target(grid, "S", target, "push", apply=True)
        assert pushed.tabs[0].notes[0].startswith("USER_ENTERED")
        assert grid.values("Summary") == [HEADER, *ROWS]
        pulled = run_target(grid, "S", target, "pull", apply=True)
        assert pulled.tabs[0].wrote_local
        assert rows_of(target.tab("Rates").local) == ROWS

    def test_selected_tabs(self, tmp_path):
        target = self.target(tmp_path)
        report = run_target(self.grid(), "S", target, tabs=["Dues"])
        assert [t.tab for t in report.tabs] == ["Dues"]

    def test_a_failing_tab_is_reported_and_the_next_runs(self, tmp_path):
        target = self.target(tmp_path)
        grid = self.grid()
        grid.write("Members", [["a", "Ada", "9"]], row=2)
        write_local(target.tab("Members"), ["a", "Ada", "1"], ["b", "Bo", "5"])
        (tmp_path / "sheets-base" / "t").mkdir(parents=True)
        write_values_csv(str(target.base_path(target.tab("Members"))), [HEADER, *ROWS])
        grid.edit_externally(
            lambda g: g.write("Members", [["b", "Bo", "6"]], row=3),
            before="values.get",
            occurrence=3,
        )
        report = run_target(grid, "S", target, apply=True)
        members, dues = report.tabs
        assert members.error is not None and "read-back" in members.error
        assert members.wrote_sheet and members.applied is None
        assert not (members.wrote_local or members.wrote_base)
        assert rows_of(target.tab("Members").local) == [
            ["a", "Ada", "1"],
            ["b", "Bo", "5"],
        ]
        assert dues.error is None and dues.wrote_base
        assert report.exit_code == 1

    @pytest.mark.parametrize(
        ("mode", "options", "message"),
        [
            ("merge", {}, "mode must be one of"),
            ("pull", {"adopt": True}, "apply only to sync tabs"),
            ("push", {"prefer": "local"}, "apply only to sync tabs"),
            ("sync", {"prefer": "mine"}, "prefer must be one of"),
            ("sync", {"tabs": ["Nope"]}, "has no tab 'Nope'"),
            ("sync", {"tabs": ["Summary"]}, r"tab\(s\) \['Summary'\] .* not sync tabs"),
        ],
    )
    def test_refusals_before_any_request(self, tmp_path, mode, options, message):
        grid = self.grid()
        with pytest.raises(ValueError, match=message):
            run_target(grid, "S", self.target(tmp_path), mode, **options)
        assert grid.calls == []

    def test_a_target_with_no_tabs_of_the_mode(self, tmp_path):
        target = make_target(tmp_path)
        with pytest.raises(ValueError, match="target 't' has no sync tabs"):
            run_target(FakeSheetGrid(), "S", target)

    def test_exit_codes(self):
        ok = TabReport(tab="a", mode="push")
        failed = TabReport(tab="b", mode="push", error="boom")
        person = TabReport(
            tab="c",
            mode="sync",
            plan=MergePlan(row_flags=[RowFlag(("x",), "remote_deleted")]),
        )
        problem = TabReport(tab="d", mode="push", problems=["bad"])
        assert SyncReport([ok]).exit_code == 0
        assert SyncReport([ok, person]).exit_code == 2
        assert SyncReport([person, failed]).exit_code == 1
        assert SyncReport([problem]).exit_code == 1
        assert SyncReport().exit_code == 0


# -- the text report --


class TestFormatReport:
    def test_a_sync_report_lists_every_section(self):
        cell = Cell(key=("a",), column="amt", base="1", local="2", sheet="3")
        plan = MergePlan(
            pushes=[cell],
            appends=[NewRow(("n",), {})],
            fold_cells=[cell],
            fold_rows=[NewRow(("f",), {})],
            conflicts=[cell],
            overrides=[
                Override(("a",), "amt", "1", "2", "3", "local", "local_owned"),
                Override(("b",), "amt", "1", "2", "3", "sheet", "prefer"),
            ],
            row_flags=[RowFlag(("z",), "local_deleted")],
        )
        report = TabReport(
            tab="Members",
            mode="sync",
            apply=True,
            plan=plan,
            deferred=[cell],
            add_columns=["new"],
            drop_columns={"old": 3},
            bootstrapped=True,
            adopted=True,
            tab_state="empty",
            wrote_sheet=True,
            wrote_base=True,
            notes=["a note"],
        )
        text = format_report(SyncReport([report]))
        assert text.splitlines() == [
            "sync tab 'Members' (apply)",
            "  note: a note",
            "  the tab has no header row: one written",
            "  columns added: 'new'",
            "  columns deleted, with their data: 'old' (3 non-blank cells)",
            "  bootstrapped: there is no base yet, so the local file was taken as the "
            "base; nothing is written to the sheet on this run. Sheet edits fold in, "
            "and local rows the sheet lacks are flagged remote_deleted. To write "
            "local-only rows to the sheet, run with --adopt instead (it is refused "
            "once a base is saved).",
            "  adopt: the local file wins every difference on the sheet; sheet-only "
            "rows are flagged, never removed",
            "  push to the sheet (1):",
            "    a / 'amt': '3' -> '2'",
            "  fold into the local file (1):",
            "    a / 'amt': '2' -> '3'",
            "  conflicts, left as they are (1):",
            "    a / 'amt': base '1', local '2', sheet '3'",
            "  held back to the next run (1):",
            "    a / 'amt': '3' -> '2'",
            "  new rows for the sheet (1): n",
            "  new rows for the local file (1): f",
            "  overrides (2):",
            "    a / 'amt': kept local (local_owned), discarded '3'",
            "    b / 'amt': kept sheet (prefer), discarded '2'",
            "  row flags (1), left for a person:",
            "    z: local_deleted",
            "  wrote: sheet, base",
        ]

    @pytest.mark.parametrize(
        ("fields", "where"),
        [
            ({"insert_row": 5, "last_row": 40}, ", above row 5"),
            ({"last_row": 40}, ", after row 40"),
            ({}, ""),
            (
                {
                    "insert_row": 5,
                    "last_row": 40,
                    "apply": True,
                    "applied": ApplyResult(0, 2, [], [5, 6]),
                },
                ", in rows 5 to 6",
            ),
            (
                {"last_row": 40, "apply": True, "applied": ApplyResult(0, 1, [], [41])},
                ", in row 41",
            ),
            ({"apply": True, "applied": ApplyResult(0, 2, [], [41, 42])}, ""),
            # An apply that stopped before the rows went out still says where.
            ({"insert_row": 5, "last_row": 40, "apply": True}, ", above row 5"),
        ],
    )
    def test_new_rows_say_where_they_go(self, fields, where):
        plan = MergePlan(appends=[NewRow(("n",), {}), NewRow(("m",), {})])
        report = TabReport(tab="T", mode="sync", plan=plan, **fields)
        assert format_report(SyncReport([report])).splitlines()[1] == (
            f"  new rows for the sheet (2){where}: n; m"
        )

    def test_a_preview_says_would(self, tmp_path):
        report = TabReport(
            tab="T",
            mode="sync",
            local=tmp_path / "t.csv",
            tab_state="missing",
            add_columns=["x"],
            plan=MergePlan(),
        )
        lines = format_report(SyncReport([report])).splitlines()
        assert lines == [
            "sync tab 'T' (preview)",
            f"  local file: {tmp_path / 't.csv'}",
            "  the tab does not exist: would be created with a header row",
            "  columns would be added: 'x'",
        ]

    @pytest.mark.parametrize(
        "plan",
        [
            MergePlan(conflicts=[Cell(("a",), "amt", "1", "2", "3")]),
            MergePlan(row_flags=[RowFlag(("z",), "local_deleted")]),
            MergePlan(held=[HeldCell(("a",), "amt", "1", "1", "x", "bad")]),
        ],
    )
    def test_work_left_for_a_person_is_not_in_sync(self, plan):
        report = TabReport(tab="T", mode="sync", plan=plan)
        assert "in sync" not in format_report(SyncReport([report]))

    def test_row_flags_beside_held_rows(self):
        plan = MergePlan(
            row_flags=[
                RowFlag(("n",), "remote_invalid"),
                RowFlag(("z",), "local_deleted"),
            ],
            held=[HeldCell(("n",), "amt", "", "", "x", "'x' is not a valid int")],
        )
        report = TabReport(tab="T", mode="sync", plan=plan)
        assert format_report(SyncReport([report])).splitlines()[1:] == [
            "  sheet values held, left for a person (1):",
            "    n / 'amt': 'x' is not a valid int",
            "  new sheet rows held for their invalid cells (1): n",
            "  row flags (1), left for a person:",
            "    z: local_deleted",
        ]

    def test_in_sync(self):
        report = TabReport(tab="T", mode="sync", plan=MergePlan())
        assert format_report(SyncReport([report])).splitlines()[-1] == (
            "  in sync: nothing to write"
        )
        push = TabReport(
            tab="T", mode="push", replacement=Replacement(1, 1, unchanged=True)
        )
        assert format_report(SyncReport([push])).splitlines() == [
            "push tab 'T' (preview)",
            "  in sync: nothing to write",
        ]

    def test_a_replacement(self):
        change = Replacement(
            before_rows=30,
            after_rows=2,
            before_cells=90,
            keyed=True,
            added=[("n",)],
            removed=[(str(i),) for i in range(25)],
            changed=[("c", "2")],
            dropped_columns={"notes": 4},
        )
        pull = TabReport(tab="T", mode="pull", apply=True, replacement=change)
        lines = format_report(SyncReport([pull])).splitlines()
        assert lines[1:4] == [
            "  the local file holds 30 rows (90 non-blank cells); it now holds 2",
            "  row count drops by 28",
            "  rows removed (25): "
            + "; ".join(str(i) for i in range(20))
            + "; and 5 more",
        ]
        assert lines[4:] == [
            "  rows added (1): n",
            "  rows changed (1): c, 2",
            "  columns dropped: 'notes' (4 non-blank cells)",
        ]
        push = TabReport(
            tab="T", mode="push", replacement=Replacement(before_rows=1, after_rows=3)
        )
        assert format_report(SyncReport([push])).splitlines()[1] == (
            "  the sheet holds 1 rows (0 non-blank cells); it would hold 3"
        )

    def test_problems_errors_and_skips(self):
        problem = TabReport(tab="T", mode="push", problems=["bad\x1b[2J"])
        error = TabReport(tab="E", mode="pull", error="boom\x07", tab_state="missing")
        skipped = TabReport(
            tab="S", mode="pull", skipped=True, notes=["no values; skipped"]
        )
        text = format_report(SyncReport([problem, error, skipped]))
        assert text.split("\n\n") == [
            "push tab 'T' (preview)\n  problems (1), so nothing is written:\n"
            "    bad\\x1b[2J",
            "pull tab 'E' (preview)\n  error: boom\\x07",
            "pull tab 'S' (preview)\n  note: no values; skipped",
        ]

    def test_sheet_supplied_strings_cannot_drive_the_terminal(self):
        evil = "\x1b]0;x\x07\x9b"
        cell = Cell(key=(evil,), column=evil, base="", local=evil, sheet="")
        report = TabReport(
            tab=evil,
            mode="sync",
            plan=MergePlan(pushes=[cell], row_flags=[RowFlag((evil,), "remote_added")]),
            add_columns=[evil],
            notes=[evil],
        )
        text = format_report(SyncReport([report]))
        assert "\x1b" not in text and "\x07" not in text and "\x9b" not in text
        assert "\\x1b]0;x\\x07\\x9b" in text


class TestBlankCells:
    def test_trailing_blank_cells_and_rows_count_as_nothing(self, tmp_path):
        tab = one_tab(tmp_path, "push")
        write_local(tab, ["a", "Ada", ""], ["b", "Bo", "2"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada"], ["b", "Bo", "2"]]})
        # Cells holding empty strings, as a read can return them.
        grid.tab("T").cells[1][2] = ""
        grid.tab("T").cells[3][0] = ""
        report = push_tab(grid, "S", tab, apply=True)
        assert replacement_of(report).unchanged
        assert writes(grid) == []

    def test_a_keyed_replacement_lists_only_what_it_has(self):
        change = Replacement(before_rows=2, after_rows=1, keyed=True, removed=[("z",)])
        push = TabReport(tab="T", mode="push", replacement=change)
        assert format_report(SyncReport([push])).splitlines()[2:] == [
            "  row count drops by 1",
            "  rows removed (1): z",
        ]
