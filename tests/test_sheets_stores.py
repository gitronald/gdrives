"""Tests for gdrives.sheets.stores, and for a sync, a pull, and a push over them.

The runs are over ``FakeSheetGrid``, with the local side and the base held by
``MemoryStore`` or by a store written here, so nothing is a file unless a test
says so.
"""

import json
from typing import Any

import pytest
from helpers import FakeSheetGrid

from gdrives.sheets import (
    ColumnSchema,
    FileStore,
    MemoryStore,
    Records,
    SheetChangedError,
    SyncReport,
    TabConfig,
    TabReport,
    Target,
    apply_tab,
    encode_rows,
    format_report,
    plan_tab,
    pull_tab,
    push_tab,
    read_records,
    run_target,
    sync_tab,
)

HEADER = ["id", "name", "amt"]
ROWS = [["a", "Ada", "1"], ["b", "Bo", "2"]]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}


def records(*rows, header=HEADER):
    return [dict(zip(header, row, strict=True)) for row in rows]


def memory(*rows, header=HEADER, label="memory"):
    return MemoryStore(header, records(*rows, header=header), label=label)


def rows_of(store):
    return [list(row.values()) for row in store.read().rows]


def make(tmp_path, local, base=None, mode="sync", **fields):
    """A target with one tab ``T`` over ``local``, its base in ``base`` if given."""
    options: dict[str, Any] = {"key": ("id",)} | fields
    tab = TabConfig(title="T", store=local, mode=mode, **options)
    stores = {"T": base} if base is not None else {}
    target = Target(
        name="t",
        spreadsheet="S",
        base=tmp_path / "sheets-base",
        tabs=(tab,),
        base_stores=stores,
    )
    return target, tab


def writes(grid):
    return [method for method in grid.methods if method not in READS]


class Failing:
    """A store that reads what ``held`` holds, and whose ``write`` raises."""

    label = "memory"

    def __init__(self, columns, rows, *, error):
        self.held = MemoryStore(columns, rows)
        self.error = error

    def exists(self):
        return True

    def read(self):
        return self.held.read()

    def write(self, columns, rows):
        raise self.error


class Drifting:
    """A computed local side that does not read the same twice."""

    label = "computed"

    def __init__(self, columns, rows):
        self.held = MemoryStore(columns, rows)
        self.reads = 0

    def exists(self):
        return True

    def read(self):
        found = self.held.read()
        found.rows[0]["name"] = f"read {self.reads}"
        self.reads += 1
        return found

    def write(self, columns, rows):
        self.held.write(columns, rows)


class TestFileStore:
    def test_it_reads_and_writes_a_record_file(self, tmp_path):
        store = FileStore(tmp_path / "data" / "m.csv")
        assert store.label == str(tmp_path / "data" / "m.csv")
        assert not store.exists()
        store.write(HEADER, records(*ROWS))
        assert store.exists()
        assert store.read() == Records(HEADER, records(*ROWS))
        assert store.path.read_bytes() == b"id,name,amt\na,Ada,1\nb,Bo,2\n"

    def test_it_writes_with_its_bom_and_newline(self, tmp_path):
        store = FileStore(tmp_path / "m.csv", bom=True, newline="crlf")
        store.write(["id"], [{"id": "a"}])
        assert store.path.read_bytes() == b"\xef\xbb\xbfid\r\na\r\n"

    def test_it_writes_a_json_file_with_its_types(self, tmp_path):
        store = FileStore(tmp_path / "m.json", types={"amt": int})
        store.write(HEADER, records(ROWS[0]))
        assert store.path.read_text() == (
            '[\n  {\n    "id": "a",\n    "name": "Ada",\n    "amt": 1\n  }\n]\n'
        )

    def test_two_stores_of_one_file_are_equal(self, tmp_path):
        assert FileStore(tmp_path / "m.csv") == FileStore(tmp_path / "m.csv")
        assert FileStore(tmp_path / "m.csv") != FileStore(tmp_path / "m.csv", bom=True)


