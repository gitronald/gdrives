"""Tests for the ``transform`` hook: cleaning the rows a tab is read as.

A transform is given the rows read from the sheet and returns them cleaned.
On a pull, everything after the parse sees the cleaned rows: the checks, the
hooks, the report, and the file. On a sync, the merge compares the cleaned
sheet with the local side and the base, while the re-read guard and the
read-back compare the tab as read, so a cell the transform changed and nobody
edited is never pushed and never trips the guard.
"""

from datetime import date

import pytest
from helpers import local_file

from gdrives.sheets import (
    CONFIG_NAME,
    SheetChangedError,
    SyncReport,
    format_report,
    parse_config,
    plan_tab,
    pull_all_tabs,
    pull_tab,
    read_records,
    run_target,
    sync_tab,
    write_values_csv,
)
from gdrives.testing import FakeSheetGrid

HEADER = ["id", "name", "amt"]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}
SENTINEL = "RAW-SENTINEL-1f3c"


def collapse(rows):
    """Collapse each run of whitespace to one space and trim each cell."""
    return [{c: " ".join(text.split()) for c, text in row.items()} for row in rows]


def make_target(tmp_path, tabs=None, **fields):
    """A target with one sync tab ``T`` keyed by ``id`` over local.csv."""
    tab = {"local": "local.csv", "key": ["id"]} | fields
    data = {"t": {"spreadsheet": "S", "tabs": tabs or {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def pull_target(tmp_path, **fields):
    return make_target(
        tmp_path, {"T": {"mode": "pull", "local": "local.csv", "key": ["id"]} | fields}
    )


def write_local(target, *rows, header=HEADER):
    write_values_csv(str(target.tabs[0].local), [header, *rows])


def write_base(target, *rows, header=HEADER):
    path = target.base_path(target.tabs[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_values_csv(str(path), [header, *rows])


def local_rows(target):
    return [list(row.values()) for row in read_records(target.tabs[0].local).rows]


def base_rows(target):
    path = target.base_path(target.tabs[0])
    return [list(row.values()) for row in read_records(path).rows]


def writes(grid):
    return [method for method in grid.methods if method not in READS]


def sync(grid, target, **options):
    return sync_tab(grid, "S", target, target.tabs[0], **options)


def format_report_of(report):
    return format_report(SyncReport(tabs=[report]))


def plan_of(report):
    assert report.plan is not None
    return report.plan


# -- pull --


class TestPull:
    def test_the_text_as_read_reaches_no_file_report_or_hook(self, tmp_path):
        target = pull_target(tmp_path)
        tab = target.tabs[0]
        grid = FakeSheetGrid(
            {"T": [HEADER, ["a", f"Ada {SENTINEL}", 1], ["b", "Bo", 2]]}
        )
        seen = []

        def clean(rows):
            seen.append(("transform", [dict(row) for row in rows]))
            return [
                {c: text.replace(SENTINEL, "Lovelace") for c, text in row.items()}
                for row in rows
            ]

        def validate(rows):
            seen.append(("validate", rows))
            return []

        def check(context):
            seen.append(("check", context.rows))
            return []

        def warn(context):
            seen.append(("warn", context.rows))
            return [f"read {row['name']}" for row in context.rows]

        report = pull_tab(
            grid,
            "S",
            tab,
            apply=True,
            validate=validate,
            check=check,
            warn=warn,
            transform=clean,
        )
        assert report.error is None and report.wrote_local
        # The transform alone is given the rows as read.
        assert [who for who, _ in seen] == ["transform", "validate", "check", "warn"]
        assert SENTINEL in str(seen[0][1])
        assert SENTINEL not in str(seen[1:])
        assert SENTINEL not in local_file(tab).read_text()
        assert SENTINEL not in format_report_of(report)
        assert SENTINEL not in repr(report)
        assert local_rows(target) == [["a", "Ada Lovelace", "1"], ["b", "Bo", "2"]]
        assert report.warnings == ["read Ada Lovelace", "read Bo"]

    def test_a_file_that_holds_the_cleaned_rows_is_unchanged(self, tmp_path):
        target = pull_target(tmp_path)
        write_local(target, ["a", "Ada Lovelace", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "  Ada   Lovelace ", 1]]})
        report = pull_tab(grid, "S", target.tabs[0], apply=True, transform=collapse)
        assert report.replacement is not None and report.replacement.unchanged
        assert not report.wrote_local

    def test_declared_dates_arrive_as_iso_8601(self, tmp_path):
        target = pull_target(tmp_path, schema={"amt": {"type": "date"}})
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", date(2024, 1, 2)]]})
        given = []

        def keep(rows):
            given.extend(rows)
            return rows

        pull_tab(grid, "S", target.tabs[0], transform=keep)
        assert given == [{"id": "a", "name": "Ada", "amt": "2024-01-02"}]

    def test_the_schema_checks_the_cleaned_value(self, tmp_path):
        target = pull_target(tmp_path, schema={"amt": {"type": "int"}})
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", " 3 "]]})
        assert pull_tab(grid, "S", target.tabs[0]).problems == [
            "T (sheet): key ('a',), column 'amt': ' 3 ' is not a valid int"
        ]
        report = pull_tab(grid, "S", target.tabs[0], transform=collapse)
        assert report.problems == []


def lower_names(rows):
    return [row | {"name": row["name"].lower()} for row in rows]


def fewer(rows):
    return rows[:-1]


def more(rows):
    return [*rows, rows[0]]


def wider(rows):
    return [row | {"extra": ""} for row in rows]


def narrower(rows):
    return [{c: v for c, v in row.items() if c != "amt"} for row in rows]


def numeric(rows):
    return [row | {"amt": 1} for row in rows]


def same_key(rows):
    return [row | {"id": "a"} for row in rows]


def blank_key(rows):
    return [row | {"id": " "} if row["id"] == "b" else row for row in rows]


class TestRefusals:
    @pytest.mark.parametrize(
        ("transform", "message"),
        [
            (fewer, "the transform returned 1 rows for 2; it returns one row"),
            (more, "the transform returned 3 rows for 2; it returns one row"),
            (wider, "row 1 with columns ['amt', 'extra', 'id', 'name'], not"),
            (narrower, "row 1 with columns ['id', 'name'], not"),
            (numeric, "row 1 with a value that is not a string in column(s) ['amt']"),
            (same_key, "tab 'T', as transformed: duplicate key ('a',) in rows [2, 3]"),
            (blank_key, "tab 'T', as transformed: blank key ['id'] in rows [3]"),
        ],
    )
    def test_a_pull_is_refused_and_the_local_side_left_alone(
        self, tmp_path, transform, message
    ):
        target = pull_target(tmp_path)
        write_local(target, ["z", "Zed", "9"])
        before = local_file(target.tabs[0]).read_bytes()
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", 1], ["b", "Bo", 2]]})
        with pytest.raises(ValueError) as caught:
            pull_tab(grid, "S", target.tabs[0], apply=True, transform=transform)
        assert message in str(caught.value)
        assert local_file(target.tabs[0]).read_bytes() == before

    def test_a_sync_is_refused_when_two_keys_are_made_equal(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, ["a", "Ada", "1"])
        before = local_file(target.tabs[0]).read_bytes()
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", 1], ["b", "Bo", 2]]})
        report = run_target(grid, "S", target, apply=True, transform=same_key)
        (tab,) = report.tabs
        assert tab.error is not None and "duplicate key ('a',)" in tab.error
        assert writes(grid) == []
        assert local_file(target.tabs[0]).read_bytes() == before
        assert not target.base_path(target.tabs[0]).exists()

    def test_a_blank_key_component_follows_blank_keys(self, tmp_path):
        """Under ``partial``, a key the transform blanks in part is still a key."""
        header = ["id", "sub", "name"]
        target = pull_target(tmp_path, key=["id", "sub"], blank_keys="partial")
        grid = FakeSheetGrid({"T": [header, ["a", " ", "Ada"]]})
        report = pull_tab(grid, "S", target.tabs[0], apply=True, transform=collapse)
        assert report.error is None
        assert local_rows(target) == [["a", "", "Ada"]]


# -- pull_all_tabs --


class TestPullAllTabs:
    def test_the_hook_is_given_each_tab_s_title(self, tmp_path):
        grid = FakeSheetGrid(
            {
                "One": [["id", "name"], ["a", " Ada  L "]],
                "Two": [["id"], ["b"]],
                "Empty": [],
            }
        )
        titles = []

        def clean(rows, title):
            titles.append(title)
            return collapse(rows)

        report = pull_all_tabs(grid, "S", tmp_path, apply=True, transform=clean)
        assert titles == ["One", "Two"]
        assert report.exit_code == 0
        assert (tmp_path / "One.csv").read_text() == "id,name\na,Ada L\n"

    def test_a_tab_the_transform_fails_for_is_reported(self, tmp_path):
        grid = FakeSheetGrid({"One": [["id"], ["a"]], "Two": [["id"], ["b"]]})

        def clean(rows, title):
            return rows if title == "One" else []

        report = pull_all_tabs(grid, "S", tmp_path, apply=True, transform=clean)
        one, two = report.tabs
        assert one.error is None and one.wrote_local
        assert two.error == "tab 'Two': the transform returned 0 rows for 1; " + (
            "it returns one row for each row given"
        )
        assert not (tmp_path / "Two.csv").exists()


# -- run_target --


class TestRunTarget:
    def tabs(self):
        return {
            "S1": {"local": "s.csv", "key": ["id"]},
            "P1": {"mode": "pull", "local": "p.csv", "key": ["id"]},
            "U1": {"mode": "push", "local": "u.csv"},
        }

    def test_the_hook_goes_to_each_pull_tab_and_each_sync_tab(self, tmp_path):
        target = make_target(tmp_path, self.tabs())
        write_values_csv(str(tmp_path / "s.csv"), [HEADER, ["a", "Ada", "1"]])
        grid = FakeSheetGrid(
            {"S1": [HEADER, ["a", "Ada ", 1]], "P1": [HEADER, ["b", " Bo", 2]]}
        )
        given = []

        def clean(rows):
            given.append([row["name"] for row in rows])
            return collapse(rows)

        pulled = run_target(grid, "S", target, "pull", apply=True, transform=clean)
        assert pulled.exit_code == 0
        assert read_records(tmp_path / "p.csv").rows == [
            {"id": "b", "name": "Bo", "amt": "2"}
        ]
        synced = run_target(grid, "S", target, "sync", transform=clean)
        assert synced.exit_code == 0
        assert given == [[" Bo"], ["Ada "]]

    def test_a_push_run_refuses_a_transform_before_any_request(self, tmp_path):
        target = make_target(tmp_path, self.tabs())
        grid = FakeSheetGrid({})
        with pytest.raises(ValueError, match="only to pull and sync tabs"):
            run_target(grid, "S", target, "push", transform=collapse)
        assert grid.calls == []


# -- sync --


@pytest.fixture
def synced(tmp_path):
    """A tab whose sheet differs from its local side and base only by spacing."""
    target = make_target(tmp_path)
    rows = [["a", "Ada Lovelace", "1"], ["b", "Bo", "2"]]
    write_local(target, *rows)
    write_base(target, *rows)
    sheet = [HEADER, ["a", "Ada   Lovelace ", 1], ["b", "Bo", 2]]
    return FakeSheetGrid({"T": sheet}), target, sheet


class TestSync:
    def test_a_cell_differing_only_by_spacing_is_in_sync_and_not_written(self, synced):
        grid, target, sheet = synced
        before = local_file(target.tabs[0]).read_bytes()
        report = sync(grid, target, apply=True, transform=collapse)
        assert not plan_of(report).has_writes and report.exit_code == 0
        assert writes(grid) == []
        assert grid.values("T") == sheet  # the sheet keeps its text
        assert local_file(target.tabs[0]).read_bytes() == before
        assert "in sync: nothing to write" in format_report_of(report)

    def test_without_the_transform_the_spacing_is_a_sheet_edit(self, synced):
        grid, target, _ = synced
        folds = plan_of(sync(grid, target)).fold_cells
        assert [c.sheet for c in folds] == ["Ada   Lovelace "]

    def test_a_real_sheet_edit_is_folded_in_cleaned(self, synced):
        grid, target, _ = synced
        grid.write("T", [["b", "  Bo   Smith", 2]], row=3)
        report = sync(grid, target, apply=True, transform=collapse)
        assert [c.sheet for c in plan_of(report).fold_cells] == ["Bo Smith"]
        assert local_rows(target)[1] == ["b", "Bo Smith", "2"]
        assert base_rows(target)[1] == ["b", "Bo Smith", "2"]
        assert writes(grid) == []
        assert grid.values("T")[2] == ["b", "  Bo   Smith", 2]

    def test_a_local_edit_is_pushed_as_written(self, synced):
        grid, target, _ = synced
        write_local(target, ["a", "Ada Lovelace", "1"], ["b", "Bo  Byron", "2"])
        report = sync(grid, target, apply=True, transform=collapse)
        assert [(c.sheet, c.local) for c in plan_of(report).pushes] == [
            ("Bo", "Bo  Byron")
        ]
        assert report.error is None and report.wrote_sheet
        # Only the pushed cell is written; the uncleaned cell keeps its text.
        assert grid.values("T") == [
            HEADER,
            ["a", "Ada   Lovelace ", 1],
            ["b", "Bo  Byron", 2],
        ]
        # The next run reads the cleaned form as a sheet edit and folds it
        # in, once; the run after that is in sync.
        again = sync(grid, target, apply=True, transform=collapse)
        assert [c.sheet for c in plan_of(again).fold_cells] == ["Bo Byron"]
        assert local_rows(target)[1] == ["b", "Bo Byron", "2"]
        third = sync(grid, target, apply=True, transform=collapse)
        assert not plan_of(third).has_writes

    def test_a_push_to_a_row_whose_key_the_transform_changed(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, ["a", "Ada", "5"], ["n", "Nu", "3"])
        write_base(target, ["a", "Ada", "1"])

        def lower(rows):
            return [row | {"id": row["id"].lower()} for row in rows]

        grid = FakeSheetGrid({"T": [HEADER, ["A", "Ada", 1]]})
        report = sync(grid, target, apply=True, transform=lower)
        plan = plan_of(report)
        assert [c.key for c in plan.pushes] == [("a",)]
        assert report.error is None
        assert grid.values("T") == [HEADER, ["A", "Ada", "5"], ["n", "Nu", "3"]]
        applied = report.applied
        assert applied is not None and applied.pushed_rows == [2]

    def test_a_transform_that_changes_rows_in_place_leaves_the_read_alone(self, synced):
        grid, target, _ = synced
        write_local(target, ["a", "Ada Lovelace", "1"], ["b", "Bo", "7"])

        def in_place(rows):
            for row in rows:
                row["name"] = " ".join(row["name"].split())
            return rows

        report = sync(grid, target, apply=True, transform=in_place)
        assert report.error is None and report.wrote_sheet

    def test_the_guard_compares_the_tab_as_read(self, synced):
        """A sheet edit after the read is refused, even one the transform erases."""
        grid, target, _ = synced
        write_local(target, ["a", "Ada Lovelace", "1"], ["b", "Bo", "7"])
        grid.edit_externally(
            lambda g: g.write("T", [["b", "Bo "]], row=3),
            before="values.get",
            occurrence=2,
        )
        with pytest.raises(SheetChangedError, match="rows edited"):
            sync(grid, target, apply=True, transform=collapse)
        assert writes(grid) == []

    def test_insert_above_places_rows_by_the_values_as_read(self, tmp_path):
        target = make_target(tmp_path, insert_above={"name": "Total"})
        write_local(target, ["a", "Ada", "1"], ["n", "Nu", "3"], ["t", "Total", ""])
        write_base(target, ["a", "Ada", "1"], ["t", "Total", ""])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada ", 1], ["t", "Total", ""]]})
        report = sync(grid, target, apply=True, transform=collapse)
        assert report.insert_row == 3
        assert grid.values("T") == [
            HEADER,
            ["a", "Ada ", 1],
            ["n", "Nu", "3"],
            ["t", "Total"],
        ]

    def test_a_restructure_merges_again_through_the_transform(self, synced):
        grid, target, _ = synced
        header = [*HEADER, "note"]
        write_local(
            target, ["a", "Ada Lovelace", "1", "x"], ["b", "Bo", "2", ""], header=header
        )
        write_base(
            target, ["a", "Ada Lovelace", "1", ""], ["b", "Bo", "2", ""], header=header
        )
        report = sync(grid, target, apply=True, add_missing=True, transform=collapse)
        assert report.error is None and report.add_columns == ["note"]
        assert grid.values("T")[0] == header
        assert grid.values("T")[1] == ["a", "Ada   Lovelace ", 1, "x"]

    @pytest.mark.parametrize(
        ("transform", "held"),
        [(None, ["Active"]), (collapse, ["Active"]), (lower_names, [])],
        ids=["none", "not-fixed", "fixed"],
    )
    def test_hold_checks_the_cleaned_value(self, tmp_path, transform, held):
        target = make_target(
            tmp_path,
            schema={"name": {"allowed": ["active", "closed"]}},
            on_invalid="hold",
        )
        write_local(target, ["a", "active", "1"])
        write_base(target, ["a", "active", "1"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Active", 1]]})
        report = sync(grid, target, apply=True, transform=transform)
        assert [h.sheet for h in plan_of(report).held] == held
        assert writes(grid) == []

    def test_an_identity_transform_makes_the_requests_of_none(self, tmp_path):
        runs = []
        for transform in (None, lambda rows: rows):
            folder = tmp_path / str(len(runs))
            folder.mkdir()
            target = make_target(folder)
            write_local(target, ["a", "Ada", "9"], ["c", "Cy", "3"])
            write_base(target, ["a", "Ada", "1"])
            grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", 1], ["b", "Bo", 2]]})
            report = sync(grid, target, apply=True, transform=transform)
            runs.append((grid.calls, report.plan, report.applied))
        assert runs[0] == runs[1]

    def test_plan_tab_takes_the_hook(self, synced):
        grid, target, _ = synced
        planned = plan_tab(grid, "S", target, target.tabs[0], transform=collapse)
        assert planned.transform is collapse
        assert planned.seen is not None and planned.table is not None
        assert planned.seen.rows[0]["name"] == "Ada Lovelace"
        assert planned.table.rows[0]["name"] == "Ada   Lovelace "
        assert planned.seen.row_numbers == planned.table.row_numbers
