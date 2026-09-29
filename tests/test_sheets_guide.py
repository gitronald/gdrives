"""The sync guide and the package surface, checked against the code.

``docs/sheets-sync.md`` is read as data: its config examples go through the
loader, its command lines through the command-line parser, and its Python
examples run against ``FakeSheetGrid``. A name the guide or the changelog
promises is imported from ``gdrives.sheets``. So an example with a field the
loader refuses, an option a command lacks, or a name that is not exported
fails the suite.
"""

import dataclasses
import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from helpers import FakeSheetGrid
from typer.testing import CliRunner

import gdrives.local
import gdrives.sheets
from gdrives.cli import app
from gdrives.sheets import (
    ApplyResult,
    MergePlan,
    TabConfig,
    Table,
    TabReport,
    Target,
    parse_config,
    write_values_csv,
)
from gdrives.sheets.config import _TAB_FIELDS, _TARGET_FIELDS

ROOT = Path(__file__).parent.parent
GUIDE = ROOT / "docs" / "sheets-sync.md"
README = ROOT / "README.md"

#: The names each step of the sync adoption work added to ``gdrives.sheets``.
PROMISED = {
    "where rows and columns land, and line endings": [
        "NEWLINES",
        "insert_point",
        "place_columns",
    ],
    "typed cells": [
        "SERIAL_TYPES",
        "column_type",
        "decode_rows",
        "encode_rows",
        "pull_serials",
        "serial_to_cell",
    ],
    "merge additions": [
        "BLANK_KEYS",
        "ON_INVALID",
        "HeldCell",
        "cell_problem",
        "check_blank_keys",
        "normalize_cell",
    ],
    "checks and hooks": ["STAGES", "CheckContext"],
    "stores": ["FileStore", "MemoryStore", "Records", "Store"],
    "push_rows and links": [
        "CELL_LINK_FIELD",
        "LINK_FIELDS",
        "RUNS_FIELD",
        "GridTooLargeError",
        "LinkedCell",
        "clear_link_format",
        "decode_errors",
        "link_clear",
        "linked_cells",
        "pull_grid",
        "push_rows",
        "strip_links",
    ],
    "tabs and tools": [
        "WIDTH_FIELDS",
        "RetryNotice",
        "TabListing",
        "get_column_widths",
        "retry_notices",
        "run_widths",
        "tab_listing",
    ],
    "reorder rows": ["ReorderResult", "reorder_rows"],
    "json entry store": ["JsonEntryStore"],
    "read as displayed": ["RENDERS"],
    "url links": [
        "URL_LINK_REASONS",
        "UrlLinkProblem",
        "set_url_links",
        "url_link_problems",
    ],
    "link audit": [
        "CELL_STYLE_FIELDS",
        "LINK_COLOR",
        "LINK_DETAIL_FIELDS",
        "LINK_STYLE_REASONS",
        "StyledCell",
        "styled_cells",
    ],
    "config hooks": ["HOOKS", "resolve_hooks", "tab_hooks"],
    "report and run seams": ["print_retry"],
    "schema by reference": ["resolve_tab", "resolve_target"],
    "typed writes": [
        "DATE_FORMATS",
        "NUMBER_FORMAT_FIELD",
        "RetypeCell",
        "RetypeReport",
        "cell_data",
        "dated_cells",
        "format_requests",
        "retype_columns",
        "to_serial",
        "typed_columns",
    ],
}

#: The fields and properties those steps added to classes that existed.
PROMISED_ATTRIBUTES = [
    (MergePlan, ["held", "sheet_writes", "local_writes", "has_writes"]),
    (Table, ["types", "blank_keys"]),
    (ApplyResult, ["pushed_cells", "appended_columns"]),
    (TabReport, ["insert_row", "last_row", "warnings", "local_label", "applied"]),
    (TabConfig, ["newline", "blank_keys", "on_invalid", "clear_links", "sheet_id"]),
    (TabConfig, ["render"]),
    (Table, ["render"]),
    (TabConfig, ["store", "local_store"]),
    (Target, ["base_stores", "base_store"]),
    (TabConfig, ["entry"]),
    (TabConfig, ["link_urls"]),
    (ApplyResult, ["linked"]),
    (TabReport, ["linked"]),
    (TabConfig, ["hooks"]),
    (TabConfig, ["typed_writes"]),
    (TabReport, ["pending", "exit_code"]),
    (gdrives.sheets.SyncReport, ["pending"]),
    (Target, ["spreadsheet_id"]),
    (TabConfig, ["schema_ref"]),
    (Target, ["base_file"]),
    (gdrives.sheets.LinkedCell, ["text", "formula"]),
]