class TestMemoryStore:
    def test_it_does_not_exist_until_it_has_columns(self):
        store = MemoryStore()
        assert not store.exists() and store.label == "memory"
        assert store.read() == Records([], [])
        store.write(HEADER, records(*ROWS))
        assert store.exists() and store.writes == 1
        assert store.read() == Records(HEADER, records(*ROWS))

    def test_a_store_given_columns_exists_with_no_rows(self):
        store = MemoryStore(["id"], label="cache")
        assert store.exists() and store.label == "cache"
        assert store.read() == Records(["id"], [])

    def test_what_it_is_given_and_what_it_returns_are_copies(self):
        given = records(*ROWS)
        store = MemoryStore(HEADER, given)
        given[0]["name"] = "changed"
        store.read().rows[0]["name"] = "changed"
        store.read().columns.append("x")
        assert store.read() == Records(HEADER, records(*ROWS))
        written = records(*ROWS)
        store.write(HEADER, written)
        written[0]["name"] = "changed"
        assert store.rows == records(*ROWS)


class TestConfig:
    def test_a_tab_with_neither_a_file_nor_a_store_is_refused(self):
        with pytest.raises(ValueError, match="tab 'T': give 'local', a file path, or"):
            TabConfig(title="T")

    def test_a_tab_with_a_store_has_no_local_file(self):
        store = MemoryStore()
        tab = TabConfig(title="T", store=store)
        assert tab.local is None and tab.local_store is store

    def test_a_tab_with_a_file_has_a_file_store_with_its_settings(self, tmp_path):
        tab = TabConfig(
            title="T",
            local=tmp_path / "m.csv",
            schema={"amt": ColumnSchema(type="int")},
            bom=True,
            newline="crlf",
        )
        assert tab.local_store == FileStore(
            tmp_path / "m.csv", types={"amt": "int"}, bom=True, newline="crlf"
        )

    def test_a_store_is_used_before_a_file(self, tmp_path):
        store = MemoryStore()
        tab = TabConfig(title="T", local=tmp_path / "m.csv", store=store)
        assert tab.local_store is store and tab.local == tmp_path / "m.csv"

    def test_the_base_is_a_file_unless_the_target_names_a_store(self, tmp_path):
        base = MemoryStore(label="base")
        target, tab = make(tmp_path, MemoryStore(), base, newline="crlf")
        assert target.base_store(tab) is base
        target, tab = make(tmp_path, MemoryStore(), newline="crlf")
        assert target.base_store(tab) == FileStore(
            tmp_path / "sheets-base" / "T.csv", newline="crlf"
        )


