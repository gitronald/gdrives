"""Tests for the stock transform ``trim_cells``.

It does to every cell what ``row_key`` does to a key, line by line so a cell's
line breaks stay, and it must satisfy what a transform must: one row for each
row given, the same columns, string values, and idempotence.
"""

import itertools

import pytest

import gdrives.sheets
from gdrives.sheets import (
    CONFIG_NAME,
    parse_config,
    pull_all_tabs,
    pull_tab,
    read_records,
    row_key,
    run_target,
    sync_tab,
    trim_cell,
    trim_cells,
    write_values_csv,
)
from gdrives.sheets.transforms import trim_cells as from_module
from gdrives.testing import FakeSheetGrid

HEADER = ["id", "name", "notes"]
NAME = "gdrives.sheets.transforms:trim_cells"


def trimmed(text):
    return trim_cells([{"c": text}])[0]["c"]


class TestTrimCell:
    def test_it_is_reexported(self):
        assert gdrives.sheets.trim_cell is trim_cell

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("  Member   ID ", "Member ID"),
            ("a \r\n  b\tc \n", "a\nb c"),
            ("", ""),
            ("plain", "plain"),
        ],
    )
    def test_one_cell_is_cleaned_as_a_row_s_cells_are(self, text, expected):
        assert trim_cell(text) == expected
        assert trim_cells([{"c": text}]) == [{"c": expected}]
        assert trim_cell(expected) == expected


class TestTrimCells:
    def test_it_is_reexported(self):
        assert trim_cells is from_module

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", ""),
            ("Ada", "Ada"),
            ("  Ada  ", "Ada"),
            ("Ada \t  Lovelace", "Ada Lovelace"),
            (" Ada ", "Ada"),
            ("one  two\nthree \t four", "one two\nthree four"),
            ("  one \n  two  ", "one\ntwo"),
            ("\n\none\n\n", "one"),
            ("one\n\n  two", "one\n\ntwo"),
            ("one \r\n two", "one\ntwo"),
            (" \t ", ""),
        ],
    )
    def test_each_cell_is_stripped_and_each_line_collapsed(self, text, expected):
        assert trimmed(text) == expected

    def test_it_returns_one_row_per_row_with_the_same_columns_in_order(self):
        rows = [{"id": " a ", "name": "A  B"}, {"id": "b", "name": ""}]
        result = trim_cells(rows)
        assert result == [{"id": "a", "name": "A B"}, {"id": "b", "name": ""}]
        assert [list(row) for row in result] == [["id", "name"]] * 2
        assert all(isinstance(v, str) for row in result for v in row.values())
        assert trim_cells([]) == []

    def test_the_rows_given_are_not_changed(self):
        rows = [{"id": " a "}]
        trim_cells(rows)
        assert rows == [{"id": " a "}]

    def test_it_is_idempotent(self):
        pieces = ["a", " ", "\t", "\n", "\r", " ", " ", "b"]
        for size in range(1, 6):
            for parts in itertools.product(pieces, repeat=size):
                once = trimmed("".join(parts))
                assert trimmed(once) == once

    def test_a_key_cell_keeps_its_row_key(self):
        pieces = ["a", " ", "\t", "\n", " ", "b"]
        for size in range(1, 6):
            for parts in itertools.product(pieces, repeat=size):
                record = {"id": "".join(parts)}
                assert row_key(trim_cells([record])[0], ["id"]) == row_key(
                    record, ["id"]
                )

    def test_the_title_argument_is_accepted_and_unused(self):
        rows = [{"c": " a  b "}]
        assert trim_cells(rows, "Members") == trim_cells(rows) == [{"c": "a b"}]


def make_target(tmp_path, tab, **fields):
    data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


SHEET = [HEADER, ["a", " Ada  L ", "line one \nline   two"], ["b", "Bo", 2]]


class TestUsed:
    def test_named_in_a_config(self, tmp_path):
        tab = {"mode": "pull", "local": "local.csv", "key": ["id"]}
        hooks = {"transform": NAME}
        target = make_target(tmp_path, tab, hooks=hooks)
        assert target.tabs[0].hooks == hooks
        grid = FakeSheetGrid({"T": SHEET})
        report = run_target(grid, "S", target, "pull", apply=True)
        assert report.exit_code == 0
        assert read_records(tmp_path / "local.csv").rows == [
            {"id": "a", "name": "Ada L", "notes": "line one\nline two"},
            {"id": "b", "name": "Bo", "notes": "2"},
        ]

    def test_passed_in_code(self, tmp_path):
        target = make_target(tmp_path, {"mode": "pull", "local": "local.csv"})
        grid = FakeSheetGrid({"T": SHEET})
        report = pull_tab(grid, "S", target.tabs[0], apply=True, transform=trim_cells)
        assert report.error is None and report.wrote_local
        assert read_records(tmp_path / "local.csv").rows[0]["name"] == "Ada L"

    def test_given_the_title_by_pull_all_tabs(self, tmp_path):
        grid = FakeSheetGrid({"One": [["id", "name"], ["a", " Ada  L "]]})
        report = pull_all_tabs(grid, "S", tmp_path, apply=True, transform=trim_cells)
        assert report.exit_code == 0
        assert (tmp_path / "One.csv").read_text() == "id,name\na,Ada L\n"

    def test_a_sheet_cell_differing_only_by_spacing_is_not_pushed(self, tmp_path):
        target = make_target(
            tmp_path,
            {"local": "local.csv", "key": ["id"], "hooks": {"transform": NAME}},
        )
        tab = target.tabs[0]
        rows = [["a", "Ada L", "x"], ["b", "Bo", "y"]]
        write_values_csv(str(tab.local), [HEADER, *rows])
        path = target.base_path(tab)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_values_csv(str(path), [HEADER, *rows])
        sheet = [HEADER, ["a", " Ada   L ", "x"], ["b", "Bo", "y"]]
        grid = FakeSheetGrid({"T": sheet})
        before = (tmp_path / "local.csv").read_bytes()
        report = sync_tab(grid, "S", target, tab, apply=True)
        assert report.error is None
        assert report.plan is not None and not report.plan.has_writes
        assert (tmp_path / "local.csv").read_bytes() == before
        reads = {"spreadsheets.get", "values.get", "values.batchGet"}
        assert [m for m in grid.methods if m not in reads] == []
        assert grid.values("T") == sheet
