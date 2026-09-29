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
from typer.testing import CliRunner

import gdrives.local
import gdrives.sheets
from gdrives.auth import CredentialInfo, credential_line
from gdrives.cli import app
from gdrives.sheets import (
    ApplyResult,
    ColumnSchema,
    FileStore,
    MemoryStore,
    MergePlan,
    TabConfig,
    Table,
    TabReport,
    Target,
    format_report,
    merge,
    parse_config,
    parse_tab,
    pull_tab,
    read_records,
    run_target,
    sync_tab,
    write_records,
    write_values_csv,
)
from gdrives.sheets.config import _TAB_FIELDS, _TARGET_FIELDS
from gdrives.testing import FakeSheetGrid

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
    (TabConfig, ["schema_ref", "resolved"]),
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


class TestGuideCredentialLine:
    def test_the_fallback_line_is_the_one_credential_line_prints(self):
        shown = [
            line
            for line in GUIDE.read_text(encoding="utf-8").splitlines()
            if line.startswith("Credential: ") and ", since " in line
        ]
        info = CredentialInfo(
            kind="service_account",
            identity="sync-bot@<project>.iam.gserviceaccount.com",
            source=Path("<config-dir>/service_account.json"),
            consent_skipped=True,
        )
        assert shown == [credential_line(info)]


class TestGuideConfigs:
    @pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
    def test_every_json_example_loads(self, path, tmp_path):
        examples = blocks(path, "json")
        assert examples
        for example in examples:
            config = parse_config(as_config(example), tmp_path / "gdrives-sheets.json")
            assert config.targets

    def test_the_guide_has_the_examples_this_reads(self):
        assert len(blocks(GUIDE, "json")) == 18
        assert len(blocks(GUIDE, "python")) == 20

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
        grid = FakeSheetGrid(
            {
                "Members": [header, *rows],
                "Dues": [
                    ["id", "dues", "paid"],
                    ["m2", "20", "TRUE"],
                    ["m1", "10", "FALSE"],
                    ["m3", "30", "TRUE"],
                ],
                "Standing": [
                    ["id", "name", "dues", "paid"],
                    ["m1", "Ada", "12", "TRUE"],
                    ["m2", "Bea", "20", "FALSE"],
                    ["m3", "Cy", "30", "TRUE"],
                ],
            }
        )
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
        # The move-over steps: the merge case reads as the guide says, and the
        # store round trip is stable for a file the library wrote only.
        moved = namespace["case_plan"]
        assert [(c.key, c.column) for c in moved.pushes] == [(("m1",), "dues")]
        assert [(c.key, c.column) for c in moved.fold_cells] == [(("m1",), "name")]
        assert [(c.key, c.column) for c in moved.conflicts] == [(("m2",), "dues")]
        assert [row.key for row in moved.appends] == [("m5",)]
        assert [row.key for row in moved.fold_rows] == [("m3",)]
        assert [(f.key, f.flag) for f in moved.row_flags] == [
            (("m4",), "local_deleted")
        ]
        assert namespace["rewrites"] == [False, True, True]
        # The tidying preview reads the Dues tab and writes nothing.
        dues_preview = namespace["dues_preview"]
        assert (dues_preview.moves, dues_preview.moved) == (1, [("m2",)])
        assert not dues_preview.applied and not dues_preview.unchanged
        # The sorted store folds the sheet's m2 row into its place.
        assert [row["id"] for row in namespace["dues_held"].read().rows] == [
            "m1",
            "m2",
            "m3",
        ]
        assert (
            namespace["dues_first"].wrote_local
            and not namespace["dues_second"].wrote_local
        )
        assert namespace["dues_pulled"].wrote_local
        # The split store lands each column in its own file.
        assert (tmp_path / "data" / "dues.csv").read_text() == (
            "id,dues,paid\nm1,12,TRUE\nm2,20,FALSE\nm3,30,TRUE\n"
        )
        assert (tmp_path / "data" / "people.csv").read_text() == (
            "id,name\nm1,Ada\nm2,Bea\nm3,Cy\n"
        )
        assert namespace["joined"].wrote_local


