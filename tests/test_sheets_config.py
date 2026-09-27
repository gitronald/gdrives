"""Tests for gdrives.sheets.config: finding, reading, and checking the sync config.

``parse_config`` is pure, so most tests hand it a dict; ``load_config`` and
``find_config`` are tested against files under ``tmp_path``. Every refusal
has its own test, and one test checks that several problems are reported
together.
"""

import json
from pathlib import Path

import pytest
from helpers import local_file

from gdrives.sheets import (
    CONFIG_NAME,
    ColumnSchema,
    Config,
    ConfigError,
    TabConfig,
    Target,
    find_config,
    load_config,
    parse_config,
)

ROOT = Path("/project")
PATH = ROOT / CONFIG_NAME
SHEET = "https://docs.google.com/spreadsheets/d/abc123"


def members(**fields):
    """A valid sync tab, with ``fields`` added or replaced."""
    return {"local": "data/members.csv", "key": ["id"]} | fields


def config(tabs=None, **fields):
    """A one-target config whose tabs default to one valid sync tab."""
    target = {"spreadsheet": SHEET, "tabs": tabs or {"Members": members()}}
    return {"roster": target | fields}


def problems_of(data):
    with pytest.raises(ConfigError) as raised:
        parse_config(data, PATH)
    return raised.value.problems


def refused(data, problem):
    """``data`` is refused with exactly one problem, ``problem``."""
    assert problems_of(data) == [problem]


