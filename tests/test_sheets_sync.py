"""Tests for gdrives.sheets.sync: planning and applying a keyed sync of one tab.

Every test runs against ``FakeSheetGrid`` and real files under ``tmp_path``,
and asserts what a run leaves behind: the sheet's cells, the local file and
base file bytes, and the calls made. A preview must make no write of any
kind, and a failed step must leave every later artifact untouched.
"""

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error

from gdrives.sheets import (
    CONFIG_NAME,
    ReadBackError,
    SheetChangedError,
    apply_tab,
    parse_config,
    plan_tab,
    read_records,
    sync_tab,
    write_values_csv,
)

HEADER = ["id", "name", "amt"]
ROWS = [["a", "Ada", "1"], ["b", "Bo", "2"]]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}


def make_target(tmp_path, **fields):
    """A target with one sync tab ``T`` keyed by ``id``, local file local.csv."""
    tab = {"local": "local.csv", "key": ["id"]} | fields
    data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def write_local(target, *rows, header=HEADER):
    write_values_csv(str(target.tabs[0].local), [header, *rows])


def write_base(target, *rows, header=HEADER):
    path = target.base_path(target.tabs[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_values_csv(str(path), [header, *rows])


def local_rows(target):
    return [list(row.values()) for row in read_records(target.tabs[0].local).rows]


def base_rows(target):
    return [
        list(row.values())
        for row in read_records(target.base_path(target.tabs[0])).rows
    ]


def snapshot(target):
    """The bytes of the local and base files, None for one that does not exist."""
    tab = target.tabs[0]
    return tuple(
        path.read_bytes() if path.exists() else None
        for path in (tab.local, target.base_path(tab))
    )


def run(grid, target, **options):
    return sync_tab(grid, "S", target, target.tabs[0], **options)


def plan_of(report):
    assert report.plan is not None
    return report.plan


def applied_of(report):
    assert report.applied is not None
    return report.applied


def writes(grid):
    return [method for method in grid.methods if method not in READS]


@pytest.fixture
def synced(tmp_path):
    """A tab, local file, and base that agree: the state after a sync."""
    target = make_target(tmp_path)
    write_local(target, *ROWS)
    write_base(target, *ROWS)
    return FakeSheetGrid({"T": [HEADER, *ROWS]}), target


class TestPreview:
    def test_a_preview_writes_nothing_and_creates_no_directory(self, tmp_path):
        target = make_target(tmp_path, local="data/local.csv")
        target.tabs[0].local.parent.mkdir()
        write_local(target, ["a", "Ada", "9"], ["c", "Cy", "3"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = run(grid, target)
        assert report.apply is False
        assert writes(grid) == []
        assert grid.values("T") == [HEADER, *ROWS]
        assert not target.base.exists()
        assert sorted(p.name for p in tmp_path.rglob("*")) == ["data", "local.csv"]

    def test_a_preview_of_a_missing_tab_writes_nothing(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        report = run(grid, target, add_missing=True, drop_extra=True)
        assert report.tab_state == "missing"
        assert [a.key for a in plan_of(report).appends] == [("a",), ("b",)]
        assert writes(grid) == []
        assert [tab.title for tab in grid.tabs] == ["Other"]

    def test_plan_tab_returns_the_read_and_the_merge(self, synced):
        grid, target = synced
        planned = plan_tab(grid, "S", target, target.tabs[0], prefer="local")
        assert planned.columns == HEADER
        assert planned.table is not None and planned.base is not None
        assert planned.table.rows == [dict(zip(HEADER, r)) for r in ROWS]
        assert planned.base.rows == planned.table.rows
        assert planned.prefer == "local"
        assert planned.report.plan is planned.plan
        assert grid.methods == ["spreadsheets.get", "values.get"]

    def test_a_bad_prefer_is_refused_before_any_request(self, synced):
        grid, target = synced
        with pytest.raises(ValueError, match="prefer must be one of"):
            plan_tab(grid, "S", target, target.tabs[0], prefer="both")
        assert grid.calls == []


class TestBootstrap:
    """With no base yet, the local file is taken as the base."""

    def test_nothing_is_written_to_the_sheet(self, tmp_path):
        target = make_target(tmp_path)
        # a: the sheet edited amt. c: only local. d: only on the sheet.
        write_local(target, ["a", "Ada", "1"], ["b", "Bo", "2"], ["c", "Cy", "3"])
        grid = FakeSheetGrid(
            {"T": [HEADER, ["a", "Ada", "5"], ["b", "Bo", "2"], ["d", "Di", "4"]]}
        )
        before = grid.values("T")
        report = run(grid, target, apply=True)
        assert report.bootstrapped
        assert writes(grid) == []
        assert grid.values("T") == before
        assert local_rows(target) == [
            ["a", "Ada", "5"],
            ["b", "Bo", "2"],
            ["c", "Cy", "3"],
            ["d", "Di", "4"],
        ]
        assert base_rows(target) == local_rows(target)
        assert [(f.key, f.flag) for f in plan_of(report).row_flags] == [
            (("c",), "remote_deleted")
        ]
        assert report.exit_code == 2
        assert (report.wrote_sheet, report.wrote_local, report.wrote_base) == (
            False,
            True,
            True,
        )

    def test_local_owned_pushes_are_held_to_the_next_run(self, tmp_path):
        target = make_target(tmp_path, local_owned=["amt"])
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "7"]]})
        report = run(grid, target, apply=True)
        assert report.bootstrapped
        assert [(c.key, c.column, c.local) for c in report.deferred] == [
            (("a",), "amt", "1")
        ]
        assert plan_of(report).pushes == [] and plan_of(report).overrides == []
        assert writes(grid) == []
        assert base_rows(target) == [["a", "Ada", "7"]]  # the sheet's value
        assert local_rows(target) == [["a", "Ada", "1"]]
        # The next run sees a local edit and pushes it.
        grid.calls.clear()
        second = run(grid, target, apply=True)
        assert not second.bootstrapped
        assert grid.values("T") == [HEADER, ["a", "Ada", "1"]]
        assert base_rows(target) == [["a", "Ada", "1"]]
        assert second.exit_code == 0

    def test_a_header_only_tab_bootstraps_too(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER]})
        report = run(grid, target, apply=True)
        assert report.bootstrapped
        assert [f.flag for f in plan_of(report).row_flags] == ["remote_deleted"] * 2
        assert writes(grid) == []

    def test_columns_added_on_a_bootstrap_keep_their_local_values(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        report = run(grid, target, apply=True, add_missing=True)
        assert report.bootstrapped
        assert report.add_columns == ["amt"]
        assert grid.values("T") == [HEADER, ["a", "Ada"]]  # values held back
        assert local_rows(target) == [["a", "Ada", "1"]]
        assert base_rows(target) == [["a", "Ada", ""]]
        run(grid, target, apply=True)
        assert grid.values("T") == [HEADER, ["a", "Ada", "1"]]


class TestSync:
    def test_a_local_edit_pushes_and_a_sheet_edit_folds(self, synced):
        grid, target = synced
        write_local(target, ["a", "Ada", "10"], ["b", "Bo", "2"], ["c", "Cy", "3"])
        grid.write("T", [["b", "Bea", "2"], ["d", "Di", "4"]], row=3)
        report = run(grid, target, apply=True)
        assert grid.values("T") == [
            HEADER,
            ["a", "Ada", "10"],
            ["b", "Bea", "2"],
            ["d", "Di", "4"],
            ["c", "Cy", "3"],
        ]
        expected = [
            ["a", "Ada", "10"],
            ["b", "Bea", "2"],
            ["c", "Cy", "3"],
            ["d", "Di", "4"],
        ]
        assert local_rows(target) == expected
        assert base_rows(target) == expected
        assert (applied_of(report).pushed, applied_of(report).appended) == (1, 1)
        assert (report.wrote_sheet, report.wrote_local, report.wrote_base) == (
            True,
            True,
            True,
        )
        assert report.exit_code == 0

    def test_a_second_identical_run_writes_nothing(self, synced):
        grid, target = synced
        write_local(target, ["a", "Ada", "10"], *ROWS[1:])
        grid.write("T", [["b", "Bea", "2"]], row=3)
        run(grid, target, apply=True)
        files = snapshot(target)
        times = [
            p.stat().st_mtime_ns
            for p in (target.tabs[0].local, target.base_path(target.tabs[0]))
        ]
        sheet = grid.values("T")
        grid.calls.clear()
        report = run(grid, target, apply=True)
        assert grid.methods == ["spreadsheets.get", "values.get"]
        assert snapshot(target) == files
        assert [
            p.stat().st_mtime_ns
            for p in (target.tabs[0].local, target.base_path(target.tabs[0]))
        ] == times
        assert grid.values("T") == sheet
        assert not (report.wrote_sheet or report.wrote_local or report.wrote_base)
        assert report.exit_code == 0

    def test_carried_local_columns_pass_through(self, tmp_path):
        target = make_target(tmp_path, columns=["id", "name"])
        write_local(target, ["a", "Ada", "keep"], header=["id", "name", "memo"])
        write_base(target, ["a", "Ada"], header=["id", "name"])
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Al"], ["z", "Zed"]]})
        run(grid, target, apply=True)
        assert local_rows(target) == [["a", "Al", "keep"], ["z", "Zed", ""]]
        assert base_rows(target) == [["a", "Al"], ["z", "Zed"]]

    def test_sheet_columns_outside_the_projection_are_left_alone(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name", "amt", "notes"], ["a", "Ada", "1", "n1"]])
        write_local(target, ["a", "Ada", "5"], ROWS[1], ["c", "Cy", "3"])
        run(grid, target, apply=True)
        assert grid.values("T") == [
            ["id", "name", "amt", "notes"],
            ["a", "Ada", "5", "n1"],
            ["b", "Bo", "2"],
            ["c", "Cy", "3"],
        ]

    def test_insert_above_places_new_rows(self, tmp_path):
        target = make_target(tmp_path, insert_above={"name": "Bo"})
        write_local(target, *ROWS, ["c", "Cy", "3"])
        write_base(target, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        run(grid, target, apply=True)
        assert grid.values("T") == [HEADER, ROWS[0], ["c", "Cy", "3"], ROWS[1]]

    def test_json_local_file_is_written_with_its_types(self, tmp_path):
        target = make_target(
            tmp_path, local="local.json", schema={"amt": {"type": "int"}}
        )
        local = target.tabs[0].local
        local.write_text('[{"id": "a", "name": "Ada", "amt": 1}]\n', encoding="utf-8")
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        run(grid, target, apply=True)
        assert local.read_text(encoding="utf-8") == (
            '[\n  {\n    "id": "a",\n    "name": "Ada",\n    "amt": 1\n  },\n'
            '  {\n    "id": "b",\n    "name": "Bo",\n    "amt": 2\n  }\n]\n'
        )

    def test_bom_is_kept_on_the_local_file(self, tmp_path):
        target = make_target(tmp_path, bom=True)
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        run(grid, target, apply=True)
        assert target.tabs[0].local.read_bytes().startswith(b"\xef\xbb\xbfid,")

    def test_the_local_file_and_the_base_are_written_with_lf(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        run(grid, target, apply=True)
        assert snapshot(target) == (
            b"id,name,amt\na,Ada,1\nb,Bo,2\n",
            b"id,name,amt\na,Ada,1\nb,Bo,2\n",
        )

    @pytest.mark.parametrize("bom", [False, True])
    def test_newline_crlf_is_kept_on_the_local_file_and_the_base(self, tmp_path, bom):
        target = make_target(tmp_path, newline="crlf", bom=bom)
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        run(grid, target, apply=True)
        mark = b"\xef\xbb\xbf" if bom else b""
        assert snapshot(target) == (
            mark + b"id,name,amt\r\na,Ada,1\r\nb,Bo,2\r\n",
            b"id,name,amt\r\na,Ada,1\r\nb,Bo,2\r\n",
        )

    def test_a_crlf_file_keeps_its_line_endings_until_it_changes(self, synced):
        # write_local and write_base write CRLF, as 0.11.0 did.
        grid, target = synced
        before = snapshot(target)
        run(grid, target, apply=True)
        assert snapshot(target) == before
        grid.write("T", [["a", "Ada", "9"]], row=2)
        run(grid, target, apply=True)
        assert snapshot(target) == (
            b"id,name,amt\na,Ada,9\nb,Bo,2\n",
            b"id,name,amt\na,Ada,9\nb,Bo,2\n",
        )

    def test_widths_are_set_after_a_sheet_write_only(self, synced, tmp_path):
        _, plain = synced
        target = make_target(tmp_path, widths={"name": 240})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        run(grid, target, apply=True)  # nothing to write to the sheet
        assert writes(grid) == []
        assert grid.tab("T").widths[1] == 100
        write_local(target, ["a", "Ada", "9"], ROWS[1])
        report = run(grid, target, apply=True)
        assert report.wrote_widths
        assert grid.tab("T").widths[1] == 240
        assert writes(grid)[-1] == "spreadsheets.batchUpdate"


class TestConflicts:
    def test_a_conflict_is_reported_and_left_and_the_rest_applies(self, synced):
        grid, target = synced
        write_local(target, ["a", "Ada", "10"], ["b", "Bo", "20"])
        grid.write("T", [["a", "Ada", "11"]], row=2)
        report = run(grid, target, apply=True)
        assert [(c.key, c.column) for c in plan_of(report).conflicts] == [
            (("a",), "amt")
        ]
        assert grid.values("T") == [HEADER, ["a", "Ada", "11"], ["b", "Bo", "20"]]
        assert local_rows(target) == [["a", "Ada", "10"], ["b", "Bo", "20"]]
        assert base_rows(target) == [["a", "Ada", "1"], ["b", "Bo", "20"]]
        assert report.exit_code == 2

    @pytest.mark.parametrize(("prefer", "value"), [("local", "10"), ("sheet", "11")])
    def test_prefer_resolves_it(self, synced, prefer, value):
        grid, target = synced
        write_local(target, ["a", "Ada", "10"], ROWS[1])
        grid.write("T", [["a", "Ada", "11"]], row=2)
        report = run(grid, target, apply=True, prefer=prefer)
        assert grid.values("T")[1] == ["a", "Ada", value]
        assert local_rows(target)[0] == ["a", "Ada", value]
        assert base_rows(target)[0] == ["a", "Ada", value]
        assert [o.reason for o in plan_of(report).overrides] == ["prefer"]
        assert report.exit_code == 0


class TestRoundTrip:
    def test_bootstrap_edits_a_conflict_and_its_resolution(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})

        first = run(grid, target, apply=True)
        assert first.bootstrapped and first.exit_code == 0
        assert base_rows(target) == ROWS

        # A local edit and a sheet edit, on different cells.
        write_local(target, ["a", "Ada", "100"], ROWS[1])
        grid.write("T", [["b", "Bob", "2"]], row=3)
        second = run(grid, target, apply=True)
        assert second.exit_code == 0
        both = [["a", "Ada", "100"], ["b", "Bob", "2"]]
        assert grid.values("T") == [HEADER, *both]
        assert local_rows(target) == both and base_rows(target) == both

        # Both sides change one cell differently.
        write_local(target, ["a", "Ada", "100"], ["b", "Rob", "2"])
        grid.write("T", [["b", "Bobby", "2"]], row=3)
        third = run(grid, target, apply=True)
        assert third.exit_code == 2
        assert [(c.local, c.sheet, c.base) for c in plan_of(third).conflicts] == [
            ("Rob", "Bobby", "Bob")
        ]
        repeat = run(grid, target)
        assert repeat.exit_code == 2  # reported again until resolved

        fourth = run(grid, target, apply=True, prefer="sheet")
        assert fourth.exit_code == 0
        settled = [["a", "Ada", "100"], ["b", "Bobby", "2"]]
        assert grid.values("T") == [HEADER, *settled]
        assert local_rows(target) == settled and base_rows(target) == settled

        grid.calls.clear()
        files = snapshot(target)
        assert run(grid, target, apply=True).exit_code == 0
        assert writes(grid) == [] and snapshot(target) == files


class TestFailureOrder:
    """Each call of an apply fails in turn; later artifacts stay untouched."""

    @staticmethod
    def scene(tmp_path):
        target = make_target(tmp_path, widths={"amt": 50})
        write_base(target, *ROWS)
        # A push (a/amt), a new row (c), and a fold (b/name).
        write_local(target, ["a", "Ada", "9"], ROWS[1], ["c", "Cy", "3"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bea", "2"]]})
        return grid, target

    CALLS = [
        ("spreadsheets.get", 1),  # list the tabs
        ("values.get", 1),  # read the tab for the plan
        ("values.get", 2),  # the guard's re-read
        ("spreadsheets.get", 2),  # the grid size for new rows
        ("values.batchUpdate", 1),  # pushes
        ("spreadsheets.batchUpdate", 1),  # new rows
        ("values.get", 3),  # read-back
        ("values.get", 4),  # widths: the header
        ("spreadsheets.get", 3),  # widths: the sheetId
        ("spreadsheets.batchUpdate", 2),  # widths
    ]

    def test_the_calls_are_the_ones_listed(self, tmp_path):
        grid, target = self.scene(tmp_path)
        run(grid, target, apply=True)
        counts = {}
        seen = []
        for method in grid.methods:
            counts[method] = counts.get(method, 0) + 1
            seen.append((method, counts[method]))
        assert seen == self.CALLS

    @pytest.mark.parametrize(("method", "occurrence"), CALLS)
    def test_a_failed_call_leaves_later_artifacts_untouched(
        self, tmp_path, method, occurrence
    ):
        grid, target = self.scene(tmp_path)
        files = snapshot(target)
        grid.fail(method, http_error(400, "boom"), occurrence=occurrence)
        with pytest.raises(HttpError):
            run(grid, target, apply=True)
        index = self.CALLS.index((method, occurrence))
        widths_start = self.CALLS.index(("values.get", 4))
        if index < widths_start:
            assert snapshot(target) == files
        else:
            assert base_rows(target) == [
                ["a", "Ada", "9"],
                ["b", "Bea", "2"],
                ["c", "Cy", "3"],
            ]
            assert local_rows(target) == base_rows(target)
        if index < self.CALLS.index(("values.batchUpdate", 1)):
            assert grid.values("T") == [HEADER, ["a", "Ada", "1"], ["b", "Bea", "2"]]
        assert grid.tab("T").widths[2] == 100

    def test_a_failed_guard_leaves_both_files_byte_identical(self, tmp_path):
        grid, target = self.scene(tmp_path)
        files = snapshot(target)
        planned = plan_tab(grid, "S", target, target.tabs[0])
        grid.write("T", [["b", "Bo", "5"]], row=3)
        with pytest.raises(SheetChangedError):
            apply_tab(grid, "S", planned)
        assert snapshot(target) == files
        assert writes(grid) == []

    def test_a_failed_read_back_leaves_both_files_byte_identical(self, tmp_path):
        grid, target = self.scene(tmp_path)
        files = snapshot(target)
        grid.edit_externally(
            lambda g: g.write("T", [["a", "Ada", "8"]], row=2),
            before="values.get",
            occurrence=3,
        )
        with pytest.raises(ReadBackError):
            run(grid, target, apply=True)
        assert snapshot(target) == files

    def test_a_failed_local_write_leaves_the_base(self, tmp_path, monkeypatch):
        grid, target = self.scene(tmp_path)
        files = snapshot(target)
        import gdrives.sheets.sync as sync

        real = sync.write_records

        def fail_local(path, *args, **kwargs):
            if path == target.tabs[0].local:
                raise OSError("disk full")
            real(path, *args, **kwargs)

        monkeypatch.setattr(sync, "write_records", fail_local)
        planned = plan_tab(grid, "S", target, target.tabs[0])
        with pytest.raises(OSError, match="disk full"):
            apply_tab(grid, "S", planned)
        assert snapshot(target) == files
        assert planned.report.wrote_sheet and not planned.report.wrote_local
        assert grid.tab("T").widths[2] == 100

    def test_a_failed_base_write_leaves_the_widths(self, tmp_path, monkeypatch):
        grid, target = self.scene(tmp_path)
        import gdrives.sheets.sync as sync

        real = sync.write_records

        def fail_base(path, *args, **kwargs):
            if path == target.base_path(target.tabs[0]):
                raise OSError("read-only")
            real(path, *args, **kwargs)

        monkeypatch.setattr(sync, "write_records", fail_base)
        planned = plan_tab(grid, "S", target, target.tabs[0])
        with pytest.raises(OSError, match="read-only"):
            apply_tab(grid, "S", planned)
        assert base_rows(target) == ROWS
        assert planned.report.wrote_local and not planned.report.wrote_base
        assert grid.tab("T").widths[2] == 100


class TestMissingAndEmptyTabs:
    @pytest.mark.parametrize("state", ["missing", "empty"])
    def test_the_tab_is_created_and_the_local_rows_appended(self, tmp_path, state):
        target = make_target(tmp_path, widths={"name": 150})
        write_local(target, *ROWS)
        grid = FakeSheetGrid(
            {"Other": [["x"]]} | ({"T": []} if state == "empty" else {})
        )
        preview = run(grid, target)
        assert preview.tab_state == state
        assert not preview.bootstrapped
        assert writes(grid) == []
        report = run(grid, target, apply=True)
        assert report.tab_state == state
        assert grid.values("T") == [HEADER, *ROWS]
        assert grid.values("Other") == [["x"]]
        assert base_rows(target) == ROWS
        assert report.wrote_sheet and report.wrote_base and not report.wrote_local
        assert grid.tab("T").widths[1] == 150
        assert report.exit_code == 0

    @pytest.mark.parametrize("state", ["missing", "empty"])
    def test_a_base_for_an_emptied_tab_is_refused(self, tmp_path, state):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        write_base(target, *ROWS)
        grid = FakeSheetGrid({"T": []} if state == "empty" else {"Other": []})
        with pytest.raises(ValueError, match=f"tab 'T' is {state} but a base exists"):
            run(grid, target, apply=True)
        assert writes(grid) == []

    def test_values_below_a_blank_header_are_refused(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"T": [[], ["a", "b"]]})
        with pytest.raises(ValueError, match="no header row but holds values"):
            run(grid, target, apply=True)
        assert writes(grid) == []

    def test_adopt_on_a_missing_tab_appends_every_row(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        report = run(grid, target, apply=True, adopt=True)
        assert report.adopted
        assert grid.values("T") == [HEADER, *ROWS]

    def test_a_tab_deleted_while_it_is_created_is_refused(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})

        def delete(g):
            g.tabs = [t for t in g.tabs if t.title != "T"]

        # After the header is written, before the second read lists the tabs.
        grid.edit_externally(delete, before="spreadsheets.get", occurrence=4)
        with pytest.raises(
            SheetChangedError, match="changed while it was restructured"
        ):
            run(grid, target, apply=True)
        assert snapshot(target)[1] is None

    def test_a_created_tab_is_flagged_when_its_header_write_fails(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        planned = plan_tab(grid, "S", target, target.tabs[0])
        # The first batchUpdate creates the tab; the second writes its header.
        grid.fail("spreadsheets.batchUpdate", http_error(500, "boom"), occurrence=2)
        with pytest.raises(HttpError):
            apply_tab(grid, "S", planned)
        assert "T" in [tab.title for tab in grid.tabs]
        assert planned.report.wrote_sheet
        assert snapshot(target)[1] is None

    def test_a_failed_tab_creation_is_not_flagged(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        planned = plan_tab(grid, "S", target, target.tabs[0])
        grid.fail("spreadsheets.batchUpdate", http_error(400, "boom"))
        with pytest.raises(HttpError):
            apply_tab(grid, "S", planned)
        assert "T" not in [tab.title for tab in grid.tabs]
        assert not planned.report.wrote_sheet


class TestColumns:
    def test_a_missing_column_needs_add_missing(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name"], ["a", "Ada"], ["b", "Bo"]])
        grid.tab("T").cells[0][2] = grid.tab("T").cells[1][2] = None
        grid.tab("T").cells[2][2] = None
        with pytest.raises(
            ValueError, match=r"lacks column\(s\) \['amt'\]; add_missing"
        ):
            run(grid, target)

    def test_add_missing_previews_then_adds_and_pushes(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name", ""], ["a", "Ada", ""], ["b", "Bo", ""]])
        preview = run(grid, target, add_missing=True)
        assert preview.add_columns == ["amt"]
        assert writes(grid) == []
        # The base has amt already, and the sheet's blank would fold: a real
        # first add follows a projection change, so drop amt from the base.
        write_base(target, ["a", "Ada"], ["b", "Bo"], header=["id", "name"])
        report = run(grid, target, apply=True, add_missing=True)
        assert report.add_columns == ["amt"]
        assert grid.values("T") == [HEADER, *ROWS]
        assert base_rows(target) == ROWS
        assert report.exit_code == 0

    def test_a_missing_key_column_is_refused(self, synced):
        grid, target = synced
        grid.write("T", [["ident", "name", "amt"]])
        with pytest.raises(ValueError, match=r"lacks key column\(s\) \['id'\]"):
            run(grid, target, add_missing=True)

    def test_drop_extra_previews_counts_then_deletes(self, synced):
        grid, target = synced
        grid.write(
            "T",
            [
                ["id", "x", "name", "amt", "y"],
                ["a", "1", "Ada", "1"],
                ["b", "", "Bo", "2", "q"],
            ],
        )
        preview = run(grid, target, drop_extra=True)
        assert preview.drop_columns == {"x": 1, "y": 1}
        assert writes(grid) == []
        report = run(grid, target, apply=True, drop_extra=True)
        assert report.drop_columns == {"x": 1, "y": 1}
        assert grid.values("T") == [HEADER, *ROWS]
        assert report.wrote_sheet

    def test_without_drop_extra_extra_columns_are_not_listed(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name", "amt", "y"]])
        assert run(grid, target).drop_columns == {}

    def test_a_column_removed_during_the_restructure_is_refused(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name", "amt", "y"]])
        grid.edit_externally(
            lambda g: g.write("T", [["id", "name", "amt", "z"]]),
            before="values.get",
            occurrence=3,
        )
        with pytest.raises(
            SheetChangedError, match="changed while it was restructured"
        ):
            run(grid, target, apply=True, drop_extra=True)
        assert snapshot(target)[0] is not None


class TestAdopt:
    def test_the_local_file_wins_every_difference(self, tmp_path):
        target = make_target(tmp_path, sheet_owned=["name"])
        write_local(target, ["a", "Ada", ""], ["b", "Bo", "2"], ["c", "Cy", "3"])
        grid = FakeSheetGrid(
            {
                "T": [
                    HEADER + ["notes"],
                    ["a", "Al", "1", "n"],
                    ["b", "Bo", "2"],
                    ["z", "Zed", "9"],
                ]
            }
        )
        preview = run(grid, target, adopt=True)
        assert [
            (c.key, c.column, c.sheet, c.local) for c in plan_of(preview).pushes
        ] == [
            (("a",), "name", "Al", "Ada"),
            (("a",), "amt", "1", ""),
        ]
        assert writes(grid) == []
        report = run(grid, target, apply=True, adopt=True)
        assert report.adopted and not report.bootstrapped
        assert grid.values("T") == [
            HEADER + ["notes"],
            ["a", "Ada", "", "n"],
            ["b", "Bo", "2"],
            ["z", "Zed", "9"],
            ["c", "Cy", "3"],
        ]
        assert [(f.key, f.flag) for f in plan_of(report).row_flags] == [
            (("z",), "remote_added")
        ]
        assert local_rows(target) == [
            ["a", "Ada", ""],
            ["b", "Bo", "2"],
            ["c", "Cy", "3"],
        ]
        assert base_rows(target) == local_rows(target)
        assert report.exit_code == 2

    def test_adopt_is_refused_once_a_base_exists(self, synced):
        grid, target = synced
        with pytest.raises(ValueError, match="adopt is only for a first sync"):
            run(grid, target, adopt=True)
        assert grid.calls == []


class TestChecks:
    def test_a_local_schema_problem_writes_nothing(self, tmp_path):
        target = make_target(tmp_path, schema={"amt": {"type": "int"}})
        write_local(target, ["a", "Ada", "x"])
        write_base(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        files = snapshot(target)
        report = run(grid, target, apply=True)
        assert report.problems == [
            "T (local): key ('a',), column 'amt': 'x' is not a valid int"
        ]
        assert report.plan is None and report.exit_code == 1
        assert grid.calls == []
        assert snapshot(target) == files

    def test_a_merged_schema_problem_writes_nothing(self, synced, tmp_path):
        grid, _ = synced
        target = make_target(tmp_path, schema={"name": {"allowed": ["Ada", "Bo"]}})
        grid.write("T", [["b", "Bob", "5"]], row=3)
        write_local(target, ["a", "Ada", "7"], ROWS[1])
        files = snapshot(target)
        report = run(grid, target, apply=True)
        assert report.problems == [
            "T (merged): key ('b',), column 'name': 'Bob' is not one of ['Ada', 'Bo']"
        ]
        assert report.plan is not None
        assert writes(grid) == []
        assert snapshot(target) == files

    def test_validate_runs_on_the_local_rows_and_the_merged_result(self, synced):
        grid, target = synced
        grid.write("T", [["c", "Cy", "3"]], row=4)
        seen = []

        def validate(rows):
            seen.append([row["id"] for row in rows])
            return [] if len(seen) == 1 else ["too many rows"]

        report = run(grid, target, apply=True, validate=validate)
        assert seen == [["a", "b"], ["a", "b", "c"]]
        assert report.problems == ["T (merged): too many rows"]
        assert writes(grid) == [] and report.exit_code == 1

    def test_validate_on_the_local_rows_stops_before_any_request(self, synced):
        grid, target = synced
        report = run(grid, target, apply=True, validate=lambda rows: ["no"])
        assert report.problems == ["T (local): no"]
        assert grid.calls == []

    def test_the_checks_all_run_before_a_restructure_writes(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        calls = []

        def validate(rows):
            calls.append(len(writes(grid)))
            return ["late"] if len(calls) > 2 else []

        report = run(grid, target, apply=True, validate=validate)
        # Both checks ran before the tab was created, and none after it.
        assert calls == [0, 0]
        assert report.problems == [] and report.wrote_sheet
        assert grid.values("T") == [HEADER, *ROWS]
        assert report.exit_code == 0

    def test_problems_and_a_sheet_write_never_come_together(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        report = run(grid, target, apply=True, validate=lambda rows: ["no"])
        assert report.problems == ["T (local): no"]
        assert not report.wrote_sheet and writes(grid) == []
        assert "Other" in [t.title for t in grid.tabs] and len(grid.tabs) == 1

    def test_a_cell_edited_during_the_restructure_is_refused(self, synced):
        grid, target = synced
        grid.write("T", [["id", "name", "amt", "y"], *ROWS])
        files = snapshot(target)
        # After the column is dropped, before the second read of the tab.
        grid.edit_externally(
            lambda g: g.write("T", [HEADER, ROWS[0], ["b", "Bob", "2"]]),
            before="values.get",
            occurrence=3,
        )
        with pytest.raises(
            SheetChangedError, match="changed while it was restructured"
        ):
            run(grid, target, apply=True, drop_extra=True)
        assert snapshot(target) == files

    def test_a_local_edit_during_the_restructure_is_refused(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        grid = FakeSheetGrid({"Other": []})
        grid.edit_externally(
            lambda g: write_local(target, *ROWS, ["c", "Cy", "3"]),
            before="spreadsheets.batchUpdate",
            occurrence=2,
        )
        with pytest.raises(
            SheetChangedError, match="changed while it was restructured"
        ):
            run(grid, target, apply=True)
        assert grid.values("T") == [HEADER]
        assert snapshot(target)[1] is None


class TestRefusals:
    def test_a_missing_local_file(self, tmp_path):
        target = make_target(tmp_path)
        grid = FakeSheetGrid({"T": [HEADER]})
        with pytest.raises(ValueError, match="local.csv does not exist"):
            run(grid, target)
        assert grid.calls == []

    def test_a_local_file_lacking_a_configured_column(self, tmp_path):
        target = make_target(tmp_path, columns=["id", "name", "amt"])
        write_local(target, ["a", "Ada"], header=["id", "name"])
        with pytest.raises(ValueError, match=r"lacks column\(s\) \['amt'\]"):
            run(FakeSheetGrid({"T": [HEADER]}), target)

    def test_a_padded_local_header_names_the_configured_column(self, tmp_path):
        # The tab's header cells are read stripped, so the file's must be too.
        target = make_target(tmp_path, columns=["id", "name", "amt"])
        write_local(target, *ROWS, header=["id ", " name", "amt"])
        write_base(target, *ROWS)
        report = run(FakeSheetGrid({"T": [HEADER, *ROWS]}), target)
        assert report.problems == []
        assert plan_of(report).needs_attention is False
        assert plan_of(report).pushes == []

    def test_a_local_file_with_no_columns(self, tmp_path):
        target = make_target(tmp_path, local="local.json")
        target.tabs[0].local.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="has no columns"):
            run(FakeSheetGrid({"T": [HEADER]}), target)

    def test_a_duplicate_local_key(self, synced):
        grid, target = synced
        write_local(target, ROWS[0], ROWS[0])
        with pytest.raises(ValueError, match="duplicate key"):
            run(grid, target)


class TestPaths:
    def test_a_title_with_a_slash_and_dots_gets_a_safe_base_file(self, tmp_path):
        data = {
            "t": {
                "spreadsheet": "S",
                "base": "snap/../snapshots",
                "tabs": {"../a/b": {"local": "data/x.csv", "key": ["id"]}},
            }
        }
        target = parse_config(data, tmp_path / "cfg" / CONFIG_NAME).target("t")
        tab = target.tabs[0]
        tab.local.parent.mkdir(parents=True)
        write_values_csv(str(tab.local), [HEADER, *ROWS])
        grid = FakeSheetGrid({"../a/b": [HEADER, *ROWS]})
        sync_tab(grid, "S", target, tab, apply=True)
        base = tmp_path / "cfg" / "snapshots" / ".._a_b.csv"
        assert base.read_bytes() == b"id,name,amt\na,Ada,1\nb,Bo,2\n"
        assert sorted(p.name for p in (tmp_path / "cfg" / "snapshots").iterdir()) == [
            ".._a_b.csv"
        ]