def blocks(path: Path, language: str) -> list[str]:
    """The fenced blocks of ``language`` in the Markdown file at ``path``."""
    text = path.read_text(encoding="utf-8")
    return re.findall(rf"^```{language}\n(.*?)^```$", text, flags=re.M | re.S)


def table_names(heading: str) -> set[str]:
    """The names in the first column of the table under a guide heading."""
    text = GUIDE.read_text(encoding="utf-8")
    section = text.split(f"### {heading}\n", 1)[1].split("\n#", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.M))


def command_lines(path: Path) -> list[list[str]]:
    """Every ``gdrives`` command line in the file's bash blocks, as arguments."""
    lines = [line for block in blocks(path, "bash") for line in block.splitlines()]
    # A trailing backslash continues a command on the next line.
    joined = re.sub(r"\\\n\s*", " ", "\n".join(lines)).splitlines()
    split = [shlex.split(line, comments=True) for line in joined]
    return [words[1:] for words in split if words[:1] == ["gdrives"]]


class TestPromisedNames:
    @pytest.mark.parametrize(
        "name", [name for names in PROMISED.values() for name in names]
    )
    def test_is_exported(self, name):
        assert name in gdrives.sheets.__all__
        assert getattr(gdrives.sheets, name) is not None

    def test_every_added_name_is_listed_once(self):
        listed = [name for names in PROMISED.values() for name in names]
        assert len(listed) == len(set(listed))

    def test_slug_is_in_local(self):
        assert gdrives.local.slug("Form responses 1") == "form-responses-1"

    @pytest.mark.parametrize(("owner", "names"), PROMISED_ATTRIBUTES)
    def test_attributes(self, owner, names):
        fields = {field.name for field in dataclasses.fields(owner)}
        for name in names:
            assert name in fields or hasattr(owner, name), f"{owner.__name__}.{name}"


def as_config(example: str) -> dict[str, Any]:
    """A guide's JSON example as a whole config, whatever part of one it shows."""
    try:
        data = json.loads(example)
    except json.JSONDecodeError:
        # A tab by itself, as `"Title": {...}`.
        tabs = json.loads("{" + example + "}")
        return {"example": {"spreadsheet": "S", "tabs": tabs}}
    if all(isinstance(value, int) for value in data.values()):
        # The output of sheets-widths, which goes under a tab's widths.
        tab = {"mode": "push", "local": "m.csv", "widths": data}
        return {"example": {"spreadsheet": "S", "tabs": {"Members": tab}}}
    return data


class TestGuideConfigs:
    @pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
    def test_every_json_example_loads(self, path, tmp_path):
        examples = blocks(path, "json")
        assert examples
        for example in examples:
            config = parse_config(as_config(example), tmp_path / "gdrives-sheets.json")
            assert config.targets

    def test_the_guide_has_the_examples_this_reads(self):
        assert len(blocks(GUIDE, "json")) == 16
        assert len(blocks(GUIDE, "python")) == 15

    def test_a_refused_example_fails(self, tmp_path):
        from gdrives.sheets import ConfigError

        example = '"Members": {"local": "m.csv", "mode": "both"}'
        with pytest.raises(ConfigError):
            parse_config(as_config(example), tmp_path / "gdrives-sheets.json")

    def test_the_field_tables_name_the_loader_s_fields(self):
        assert table_names("Tab fields") == set(_TAB_FIELDS)
        assert table_names("Target fields") == set(_TARGET_FIELDS)