class TestValidConfig:
    def test_a_full_example_loads(self):
        data = config(
            {
                "Members": {
                    "mode": "sync",
                    "local": "data/members.csv",
                    "key": ["member_id"],
                    "columns": ["member_id", "name", "status", "paid", "notes"],
                    "local_owned": ["status"],
                    "sheet_owned": ["notes"],
                    "owns_rows": False,
                    "schema": {
                        "member_id": {"required": True},
                        "paid": {"type": "bool"},
                        "status": {"allowed": ["active", "closed"]},
                    },
                    "bootstrap": "local",
                    "insert_above": {"status": ["closed"]},
                    "widths": {"notes": 320},
                    "bom": True,
                    "newline": "crlf",
                    "blank_keys": "partial",
                    "on_invalid": "hold",
                    "clear_links": True,
                    "sheet_id": 0,
                },
                "Summary": {"mode": "push", "local": "output/summary.json"},
            },
            base="sheets-base/roster",
            input_option="RAW",
        )
        loaded = parse_config(data, PATH)
        assert loaded == Config(
            path=PATH,
            targets={
                "roster": Target(
                    name="roster",
                    spreadsheet=SHEET,
                    base=ROOT / "sheets-base/roster",
                    input_option="RAW",
                    tabs=(
                        TabConfig(
                            title="Members",
                            local=ROOT / "data/members.csv",
                            mode="sync",
                            key=("member_id",),
                            columns=("member_id", "name", "status", "paid", "notes"),
                            local_owned=("status",),
                            sheet_owned=("notes",),
                            owns_rows=False,
                            schema={
                                "member_id": ColumnSchema(required=True),
                                "paid": ColumnSchema(type="bool"),
                                "status": ColumnSchema(allowed=("active", "closed")),
                            },
                            bootstrap="local",
                            insert_above={"status": ("closed",)},
                            widths={"notes": 320},
                            bom=True,
                            newline="crlf",
                            blank_keys="partial",
                            on_invalid="hold",
                            clear_links=True,
                            sheet_id=0,
                        ),
                        TabConfig(
                            title="Summary",
                            local=ROOT / "output/summary.json",
                            mode="push",
                        ),
                    ),
                )
            },
        )

    def test_defaults(self):
        target = parse_config(config(), PATH).target("roster")
        assert target.base == ROOT / "sheets-base" / "roster"
        assert target.input_option == "RAW"
        (tab,) = target.tabs
        assert tab == TabConfig(
            title="Members", local=ROOT / "data/members.csv", key=("id",)
        )
        assert tab.columns is None
        assert tab.insert_above is None
        assert tab.newline == "lf"
        assert tab.blank_keys == "refuse"
        assert tab.on_invalid == "refuse"
        assert tab.clear_links is False
        assert tab.sheet_id is None

    def test_default_base_uses_a_safe_target_name(self):
        data = {"a/../b": config()["roster"]}
        assert parse_config(data, PATH).target("a/../b").base == (
            ROOT / "sheets-base" / "a_.._b"
        )

    def test_relative_paths_resolve_against_the_config_folder(self):
        data = config({"T": members(local="../shared/./t.tsv")}, base="snap/../base")
        target = parse_config(data, ROOT / "sub" / CONFIG_NAME).target("roster")
        assert target.base == ROOT / "sub" / "base"
        assert target.tabs[0].local == ROOT / "shared" / "t.tsv"

    def test_absolute_paths_are_kept(self):
        data = config({"T": members(local="/elsewhere/t.csv")}, base="/snapshots")
        target = parse_config(data, PATH).target("roster")
        assert target.base == Path("/snapshots")
        assert target.tabs[0].local == Path("/elsewhere/t.csv")

    def test_spreadsheet_is_kept_as_written(self):
        data = config(spreadsheet="My Drive/projects/roster")
        assert parse_config(data, PATH).target("roster").spreadsheet == (
            "My Drive/projects/roster"
        )

    def test_user_entered_is_allowed_without_sync_tabs(self):
        data = config(
            {"S": {"mode": "push", "local": "s.csv"}}, input_option="USER_ENTERED"
        )
        assert parse_config(data, PATH).target("roster").input_option == "USER_ENTERED"

    def test_pull_and_push_need_no_key(self):
        data = config(
            {
                "P": {"mode": "pull", "local": "p.csv"},
                "Q": {
                    "mode": "push",
                    "local": "q.csv",
                    "key": ["id"],
                    "widths": {"a": 5},
                },
            }
        )
        tabs = parse_config(data, PATH).target("roster").tabs
        assert [(tab.mode, tab.key) for tab in tabs] == [
            ("pull", ()),
            ("push", ("id",)),
        ]

    def test_insert_above_takes_a_single_value(self):
        data = config({"T": members(insert_above={"status": "closed"})})
        tab = parse_config(data, PATH).target("roster").tabs[0]
        assert tab.insert_above == {"status": ("closed",)}

    def test_schema_types_for_a_json_file(self):
        data = config({"T": members(schema={"n": {"type": "int"}, "s": {}})})
        tab = parse_config(data, PATH).target("roster").tabs[0]
        assert tab.types == {"n": "int", "s": "str"}

    def test_extensions_are_matched_in_any_case(self):
        data = config({"T": members(local="data/T.CSV")})
        assert (
            local_file(parse_config(data, PATH).target("roster").tabs[0]).name
            == "T.CSV"
        )


class TestLookups:
    def test_unknown_target_names_the_others(self):
        loaded = parse_config(config(), PATH)
        with pytest.raises(ValueError, match=r"no target 'x'; targets: \['roster'\]"):
            loaded.target("x")

    def test_unknown_tab_names_the_others(self):
        target = parse_config(config(), PATH).target("roster")
        with pytest.raises(ValueError, match=r"no tab 'x'; tabs: \['Members'\]"):
            target.tab("x")
        assert target.tab("Members").title == "Members"

    @pytest.mark.parametrize(
        ("title", "name"),
        [
            ("Members", "Members.csv"),
            ("a/b", "a_b.csv"),
            ("..", "__.csv"),
            ("v1.2", "v1.2.csv"),
        ],
    )
    def test_base_path_is_one_csv_per_tab_named_safely(self, title, name):
        target = parse_config(config({title: members()}), PATH).target("roster")
        path = target.base_path(target.tab(title))
        assert path == ROOT / "sheets-base" / "roster" / name
        assert path.parent == target.base


class TestTopLevelProblems:
    def test_not_an_object(self):
        refused([], "expected a JSON object of targets")

    def test_no_targets(self):
        refused({}, "names no targets")

    def test_target_not_an_object(self):
        refused({"roster": []}, "target 'roster': expected an object")

    def test_blank_target_name(self):
        refused({" ": config()["roster"]}, "a target has a blank name")


