"""Tests for the report and run seams: ``pending``, ``exit_code``, and their kin.

``TabReport`` and ``SyncReport`` say whether ``--apply`` would write anything
and what exit code they add up to, ``format_report`` takes either, and
``run_target`` finds the spreadsheet in the target when it is not given one.
Runs use ``FakeSheetGrid`` and files under ``tmp_path``.
"""

from typing import Any

import pytest

from gdrives.resolve import DrivePathError
from gdrives.sheets import (
    CONFIG_NAME,
    Cell,
    HeldCell,
    LinkSweep,
    MemoryStore,
    MergePlan,
    NewRow,
    Replacement,
    RowFlag,
    SyncReport,
    TabConfig,
    TabLinks,
    TabReport,
    Target,
    UrlLinkProblem,
    format_report,
    parse_config,
    pending_hint,
    pull_tab,
    push_rows,
    run_target,
    sync_tab,
    write_values_csv,
)
from gdrives.testing import FakeSheetGrid

HEADER = ["id", "name"]
ROWS = [["a", "Ada"], ["b", "Bo"]]
CELL = Cell(("a",), "name", "Ada", "Ann", "Ada")


def make_target(tmp_path, mode="sync", spreadsheet="S"):
    tab = {"mode": mode, "local": "local.csv"}
    if mode == "sync":
        tab["key"] = ["id"]
    data = {"t": {"spreadsheet": spreadsheet, "tabs": {"T": tab}}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def write_files(target, local, base=None):
    write_values_csv(str(target.tabs[0].local), [HEADER, *local])
    if base is not None:
        path = target.base_path(target.tabs[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        write_values_csv(str(path), [HEADER, *base])


def report(**fields):
    return TabReport(tab="T", mode="sync", **fields)


class TestExitCode:
    """A tab's exit code is the one a run of only that tab adds up to."""

    CASES = {
        "clean": report(plan=MergePlan()),
        "empty": report(),
        "error": report(error="boom"),
        "problems": report(problems=["bad"], plan=MergePlan()),
        "error and conflict": report(error="boom", plan=MergePlan(conflicts=[CELL])),
        "conflict": report(plan=MergePlan(conflicts=[CELL])),
        "row flag": report(plan=MergePlan(row_flags=[RowFlag(("a",), "remote_added")])),
        "held": report(plan=MergePlan(held=[HeldCell(("a",), "n", "", "", "x", "no")])),
        "writes only": report(plan=MergePlan(pushes=[CELL])),
    }
    CODES = {
        "clean": 0,
        "empty": 0,
        "error": 1,
        "problems": 1,
        "error and conflict": 1,
        "conflict": 2,
        "row flag": 2,
        "held": 2,
        "writes only": 0,
    }

    @pytest.mark.parametrize("case", CASES)
    def test_the_two_reports_agree(self, case):
        tab = self.CASES[case]
        assert tab.exit_code == SyncReport(tabs=[tab]).exit_code == self.CODES[case]

    def test_a_run_takes_the_worst_of_its_tabs(self):
        tabs = [self.CASES["conflict"], self.CASES["error"], self.CASES["clean"]]
        assert SyncReport(tabs=tabs).exit_code == 1
        assert SyncReport(tabs=tabs[::2]).exit_code == 2
        assert SyncReport().exit_code == 0


class TestPendingByReport:
    """What ``pending`` counts, from the report's own fields."""

    @pytest.mark.parametrize(
        ("tab", "pending"),
        [
            (report(plan=MergePlan()), False),
            (report(plan=MergePlan(pushes=[CELL])), True),
            (report(plan=MergePlan(appends=[NewRow(("c",), {"name": "Cy"})])), True),
            (report(plan=MergePlan(fold_cells=[CELL])), True),
            (report(plan=MergePlan(fold_rows=[NewRow(("c",), {"name": "Cy"})])), True),
            (report(plan=MergePlan(), add_columns=["phone"]), True),
            (report(plan=MergePlan(), drop_columns={"phone": 0}), True),
            (report(plan=MergePlan(), tab_state="missing"), True),
            (report(plan=MergePlan(), tab_state="empty"), True),
            (report(plan=MergePlan(), stale_base=True), True),
            (report(plan=MergePlan(), stale_local=True), True),
            (report(plan=MergePlan(), bootstrapped=True), False),
            (report(plan=MergePlan(), adopted=True), False),
            (report(plan=MergePlan(), stale_base=True, apply=True), False),
            (report(plan=MergePlan(), stale_base=True, error="boom"), False),
            (report(stale_base=True), False),
            (report(plan=MergePlan(conflicts=[CELL])), False),
            (report(plan=MergePlan(conflicts=[CELL], pushes=[CELL])), True),
            (report(plan=MergePlan(pushes=[CELL]), apply=True), False),
            (report(plan=MergePlan(pushes=[CELL]), error="boom"), False),
            (report(plan=MergePlan(pushes=[CELL]), problems=["bad"]), False),
            (report(add_columns=["phone"]), False),
            (report(replacement=Replacement(1, 1, unchanged=True)), False),
            (report(replacement=Replacement(1, 1)), True),
            (report(replacement=Replacement(1, 1), apply=True), False),
            (report(replacement=Replacement(1, 1), error="boom"), False),
        ],
    )
    def test_a_tab(self, tab, pending):
        assert tab.pending is pending

    def test_a_run_is_pending_when_any_tab_is(self):
        quiet, busy = report(plan=MergePlan()), report(plan=MergePlan(pushes=[CELL]))
        assert SyncReport(tabs=[quiet, busy]).pending is True
        assert SyncReport(tabs=[quiet, quiet]).pending is False
        assert SyncReport().pending is False


class TestBaseOnly:
    """A preview pending for the base alone is told apart from one that writes."""

    @pytest.mark.parametrize(
        ("tab", "base_only"),
        [
            (report(plan=MergePlan(), stale_base=True), True),
            (report(plan=MergePlan(pushes=[CELL]), stale_base=True), False),
            (report(plan=MergePlan(), stale_base=True, stale_local=True), False),
            (report(plan=MergePlan(), stale_base=True, add_columns=["phone"]), False),
            (report(plan=MergePlan(), stale_base=True, tab_state="missing"), False),
            (report(plan=MergePlan()), False),
            (report(plan=MergePlan(), stale_base=True, apply=True), False),
            (report(replacement=Replacement(1, 1)), False),
        ],
    )
    def test_a_tab(self, tab, base_only):
        assert tab.base_only is base_only

    def test_a_run_is_base_only_when_every_pending_tab_is(self):
        base = report(plan=MergePlan(), stale_base=True)
        busy = report(plan=MergePlan(pushes=[CELL]))
        quiet = report(plan=MergePlan())
        assert SyncReport(tabs=[base, quiet, base]).base_only is True
        assert SyncReport(tabs=[base, busy]).base_only is False
        assert SyncReport(tabs=[quiet]).base_only is False
        assert SyncReport().base_only is False


class TestPendingHint:
    """The line the commands print after a preview, for a caller's own printing."""

    WRITE = "Preview only; rerun with --apply to write."
    BASE = "Preview only; rerun with --apply to save the base."

    @pytest.mark.parametrize(
        ("tab", "hint"),
        [
            (report(plan=MergePlan(pushes=[CELL])), WRITE),
            (report(replacement=Replacement(1, 1)), WRITE),
            (report(plan=MergePlan(), stale_base=True), BASE),
            (report(plan=MergePlan(pushes=[CELL]), stale_base=True), WRITE),
            (report(plan=MergePlan()), None),
            (report(plan=MergePlan(pushes=[CELL]), apply=True), None),
            (report(plan=MergePlan(pushes=[CELL]), problems=["bad"]), None),
            (report(plan=MergePlan(pushes=[CELL]), error="boom"), None),
        ],
    )
    def test_a_tab_and_a_run_of_it_read_alike(self, tab, hint):
        assert pending_hint(tab) == hint
        assert pending_hint(SyncReport(tabs=[tab])) == hint

    def test_a_run_writes_when_any_tab_does(self):
        base = report(plan=MergePlan(), stale_base=True)
        busy = report(plan=MergePlan(pushes=[CELL]))
        assert pending_hint(SyncReport(tabs=[base, busy])) == self.WRITE
        assert pending_hint(SyncReport()) is None

    def test_a_sweep_and_a_tab_of_it(self):
        found = UrlLinkProblem(2, "link", "https://example.com/a", ("no link",))
        left = TabLinks("T", "#1155cc", problems=(found,))
        fixed = TabLinks("T", "#1155cc", problems=(found,), applied=True)
        assert pending_hint(left) == self.WRITE
        assert pending_hint(LinkSweep((fixed, left))) == self.WRITE
        assert pending_hint(fixed) is None
        assert pending_hint(LinkSweep((fixed,), apply=True)) is None

    def test_it_is_the_line_a_command_prints(self, capsys):
        from gdrives.sheets.commands import _hint_pending

        run = SyncReport(tabs=[report(plan=MergePlan(), stale_base=True)])
        _hint_pending(run)
        assert capsys.readouterr().err == f"{TestPendingHint.BASE}\n"
        assert pending_hint(run) == TestPendingHint.BASE


class TestPendingOfRuns:
    def sync(self, tmp_path, sheet, local, base, **options):
        target = make_target(tmp_path)
        write_files(target, local, base)
        grid = FakeSheetGrid({"T": [HEADER, *sheet]} if sheet is not None else {})
        return sync_tab(grid, "S", target, target.tabs[0], **options)

    def test_a_sync_in_step_is_not_pending(self, tmp_path):
        assert not self.sync(tmp_path, ROWS, ROWS, ROWS).pending

    def test_a_local_edit_is_pending_until_it_is_applied(self, tmp_path):
        edited = [["a", "Ann"], ROWS[1]]
        assert self.sync(tmp_path, ROWS, edited, ROWS).pending
        assert not self.sync(tmp_path, ROWS, edited, ROWS, apply=True).pending

    def test_a_sheet_edit_is_pending_for_the_local_file(self, tmp_path):
        assert self.sync(tmp_path, [["a", "Ann"], ROWS[1]], ROWS, ROWS).pending

    def test_a_first_run_saves_a_base(self, tmp_path):
        first = self.sync(tmp_path, ROWS, ROWS, None)
        assert first.bootstrapped and first.pending
        assert "in sync" in format_report(first)

    EDITED = [["a", "Ann"], ROWS[1]]
    SHEET_EDIT = [["a", "Amy"], ROWS[1]]
    CASES: dict[str, tuple[Any, Any, Any, dict[str, Any]]] = {
        "in sync": (ROWS, ROWS, ROWS, {}),
        "local edit": (ROWS, EDITED, ROWS, {}),
        "sheet edit": (EDITED, ROWS, ROWS, {}),
        "both sides made the same edit": (EDITED, EDITED, ROWS, {}),
        "first sync in step": (ROWS, ROWS, None, {}),
        "first sync with differences": (EDITED, ROWS, None, {}),
        "first sync adopted": (EDITED, ROWS, None, {"adopt": True}),
        "rows reordered locally": (ROWS, ROWS[::-1], ROWS, {}),
        "conflicts only": (SHEET_EDIT, EDITED, ROWS, {}),
        "column add": (ROWS, ROWS, ROWS, {"add_missing": True}),
        "tab missing": (None, ROWS, None, {}),
    }

    @pytest.mark.parametrize("case", CASES)
    def test_pending_is_exactly_what_an_apply_writes(self, tmp_path, case):
        sheet, local, base, options = self.CASES[case]
        if case == "column add":
            # The local file and the base carry a column the sheet lacks.
            target = make_target(tmp_path)
            wide = [*HEADER, "phone"]
            rows = [[*row, ""] for row in ROWS]
            write_values_csv(str(target.tabs[0].local), [wide, *rows])
            path = target.base_path(target.tabs[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            write_values_csv(str(path), [wide, *rows])
            grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
            preview = sync_tab(grid, "S", target, target.tabs[0], **options)
            applied = sync_tab(grid, "S", target, target.tabs[0], apply=True, **options)
        else:
            preview = self.sync(tmp_path, sheet, local, base, **options)
            applied = self.sync(tmp_path, sheet, local, base, apply=True, **options)
        assert applied.error is None and not applied.problems
        wrote = (
            applied.wrote_sheet
            or applied.wrote_local
            or applied.wrote_base
            or bool(applied.add_columns or applied.drop_columns)
            or applied.tab_state != "present"
        )
        assert preview.pending is wrote
        assert not applied.pending

    def test_the_equivalence_cases_cover_both_answers(self, tmp_path):
        answers = {
            self.sync(tmp_path / name, s, lo, b, **o).pending
            for name, (s, lo, b, o) in self.CASES.items()
            if name != "column add"
        }
        assert answers == {True, False}

    def test_a_base_alone_is_base_only(self, tmp_path):
        both = self.sync(tmp_path, self.EDITED, self.EDITED, ROWS)
        assert both.pending and both.stale_base and not both.stale_local
        assert both.plan is not None and not both.plan.has_writes
        assert both.base_only and "in sync" in format_report(both)

    def test_rows_reordered_locally_save_the_base_alone(self, tmp_path):
        # The merge keeps the local order for the file, so only the base moves.
        reordered = self.sync(tmp_path, ROWS, ROWS[::-1], ROWS)
        assert reordered.stale_base and not reordered.stale_local
        assert reordered.base_only

    def test_a_local_row_missing_a_carried_column_rewrites_the_local_file(self):
        # A store can hold rows that lack a column outside the projection; the
        # merge fills it in, so an apply writes the local side alone.
        records = [{"id": "a", "name": "Ada"}, {"id": "b", "name": "Bo", "note": "x"}]
        local = MemoryStore(["id", "name", "note"], records, label="local")
        base = MemoryStore(HEADER, [dict(zip(HEADER, r, strict=True)) for r in ROWS])
        tab = TabConfig(
            title="T", store=local, mode="sync", key=("id",), columns=tuple(HEADER)
        )
        target = Target(name="t", spreadsheet="S", tabs=(tab,), base_stores={"T": base})
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        preview = sync_tab(grid, "S", target, tab)
        assert preview.plan is not None and not preview.plan.has_writes
        assert preview.stale_local and not preview.stale_base
        assert preview.pending and not preview.base_only
        applied = sync_tab(grid, "S", target, tab, apply=True)
        assert applied.wrote_local and not applied.wrote_base

    def test_a_column_alone_is_pending(self, tmp_path):
        target = make_target(tmp_path)
        wide = [*HEADER, "phone"]
        write_values_csv(str(target.tabs[0].local), [wide, *[[*r, ""] for r in ROWS]])
        path = target.base_path(target.tabs[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        write_values_csv(str(path), [wide, *[[*r, ""] for r in ROWS]])
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        tab = sync_tab(grid, "S", target, target.tabs[0], add_missing=True)
        assert tab.plan is not None and not tab.plan.has_writes
        assert tab.add_columns == ["phone"]
        assert tab.pending

    def test_a_refused_tab_is_not_pending(self, tmp_path):
        target = make_target(tmp_path)
        write_files(target, [["a", "Ada"], ["a", "Bo"]], ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        tab = run_target(grid, "S", target).tabs[0]
        assert tab.error is not None and not tab.pending

    def test_a_pull_is_pending_when_the_file_would_change(self, tmp_path):
        target = make_target(tmp_path, "pull")
        write_files(target, ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        assert not pull_tab(grid, "S", target.tabs[0]).pending
        grid = FakeSheetGrid({"T": [HEADER, *ROWS, ["c", "Cy"]]})
        assert pull_tab(grid, "S", target.tabs[0]).pending
        assert not pull_tab(grid, "S", target.tabs[0], apply=True).pending

    def test_a_push_is_pending_when_the_sheet_would_change(self):
        records = [dict(zip(HEADER, row, strict=True)) for row in ROWS]
        same = FakeSheetGrid({"T": [HEADER, *ROWS]})
        assert not push_rows(same, "S", "T", HEADER, records).pending
        other = FakeSheetGrid({"T": [HEADER, ["a", "Ann"]]})
        assert push_rows(other, "S", "T", HEADER, records).pending
        assert not push_rows(other, "S", "T", HEADER, records, apply=True).pending

    def test_a_push_to_a_missing_tab_is_pending(self):
        records = [dict(zip(HEADER, row, strict=True)) for row in ROWS]
        assert push_rows(FakeSheetGrid({"U": []}), "S", "T", HEADER, records).pending


class TestFormatReport:
    def test_a_tab_report_renders_as_its_block_in_a_run(self, tmp_path):
        target = make_target(tmp_path, "pull")
        write_files(target, ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS, ["c", "Cy"]]})
        tab = pull_tab(grid, "S", target.tabs[0])
        assert format_report(tab) == format_report(SyncReport(tabs=[tab]))
        assert format_report(tab).startswith("pull tab 'T' (preview)\n")

    def test_a_run_of_two_tabs_is_two_blocks(self):
        one, two = report(), TabReport(tab="U", mode="push")
        text = format_report(SyncReport(tabs=[one, two]))
        assert text == f"{format_report(one)}\n\n{format_report(two)}"


class TestSpreadsheetId:
    @pytest.mark.parametrize(
        ("spreadsheet", "expected"),
        [
            ("https://docs.google.com/spreadsheets/d/abc123/edit#gid=0", "abc123"),
            ("abc123", "abc123"),
        ],
    )
    def test_a_url_or_an_id(self, tmp_path, spreadsheet, expected):
        target = make_target(tmp_path, spreadsheet=spreadsheet)
        assert target.spreadsheet_id == expected

    def test_a_drive_path_is_refused_naming_resolve_file_id(self, tmp_path):
        target = make_target(tmp_path, spreadsheet="My Drive/clubs/Roster")
        with pytest.raises(ValueError, match=r"Drive path.*resolve_file_id"):
            target.spreadsheet_id

    def test_a_target_built_in_code(self):
        assert Target(name="t", spreadsheet="abc123").spreadsheet_id == "abc123"
        blank = Target(name="t", spreadsheet=" ")
        with pytest.raises(DrivePathError, match="must not be empty"):
            blank.spreadsheet_id

    def test_run_target_takes_it_from_the_target(self, tmp_path):
        target = make_target(tmp_path, spreadsheet="abc123")
        write_files(target, ROWS, ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        report = run_target(grid, None, target)
        assert report.exit_code == 0
        assert {kwargs["spreadsheetId"] for _, kwargs in grid.calls} == {"abc123"}

    def test_run_target_refuses_a_path_before_any_request(self, tmp_path):
        target = make_target(tmp_path, spreadsheet="My Drive/clubs/Roster")
        grid = FakeSheetGrid({"T": []})
        with pytest.raises(ValueError, match="resolve_file_id"):
            run_target(grid, None, target)
        assert grid.calls == []

    def test_a_given_id_wins(self, tmp_path):
        target = make_target(tmp_path, spreadsheet="My Drive/clubs/Roster")
        write_files(target, ROWS, ROWS)
        grid = FakeSheetGrid({"T": [HEADER, *ROWS]})
        assert run_target(grid, "S", target).exit_code == 0