def parses(words: list[str]) -> bool:
    """Whether the command and the options of a command line exist.

    With ``--help`` added the line is parsed and nothing runs: an unknown
    command or option is a usage error, and a known one prints the help.
    """
    return CliRunner().invoke(app, [*words, "--help"]).exit_code == 0


class TestGuideCommands:
    @pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
    def test_every_command_line_parses(self, path):
        lines = command_lines(path)
        assert lines
        assert [line for line in lines if not parses(line)] == []

    def test_a_command_or_an_option_that_is_missing_fails(self):
        assert parses(["sheets-pull", "roster", "--apply"])
        assert not parses(["sheets-pull", "roster", "--nope"])
        assert not parses(["sheets-nope", "roster"])


class TestGuidePython:
    def test_the_examples_run_in_order(self, tmp_path, monkeypatch):
        """Each block runs in one namespace, as a reader following the guide."""
        header = ["member_id", "name", "status", "website"]
        rows = [
            ["m1", "Ada", "active", "example.com"],
            ["m2", "Bea", "closed", ""],
            ["m3", "Cy", "active", ""],
        ]
        grid = FakeSheetGrid({"Members": [header, *rows]})
        write_values_csv(tmp_path / "data" / "members.csv", [header, *rows])
        held = [dict(zip(header[:3], row[:3], strict=True)) for row in rows]
        workbook = tmp_path / "data" / "workbook.json"
        workbook.write_text(json.dumps({"Members": held, "Summary": []}))
        quick_start = blocks(GUIDE, "json")[0]
        (tmp_path / "gdrives-sheets.json").write_text(quick_start)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: grid
        )
        namespace: dict[str, Any] = {"service": grid}
        exits = []
        for example in blocks(GUIDE, "python"):
            try:
                exec(compile(example, str(GUIDE), "exec"), namespace)  # noqa: S102
            except SystemExit as e:
                exits.append(e.code)
        # The library example ends by exiting with the run's code.
        assert exits == [0]
        assert grid.tab("Summary").cells[0][:3] == ["id", "total", "paid"]
        # The typed writes example turned the text the push wrote into values.
        assert grid.tab("Summary").cells[1][:3] == [1, 2.5, True]
        assert namespace["found"].changes and not namespace["found"].unparsed
        # The ordering example put the active row m3 above the closed m2.
        assert namespace["preview"].moves == 1
        members = [row[0] for row in grid.values("Members")]
        assert members == ["member_id", "m1", "m3", "m2"]
        assert json.loads(workbook.read_text())["Members"] == held
        # The hooks example runs the hooks, and its check finds the column that the
        # links example reads, which the store's tab does not declare.
        (checked,) = namespace["report"].tabs
        assert checked.problems == ["Members (merged): undeclared column 'website'"]
        # The pull_records example read the Summary tab the typed writes made.
        assert namespace["typed"] == [{"id": 1, "total": 2.5, "paid": True}]
        # The audit example sorts cells of each kind, on a tab that holds them.
        assert namespace["kinds"] == {}
        formats = grid.tab("Members").formats
        formats[(1, 3)] = {"link": "https://own.io"}
        grid.tab("Members").cells[1][3] = "https://own.io"
        formats[(2, 3)] = {"link": "https://x.io"}
        grid.tab("Members").cells[2][3] = "click"
        formats[(3, 3)] = {"link": "https://y.io", "formula": "=HYPERLINK(1)"}
        grid.tab("Members").cells[3][3] = "label"
        formats[(1, 1)] = {"link": "https://z.io"}
        grid.tab("Members").cells[1][1] = None
        formats[(2, 0)] = {"underline": True}
        grid.tab("Members").cells[2][0] = "m3"
        (audit,) = [b for b in blocks(GUIDE, "python") if "kinds:" in b]
        exec(compile(audit, str(GUIDE), "exec"), namespace)  # noqa: S102
        assert namespace["kinds"] == {
            "link to its own text": [(2, "website")],
            "link to somewhere else": [(3, "website")],
            "formula link": [(4, "website")],
            "link on an empty cell": [(2, "name")],
            "link styling with no link": [(3, "member_id")],
        }
        # The transform example previews a sync of the same tab, in sync.
        assert namespace["cleaned"].exit_code == 0