class TestTargetProblems:
    def test_unknown_field(self):
        refused(config(sheet="x"), "target 'roster': unknown field(s) ['sheet']")

    @pytest.mark.parametrize("value", [None, "", "  ", 3])
    def test_bad_spreadsheet(self, value):
        data = config()
        if value is None:
            del data["roster"]["spreadsheet"]
        else:
            data["roster"]["spreadsheet"] = value
        refused(data, "target 'roster': 'spreadsheet' must be a URL, file ID, or path")

    @pytest.mark.parametrize("value", ["", 7])
    def test_bad_base(self, value):
        refused(config(base=value), "target 'roster': 'base' must be a directory path")

    @pytest.mark.parametrize("base", [".gdrives/base", "x/.gdrives", "a/.gdrives/b"])
    def test_base_inside_the_cache_directory(self, base):
        refused(
            config(base=base),
            f"target 'roster': 'base' {base!r} is inside a .gdrives directory, "
            "which is a cache; keep the base where it is committed",
        )

    def test_unknown_input_option(self):
        refused(
            config(input_option="raw"),
            "target 'roster': 'input_option' must be one of "
            "['RAW', 'USER_ENTERED'], not 'raw'",
        )

    @pytest.mark.parametrize("tabs", [None, {}, [], "Members"])
    def test_bad_tabs(self, tabs):
        data = config()
        if tabs is None:
            del data["roster"]["tabs"]
        else:
            data["roster"]["tabs"] = tabs
        refused(
            data, "target 'roster': 'tabs' must be an object naming one or more tabs"
        )

    def test_user_entered_on_a_sync_tab(self):
        refused(
            config(input_option="USER_ENTERED"),
            "target 'roster', tab 'Members': a sync tab writes with RAW input; "
            "USER_ENTERED rewrites values, so they would never read back as written",
        )

    def test_two_tabs_writing_one_local_file(self):
        refused(
            config(
                {
                    "A": members(local="data/x.csv"),
                    "B": {"mode": "pull", "local": "data/../data/X.CSV"},
                }
            ),
            f"{ROOT / 'data' / 'x.csv'} would be written by more than one tab: "
            "target 'roster', tab 'A' (local file); "
            "target 'roster', tab 'B' (local file)",
        )

    def test_push_tabs_may_share_a_local_file(self):
        push = {"mode": "push", "local": "data/x.csv"}
        data = config({"A": push, "B": push, "C": members(local="data/y.csv")})
        assert len(parse_config(data, PATH).target("roster").tabs) == 3

    @pytest.mark.parametrize(("first", "second"), [("a/b", "a\\b"), ("Notes", "NOTES")])
    def test_two_sync_tabs_whose_base_files_collide(self, first, second):
        base = ROOT / "sheets-base" / "roster" / f"{first.replace('/', '_')}.csv"
        refused(
            config({first: members(local="1.csv"), second: members(local="2.csv")}),
            f"{base} would be written by more than one tab: "
            f"target 'roster', tab {first!r} (base); "
            f"target 'roster', tab {second!r} (base)",
        )

    def test_base_names_of_pull_and_push_tabs_do_not_collide(self):
        data = config(
            {
                "a/b": members(local="1.csv"),
                "a\\b": {"mode": "push", "local": "2.csv"},
                "a_b": {"mode": "pull", "local": "3.csv"},
            }
        )
        assert len(parse_config(data, PATH).target("roster").tabs) == 3

    def test_two_targets_sharing_a_base_file(self):
        one = config(base="shared")["roster"]
        two = config({"Members": members(local="other.csv")}, base="shared")
        refused(
            {"one": one, "two": two["roster"]},
            f"{ROOT / 'shared' / 'Members.csv'} would be written by more than one "
            "tab: target 'one', tab 'Members' (base); target 'two', tab 'Members' "
            "(base)",
        )

    def test_a_local_file_that_is_a_base_file(self):
        refused(
            config(
                {
                    "Members": members(),
                    "Copy": {"mode": "pull", "local": "sheets-base/roster/Members.csv"},
                }
            ),
            f"{ROOT / 'sheets-base/roster/Members.csv'} would be written by more "
            "than one tab: target 'roster', tab 'Members' (base); "
            "target 'roster', tab 'Copy' (local file)",
        )