class Sorted:
    """A store that sorts its rows by ``id`` when it is written, as the guide's does."""

    def __init__(self, columns=None, rows=()):
        self.inner = MemoryStore(columns, rows)
        self.label = "sorted"

    def exists(self):
        return self.inner.exists()

    def read(self):
        return self.inner.read()

    def write(self, columns, rows):
        self.inner.write(columns, sorted(rows, key=lambda row: row["id"]))

    @property
    def rows(self):
        return self.inner.rows


class TestMovingOverClaims:
    """The causes and the steps of "Moving an existing sync over", one by one."""

    def test_a_padded_header_is_stripped_on_the_sheet_and_in_a_file(self, tmp_path):
        grid = [[" name ", "First  name"], ["Ada", "A"]]
        table = parse_tab("Members", grid, None)
        # Leading and trailing whitespace only; a run inside a name stays.
        assert table.header == ["name", "First  name"]
        path = tmp_path / "m.csv"
        path.write_text(" name ,First  name\nAda,A\n")
        assert read_records(path).columns == ["name", "First  name"]
        (tmp_path / "m.json").write_text('[{" name ": "Ada"}]')
        assert read_records(tmp_path / "m.json").columns == ["name"]

    def test_the_configured_spelling_is_not_stripped(self):
        grid = [["name "], ["Ada"]]
        with pytest.raises(ValueError, match=r"has no column\(s\) \['name '\]"):
            parse_tab("Members", grid, ["name "])
        assert parse_tab("Members", grid, ["name"]).rows == [{"name": "Ada"}]

    def test_headers_that_differ_in_padding_are_a_repeat(self):
        with pytest.raises(ValueError, match=r"header repeats \['name'\]"):
            parse_tab("Members", [["name", "name "], ["a", "b"]], None)

    def test_a_padded_header_is_written_stripped(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text(" id , name \nm1,Ada\n")
        FileStore(path).write(*FileStore(path).read())
        assert path.read_text() == "id,name\nm1,Ada\n"

    def test_an_empty_cell_is_bare_and_a_quoted_file_reads_the_same(self, tmp_path):
        ours = tmp_path / "ours.csv"
        write_records(ours, ["id", "name"], [{"id": "m1", "name": ""}])
        assert ours.read_bytes() == b"id,name\nm1,\n"
        other = tmp_path / "other.csv"
        other.write_bytes(b'"id","name"\r\n"m1",""\r\n')
        assert read_records(other) == read_records(ours)

    def test_a_preview_ignores_quoting_and_a_fold_rewrites_the_file(self, tmp_path):
        path = tmp_path / "dues.csv"
        original = b'"id","dues"\n"m1",""\n"m2","20"\n'
        path.write_bytes(original)
        base = MemoryStore(["id", "dues"], read_records(path).rows)
        sheet = FakeSheetGrid({"Dues": [["id", "dues"], ["m1", ""], ["m2", "20"]]})
        tab = TabConfig(title="Dues", key=("id",), store=FileStore(path))
        target = Target(
            name="roster", spreadsheet="S", tabs=(tab,), base_stores={"Dues": base}
        )
        preview = sync_tab(sheet, "S", target, tab)
        assert preview.exit_code == 0 and not preview.wrote_local
        # Nothing to fold: an applied run leaves the bytes alone.
        sync_tab(sheet, "S", target, tab, apply=True)
        assert path.read_bytes() == original
        # A sheet edit folds in, and the whole file loses the quotes it did not need.
        sheet.tab("Dues").cells[2][1] = "25"
        sync_tab(sheet, "S", target, tab, apply=True)
        assert path.read_bytes() == b"id,dues\nm1,\nm2,25\n"

    def test_a_pull_leaves_an_unchanged_quoted_file_alone(self, tmp_path):
        path = tmp_path / "dues.csv"
        original = b'"id","dues"\n"m1","10"\n'
        path.write_bytes(original)
        sheet = FakeSheetGrid({"Dues": [["id", "dues"], ["m1", "10"]]})
        tab = TabConfig(title="Dues", key=("id",), store=FileStore(path), mode="pull")
        pull_tab(sheet, "S", tab, apply=True)
        assert path.read_bytes() == original
        sheet.tab("Dues").cells[1][1] = "11"
        pull_tab(sheet, "S", tab, apply=True)
        assert path.read_bytes() == b"id,dues\nm1,11\n"

    def test_a_bom_is_dropped_unless_the_store_keeps_it(self, tmp_path):
        path = tmp_path / "m.csv"
        original = "﻿id,name\nm1,Ada\n".encode()
        path.write_bytes(original)
        FileStore(path).write(*FileStore(path).read())
        assert path.read_bytes() == b"id,name\nm1,Ada\n"
        path.write_bytes(original)
        FileStore(path, bom=True).write(*FileStore(path).read())
        assert path.read_bytes() == original

    def test_crlf_needs_the_stores_newline(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_bytes(b"id,name\r\nm1,Ada\r\n")
        FileStore(path).write(*FileStore(path).read())
        assert path.read_bytes() == b"id,name\nm1,Ada\n"
        path.write_bytes(b"id,name\r\nm1,Ada\r\n")
        FileStore(path, newline="crlf").write(*FileStore(path).read())
        assert path.read_bytes() == b"id,name\r\nm1,Ada\r\n"

    def test_a_json_file_is_rewritten_in_the_library_s_format(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('[{"id":"m1","name":"Ada"}]')
        FileStore(path).write(*FileStore(path).read())
        assert path.read_text() == (
            '[\n  {\n    "id": "m1",\n    "name": "Ada"\n  }\n]\n'
        )
        before = path.read_bytes()
        FileStore(path).write(*FileStore(path).read())
        assert path.read_bytes() == before

    def test_a_typed_column_compares_by_value_and_an_untyped_one_by_text(self):
        columns = ["id", "amount", "paid"]
        base = [{"id": "m1", "amount": "3.0", "paid": "true"}]
        local = [{"id": "m1", "amount": "3.0", "paid": "true"}]
        sheet = [{"id": "m1", "amount": "3", "paid": "TRUE"}]
        typed = merge(
            base,
            local,
            sheet,
            ["id"],
            columns,
            types={"amount": "float", "paid": "bool"},
        )
        assert not typed.has_writes and not typed.needs_attention
        untyped = merge(base, local, sheet, ["id"], columns)
        assert [(c.column, c.sheet) for c in untyped.fold_cells] == [
            ("amount", "3"),
            ("paid", "TRUE"),
        ]

    @pytest.mark.parametrize(
        ("blank_keys", "refused"), [("refuse", True), ("partial", False)]
    )
    def test_a_partly_blank_key(self, blank_keys, refused):
        columns = ["region", "id", "v"]
        local = [{"region": "", "id": "7", "v": "x"}]
        args = ([], local, [], ["region", "id"], columns)
        if refused:
            with pytest.raises(ValueError, match=r"blank key \['region', 'id'\]"):
                merge(*args, blank_keys=blank_keys)
        else:
            assert [row.key for row in merge(*args, blank_keys=blank_keys).appends] == [
                ("", "7")
            ]

    @pytest.mark.parametrize("blank_keys", ["refuse", "partial"])
    def test_a_wholly_blank_key_is_refused_on_either_setting(self, blank_keys):
        columns = ["region", "id", "v"]
        local = [{"region": " ", "id": "", "v": "x"}]
        with pytest.raises(ValueError, match="blank key"):
            merge([], local, [], ["region", "id"], columns, blank_keys=blank_keys)

    def test_the_base_is_refused_for_a_blank_key_too(self):
        base = [{"id": "", "v": "x"}]
        with pytest.raises(ValueError, match=r"base: blank key \['id'\]"):
            merge(base, [], [], ["id"], ["id", "v"])

    def test_a_row_of_blank_cells_is_skipped_not_refused(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,v\nm1,x\n,\n")
        assert read_records(path).rows == [{"id": "m1", "v": "x"}]
        assert parse_tab(
            "T", [["id", "v"], ["m1", "x"], ["", ""]], None, ["id"]
        ).rows == [{"id": "m1", "v": "x"}]

    def test_a_sorted_store_is_quiet_for_a_sync_and_not_for_a_pull(self):
        sheet = FakeSheetGrid({"Dues": [["id", "dues"], ["m2", "2"], ["m1", "1"]]})
        # The local side is out of order and has nothing to fold: a sync leaves it.
        local = MemoryStore(
            ["id", "dues"], [{"id": "m2", "dues": "2"}, {"id": "m1", "dues": "1"}]
        )
        base = Sorted(
            ["id", "dues"], [{"id": "m1", "dues": "1"}, {"id": "m2", "dues": "2"}]
        )
        tab = TabConfig(title="Dues", key=("id",), store=local)
        target = Target(
            name="roster", spreadsheet="S", tabs=(tab,), base_stores={"Dues": base}
        )
        quiet = sync_tab(sheet, "S", target, tab, apply=True)
        assert quiet.exit_code == 0 and not quiet.wrote_local and local.writes == 0
        # A pull compares the lists in order, and writes a sorted store each time.
        pulling = TabConfig(title="Dues", key=("id",), store=Sorted(["id", "dues"]))
        first = pull_tab(sheet, "S", pulling, apply=True)
        again = pull_tab(sheet, "S", pulling, apply=True)
        assert first.wrote_local and again.wrote_local
        replacement = again.replacement
        assert replacement is not None and not replacement.unchanged
        assert not (replacement.added or replacement.removed or replacement.changed)
        assert "it now holds 2" in format_report(again)

    def test_an_edit_applied_and_reverted_returns_the_files(self):
        local = MemoryStore(["id", "dues"], [{"id": "m1", "dues": "1"}])
        base = MemoryStore(["id", "dues"], [{"id": "m1", "dues": "1"}])
        sheet = FakeSheetGrid({"Dues": [["id", "dues"], ["m1", "1"]]})
        tab = TabConfig(title="Dues", key=("id",), store=local)
        target = Target(
            name="roster", spreadsheet="S", tabs=(tab,), base_stores={"Dues": base}
        )
        sheet.tab("Dues").cells[1][1] = "2"
        sync_tab(sheet, "S", target, tab, apply=True)
        assert local.rows == base.rows == [{"id": "m1", "dues": "2"}]
        sheet.tab("Dues").cells[1][1] = "1"
        sync_tab(sheet, "S", target, tab, apply=True)
        assert local.rows == base.rows == [{"id": "m1", "dues": "1"}]


class TestTidyingClaims:
    def make(self, strict):
        sheet = FakeSheetGrid(
            {
                "Dues": [
                    ["id", "dues", "note"],
                    ["m1", "10", "late"],
                ]
            }
        )
        schema = {"id": ColumnSchema(), "dues": ColumnSchema("float")}
        store = MemoryStore(["id", "dues"], [{"id": "m1", "dues": "10"}])
        tab = TabConfig(
            title="Dues", key=("id",), store=store, schema=schema, strict_schema=strict
        )
        target = Target(
            name="roster",
            spreadsheet="S",
            tabs=(tab,),
            base_stores={"Dues": MemoryStore(["id", "dues"], store.rows)},
        )
        return sheet, target

    def test_a_strict_preview_lists_the_undeclared_column_and_writes_nothing(self):
        sheet, target = self.make(True)
        report = run_target(sheet, "S", target, "sync")
        (dues,) = report.tabs
        assert dues.problems == [
            "Dues (sheet): column 'note' has no schema entry, and strict_schema "
            "is true: declare it in the tab's schema, or set strict_schema to "
            "'local' to leave the sheet's own columns alone"
        ]
        assert report.exit_code == 1 and not dues.wrote_local
        assert sheet.values("Dues") == [["id", "dues", "note"], ["m1", "10", "late"]]

    def test_local_leaves_the_sheet_s_own_columns_alone(self):
        sheet, target = self.make("local")
        assert run_target(sheet, "S", target, "sync").exit_code == 0


class TestSortedStoreBase:
    def run(self, wrap):
        sheet = FakeSheetGrid(
            {"Dues": [["id", "dues"], ["m2", "2"], ["m1", "1"], ["m3", "3"]]}
        )
        rows = [{"id": "m3", "dues": "3"}, {"id": "m1", "dues": "1"}]
        local = Sorted(["id", "dues"], rows)
        base = Sorted() if wrap else MemoryStore()
        tab = TabConfig(title="Dues", key=("id",), store=local)
        target = Target(
            name="roster", spreadsheet="S", tabs=(tab,), base_stores={"Dues": base}
        )
        sync_tab(sheet, "S", target, tab, apply=True)
        return sync_tab(sheet, "S", target, tab, apply=True)

    def test_an_unsorted_base_is_saved_again_after_the_sort(self):
        assert self.run(wrap=False).wrote_base

    def test_a_sorted_base_is_not(self):
        assert not self.run(wrap=True).wrote_base