class TestSync:
    def test_a_first_sync_a_second_and_the_adopt_refusal(self, tmp_path):
        local, base = memory(*ROWS), MemoryStore(label="the base")
        target, tab = make(tmp_path, local, base)
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "9"], ["c", "Cy", "3"]]})

        first = sync_tab(grid, "S", target, tab, apply=True)
        assert first.bootstrapped and first.local is None
        assert first.local_label == "memory"
        assert rows_of(local) == [["a", "Ada", "9"], ["b", "Bo", "2"], ["c", "Cy", "3"]]
        assert rows_of(base) == [["a", "Ada", "9"], ["b", "Bo", "2"], ["c", "Cy", "3"]]
        assert writes(grid) == [] and not list(tmp_path.iterdir())

        local.rows[0]["name"] = "Al"
        grid.write("T", [["c", "Cyd", "3"]], row=3)
        second = sync_tab(grid, "S", target, tab, apply=True)
        assert second.wrote_sheet and second.wrote_local and second.wrote_base
        assert rows_of(local) == [["a", "Al", "9"], ["b", "Bo", "2"], ["c", "Cyd", "3"]]
        assert rows_of(base) == rows_of(local)
        assert grid.values("T") == [HEADER, ["a", "Al", "9"], ["c", "Cyd", "3"]]
        assert (local.writes, base.writes) == (2, 2)

        third = sync_tab(grid, "S", target, tab, apply=True)
        assert not (third.wrote_local or third.wrote_base)
        assert (local.writes, base.writes) == (2, 2)

        with pytest.raises(
            ValueError,
            match="adopt is only for a first sync, and a base exists at the base",
        ):
            sync_tab(grid, "S", target, tab, adopt=True)

    def test_a_base_for_an_emptied_tab_names_the_store(self, tmp_path):
        target, tab = make(tmp_path, memory(*ROWS), memory(*ROWS, label="the base"))
        with pytest.raises(ValueError, match="is empty but a base exists at the base:"):
            sync_tab(FakeSheetGrid({"T": []}), "S", target, tab)

    def test_the_local_store_is_written_rows_of_one_shape(self, tmp_path):
        local = MemoryStore([*HEADER, "memo"], [])
        target, tab = make(tmp_path, local, MemoryStore(), columns=tuple(HEADER))
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        sync_tab(grid, "S", target, tab, apply=True)
        assert local.columns == [*HEADER, "memo"]
        assert local.rows == records(
            *[[*row, ""] for row in ROWS], header=[*HEADER, "memo"]
        )

    def test_a_missing_local_side_is_refused_by_its_label(self, tmp_path):
        target, tab = make(tmp_path, MemoryStore(label="the rows"))
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        with pytest.raises(
            ValueError, match="tab 'T': local store the rows does not exist"
        ):
            sync_tab(grid, "S", target, tab)
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("local", "message"),
        [
            (MemoryStore([]), "local store memory has no columns"),
            (MemoryStore(["id"]), r"local store memory lacks column\(s\) \['amt'\]"),
        ],
    )
    def test_refusals_of_the_local_side_name_the_store(self, tmp_path, local, message):
        target, tab = make(
            tmp_path, local, columns=("id", "amt") if local.columns else None
        )
        with pytest.raises(ValueError, match=message):
            sync_tab(FakeSheetGrid({"T": [HEADER]}), "S", target, tab)

    def test_a_failed_local_write_leaves_the_base(self, tmp_path):
        local = Failing(
            HEADER, records(["a", "Ada", "9"], ROWS[1]), error=OSError("full")
        )
        base = memory(*ROWS)
        target, tab = make(tmp_path, local, base, widths={"amt": 50})
        grid = FakeSheetGrid({"T": [HEADER, ROWS[0], ["b", "Bea", "2"]]})
        planned = plan_tab(grid, "S", target, tab)
        with pytest.raises(OSError, match="full"):
            apply_tab(grid, "S", planned)
        report = planned.report
        assert report.wrote_sheet and not report.wrote_local and not report.wrote_base
        assert grid.values("T")[1] == ["a", "Ada", "9"]
        assert rows_of(base) == ROWS and base.writes == 0
        assert grid.tab("T").widths[2] == 100
        # The next run sees the sheet write as in sync, and folds again.
        again = sync_tab(grid, "S", target, tab)
        assert again.plan is not None and not again.plan.sheet_writes

    def test_a_failed_base_write_leaves_the_widths(self, tmp_path):
        local = memory(["a", "Ada", "9"], ROWS[1])
        base = Failing(HEADER, records(*ROWS), error=ValueError("no room"))
        target, tab = make(tmp_path, local, base, widths={"amt": 50})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        planned = plan_tab(grid, "S", target, tab)
        with pytest.raises(ValueError, match="no room"):
            apply_tab(grid, "S", planned)
        report = planned.report
        assert report.wrote_sheet and not report.wrote_base
        assert grid.tab("T").widths[2] == 100

    def test_a_store_error_is_reported_for_its_tab(self, tmp_path):
        local = Failing(HEADER, records(*ROWS), error=OSError("full"))
        target, _ = make(tmp_path, local, memory(*ROWS))
        grid = FakeSheetGrid({"T": [HEADER, ROWS[0], ["b", "Bea", "2"]]})
        report = run_target(grid, "S", target, "sync", apply=True)
        (tab,) = report.tabs
        assert tab.error == "full" and report.exit_code == 1
        assert tab.local is None and tab.local_label == "memory"

    def test_a_local_side_that_reads_differently_stops_a_restructure(self, tmp_path):
        local = Drifting(HEADER, records(*ROWS))
        target, tab = make(tmp_path, local, MemoryStore())
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"], ["b", "Bo"]]})
        with pytest.raises(
            SheetChangedError, match="changed while it was restructured"
        ):
            sync_tab(grid, "S", target, tab, apply=True, add_missing=True)
        assert local.held.writes == 0