class TestTabProblems:
    WHERE = "target 'roster', tab 'Members'"

    def tab_refused(self, tab, problem):
        refused(config({"Members": tab}), f"{self.WHERE}: {problem}")

    def test_not_an_object(self):
        self.tab_refused([], "expected an object")

    def test_blank_title(self):
        refused(config({"": members()}), "target 'roster': a tab has a blank title")

    def test_unknown_field(self):
        self.tab_refused(members(keys=["id"]), "unknown field(s) ['keys']")

    def test_unknown_mode(self):
        self.tab_refused(
            members(mode="merge"),
            "'mode' must be one of ['pull', 'push', 'sync'], not 'merge'",
        )

    @pytest.mark.parametrize("mode", ["pull", "push"])
    def test_sync_only_fields_on_another_mode(self, mode):
        self.tab_refused(
            {
                "mode": mode,
                "local": "m.csv",
                "local_owned": ["a"],
                "owns_rows": True,
                "bootstrap": "local",
                "insert_above": {"a": "x"},
                "on_invalid": "hold",
            },
            "['local_owned', 'owns_rows', 'bootstrap', 'insert_above', "
            "'on_invalid'] apply "
            "only to a sync tab",
        )

    def test_widths_on_a_pull_tab(self):
        self.tab_refused(
            {"mode": "pull", "local": "m.csv", "widths": {"a": 10}},
            "'widths' do not apply to a pull tab",
        )

    @pytest.mark.parametrize("local", [None, "", 3])
    def test_bad_local(self, local):
        tab = members()
        if local is None:
            del tab["local"]
        else:
            tab["local"] = local
        self.tab_refused(tab, "'local' must be a file path")

    @pytest.mark.parametrize("local", ["data/members.xlsx", "data/members"])
    def test_unsupported_local_extension(self, local):
        self.tab_refused(
            members(local=local),
            f"'local' {local!r} must end in one of ['.csv', '.json', '.tsv']",
        )

    def test_bom_not_a_bool(self):
        self.tab_refused(members(bom="yes"), "'bom' must be true or false")

    def test_bom_on_json(self):
        self.tab_refused(
            members(local="m.json", bom=True),
            "'bom' applies only to a .csv or .tsv file",
        )

    @pytest.mark.parametrize("sheet_id", ["0", 1.5, True, -1, [1]])
    def test_sheet_id_not_a_whole_number(self, sheet_id):
        self.tab_refused(
            members(sheet_id=sheet_id),
            f"'sheet_id' must be a whole number, the tab's sheetId, not {sheet_id!r}",
        )

    @pytest.mark.parametrize("mode", ["sync", "pull", "push"])
    def test_sheet_id_applies_to_every_mode(self, mode):
        tab = members(mode=mode, sheet_id=1234567890)
        target = parse_config(config({"Members": tab}), PATH).target("roster")
        assert target.tabs[0].sheet_id == 1234567890

    def test_two_tabs_of_a_target_with_one_sheet_id(self):
        tabs = {
            "A": members(sheet_id=7, local="a.csv"),
            "B": members(sheet_id=8, local="b.csv"),
            "C": members(sheet_id=7, local="c.csv"),
            "D": members(local="d.csv"),
            "E": members(local="e.csv"),
        }
        with pytest.raises(ConfigError) as raised:
            parse_config(config(tabs), PATH)
        assert raised.value.problems == [
            "target 'roster', tab 'C': 'sheet_id' 7 is tab 'A' too"
        ]

    def test_two_targets_may_name_one_sheet_id(self):
        data = {
            "one": config({"A": members(sheet_id=7, local="a.csv")})["roster"],
            "two": config({"A": members(sheet_id=7, local="b.csv")})["roster"],
        }
        assert set(parse_config(data, PATH).targets) == {"one", "two"}

    @pytest.mark.parametrize("clear_links", ["yes", 1, None])
    def test_clear_links_not_a_bool(self, clear_links):
        self.tab_refused(
            members(clear_links=clear_links), "'clear_links' must be true or false"
        )

    @pytest.mark.parametrize("clear_links", [True, False])
    def test_clear_links_on_a_pull_tab(self, clear_links):
        self.tab_refused(
            {"mode": "pull", "local": "m.csv", "clear_links": clear_links},
            "'clear_links' does not apply to a pull tab",
        )

    @pytest.mark.parametrize("mode", ["sync", "push"])
    def test_clear_links_on_a_sync_or_a_push_tab(self, mode):
        tab = members(mode=mode, clear_links=True)
        target = parse_config(config({"Members": tab}), PATH).target("roster")
        assert target.tabs[0].clear_links is True

    @pytest.mark.parametrize("on_invalid", ["skip", "Hold", "", True, None, 1])
    def test_on_invalid_not_a_setting(self, on_invalid):
        self.tab_refused(
            members(on_invalid=on_invalid),
            f"'on_invalid' must be one of ['hold', 'refuse'], not {on_invalid!r}",
        )

    @pytest.mark.parametrize("blank_keys", ["allow", "Partial", "", True, None, 1])
    def test_blank_keys_not_a_setting(self, blank_keys):
        self.tab_refused(
            members(blank_keys=blank_keys),
            f"'blank_keys' must be one of ['partial', 'refuse'], not {blank_keys!r}",
        )

    @pytest.mark.parametrize("mode", ["sync", "pull", "push"])
    def test_blank_keys_applies_to_every_mode(self, mode):
        tab = members(mode=mode, blank_keys="partial")
        target = parse_config(config({"Members": tab}), PATH).target("roster")
        assert target.tabs[0].blank_keys == "partial"

    @pytest.mark.parametrize("newline", ["cr", "LF", "\n", True, None])
    def test_newline_not_a_line_ending(self, newline):
        self.tab_refused(
            members(newline=newline),
            f"'newline' must be one of ['crlf', 'lf'], not {newline!r}",
        )

    def test_newline_on_json(self):
        self.tab_refused(
            members(local="m.json", newline="crlf"),
            "'newline' applies only to a .csv or .tsv file",
        )

    def test_newline_lf_on_json_is_what_a_json_file_gets(self):
        tab = members(local="m.json", newline="lf")
        target = parse_config(config({"Members": tab}), PATH).target("roster")
        assert target.tabs[0].newline == "lf"

    @pytest.mark.parametrize("mode", ["sync", "pull", "push"])
    def test_newline_applies_to_every_mode(self, mode):
        tab = members(mode=mode, newline="crlf")
        target = parse_config(config({"Members": tab}), PATH).target("roster")
        assert target.tabs[0].newline == "crlf"

    @pytest.mark.parametrize("key", [None, []])
    def test_sync_tab_without_a_key(self, key):
        tab = members()
        if key is None:
            del tab["key"]
        else:
            tab["key"] = key
        self.tab_refused(tab, "a sync tab needs a 'key' of one or more columns")

    @pytest.mark.parametrize("key", ["id", [""], [1], ["id", " "]])
    def test_malformed_key(self, key):
        # A malformed key is also an absent one, which a sync tab needs.
        assert problems_of(config({"Members": members(key=key)})) == [
            f"{self.WHERE}: 'key' must be a list of column names",
            f"{self.WHERE}: a sync tab needs a 'key' of one or more columns",
        ]

    def test_repeated_key(self):
        self.tab_refused(members(key=["id", "id"]), "'key' repeats ['id']")

    @pytest.mark.parametrize("columns", [[], "id", ["id", ""], None])
    def test_malformed_columns(self, columns):
        self.tab_refused(
            members(columns=columns),
            "'columns' must be a list of one or more column names",
        )

    def test_repeated_columns(self):
        self.tab_refused(members(columns=["id", "a", "a"]), "'columns' repeats ['a']")

    def test_key_outside_columns(self):
        self.tab_refused(
            members(key=["id", "no"], columns=["id"]),
            "key column(s) ['no'] not in 'columns'",
        )

    @pytest.mark.parametrize("owner", ["local_owned", "sheet_owned"])
    def test_owned_column_outside_columns(self, owner):
        self.tab_refused(
            members(columns=["id", "a"], **{owner: ["b"]}),
            f"{owner} column(s) ['b'] not in 'columns'",
        )

    @pytest.mark.parametrize("owner", ["local_owned", "sheet_owned"])
    def test_owned_key_column(self, owner):
        self.tab_refused(
            members(**{owner: ["id"]}), f"key column(s) ['id'] cannot be {owner}"
        )

    @pytest.mark.parametrize("owner", ["local_owned", "sheet_owned"])
    def test_malformed_ownership(self, owner):
        self.tab_refused(
            members(**{owner: "a"}), f"{owner!r} must be a list of column names"
        )

    def test_overlapping_ownership(self):
        self.tab_refused(
            members(local_owned=["a", "b"], sheet_owned=["b"]),
            "column(s) ['b'] are both local_owned and sheet_owned",
        )

    def test_owns_rows_not_a_bool(self):
        self.tab_refused(members(owns_rows="no"), "'owns_rows' must be true or false")

    def test_unknown_bootstrap(self):
        self.tab_refused(
            members(bootstrap="adopt"),
            "'bootstrap' must be one of ['local'], not 'adopt' "
            "(--adopt is a flag, not a config value)",
        )

    def test_schema_not_an_object(self):
        self.tab_refused(
            members(schema=["id"]), "'schema' must be an object of columns"
        )

    def test_schema_blank_column(self):
        self.tab_refused(members(schema={" ": {}}), "'schema' names a blank column")

    def test_schema_entry_not_an_object(self):
        self.tab_refused(members(schema={"a": "int"}), "schema 'a': expected an object")

    def test_schema_unknown_field(self):
        self.tab_refused(
            members(schema={"a": {"kind": "int"}}),
            "schema 'a': unknown field(s) ['kind']",
        )

    def test_schema_unknown_type(self):
        self.tab_refused(
            members(schema={"a": {"type": "number"}}),
            "schema 'a': 'type' must be one of "
            "['bool', 'date', 'datetime', 'float', 'int', 'str'], not 'number'",
        )

    def test_schema_required_not_a_bool(self):
        self.tab_refused(
            members(schema={"a": {"required": 1}}),
            "schema 'a': 'required' must be true or false",
        )

    @pytest.mark.parametrize("allowed", [[], "x", [["x"]], [None]])
    def test_schema_bad_allowed(self, allowed):
        self.tab_refused(
            members(schema={"a": {"allowed": allowed}}),
            "schema 'a': 'allowed' must be a list of one or more values",
        )

    def test_schema_column_outside_columns(self):
        self.tab_refused(
            members(columns=["id"], schema={"id": {}, "a": {}}),
            "schema column(s) ['a'] not in 'columns'",
        )

    @pytest.mark.parametrize(
        "insert_above",
        [
            [],
            {},
            {"a": "x", "b": "y"},
            {"": "x"},
            {"a": []},
            {"a": [["x"]]},
            {"a": None},
            {"a": {"x": 1}},
        ],
    )
    def test_malformed_insert_above(self, insert_above):
        self.tab_refused(
            members(insert_above=insert_above),
            "'insert_above' must be one {column: value or [values]} pair",
        )

    def test_insert_above_outside_columns(self):
        self.tab_refused(
            members(columns=["id"], insert_above={"status": "x"}),
            "insert_above column(s) ['status'] not in 'columns'",
        )

    def test_widths_not_an_object(self):
        self.tab_refused(members(widths=[1]), "'widths' must be an object of columns")

    @pytest.mark.parametrize("width", [0, -3, 1.5, "10", True, None])
    def test_bad_width(self, width):
        self.tab_refused(
            members(widths={"a": width}),
            f"width of 'a' must be a whole number of pixels, at least 1, not {width!r}",
        )

    def test_blank_width_column(self):
        self.tab_refused(
            members(widths={"": 10}),
            "width of '' must be a whole number of pixels, at least 1, not 10",
        )

    def test_widths_outside_columns(self):
        self.tab_refused(
            members(columns=["id"], widths={"id": 10, "b": 20}),
            "widths column(s) ['b'] not in 'columns'",
        )


