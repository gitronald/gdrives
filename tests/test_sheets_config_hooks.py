"""Tests for hooks named in the config file as ``module:function``.

Reading a config checks a name's form and imports nothing. The names are
found when a run starts (``resolve_hooks``), before any request, and a found
hook is wrapped so what it raises, or a wrong return, is the tab's error. The
modules the tests import are written under ``tmp_path``, each under a name no
other test uses, and put on ``sys.path`` for the test alone.
"""

import importlib
import itertools
import json
import sys
import textwrap
from pathlib import Path

import pytest
from helpers import FakeSheetGrid
from typer.testing import CliRunner

from gdrives.cli import app
from gdrives.sheets import (
    CONFIG_NAME,
    ConfigError,
    TabConfig,
    load_config,
    parse_config,
    pull_tab,
    push_tab,
    read_records,
    resolve_hooks,
    run_target,
    tab_hooks,
    write_values_csv,
)
from gdrives.sheets.hooks import _chained, _joined

HEADER = ["member_id", "name", "status"]
ROWS = [["m1", "Ada", "active"], ["m2", "Bo", "active"]]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}

_NAMES = itertools.count()


@pytest.fixture
def module(tmp_path, monkeypatch):
    """Write a module under ``tmp_path``; return the name it imports by."""
    folder = tmp_path / "hook_modules"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    made: list[str] = []

    def write(source: str) -> str:
        name = f"config_hooks_{next(_NAMES)}"
        (folder / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
        importlib.invalidate_caches()
        made.append(name)
        return name

    yield write
    for name in made:
        sys.modules.pop(name, None)


@pytest.fixture
def imports(monkeypatch):
    """Every ``importlib.import_module`` call the hooks module makes, by name."""
    seen: list[str] = []
    real = importlib.import_module

    def spy(name, package=None):
        seen.append(name)
        return real(name, package)

    monkeypatch.setattr("gdrives.sheets.hooks.importlib.import_module", spy)
    return seen


CHECKS = """
    CALLS = []


    def validate(rows):
        CALLS.append("validate")
        return [f"{row['member_id']} is {row['status']}" for row in rows
                if row["status"] != "active"]


    def check(context):
        CALLS.append(f"check {context.stage}")
        return []


    def warn(context):
        CALLS.append("warn")
        return [f"{len(context.rows)} rows"]


    def upper(rows):
        CALLS.append("transform")
        return [{c: v.upper() for c, v in row.items()} for row in rows]


    def fails(rows):
        raise KeyError("member_id")


    def says(rows):
        return "one message"


    def nothing(rows):
        return None


    NOT_A_FUNCTION = 3
"""


def target_of(tmp_path, tabs, **fields):
    data = {"t": {"spreadsheet": "S", "tabs": tabs} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def problems_of(tmp_path, tabs, **fields):
    with pytest.raises(ConfigError) as caught:
        target_of(tmp_path, tabs, **fields)
    return caught.value.problems


# -- the config --


class TestTheConfigNamesHooks:
    def test_a_tab_keeps_the_names_and_nothing_is_imported(self, tmp_path, imports):
        hooks = {"validate": "pkg.checks:validate", "warn": "checks:warn"}
        tab = {"local": "m.csv", "key": ["member_id"], "hooks": hooks}
        (loaded,) = target_of(tmp_path, {"Members": tab}).tabs
        assert loaded.hooks == hooks
        assert imports == []

    def test_load_config_imports_nothing(self, tmp_path, imports):
        tab = {"local": "m.csv", "key": ["id"], "hooks": {"check": "nowhere:check"}}
        path = tmp_path / CONFIG_NAME
        path.write_text(json.dumps({"t": {"spreadsheet": "S", "tabs": {"T": tab}}}))
        assert load_config(path).target("t").tabs[0].hooks == {"check": "nowhere:check"}
        assert imports == []

    def test_a_target_s_hooks_are_its_tabs_defaults(self, tmp_path):
        tabs = {
            "Members": {
                "local": "m.csv",
                "key": ["id"],
                "hooks": {"validate": "own:validate"},
            },
            "Totals": {"mode": "pull", "local": "t.csv"},
            "Summary": {"mode": "push", "local": "s.csv"},
        }
        defaults = {"validate": "all:validate", "transform": "all:clean"}
        target = target_of(tmp_path, tabs, hooks=defaults)
        members, totals, summary = target.tabs
        assert members.hooks == {"validate": "own:validate", "transform": "all:clean"}
        assert totals.hooks == defaults
        # A push tab takes no transform, so it is not given the target's.
        assert summary.hooks == {"validate": "all:validate"}

    def test_no_hooks_is_an_empty_mapping(self, tmp_path):
        (tab,) = target_of(tmp_path, {"T": {"local": "m.csv", "key": ["id"]}}).tabs
        assert tab.hooks == {}

    @pytest.mark.parametrize(
        ("hooks", "problem"),
        [
            ("checks:validate", "'hooks' must be an object naming one or more of"),
            ({}, "'hooks' must be an object naming one or more of"),
            ({"validat": "m:f"}, "'hooks' names unknown hook(s) ['validat']"),
            ({"check": "checks"}, "hook 'check' must be 'module:function'"),
            ({"check": "a:b:c"}, "hook 'check' must be 'module:function'"),
            ({"check": "pkg.:f"}, "hook 'check' must be 'module:function'"),
            ({"check": ".pkg:f"}, "hook 'check' must be 'module:function'"),
            ({"check": "pkg:1f"}, "hook 'check' must be 'module:function'"),
            ({"check": "pkg:"}, "hook 'check' must be 'module:function'"),
            ({"check": 3}, "hook 'check' must be 'module:function', not 3"),
        ],
    )
    def test_a_malformed_hooks_field_is_refused(self, tmp_path, hooks, problem):
        tab = {"local": "m.csv", "key": ["id"], "hooks": hooks}
        (found,) = problems_of(tmp_path, {"T": tab})
        assert found.startswith("target 't', tab 'T': " + problem)

    def test_a_push_tab_takes_no_transform(self, tmp_path):
        tab = {"mode": "push", "local": "m.csv", "hooks": {"transform": "m:clean"}}
        assert problems_of(tmp_path, {"T": tab}) == [
            "target 't', tab 'T': hook 'transform' applies only to pull and sync tabs"
        ]

    def test_a_target_s_malformed_hooks_are_refused(self, tmp_path):
        tabs = {"T": {"local": "m.csv", "key": ["id"]}}
        found = problems_of(tmp_path, tabs, hooks={"warn": "nope", "nope": "m:f"})
        assert found == [
            "target 't': 'hooks' names unknown hook(s) ['nope']; hooks: "
            "['check', 'transform', 'validate', 'warn']",
            "target 't': hook 'warn' must be 'module:function', not 'nope'",
        ]

    def test_a_tab_built_in_code_is_checked(self):
        with pytest.raises(ValueError, match="must be 'module:function'"):
            TabConfig("T", Path("m.csv"), hooks={"check": "nope"})
        with pytest.raises(ValueError, match="applies only to pull and sync"):
            TabConfig("T", Path("m.csv"), mode="push", hooks={"transform": "m:f"})
        tab = TabConfig("T", Path("m.csv"), hooks={"check": "m:f"})
        assert tab.hooks == {"check": "m:f"}


# -- finding the functions --


class TestResolveHooks:
    def test_the_names_are_found_and_wrapped(self, tmp_path, module):
        name = module(CHECKS)
        tabs = {
            "Members": {
                "local": "m.csv",
                "key": ["member_id"],
                "hooks": {"validate": f"{name}:validate"},
            },
            "Totals": {"mode": "pull", "local": "t.csv"},
        }
        found = resolve_hooks(target_of(tmp_path, tabs))
        assert set(found) == {"Members", "Totals"}
        assert found["Totals"] == {}
        validate = found["Members"]["validate"]
        rows = [{"member_id": "m1", "status": "closed"}]
        assert validate(rows) == ["m1 is closed"]
        assert sys.modules[name].CALLS == ["validate"]

    def test_every_name_that_does_not_resolve_is_listed(self, tmp_path, module):
        name = module(CHECKS)
        broken = module("raise RuntimeError('broken at import')")
        hooks = {
            "validate": "no_such_module_here:validate",
            "check": f"{name}:missing",
            "warn": f"{name}:NOT_A_FUNCTION",
            "transform": f"{broken}:clean",
        }
        tab = {"local": "m.csv", "key": ["id"], "hooks": hooks}
        with pytest.raises(ConfigError) as caught:
            resolve_hooks(target_of(tmp_path, {"T": tab}))
        assert str(caught.value).startswith("target 't': 4 problems:")
        assert caught.value.problems == [
            "tab 'T': hook 'validate' 'no_such_module_here:validate': cannot "
            "import 'no_such_module_here': ModuleNotFoundError: No module named "
            "'no_such_module_here'",
            f"tab 'T': hook 'check' '{name}:missing': module {name!r} has no 'missing'",
            f"tab 'T': hook 'warn' '{name}:NOT_A_FUNCTION': 'NOT_A_FUNCTION' is "
            "not callable: it is 3",
            f"tab 'T': hook 'transform' '{broken}:clean': cannot import "
            f"{broken!r}: RuntimeError: broken at import",
        ]

    def test_only_the_tabs_asked_for_are_found(self, tmp_path, imports):
        tabs = {
            "A": {"local": "a.csv", "key": ["id"], "hooks": {"check": "nowhere:f"}},
            "B": {"local": "b.csv", "key": ["id"]},
        }
        assert resolve_hooks(target_of(tmp_path, tabs), ["B"]) == {"B": {}}
        assert imports == []

    def test_tab_hooks_raises_for_its_tab(self, tmp_path):
        tab = {"local": "m.csv", "key": ["id"], "hooks": {"check": "nowhere:f"}}
        (loaded,) = target_of(tmp_path, {"T": tab}).tabs
        with pytest.raises(ConfigError, match=r"^tab 'T': 1 problem:"):
            tab_hooks(loaded)

    def test_a_config_with_no_hooks_imports_nothing(self, tmp_path, imports):
        target = target_of(tmp_path, {"T": {"local": "m.csv", "key": ["id"]}})
        assert resolve_hooks(target) == {"T": {}}
        assert tab_hooks(target.tabs[0]) == {}
        assert imports == []


class TestWrappedHooks:
    def found(self, tmp_path, module, hook, function, mode="pull"):
        name = module(CHECKS)
        tab = {"mode": mode, "local": "m.csv", "hooks": {hook: f"{name}:{function}"}}
        return tab_hooks(target_of(tmp_path, {"T": tab}).tabs[0])[hook]

    def test_what_a_hook_raises_names_the_tab_the_hook_and_the_name(
        self, tmp_path, module
    ):
        validate = self.found(tmp_path, module, "validate", "fails")
        with pytest.raises(ValueError) as caught:
            validate([])
        assert str(caught.value) == (
            f"tab 'T': hook validate '{config_name(caught)}:fails' raised "
            "KeyError: 'member_id'"
        )
        assert isinstance(caught.value.__cause__, KeyError)

    def test_a_string_is_not_a_list_of_messages(self, tmp_path, module):
        check = self.found(tmp_path, module, "check", "says")
        with pytest.raises(ValueError, match="returned a str, not a list") as caught:
            check(None)
        assert "one message" not in str(caught.value)

    def test_a_list_of_other_things_is_not_a_list_of_messages(self, tmp_path, module):
        warn = self.found(tmp_path, module, "warn", "upper")
        rows = [{"a": "x" * 80}]
        with pytest.raises(ValueError) as caught:
            warn(rows)
        assert str(caught.value).endswith(
            "returned a list holding a dict, not a list of messages"
        )
        assert "XXXX" not in str(caught.value)

    @pytest.mark.parametrize("function", ["nothing", "says"])
    def test_a_transform_that_returns_no_rows_is_refused(
        self, tmp_path, module, function
    ):
        transform = self.found(tmp_path, module, "transform", function)
        with pytest.raises(ValueError, match="not a list of rows"):
            transform([])

    def test_a_transform_s_rows_come_back_as_a_list(self, tmp_path, module):
        transform = self.found(tmp_path, module, "transform", "upper")
        assert transform(({"a": "x"},)) == [{"a": "X"}]


def config_name(caught) -> str:
    """The module name inside a wrapped hook's message."""
    return str(caught.value).split("'")[3].split(":")[0]


class TestJoining:
    def test_messages_are_the_first_s_then_the_second_s(self):
        joined = _joined(lambda rows: ["a"], lambda rows: ["b"])
        assert joined is not None and joined([]) == ["a", "b"]

    def test_one_of_two_is_itself(self):
        def one(rows):
            return ["a"]

        assert _joined(one, None) is one and _joined(None, one) is one
        assert _joined(None, None) is None

    def test_transforms_run_in_order(self):
        chained = _chained(lambda text: text + "1", lambda text: text + "2")
        assert chained is not None and chained("") == "12"
        assert _chained(None, None) is None


# -- a run --


def grid_of(extra=None) -> FakeSheetGrid:
    return FakeSheetGrid(
        {"Members": [HEADER, *ROWS], "Totals": [HEADER, *ROWS]} | (extra or {})
    )


class TestARun:
    def pull_target(self, tmp_path, hooks, **more):
        tabs = {
            "Members": {"mode": "pull", "local": "m.csv", "hooks": hooks},
            "Totals": {"mode": "pull", "local": "t.csv"},
        } | more
        return target_of(tmp_path, tabs)

    def test_config_hooks_run_before_the_ones_given(self, tmp_path, module):
        name = module(CHECKS)
        hooks = {"validate": f"{name}:validate", "transform": f"{name}:upper"}
        target = self.pull_target(tmp_path, hooks)
        order: list[str] = []

        def validate(rows):
            order.append("code validate")
            return ["from code"]

        def transform(rows):
            order.append(f"code transform of {rows[0]['name']}")
            return rows

        report = run_target(
            grid_of(), "S", target, "pull", validate=validate, transform=transform
        )
        members, totals = report.tabs
        # The config's transform upper-cased the rows the code's transform saw,
        # so the config's validate finds every status not "active".
        assert members.problems == [
            "Members (sheet): M1 is ACTIVE",
            "Members (sheet): M2 is ACTIVE",
            "Members (sheet): from code",
        ]
        assert sys.modules[name].CALLS == ["transform", "validate"]
        assert order == [
            "code transform of ADA",
            "code validate",
            "code transform of Ada",
            "code validate",
        ]
        assert totals.problems == ["Totals (sheet): from code"]

    def test_a_hook_that_raises_is_its_tab_s_error(self, tmp_path, module):
        name = module(CHECKS)
        target = self.pull_target(tmp_path, {"validate": f"{name}:fails"})
        report = run_target(grid_of(), "S", target, "pull", apply=True)
        members, totals = report.tabs
        assert members.error == (
            f"tab 'Members': hook validate '{name}:fails' raised KeyError: 'member_id'"
        )
        assert totals.error is None and totals.wrote_local
        assert report.exit_code == 1
        assert not (tmp_path / "m.csv").exists()

    def test_a_name_that_does_not_resolve_stops_the_run_before_any_request(
        self, tmp_path, module
    ):
        name = module(CHECKS)
        target = self.pull_target(tmp_path, {"validate": f"{name}:nope"})
        grid = grid_of()
        with pytest.raises(ConfigError, match="has no 'nope'"):
            run_target(grid, "S", target, "pull")
        assert grid.calls == []

    def test_a_tab_run_by_itself_runs_its_hooks(self, tmp_path, module):
        name = module(CHECKS)
        tabs = {
            "Members": {
                "mode": "pull",
                "local": "m.csv",
                "hooks": {"warn": f"{name}:warn"},
            },
            "Summary": {
                "mode": "push",
                "local": "s.csv",
                "hooks": {"validate": f"{name}:validate"},
            },
        }
        pulled, pushed = target_of(tmp_path, tabs).tabs
        report = pull_tab(grid_of(), "S", pulled)
        assert report.warnings == ["2 rows"]
        write_values_csv(tmp_path / "s.csv", [HEADER, ["m3", "Cy", "closed"]])
        report = push_tab(grid_of({"Summary": [HEADER]}), "S", pushed)
        assert report.problems == ["Summary (local): m3 is closed"]


# -- the commands --


class Project:
    """A config under ``tmp_path``, the fake sheet, and what was asked of it."""

    def __init__(self, root: Path, grid: FakeSheetGrid) -> None:
        self.root = root
        self.grid = grid
        self.scopes: list[list[str] | None] = []

    def configure(self, tabs, **fields) -> None:
        data = {"roster": {"spreadsheet": "SHEET", "tabs": tabs} | fields}
        (self.root / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")

    def invoke(self, *args: str):
        return CliRunner().invoke(app, list(args))

    def writes(self) -> list[str]:
        return [method for method in self.grid.methods if method not in READS]


@pytest.fixture
def project(tmp_path, monkeypatch):
    grid = grid_of({"Summary": [HEADER]})
    made = Project(tmp_path, grid)
    write_values_csv(tmp_path / "members.csv", [HEADER, *ROWS])
    write_values_csv(tmp_path / "sheets-base/roster/Members.csv", [HEADER, *ROWS])
    write_values_csv(tmp_path / "summary.csv", [HEADER, ["m3", "Cy", "closed"]])
    monkeypatch.chdir(tmp_path)

    def build(scopes=None):
        made.scopes.append(scopes)
        return grid

    monkeypatch.setattr("gdrives.auth.build_sheets_service", build)
    monkeypatch.setattr(
        "gdrives.auth.describe_credentials", lambda scopes=None, *, force=False: None
    )
    return made


TABS = {
    "Members": {"local": "members.csv", "key": ["member_id"]},
    "Totals": {"mode": "pull", "local": "totals.csv"},
    "Summary": {"mode": "push", "local": "summary.csv"},
}


class TestTheCommands:
    @pytest.mark.parametrize(
        ("command", "tab", "stage"),
        [
            ("sheets-sync", "Members", "merged"),
            ("sheets-pull", "Totals", "sheet"),
            ("sheets-push", "Summary", "local"),
        ],
    )
    def test_each_command_runs_the_config_s_hooks(
        self, project, module, command, tab, stage
    ):
        name = module(CHECKS)
        hooks = {"check": f"{name}:check", "warn": f"{name}:warn"}
        project.configure(TABS, hooks=hooks)
        result = project.invoke(command, "roster")
        assert result.exit_code == 0, result.output
        assert "  warnings (1):" in result.stdout
        assert sys.modules[name].CALLS[-2:] == [f"check {stage}", "warn"]

    def test_a_blocking_hook_s_message_reaches_the_report(self, project, module):
        name = module(CHECKS)
        tabs = TABS | {
            "Summary": TABS["Summary"] | {"hooks": {"validate": f"{name}:validate"}}
        }
        project.configure(tabs)
        result = project.invoke("sheets-push", "roster", "--apply")
        assert result.exit_code == 1
        assert "  problems (1), so nothing is written:" in result.stdout
        assert "Summary (local): m3 is closed" in result.stdout
        assert project.writes() == []

    def test_a_pull_writes_what_the_config_s_transform_returns(self, project, module):
        name = module(CHECKS)
        project.configure(TABS, hooks={"transform": f"{name}:upper"})
        result = project.invoke("sheets-pull", "roster", "--apply")
        assert result.exit_code == 0, result.output
        rows = read_records(project.root / "totals.csv").rows
        assert [row["name"] for row in rows] == ["ADA", "BO"]

    def test_a_hook_that_raises_is_reported_and_exits_1(self, project, module):
        name = module(CHECKS)
        project.configure(TABS, hooks={"validate": f"{name}:fails"})
        result = project.invoke("sheets-pull", "roster")
        assert result.exit_code == 1
        assert f"hook validate '{name}:fails' raised KeyError" in result.stdout

    def test_a_name_that_does_not_resolve_is_refused_before_any_request(
        self, project, module
    ):
        name = module(CHECKS)
        project.configure(TABS, hooks={"check": f"{name}:nope"})
        result = project.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 1
        assert f"module {name!r} has no 'nope'" in result.output
        assert project.grid.calls == [] and project.scopes == []
        assert "Spreadsheet ID" not in result.output

    def test_a_malformed_name_is_refused_before_any_request(self, project, imports):
        project.configure(TABS, hooks={"check": "not a name"})
        result = project.invoke("sheets-pull", "roster")
        assert result.exit_code == 1
        assert "hook 'check' must be 'module:function'" in result.output
        assert project.grid.calls == [] and imports == []

    def test_only_the_tabs_run_are_imported(self, project, imports):
        tabs = TABS | {
            "Summary": TABS["Summary"] | {"hooks": {"check": "nowhere_at_all:f"}}
        }
        project.configure(tabs)
        result = project.invoke("sheets-pull", "roster", "--tab", "Totals")
        assert result.exit_code == 0, result.output
        assert imports == []

    @pytest.mark.parametrize("command", ["sheets-sync", "sheets-pull", "sheets-push"])
    def test_a_config_with_no_hooks_imports_nothing(self, project, imports, command):
        project.configure(TABS)
        result = project.invoke(command, "roster")
        assert result.exit_code == 0, result.output
        assert imports == []
