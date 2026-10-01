"""Tests for the schema export: ``schema_rows``, ``format_schema``, ``sheets-schema``.

A read of the config alone: no request and no credential. A schema named as
``module:attribute`` is imported, hooks are not. Modules are written under
``tmp_path`` as ``clubtools_docs_<n>`` and put on ``sys.path`` for the test.
"""

import csv
import importlib
import io
import itertools
import json
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gdrives.cli import app
from gdrives.sheets import (
    CONFIG_NAME,
    ColumnSchema,
    ConfigError,
    format_schema,
    parse_config,
    read_records,
    resolve_schemas,
    resolve_target,
    schema_rows,
)
from gdrives.sheets.schema import SCHEMA_COLUMNS

_NAMES = itertools.count()

MEMBERS = {
    "member_id": {"required": True, "description": "The member's number."},
    "status": {"allowed": ["active", "closed"], "present": True},
    "paid": {"type": "bool", "strict": True},
    "link": {
        "pattern": "https://example\\.com/[0-9]+",
        "description": 'A, "quoted"',
        "pattern_hint": "a member page link",
    },
}


def target_of(tmp_path, tabs, **fields):
    data = {"roster": {"spreadsheet": "S", "tabs": tabs} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("roster")


def members(**fields):
    return {"local": "members.csv", "key": ["member_id"], "schema": MEMBERS} | fields


@pytest.fixture
def module(tmp_path, monkeypatch):
    folder = tmp_path / "docs_modules"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    made: list[str] = []

    def write(source: str) -> str:
        name = f"clubtools_docs_{next(_NAMES)}"
        (folder / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
        importlib.invalidate_caches()
        made.append(name)
        return name

    yield write
    for name in made:
        sys.modules.pop(name, None)


class TestSchemaRows:
    def test_one_row_per_declared_column_in_order(self, tmp_path):
        rows = schema_rows(target_of(tmp_path, {"Members": members()}))
        assert [row["column"] for row in rows] == list(MEMBERS)
        assert all(list(row) == list(SCHEMA_COLUMNS) for row in rows)
        assert rows[0] == {
            "tab": "Members",
            "column": "member_id",
            "key": "TRUE",
            "type": "str",
            "required": "TRUE",
            "present": "FALSE",
            "strict": "FALSE",
            "allowed": "",
            "pattern": "",
            "description": "The member's number.",
            "pattern_hint": "",
        }
        status, paid, link = rows[1:]
        assert status["allowed"] == '["active", "closed"]'
        assert status["present"] == "TRUE" and status["key"] == "FALSE"
        assert paid["type"] == "bool" and paid["strict"] == "TRUE"
        assert link["pattern"] == "https://example\\.com/[0-9]+"
        assert link["pattern_hint"] == "a member page link"

    def test_tabs_follow_the_config_and_tabs_selects(self, tmp_path):
        tabs = {
            "Members": members(),
            "Dues": {"local": "dues.csv", "key": ["id"], "schema": {"id": {}}},
            "Empty": {"mode": "pull", "local": "empty.csv"},
        }
        target = target_of(tmp_path, tabs)
        rows = schema_rows(target)
        assert [row["tab"] for row in rows] == ["Members"] * 4 + ["Dues"]
        assert [row["tab"] for row in schema_rows(target, ["Dues", "Empty"])] == [
            "Dues"
        ]
        with pytest.raises(ValueError, match="has no tab 'Nope'"):
            schema_rows(target, ["Nope"])

    def test_allowed_values_are_canonical_cell_strings(self, tmp_path):
        schema = {
            "n": {"type": "int", "allowed": [1, 2.0, "3"]},
            "b": {"type": "bool", "allowed": [True]},
        }
        target = target_of(tmp_path, {"T": members(schema=schema)})
        n, b = schema_rows(target)
        assert n["allowed"] == '["1", "2", "3"]'
        assert b["allowed"] == '["TRUE"]'

    def test_a_set_of_allowed_values_is_sorted(self):
        from dataclasses import replace

        from gdrives.sheets import TabConfig, Target

        spec = ColumnSchema(allowed=frozenset({"b", "a", "é"}))
        tab = TabConfig("T", Path("t.csv"), schema={"c": spec})
        rows = schema_rows(replace(Target("t", "S"), tabs=(tab,)))
        assert rows[0]["allowed"] == '["a", "b", "é"]'

    def test_undeclared_key_and_projection_columns_are_not_listed(self, tmp_path):
        tab = members(columns=["member_id", "name", "status"], schema={"status": {}})
        rows = schema_rows(target_of(tmp_path, {"T": tab}))
        assert [row["column"] for row in rows] == ["status"]

    def test_an_unresolved_reference_is_refused(self, tmp_path):
        tab = {"local": "m.csv", "key": ["id"], "schema": "clubtools.schema:MEMBERS"}
        with pytest.raises(ValueError, match="not resolved; resolve it first"):
            schema_rows(target_of(tmp_path, {"T": tab}))


class TestFormatSchema:
    def test_csv_with_a_header_lf_endings_and_quoting(self, tmp_path):
        text = format_schema(schema_rows(target_of(tmp_path, {"Members": members()})))
        lines = text.split("\n")
        assert lines[0] == ",".join(SCHEMA_COLUMNS)
        assert "\r" not in text and text.endswith("\n")
        assert (
            lines[1]
            == "Members,member_id,TRUE,str,TRUE,FALSE,FALSE,,,The member's number.,"
        )
        assert lines[4].endswith(",a member page link")
        assert '"A, ""quoted"""' in lines[4]

    def test_values_are_exact_unless_formulas_are_escaped(self, tmp_path):
        rows = schema_rows(target_of(tmp_path, {"Members": members()}))
        rows[0]["description"] = "=HYPERLINK(1)"
        rows[1]["pattern"] = "-x"
        exact = list(csv.reader(io.StringIO(format_schema(rows))))
        rows[3]["pattern_hint"] = "+1"
        exact = list(csv.reader(io.StringIO(format_schema(rows))))
        assert (exact[1][9], exact[2][8], exact[4][10]) == ("=HYPERLINK(1)", "-x", "+1")
        text = format_schema(rows, escape_formulas=True)
        escaped = list(csv.reader(io.StringIO(text)))
        found = (escaped[1][9], escaped[2][8], escaped[4][10])
        assert found == ("'=HYPERLINK(1)", "'-x", "'+1")
        assert escaped[0] == exact[0] and escaped[1][:9] == exact[1][:9]
        assert rows[1]["pattern"] == "-x"

    def test_no_rows_is_the_header_alone(self):
        assert format_schema([]) == ",".join(SCHEMA_COLUMNS) + "\n"


class TestResolveSchemas:
    def test_it_fills_a_reference_and_imports_no_hook(self, tmp_path, module):
        name = module(
            "from gdrives.sheets import ColumnSchema\n"
            "SCHEMA = {'id': ColumnSchema(description='Number')}"
        )
        tab = {
            "local": "m.csv",
            "key": ["id"],
            "schema": f"{name}:SCHEMA",
            "hooks": {"check": "clubtools_missing_hooks:check"},
        }
        target = target_of(tmp_path, {"T": tab})
        (resolved,) = resolve_schemas(target).tabs
        assert resolved.schema_ref is None
        assert resolved.schema["id"].description == "Number"
        # resolve_target also finds the hook, and refuses the missing module.
        with pytest.raises(ConfigError, match="clubtools_missing_hooks"):
            resolve_target(target)

    def test_a_schema_that_does_not_resolve_is_a_config_error(self, tmp_path):
        tab = {"local": "m.csv", "key": ["id"], "schema": "clubtools_missing:X"}
        with pytest.raises(ConfigError, match="cannot import 'clubtools_missing'"):
            resolve_schemas(target_of(tmp_path, {"T": tab}))

    def test_tabs_left_out_are_not_imported(self, tmp_path):
        tab = {"local": "m.csv", "key": ["id"], "schema": "clubtools_missing:X"}
        tabs = {"A": tab, "B": members(local="b.csv")}
        found = resolve_schemas(target_of(tmp_path, tabs), ["B"])
        assert found.tab("A").schema_ref == "clubtools_missing:X"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def refuse(*args, **kwargs):
        raise AssertionError("sheets-schema must not build a service")

    monkeypatch.setattr("gdrives.auth.build_sheets_service", refuse)
    monkeypatch.setattr("gdrives.auth.describe_credentials", refuse)
    return tmp_path


def configure(root: Path, tabs, **fields) -> None:
    data = {"roster": {"spreadsheet": "SHEET", "tabs": tabs} | fields}
    (root / CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")


def invoke(*args: str):
    return CliRunner().invoke(app, ["sheets-schema", *args])


class TestTheCommand:
    def test_csv_goes_to_stdout(self, project):
        configure(project, {"Members": members()})
        result = invoke("roster")
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith(",".join(SCHEMA_COLUMNS) + "\n")
        assert result.stdout.count("\n") == 5

    def test_output_files_by_extension(self, project):
        configure(project, {"Members": members()})
        for name in ("schema.csv", "schema.tsv", "schema.json"):
            result = invoke("roster", "-o", str(project / name))
            assert result.exit_code == 0, result.output
            assert "Wrote 4 column(s)" in result.output
        assert (project / "schema.csv").read_bytes().count(b"\r") == 0
        rows = read_records(project / "schema.tsv").rows
        assert rows[1]["allowed"] == '["active", "closed"]'
        assert read_records(project / "schema.json").rows == rows
        assert read_records(project / "schema.csv").rows == rows

    def test_escape_formulas_applies_to_stdout_and_delimited_files(self, project):
        fields = {"status": {"pattern": "-x", "description": "=SUM(A1)"}}
        configure(project, {"Members": members() | {"schema": fields}})
        exact = invoke("roster")
        assert (
            exact.stdout.split("\n")[1]
            == "Members,status,FALSE,str,FALSE,FALSE,FALSE,,-x,=SUM(A1),"
        )
        result = invoke("roster", "--escape-formulas")
        assert result.exit_code == 0, result.output
        assert result.stdout.split("\n")[1] == (
            "Members,status,FALSE,str,FALSE,FALSE,FALSE,,'-x,'=SUM(A1),"
        )
        for name in ("schema.csv", "schema.tsv"):
            done = invoke("roster", "-o", str(project / name), "--escape-formulas")
            assert done.exit_code == 0, done.output
            (row,) = read_records(project / name).rows
            assert (row["pattern"], row["description"]) == ("'-x", "'=SUM(A1)")
        invoke("roster", "-o", str(project / "exact.csv"))
        (row,) = read_records(project / "exact.csv").rows
        assert (row["pattern"], row["description"]) == ("-x", "=SUM(A1)")

    def test_escape_formulas_is_refused_for_json(self, project):
        configure(project, {"Members": members()})
        result = invoke("roster", "-o", str(project / "s.json"), "--escape-formulas")
        assert result.exit_code == 1
        assert "escaping formulas applies only to .csv and .tsv" in result.output
        assert not (project / "s.json").exists()

    def test_tab_and_config_options(self, project):
        tabs = {
            "Members": members(),
            "Dues": {"local": "dues.csv", "key": ["id"], "schema": {"id": {}}},
        }
        configure(project, tabs)
        result = invoke("roster", "--tab", "Dues", "--tab", "Dues")
        assert result.exit_code == 0 and result.stdout.count("\n") == 2
        other = project / "elsewhere" / "cfg.json"
        other.parent.mkdir()
        other.write_text((project / CONFIG_NAME).read_text())
        (project / CONFIG_NAME).unlink()
        assert invoke("roster", "--config", str(other)).exit_code == 0

    def test_a_referenced_schema_runs_and_a_hook_does_not(self, project, module):
        name = module(
            "from gdrives.sheets import ColumnSchema\n"
            "SCHEMA = {'id': ColumnSchema('int', description='Number')}"
        )
        tab = {
            "local": "m.csv",
            "key": ["id"],
            "schema": f"{name}:SCHEMA",
            "hooks": {"check": "clubtools_missing_hooks:check"},
        }
        configure(project, {"Members": tab})
        result = invoke("roster")
        assert result.exit_code == 0, result.output
        assert "Members,id,TRUE,int,FALSE,FALSE,FALSE,,,Number" in result.stdout

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["nope"], "no target"),
            (["roster", "--tab", "Nope"], "has no tab 'Nope'"),
            (["roster", "-o", "schema.txt"], "unsupported record file type '.txt'"),
        ],
    )
    def test_a_problem_exits_1(self, project, args, message):
        configure(project, {"Members": members()})
        result = invoke(*args)
        assert result.exit_code == 1
        assert message in result.output
        assert not (project / "schema.txt").exists()

    def test_a_schema_that_does_not_resolve_exits_1(self, project):
        tab = {"local": "m.csv", "key": ["id"], "schema": "clubtools_missing:X"}
        configure(project, {"Members": tab})
        result = invoke("roster")
        assert result.exit_code == 1
        assert "cannot import 'clubtools_missing'" in result.output