class TestEveryProblemAtOnce:
    def test_problems_across_targets_and_tabs_are_reported_together(self):
        data = {
            "roster": {
                "spreadsheet": "",
                "base": ".gdrives",
                "tabs": {
                    "Members": members(mode="merge", key=[]),
                    "Dues": {"local": "dues.txt", "key": ["id"], "widths": {"a": 0}},
                },
            },
            "ledger": config(input_option="USER_ENTERED")["roster"],
        }
        with pytest.raises(ConfigError) as raised:
            parse_config(data, PATH)
        assert raised.value.problems == [
            "target 'roster': 'spreadsheet' must be a URL, file ID, or path",
            "target 'roster': 'base' '.gdrives' is inside a .gdrives directory, "
            "which is a cache; keep the base where it is committed",
            "target 'roster', tab 'Members': 'mode' must be one of "
            "['pull', 'push', 'sync'], not 'merge'",
            "target 'roster', tab 'Dues': 'local' 'dues.txt' must end in one of "
            "['.csv', '.json', '.tsv']",
            "target 'roster', tab 'Dues': width of 'a' must be a whole number of "
            "pixels, at least 1, not 0",
            "target 'ledger', tab 'Members': a sync tab writes with RAW input; "
            "USER_ENTERED rewrites values, so they would never read back as written",
        ]
        message = str(raised.value)
        assert message.startswith(f"{PATH}: 6 problems:\n  - target 'roster': ")
        assert message.count("\n  - ") == 6

    def test_one_problem_is_singular(self):
        with pytest.raises(ConfigError, match=r": 1 problem:\n  - names no targets$"):
            parse_config({}, PATH)

    def test_config_error_is_a_value_error(self):
        assert issubclass(ConfigError, ValueError)
        assert ConfigError("src", ["a"]).source == "src"


