"""Tests for a tab's ``schema`` named in the config as ``module:attribute``.

Reading a config checks the reference's form and imports nothing. A run
resolves it before its first request, in the pass that finds the hooks
(``resolve_target``), and checks the schema it finds as a config's own
``schema`` object is checked at load, in the same words. The modules the
tests import are written under ``tmp_path`` as ``clubtools_schema_<n>``, each
under a name no other test uses, and put on ``sys.path`` for the test alone.
"""

import importlib
import itertools
import json
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest
from helpers import FakeSheetGrid
from typer.testing import CliRunner

from gdrives.auth import CredentialInfo
from gdrives.cli import app
from gdrives.sheets import (
    CONFIG_NAME,
    ColumnSchema,
    ConfigError,
    JsonEntryStore,
    TabConfig,
    Target,
    load_config,
    parse_config,
    plan_tab,
    pull_tab,
    push_tab,
    resolve_tab,
    resolve_target,
    run_target,
    sync_tab,
    write_values_csv,
)

HEADER = ["member_id", "name", "dues"]
ROWS = [["m1", "Ada", 10], ["m2", "Bo", 20]]
# The same rows as a local file holds them.
TEXT_ROWS = [[str(cell) for cell in row] for row in ROWS]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}

_NAMES = itertools.count()

MEMBERS = """
    from gdrives.sheets import ColumnSchema

    CALLS = []

    MEMBERS = {"member_id": ColumnSchema(required=True), "dues": ColumnSchema("int")}


    def by_title(title):
        CALLS.append(title)
        return {"dues": ColumnSchema("int")} if title == "Members" else {}


    def fails(title):
        raise KeyError(title)


    def listed(title):
        return ["dues"]


    NOT_A_SCHEMA = ["dues"]
"""


@pytest.fixture
def module(tmp_path, monkeypatch):
    """Write a module under ``tmp_path``; return the name it imports by."""
    folder = tmp_path / "schema_modules"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    made: list[str] = []

    def write(source: str) -> str:
        name = f"clubtools_schema_{next(_NAMES)}"
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