class TestPullAndPush:
    def test_a_pull_creates_and_then_replaces_the_local_side(self, tmp_path):
        local = MemoryStore()
        _, tab = make(tmp_path, local, mode="pull")
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.wrote_local and report.local_label == "memory"
        assert rows_of(local) == ROWS
        again = pull_tab(grid, "S", tab, apply=True)
        assert not again.wrote_local and local.writes == 1
        grid.write("T", [["a", "Ada", "9"]], row=2)
        pull_tab(grid, "S", tab)
        assert rows_of(local) == ROWS
        pull_tab(grid, "S", tab, apply=True)
        assert rows_of(local) == [["a", "Ada", "9"], ROWS[1]]

    def test_a_push_writes_the_local_side_to_the_tab(self, tmp_path):
        local = memory(*ROWS)
        _, tab = make(tmp_path, local, mode="push")
        grid = FakeSheetGrid({"T": []})
        report = push_tab(grid, "S", tab, apply=True)
        assert report.wrote_sheet and grid.values("T") == [HEADER, *ROWS]
        assert local.writes == 0

    @pytest.mark.parametrize(
        ("local", "message"),
        [
            (MemoryStore(), "local store memory does not exist"),
            (MemoryStore(HEADER), "local store memory has no rows"),
            (
                memory(ROWS[0], ROWS[0]),
                r"local store memory: duplicate key \('a',\) in rows \[1, 2\]",
            ),
        ],
    )
    def test_refusals_of_a_push_name_the_store(self, tmp_path, local, message):
        _, tab = make(tmp_path, local, mode="push")
        with pytest.raises(ValueError, match=message):
            push_tab(FakeSheetGrid({"T": []}), "S", tab, apply=True)

    def test_a_file_is_still_called_a_file(self, tmp_path):
        tab = TabConfig(title="T", local=tmp_path / "m.csv", mode="push")
        with pytest.raises(ValueError, match=r"tab 'T': local file .*m\.csv does not"):
            push_tab(FakeSheetGrid({"T": []}), "S", tab)


class WorkbookTab:
    """The guide's example store: one tab of a JSON file that holds several."""

    def __init__(self, path, name, columns):
        self.path, self.name, self.columns = path, name, list(columns)
        self.label = f"{path}#{name}"

    def _tabs(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def exists(self):
        return self.name in self._tabs()

    def read(self):
        return Records(self.columns, encode_rows(self._tabs()[self.name], self.columns))

    def write(self, columns, rows):
        tabs = self._tabs() | {self.name: [dict(row) for row in rows]}
        self.path.write_text(json.dumps(tabs, indent=2) + "\n")


class TestGuideExample:
    def test_one_tab_of_a_file_that_holds_several(self, tmp_path):
        path = tmp_path / "workbook.json"
        held = {"Summary": [{"total": 2}], "T": [{"id": "a", "name": "Ada", "amt": 1}]}
        path.write_text(json.dumps(held))
        tab = TabConfig(title="T", key=("id",), store=WorkbookTab(path, "T", HEADER))
        target = Target(
            name="t", spreadsheet="S", base=tmp_path / "sheets-base", tabs=(tab,)
        )
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "1"], ["b", "Bo", "2"]]})
        report = run_target(grid, "S", target, "sync", apply=True)
        assert report.exit_code == 0, format_report(report)
        assert report.tabs[0].local_label == f"{path}#T"
        assert json.loads(path.read_text()) == {
            "Summary": [{"total": 2}],
            "T": records(["a", "Ada", "1"], ["b", "Bo", "2"]),
        }
        # The base is the target's file, since no store was named for it.
        assert read_records(tmp_path / "sheets-base" / "T.csv").rows == records(
            ["a", "Ada", "1"], ["b", "Bo", "2"]
        )


class TestReport:
    def test_a_store_is_named_by_its_label(self):
        report = TabReport(tab="T", mode="pull", local_label="the\x1brows")
        assert format_report(SyncReport([report])).splitlines() == [
            "pull tab 'T' (preview)",
            "  local store: the\\x1brows",
        ]

    def test_a_file_is_named_as_before(self, tmp_path):
        tab = TabConfig(title="T", local=tmp_path / "m.csv", mode="pull")
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.local == tmp_path / "m.csv"
        assert report.local_label == str(tmp_path / "m.csv")
        assert format_report(SyncReport([report])).splitlines()[1] == (
            f"  local file: {tmp_path / 'm.csv'}"
        )
        assert read_records(tmp_path / "m.csv").rows == records(*ROWS)