class TestLoad:
    def write(self, folder, data):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / CONFIG_NAME
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_an_explicit_path(self, tmp_path):
        path = self.write(tmp_path / "cfg", config())
        loaded = load_config(path)
        assert loaded.path == path
        assert (
            loaded.target("roster").tabs[0].local == tmp_path / "cfg/data/members.csv"
        )

    def test_loads_a_relative_path_against_the_working_directory(
        self, tmp_path, monkeypatch
    ):
        self.write(tmp_path, config())
        monkeypatch.chdir(tmp_path)
        assert load_config(CONFIG_NAME).path == tmp_path / CONFIG_NAME

    def test_finds_the_file_from_the_working_directory_upward(
        self, tmp_path, monkeypatch
    ):
        path = self.write(tmp_path, config())
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        loaded = load_config()
        assert loaded.path == path
        # Paths are relative to the file, not to where the command ran.
        assert loaded.target("roster").base == tmp_path / "sheets-base/roster"

    def test_nearest_file_wins(self, tmp_path):
        self.write(tmp_path, config())
        inner = self.write(tmp_path / "inner", config())
        assert find_config(tmp_path / "inner" / "deeper") == inner
        assert find_config(tmp_path / "inner") == inner

    def test_a_directory_with_the_name_is_not_a_config(self, tmp_path):
        (tmp_path / "x" / CONFIG_NAME).mkdir(parents=True)
        path = self.write(tmp_path, config())
        assert find_config(tmp_path / "x") == path

    def test_no_config_anywhere(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        with pytest.raises(
            ConfigError, match=f"no {CONFIG_NAME} here or in any parent"
        ):
            find_config()

    def test_a_byte_order_mark_is_accepted(self, tmp_path):
        path = tmp_path / CONFIG_NAME
        path.write_text("﻿" + json.dumps(config()), encoding="utf-8")
        assert load_config(path).target("roster").name == "roster"

    def test_invalid_json(self, tmp_path):
        path = tmp_path / CONFIG_NAME
        path.write_text("{", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid JSON") as raised:
            load_config(path)
        assert raised.value.source == str(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="cannot read the file"):
            load_config(tmp_path / "nope.json")

    def test_problems_name_the_file(self, tmp_path):
        path = self.write(tmp_path, {})
        with pytest.raises(ConfigError, match=f"^{path}: 1 problem"):
            load_config(path)