def target_of(tmp_path, tabs, **fields):
    data = {"t": {"spreadsheet": "S", "tabs": tabs} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def problems_of(tmp_path, tabs, **fields):
    with pytest.raises(ConfigError) as caught:
        target_of(tmp_path, tabs, **fields)
    return caught.value.problems


def resolve_problems(target, tabs=None):
    with pytest.raises(ConfigError) as caught:
        resolve_target(target, tabs)
    return caught.value.problems


def tab(**fields):
    """A valid sync tab, with ``fields`` added or replaced."""
    return {"local": "m.csv", "key": ["member_id"]} | fields


def grid_of(extra=None) -> FakeSheetGrid:
    return FakeSheetGrid({"Members": [HEADER, *ROWS]} | (extra or {}))


# -- the config --


class TestTheConfigNamesASchema:
    def test_the_reference_is_kept_and_nothing_is_imported(self, tmp_path, imports):
        (loaded,) = target_of(
            tmp_path, {"Members": tab(schema="clubtools.schema:MEMBERS")}
        ).tabs
        assert loaded.schema_ref == "clubtools.schema:MEMBERS"
        assert loaded.schema == {}
        assert imports == []

    def test_load_config_imports_nothing(self, tmp_path, imports, module):
        # One reference to a module that does not exist, one to a module
        # whose import would raise: loading imports neither.
        broken = module("raise RuntimeError('broken at import')")
        tabs = {
            "Members": tab(schema="clubtools_nowhere.schema:MEMBERS"),
            "Dues": tab(local="d.csv", schema=f"{broken}:DUES"),
        }
        path = tmp_path / CONFIG_NAME
        path.write_text(json.dumps({"t": {"spreadsheet": "S", "tabs": tabs}}))
        members, dues = load_config(path).target("t").tabs
        assert members.schema_ref == "clubtools_nowhere.schema:MEMBERS"
        assert dues.schema_ref == f"{broken}:DUES"
        assert imports == []
        assert broken not in sys.modules

    @pytest.mark.parametrize(
        ("schema", "problem"),
        [
            (
                "clubtools",
                "'schema' must be an object of columns or 'module:attribute', "
                "not 'clubtools'",
            ),
            (
                "a:b:c",
                "'schema' must be an object of columns or 'module:attribute', "
                "not 'a:b:c'",
            ),
            (3, "'schema' must be an object of columns"),
        ],
    )
    def test_a_malformed_reference_is_refused(self, tmp_path, schema, problem):
        assert problems_of(tmp_path, {"T": tab(schema=schema)}) == [
            f"target 't', tab 'T': {problem}"
        ]

    def test_the_checks_that_need_the_columns_wait(self, tmp_path, imports):
        # Inline, "status" outside the projection is refused at load; by
        # reference nothing is known of the columns until a run.
        fields = {"columns": ["member_id", "name"], "schema": "m:SCHEMA"}
        (loaded,) = target_of(tmp_path, {"T": tab(**fields)}).tabs
        assert loaded.columns == ("member_id", "name")
        assert imports == []

    def test_two_tabs_writing_one_file_are_refused_with_a_reference(self, tmp_path):
        tabs = {
            "A": {"mode": "pull", "local": "m.json", "schema": "m:A"},
            "B": {"mode": "pull", "local": "m.json"},
        }
        (found,) = problems_of(tmp_path, tabs)
        assert "would be written by more than one tab" in found

    def test_a_base_file_entry_is_checked_with_a_reference(self, tmp_path):
        tabs = {
            "A": tab(schema="m:A"),
            "B": {"mode": "pull", "local": "base.json"},
        }
        (found,) = problems_of(tmp_path, tabs, base_file="base.json")
        assert "would be written whole and by entry" in found


class TestATabBuiltInCode:
    def test_a_reference_is_checked_by_form(self):
        tab = TabConfig("T", Path("m.csv"), schema_ref="clubtools.schema:MEMBERS")
        assert tab.schema_ref == "clubtools.schema:MEMBERS"
        with pytest.raises(
            ValueError,
            match=r"tab 'T': 'schema_ref' must be 'module:attribute', not 'nope'",
        ):
            TabConfig("T", Path("m.csv"), schema_ref="nope")

    def test_schema_and_schema_ref_contradict_each_other(self):
        with pytest.raises(
            ValueError,
            match="tab 'T': 'schema' and 'schema_ref' contradict each other",
        ):
            TabConfig(
                "T",
                Path("m.csv"),
                schema={"dues": ColumnSchema("int")},
                schema_ref="clubtools.schema:MEMBERS",
            )

    def test_an_unresolved_tab_has_no_types_and_no_store(self):
        tab = TabConfig("T", Path("m.json"), schema_ref="clubtools.schema:MEMBERS")
        message = (
            "tab 'T': schema 'clubtools.schema:MEMBERS' is not resolved; a run "
            "resolves it before its first request"
        )
        with pytest.raises(ValueError, match=message):
            _ = tab.types
        with pytest.raises(ValueError, match=message):
            _ = tab.local_store

    def test_an_unresolved_tab_has_no_base_file_store(self, tmp_path):
        target = target_of(tmp_path, {"T": tab(schema="m:S")}, base_file="b.json")
        assert target.base_stores == {}
        with pytest.raises(ValueError, match="is not resolved"):
            target.base_store(target.tabs[0])

    def test_a_tab_built_in_code_resolves(self, module):
        name = module(MEMBERS)
        tab = TabConfig("Members", Path("m.csv"), schema_ref=f"{name}:by_title")
        resolved = resolve_tab(tab)
        assert resolved.schema == {"dues": ColumnSchema("int")}
        assert resolved.schema_ref is None
        assert resolved.types == {"dues": "int"}
        # A tab with no reference is returned as it is.
        assert resolve_tab(resolved) is resolved

    def test_a_target_built_in_code_takes_a_base_file(self, tmp_path):
        tab = TabConfig("T", tmp_path / "m.csv", key=("id",))
        target = Target("t", "S", tabs=(tab,), base_file=tmp_path / "b.json")
        assert target.base_store(tab) == JsonEntryStore(
            tmp_path / "b.json", "T", types={}
        )


# -- resolving --


class TestResolve:
    def test_a_mapping_is_the_schema(self, tmp_path, module):
        name = module(MEMBERS)
        target = target_of(tmp_path, {"Members": tab(schema=f"{name}:MEMBERS")})
        (resolved,) = resolve_target(target).tabs
        assert resolved.schema == {
            "member_id": ColumnSchema(required=True),
            "dues": ColumnSchema("int"),
        }
        assert resolved.schema_ref is None

    def test_a_function_is_given_the_tab_s_title(self, tmp_path, module):
        name = module(MEMBERS)
        ref = f"{name}:by_title"
        tabs = {"Members": tab(schema=ref), "Dues": tab(local="d.csv", schema=ref)}
        members, dues = resolve_target(target_of(tmp_path, tabs)).tabs
        assert members.schema == {"dues": ColumnSchema("int")}
        assert dues.schema == {}
        assert sys.modules[name].CALLS == ["Members", "Dues"]

    def test_only_the_tabs_asked_for_are_resolved(self, tmp_path, module, imports):
        tabs = {
            "A": tab(local="a.csv", schema="clubtools_nowhere:A"),
            "B": tab(local="b.csv"),
        }
        target = target_of(tmp_path, tabs)
        a, b = resolve_target(target, ["B"]).tabs
        assert a.schema_ref == "clubtools_nowhere:A"
        assert b is target.tabs[1]
        assert imports == []

    def test_a_config_with_no_reference_imports_nothing(self, tmp_path, imports):
        target = target_of(tmp_path, {"T": tab()})
        assert resolve_target(target).tabs == target.tabs
        assert imports == []

    def test_every_reference_that_does_not_resolve_is_listed(self, tmp_path, module):
        name = module(MEMBERS)
        broken = module("raise RuntimeError('broken at import')")
        refs = {
            "A": "clubtools_nowhere:A",
            "B": f"{broken}:B",
            "C": f"{name}:MISSING",
            "D": f"{name}:NOT_A_SCHEMA",
            "E": f"{name}:fails",
            "F": f"{name}:listed",
        }
        tabs = {
            title: tab(local=f"{title}.csv", schema=ref) for title, ref in refs.items()
        }
        with pytest.raises(ConfigError) as caught:
            resolve_target(target_of(tmp_path, tabs))
        assert str(caught.value).startswith("target 't': 6 problems:")
        assert caught.value.problems == [
            "tab 'A': schema 'clubtools_nowhere:A': cannot import "
            "'clubtools_nowhere': ModuleNotFoundError: No module named "
            "'clubtools_nowhere'",
            f"tab 'B': schema '{broken}:B': cannot import {broken!r}: "
            "RuntimeError: broken at import",
            f"tab 'C': schema '{name}:MISSING': module {name!r} has no 'MISSING'",
            f"tab 'D': schema '{name}:NOT_A_SCHEMA' is a list, not a mapping of "
            "column name to ColumnSchema or a function that returns one",
            f"tab 'E': schema '{name}:fails' raised KeyError: 'E'",
            f"tab 'F': schema '{name}:listed' returned a list, not a mapping of "
            "column name to ColumnSchema",
        ]

    @pytest.mark.parametrize(
        ("source", "problems"),
        [
            (
                "SCHEMA = {3: ColumnSchema(), 'dues': ColumnSchema('int')}",
                ["'schema' names a column that is not a string: 3"],
            ),
            (
                "SCHEMA = {'dues': {'type': 'int'}}",
                ["schema 'dues': expected a ColumnSchema, not a dict"],
            ),
            (
                "SCHEMA = {'dues': 'int'}",
                ["schema 'dues': expected a ColumnSchema, not a str"],
            ),
        ],
    )
    def test_a_value_the_json_form_cannot_hold_is_refused(
        self, tmp_path, module, source, problems
    ):
        name = module("from gdrives.sheets import ColumnSchema\n" + source)
        target = target_of(tmp_path, {"T": tab(schema=f"{name}:SCHEMA")})
        prefix = f"tab 'T': schema '{name}:SCHEMA': "
        assert resolve_problems(target) == [prefix + p for p in problems]

    def test_a_hook_and_a_schema_are_listed_together(self, tmp_path, module):
        name = module(MEMBERS)
        tabs = {
            "T": tab(schema=f"{name}:MISSING", hooks={"check": f"{name}:nope"}),
        }
        assert resolve_problems(target_of(tmp_path, tabs)) == [
            f"tab 'T': schema '{name}:MISSING': module {name!r} has no 'MISSING'",
            f"tab 'T': hook 'check' '{name}:nope': module {name!r} has no 'nope'",
        ]

    def test_resolve_tab_raises_for_its_tab(self, tmp_path):
        (loaded,) = target_of(tmp_path, {"T": tab(schema="clubtools_nowhere:S")}).tabs
        with pytest.raises(ConfigError, match=r"^tab 'T': 1 problem:"):
            resolve_tab(loaded)


# -- the same checks, in the same words --

# Each case: the tab's other fields, the schema as a config's JSON object, and
# a module defining SCHEMA, the same schema in Python.
SAME_CHECKS = {
    "projection": (
        {"columns": ["member_id", "name"]},
        {"status": {}},
        "SCHEMA = {'status': ColumnSchema()}",
    ),
    "exclude": (
        {"mode": "pull", "exclude": ["status"]},
        {"status": {}},
        "SCHEMA = {'status': ColumnSchema()}",
    ),
    "blank column": ({}, {" ": {}}, "SCHEMA = {' ': ColumnSchema()}"),
    "required": (
        {},
        {"dues": {"required": "yes"}},
        "SCHEMA = {'dues': ColumnSchema(required='yes')}",
    ),
    "present": (
        {},
        {"dues": {"present": 1}},
        "SCHEMA = {'dues': ColumnSchema(present=1)}",
    ),
    "strict": (
        {},
        {"paid": {"type": "bool", "strict": "yes"}},
        "SCHEMA = {'paid': ColumnSchema('bool', strict='yes')}",
    ),
    "pattern not a string": (
        {},
        {"link": {"pattern": 5}},
        "spec = ColumnSchema()\n"
        "object.__setattr__(spec, 'pattern', 5)\n"
        "SCHEMA = {'link': spec}",
    ),
    "description not a string": (
        {},
        {"link": {"description": 5}},
        "spec = ColumnSchema()\n"
        "object.__setattr__(spec, 'description', 5)\n"
        "SCHEMA = {'link': spec}",
    ),
    "pattern does not compile": (
        {},
        {"link": {"pattern": "[0-9"}},
        "spec = ColumnSchema()\n"
        "object.__setattr__(spec, 'pattern', '[0-9')\n"
        "SCHEMA = {'link': spec}",
    ),
    "pattern type": (
        {},
        {"dues": {"type": "int", "pattern": "[0-9]+"}},
        "spec = ColumnSchema('int')\n"
        "object.__setattr__(spec, 'pattern', '[0-9]+')\n"
        "SCHEMA = {'dues': spec}",
    ),
    "allowed empty": (
        {},
        {"status": {"allowed": []}},
        "SCHEMA = {'status': ColumnSchema(allowed=[])}",
    ),
    "allowed a string": (
        {},
        {"status": {"allowed": "active"}},
        "SCHEMA = {'status': ColumnSchema(allowed='active')}",
    ),
    "allowed not a scalar": (
        {},
        {"status": {"allowed": ["active", None]}},
        "SCHEMA = {'status': ColumnSchema(allowed=('active', None))}",
    ),
    "type": (
        {},
        {"dues": {"type": "money"}},
        # ColumnSchema refuses the type itself; one changed afterwards is still
        # caught when the reference is resolved.
        "spec = ColumnSchema()\n"
        "object.__setattr__(spec, 'type', 'money')\n"
        "SCHEMA = {'dues': spec}",
    ),
    "strict type": (
        {},
        {"dues": {"type": "int", "strict": True}},
        "spec = ColumnSchema('int')\n"
        "object.__setattr__(spec, 'strict', True)\n"
        "SCHEMA = {'dues': spec}",
    ),
    "several": (
        {"columns": ["member_id", "name", "dues"]},
        {"dues": {"required": "x", "present": "y"}, "status": {}},
        "SCHEMA = {'dues': ColumnSchema(required='x', present='y'),\n"
        "          'status': ColumnSchema()}",
    ),
}


class TestTheSameChecks:
    @pytest.mark.parametrize(
        ("fields", "inline", "source"), SAME_CHECKS.values(), ids=list(SAME_CHECKS)
    )
    def test_a_referenced_schema_is_refused_as_an_inline_one(
        self, tmp_path, module, fields, inline, source
    ):
        at_load = problems_of(tmp_path, {"T": tab(**fields, schema=inline)})
        load_prefix = "target 't', tab 'T': "
        assert at_load and all(p.startswith(load_prefix) for p in at_load)

        name = module("from gdrives.sheets import ColumnSchema\n" + source)
        target = target_of(tmp_path, {"T": tab(**fields, schema=f"{name}:SCHEMA")})
        found = resolve_problems(target)
        prefix = f"tab 'T': schema '{name}:SCHEMA': "
        assert all(p.startswith(prefix) for p in found)
        assert [p.removeprefix(prefix) for p in found] == [
            p.removeprefix(load_prefix) for p in at_load
        ]

    def test_a_referenced_schema_takes_allowed_dates(self, tmp_path, module):
        name = module(
            "from datetime import date, datetime\n"
            "from gdrives.sheets import ColumnSchema\n"
            "SCHEMA = {\n"
            "    'joined': ColumnSchema('date', allowed=[date(2026, 1, 1)]),\n"
            "    'seen': ColumnSchema('datetime', allowed=[datetime(2026, 1, 1, 9)]),\n"
            "}"
        )
        target = target_of(tmp_path, {"T": tab(local="m.csv", schema=f"{name}:SCHEMA")})
        (resolved,) = resolve_target(target).tabs
        assert resolved.schema["joined"].allowed == [date(2026, 1, 1)]

    @pytest.mark.parametrize("strict", [True, "local"])
    def test_strict_schema_lets_a_schema_name_a_column_outside_columns(
        self, tmp_path, module, strict
    ):
        fields = {"columns": ["member_id", "name"], "strict_schema": strict}
        (inline,) = target_of(
            tmp_path, {"T": tab(**fields, schema={"status": {}})}
        ).tabs
        name = module(
            "from gdrives.sheets import ColumnSchema\n"
            "SCHEMA = {'status': ColumnSchema()}"
        )
        target = target_of(tmp_path, {"T": tab(**fields, schema=f"{name}:SCHEMA")})
        (resolved,) = resolve_target(target).tabs
        assert resolved.schema == inline.schema


# -- a run --


class TestARun:
    def test_a_pull_to_a_json_file_is_typed_by_the_referenced_schema(
        self, tmp_path, module
    ):
        name = module(MEMBERS)
        tabs = {
            "Members": {"mode": "pull", "local": "m.json", "schema": f"{name}:by_title"}
        }
        report = run_target(
            grid_of(), "S", target_of(tmp_path, tabs), "pull", apply=True
        )
        assert report.exit_code == 0, report
        assert json.loads((tmp_path / "m.json").read_text()) == [
            {"member_id": "m1", "name": "Ada", "dues": 10},
            {"member_id": "m2", "name": "Bo", "dues": 20},
        ]

    @pytest.mark.parametrize(
        ("joined", "problems"),
        [
            ("2026-01-01", []),
            (
                "2026-02-01",
                [
                    "Members (local): key ('m1',), column 'joined': "
                    "'2026-02-01' is not one of ['2026-01-01']"
                ],
            ),
        ],
    )
    def test_an_allowed_date_of_a_referenced_schema_is_checked(
        self, tmp_path, module, joined, problems
    ):
        name = module(
            "from datetime import date\n"
            "from gdrives.sheets import ColumnSchema\n"
            "SCHEMA = {'joined': ColumnSchema('date', allowed=[date(2026, 1, 1)])}"
        )
        header = ["member_id", "joined"]
        write_values_csv(tmp_path / "m.csv", [header, ["m1", joined]])
        tabs = {"Members": tab(schema=f"{name}:SCHEMA")}
        report = run_target(
            FakeSheetGrid({"Members": [header, ["m1", joined]]}),
            "S",
            target_of(tmp_path, tabs),
            "sync",
        )
        (members,) = report.tabs
        assert members.problems == problems

    def test_a_base_file_is_typed_by_the_referenced_schema(self, tmp_path, module):
        name = module(MEMBERS)
        write_values_csv(tmp_path / "m.csv", [HEADER, *TEXT_ROWS])
        tabs = {"Members": tab(schema=f"{name}:MEMBERS")}
        target = target_of(tmp_path, tabs, base_file="base.json")
        report = run_target(grid_of(), "S", target, "sync", apply=True)
        assert report.exit_code == 0, report
        assert json.loads((tmp_path / "base.json").read_text())["Members"] == [
            {"member_id": "m1", "name": "Ada", "dues": 10},
            {"member_id": "m2", "name": "Bo", "dues": 20},
        ]

    def test_the_referenced_schema_is_checked(self, tmp_path, module):
        name = module(MEMBERS)
        write_values_csv(tmp_path / "m.csv", [HEADER, ["m1", "Ada", "ten"]])
        tabs = {"Members": tab(schema=f"{name}:MEMBERS")}
        report = run_target(grid_of(), "S", target_of(tmp_path, tabs), "sync")
        (members,) = report.tabs
        assert members.problems == [
            "Members (local): key ('m1',), column 'dues': 'ten' is not a valid int"
        ]

    def test_a_bad_hook_and_a_bad_schema_stop_the_run_before_any_request(
        self, tmp_path, module
    ):
        name = module(MEMBERS)
        tabs = {
            "Members": tab(schema=f"{name}:MISSING", hooks={"warn": f"{name}:nope"})
        }
        grid = grid_of()
        with pytest.raises(ConfigError) as caught:
            run_target(grid, "S", target_of(tmp_path, tabs), "sync")
        assert len(caught.value.problems) == 2
        assert grid.calls == []

    @pytest.mark.parametrize("run", [plan_tab, sync_tab])
    def test_a_sync_tab_run_by_itself_resolves_its_schema(self, tmp_path, module, run):
        name = module(MEMBERS)
        write_values_csv(tmp_path / "m.csv", [HEADER, ["m1", "Ada", "ten"]])
        target = target_of(tmp_path, {"Members": tab(schema=f"{name}:MEMBERS")})
        found = run(grid_of(), "S", target, target.tabs[0])
        report = found if run is sync_tab else found.report
        assert report.problems == [
            "Members (local): key ('m1',), column 'dues': 'ten' is not a valid int"
        ]

    def test_a_pull_or_push_tab_run_by_itself_resolves_its_schema(
        self, tmp_path, module
    ):
        name = module(MEMBERS)
        ref = f"{name}:by_title"
        tabs = {
            "Members": {"mode": "pull", "local": "m.json", "schema": ref},
            "Summary": {"mode": "push", "local": "s.csv", "schema": ref},
        }
        pulled, pushed = target_of(tmp_path, tabs).tabs
        report = pull_tab(grid_of(), "S", pulled, apply=True)
        assert report.wrote_local
        assert json.loads((tmp_path / "m.json").read_text())[0]["dues"] == 10
        write_values_csv(tmp_path / "s.csv", [HEADER, ["m3", "Cy", "3"]])
        report = push_tab(grid_of({"Summary": [HEADER]}), "S", pushed)
        assert report.error is None and not report.problems
        assert sys.modules[name].CALLS == ["Members", "Summary"]

    def test_a_tab_run_by_itself_lists_its_hook_and_its_schema_together(
        self, tmp_path, module
    ):
        name = module(MEMBERS)
        tabs = {
            "Members": {
                "mode": "pull",
                "local": "m.csv",
                "schema": f"{name}:MISSING",
                "hooks": {"warn": f"{name}:nope"},
            }
        }
        grid = grid_of()
        with pytest.raises(ConfigError) as caught:
            pull_tab(grid, "S", target_of(tmp_path, tabs).tabs[0])
        assert str(caught.value).startswith("tab 'Members': 2 problems:")
        assert grid.calls == []


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
    write_values_csv(tmp_path / "members.csv", [HEADER, *TEXT_ROWS])
    write_values_csv(tmp_path / "summary.csv", [HEADER, ["m3", "Cy", "3"]])
    monkeypatch.chdir(tmp_path)

    def build(scopes=None):
        made.scopes.append(scopes)
        return grid

    monkeypatch.setattr("gdrives.auth.build_sheets_service", build)
    monkeypatch.setattr(
        "gdrives.auth.describe_credentials",
        lambda scopes=None, *, force=False: CredentialInfo(kind="adc"),
    )
    return made


class TestTheCommands:
    def test_a_pull_writes_a_json_file_typed_by_the_reference(self, project, module):
        name = module(MEMBERS)
        tabs = {
            "Members": {"mode": "pull", "local": "m.json", "schema": f"{name}:MEMBERS"}
        }
        project.configure(tabs)
        result = project.invoke("sheets-pull", "roster", "--apply")
        assert result.exit_code == 0, result.output
        rows = json.loads((project.root / "m.json").read_text())
        assert [row["dues"] for row in rows] == [10, 20]

    def test_a_sync_writes_a_base_file_typed_by_the_reference(self, project, module):
        name = module(MEMBERS)
        tabs = {"Members": tab(local="members.csv", schema=f"{name}:MEMBERS")}
        project.configure(tabs, base_file="base.json")
        result = project.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 0, result.output
        base = json.loads((project.root / "base.json").read_text())
        assert [row["dues"] for row in base["Members"]] == [10, 20]

    def test_a_push_checks_the_referenced_schema(self, project, module):
        name = module(MEMBERS)
        write_values_csv(project.root / "summary.csv", [HEADER, ["m3", "Cy", "x"]])
        tabs = {
            "Summary": {
                "mode": "push",
                "local": "summary.csv",
                "schema": f"{name}:MEMBERS",
            }
        }
        project.configure(tabs)
        result = project.invoke("sheets-push", "roster", "--apply")
        assert result.exit_code == 1
        assert "column 'dues': 'x' is not a valid int" in result.stdout
        assert project.writes() == []

    def test_a_bad_hook_and_a_bad_schema_are_refused_together_before_any_request(
        self, project, module
    ):
        name = module(MEMBERS)
        tabs = {
            "Members": tab(
                local="members.csv",
                schema=f"{name}:MISSING",
                hooks={"check": f"{name}:nope"},
            )
        }
        project.configure(tabs)
        result = project.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 1
        assert f"schema '{name}:MISSING': module {name!r} has no 'MISSING'" in (
            result.output
        )
        assert f"hook 'check' '{name}:nope': module {name!r} has no 'nope'" in (
            result.output
        )
        assert project.grid.calls == [] and project.scopes == []
        assert "Spreadsheet ID" not in result.output

    def test_a_schema_check_at_resolution_is_a_config_problem(self, project, module):
        name = module(MEMBERS)
        tabs = {
            "Members": tab(
                local="members.csv",
                columns=["member_id", "name"],
                schema=f"{name}:MEMBERS",
            )
        }
        project.configure(tabs)
        result = project.invoke("sheets-sync", "roster")
        assert result.exit_code == 1
        assert (
            f"tab 'Members': schema '{name}:MEMBERS': schema column(s) "
            "['dues'] not in 'columns'"
        ) in result.output
        assert project.grid.calls == []

    def test_only_the_tabs_run_are_imported(self, project, imports):
        tabs = {
            "Members": tab(local="members.csv"),
            "Summary": {
                "mode": "push",
                "local": "summary.csv",
                "schema": "clubtools_nowhere:S",
            },
        }
        project.configure(tabs, base_file="base.json")
        result = project.invoke("sheets-sync", "roster")
        assert result.exit_code == 0, result.output
        assert imports == []
