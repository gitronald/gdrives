"""Tests for the opt-in schema checks of step 12: 'present' and 'strict'.

``present`` reports a schema column missing from a header, once, whether or
not the tab has rows. ``strict`` narrows ``bool`` and ``date`` cells to their
exact form (``TRUE``/``FALSE``, ``YYYY-MM-DD``), and is checked through the
same ``cell_problem`` every other schema rule goes through. Both are opt-in
and off by default: a config or a call written before this step behaves the
same, with the same report (``TestUnchangedDefaults``).
"""

from helpers import FakeSheetGrid, local_file

from gdrives.sheets import (
    CONFIG_NAME,
    ColumnSchema,
    SyncReport,
    format_report,
    parse_config,
    plan_tab,
    pull_tab,
    push_rows,
    push_tab,
    read_records,
    sync_tab,
    write_values_csv,
)

HEADER = ["id", "name", "amt"]
ROWS = [["a", "Ada", "1"], ["b", "Bo", "2"]]


def make_target(tmp_path, **fields):
    """A target with one sync tab ``T`` keyed by ``id``, local file local.csv."""
    tab = {"local": "local.csv", "key": ["id"]} | fields
    data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def one_tab(tmp_path, mode, **fields):
    tab = {"mode": mode, "local": "local.csv"} | fields
    data = {"t": {"spreadsheet": "S", "tabs": {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t").tabs[0]


def write_local(tab_or_target, *rows, header=HEADER):
    target = tab_or_target.tabs[0] if hasattr(tab_or_target, "tabs") else tab_or_target
    write_values_csv(str(target.local), [header, *rows])


def write_base(target, *rows, header=HEADER):
    path = target.base_path(target.tabs[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_values_csv(str(path), [header, *rows])


def run_sync(grid, target, **options):
    return sync_tab(grid, "S", target, target.tabs[0], **options)


# -- presence: sync --


class TestSyncPresence:
    def test_a_local_column_the_header_lacks_blocks_before_any_request(self, tmp_path):
        target = make_target(tmp_path, schema={"amt": {"present": True}})
        write_local(target, ["a", "Ada"], header=["id", "name"])
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [
            "T (local): column 'amt' is declared present and the header lacks it"
        ]
        assert grid.calls == []

    def test_a_carried_present_column_missing_on_the_sheet_is_a_problem(self, tmp_path):
        # 'amt' is declared present but kept out of the projection, which
        # strict_schema allows a schema entry to do.
        target = make_target(
            tmp_path,
            columns=["id", "name"],
            schema={"id": {}, "name": {}, "amt": {"present": True}},
            strict_schema=True,
        )
        write_local(target, ["a", "Ada", "1"])
        write_base(target, ["a", "Ada"], header=["id", "name"])
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [
            "T (sheet): column 'amt' is declared present and the header lacks it"
        ]

    def test_a_column_this_run_is_adding_is_not_reported(self, tmp_path):
        target = make_target(tmp_path, schema={"amt": {"present": True}})
        write_local(target, *ROWS)
        write_base(target, ["a", "Ada"], ["b", "Bo"], header=["id", "name"])
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"], ["b", "Bo"]]})
        report = run_sync(grid, target, apply=True, add_missing=True)
        assert report.problems == []

    def test_reported_once_for_a_tab_with_no_rows(self, tmp_path):
        target = make_target(
            tmp_path,
            columns=["id", "name"],
            schema={"id": {}, "name": {}, "amt": {"present": True}},
            strict_schema=True,
        )
        write_local(target, ["a", "Ada", "1"])
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [
            "T (sheet): column 'amt' is declared present and the header lacks it"
        ]


# -- presence: pull --


class TestPullPresence:
    def test_a_missing_header_column_blocks_before_any_write(self, tmp_path):
        tab = one_tab(tmp_path, "pull", schema={"amt": {"present": True}})
        grid = FakeSheetGrid({"T": [["id", "name"], ["a", "Ada"]]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (sheet): column 'amt' is declared present and the header lacks it"
        ]
        assert not local_file(tab).exists()

    def test_reported_on_a_tab_with_no_rows(self, tmp_path):
        tab = one_tab(tmp_path, "pull", schema={"amt": {"present": True}})
        grid = FakeSheetGrid({"T": [["id", "name"]]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (sheet): column 'amt' is declared present and the header lacks it"
        ]

    def test_a_present_column_outside_the_projection_is_still_checked(self, tmp_path):
        tab = one_tab(
            tmp_path,
            "pull",
            columns=["id", "name"],
            schema={"id": {}, "name": {}, "amt": {"present": True}},
            strict_schema=True,
        )
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == []


# -- presence: push --


class TestPushPresence:
    def test_push_tab_blocks_before_any_request(self, tmp_path):
        tab = one_tab(tmp_path, "push", schema={"amt": {"present": True}})
        write_local(tab, ["a", "Ada"], header=["id", "name"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = push_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (local): column 'amt' is declared present and the header lacks it"
        ]
        assert grid.calls == []

    def test_push_tab_reported_on_an_empty_local_file(self, tmp_path):
        tab = one_tab(tmp_path, "push", schema={"amt": {"present": True}})
        write_local(tab, header=["id", "name"])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = push_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (local): column 'amt' is declared present and the header lacks it"
        ]

    def test_push_rows_direct_call(self):
        report = push_rows(
            FakeSheetGrid({"T": [HEADER]}),
            "S",
            "T",
            ["id", "name"],
            [{"id": "a", "name": "Ada"}],
            schema={"amt": ColumnSchema(present=True)},
        )
        assert report.problems == [
            "T (local): column 'amt' is declared present and the header lacks it"
        ]

    def test_push_rows_reported_on_an_empty_row_list(self):
        report = push_rows(
            FakeSheetGrid({"T": [HEADER]}),
            "S",
            "T",
            ["id", "name"],
            [],
            schema={"amt": ColumnSchema(present=True)},
        )
        assert report.problems == [
            "T (local): column 'amt' is declared present and the header lacks it"
        ]

    def test_push_rows_default_is_off(self):
        report = push_rows(
            FakeSheetGrid({"T": [HEADER]}),
            "S",
            "T",
            ["id", "name"],
            [{"id": "a", "name": "Ada"}],
        )
        assert report.problems == []


# -- strict forms --


class TestStrictForms:
    def test_a_respelled_bool_is_a_problem_only_under_strict(self, tmp_path):
        tab = one_tab(tmp_path, "push", key=["id"], schema={"paid": {"type": "bool"}})
        write_local(tab, ["a", "true"], header=["id", "paid"])
        report = push_tab(FakeSheetGrid({"T": [["id", "paid"]]}), "S", tab, apply=True)
        assert report.problems == []

        strict_tab = one_tab(
            tmp_path,
            "push",
            key=["id"],
            schema={"paid": {"type": "bool", "strict": True}},
        )
        write_local(strict_tab, ["a", "true"], header=["id", "paid"])
        report = push_tab(
            FakeSheetGrid({"T": [["id", "paid"]]}), "S", strict_tab, apply=True
        )
        assert report.problems == [
            "T (local): key ('a',), column 'paid': "
            "'true' is not TRUE or FALSE, and the column is strict"
        ]

    def test_a_non_iso_date_form_is_a_problem_only_under_strict(self, tmp_path):
        tab = one_tab(
            tmp_path,
            "pull",
            schema={"due": {"type": "date", "strict": True}},
            key=["id"],
        )
        grid = FakeSheetGrid({"T": [["id", "due"], ["a", "20260927"]]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == [
            "T (sheet): key ('a',), column 'due': "
            "'20260927' is not YYYY-MM-DD, and the column is strict"
        ]

    def test_a_held_cell_under_on_invalid_hold(self, tmp_path):
        target = make_target(
            tmp_path,
            schema={"amt": {"type": "bool", "strict": True}},
            on_invalid="hold",
        )
        write_local(target, ["a", "Ada", "FALSE"])
        write_base(target, ["a", "Ada", "FALSE"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "true"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [] and report.exit_code == 2
        plan = report.plan
        assert plan is not None
        assert [(h.key, h.column, h.sheet, h.reason) for h in plan.held] == [
            (
                ("a",),
                "amt",
                "true",
                "'true' is not TRUE or FALSE, and the column is strict",
            )
        ]
        # Nothing was folded: the local file keeps its own value.
        assert read_records(local_file(target.tabs[0])).rows[0]["amt"] == "FALSE"

    def test_a_respelling_that_compares_equal_is_a_problem_and_no_edit(self, tmp_path):
        # local 'TRUE' and sheet 'true' agree once compared, so nothing would
        # be pushed or folded either way; a strict column still flags it.
        target = make_target(tmp_path, schema={"amt": {"type": "bool", "strict": True}})
        write_local(target, ["a", "Ada", "TRUE"])
        write_base(target, ["a", "Ada", "TRUE"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "true"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [
            "T (sheet): key ('a',), column 'amt': "
            "'true' is not TRUE or FALSE, and the column is strict"
        ]
        assert report.plan is not None
        assert report.plan.pushes == [] and report.plan.fold_cells == []
        # The local file was never touched.
        assert read_records(local_file(target.tabs[0])).rows[0]["amt"] == "TRUE"

    def test_a_new_sheet_row_has_no_local_row_to_compare(self, tmp_path):
        # A row only on the sheet is a fold candidate, checked by the merge's
        # own hold mechanism, not this one; no local row means nothing to
        # compare against for the respelling check.
        target = make_target(tmp_path, schema={"amt": {"type": "bool", "strict": True}})
        write_local(target, ["a", "Ada", "TRUE"])
        write_base(target, ["a", "Ada", "TRUE"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "TRUE"], ["b", "Bo", "TRUE"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == []

    def test_identical_text_is_not_reported(self, tmp_path):
        # Both sides hold the exact same, already-valid spelling: nothing to
        # find, without even normalizing it.
        target = make_target(tmp_path, schema={"amt": {"type": "bool", "strict": True}})
        write_local(target, ["a", "Ada", "TRUE"])
        write_base(target, ["a", "Ada", "TRUE"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "TRUE"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == []

    def test_a_later_strict_column_is_still_checked(self, tmp_path):
        # 'flag' compares equal and is valid; 'other' compares equal and is
        # not: the loop must not stop at the first strict column.
        target = make_target(
            tmp_path,
            schema={
                "flag": {"type": "bool", "strict": True},
                "other": {"type": "bool", "strict": True},
            },
        )
        write_local(target, ["a", "TRUE", "TRUE"], header=["id", "flag", "other"])
        write_base(target, ["a", "TRUE", "TRUE"], header=["id", "flag", "other"])
        grid = FakeSheetGrid({"T": [["id", "flag", "other"], ["a", "TRUE", "true"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [
            "T (sheet): key ('a',), column 'other': "
            "'true' is not TRUE or FALSE, and the column is strict"
        ]

    def test_comparison_is_unchanged_without_strict(self, tmp_path):
        target = make_target(tmp_path, schema={"amt": {"type": "bool"}})
        write_local(target, ["a", "Ada", "TRUE"])
        write_base(target, ["a", "Ada", "TRUE"])
        grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", "true"]]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == []


# -- unchanged defaults --


class TestUnchangedDefaults:
    """A 0.13.0-style config, with no 'present' or 'strict' field, is unaffected."""

    def test_a_sync_reports_as_before(self, tmp_path):
        target = make_target(tmp_path, schema={"amt": {"type": "int"}})
        write_local(target, *ROWS)
        write_base(target, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = run_sync(grid, target, apply=True)
        assert report.problems == [] and report.exit_code == 0
        lines = format_report(SyncReport([report])).splitlines()
        assert lines[-1] == "  in sync: nothing to write"

    def test_a_pull_reports_as_before(self, tmp_path):
        tab = one_tab(tmp_path, "pull", schema={"amt": {"type": "int"}})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = pull_tab(grid, "S", tab, apply=True)
        assert report.problems == []
        assert read_records(local_file(tab)).rows == [
            {"id": "a", "name": "Ada", "amt": "1"},
            {"id": "b", "name": "Bo", "amt": "2"},
        ]

    def test_a_push_reports_as_before(self, tmp_path):
        tab = one_tab(tmp_path, "push", schema={"amt": {"type": "int"}})
        write_local(tab, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = push_tab(grid, "S", tab, apply=True)
        assert report.problems == []
        assert report.replacement is not None and report.replacement.unchanged

    def test_plan_tab_takes_no_new_arguments(self, tmp_path):
        target = make_target(tmp_path)
        write_local(target, *ROWS)
        write_base(target, *ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        planned = plan_tab(grid, "S", target, target.tabs[0])
        assert planned.report.problems == []


class TestRespellings:
    SCHEMA = {"on": ColumnSchema(type="bool", strict=True)}

    def problems(self, local, sheet):
        from gdrives.sheets.sync import _respelling_problems

        rows = lambda text: [{"id": "a", "on": text}]  # noqa: E731
        return _respelling_problems("T", rows(local), rows(sheet), self.SCHEMA, ["id"])

    def test_a_sheet_respelling_that_fails_the_strict_form_is_a_problem(self):
        assert self.problems("TRUE", "true") == [
            "T (sheet): key ('a',), column 'on': 'true' is not TRUE or FALSE, "
            "and the column is strict"
        ]

    def test_a_sheet_cell_in_the_strict_form_is_none(self):
        assert self.problems("true", "TRUE") == []
