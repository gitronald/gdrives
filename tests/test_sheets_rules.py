"""Tests for gdrives.sheets conditional format rules.

Rules live on the spreadsheet resource, so the helpers read through
``spreadsheets.get`` (the fake's ``meta`` response) and write through
``spreadsheets.batchUpdate`` (``spreadsheetBatchUpdate``). As in test_sheets.py,
``FakeSheetsService`` records each ``(method, kwargs)`` call, and the ``run_*``
entry points patch ``build_sheets_service`` at its source (``gdrives.auth``).
"""

import json

import pytest
from helpers import FakeSheetsService, patch_sheets_service

from gdrives.sheets import (
    a1_to_grid_range,
    add_conditional_rule,
    build_formula_rule,
    color_to_hex,
    column_index,
    delete_conditional_rule,
    describe_rule,
    format_rules,
    grid_range_to_a1,
    hex_to_color,
    list_conditional_rules,
    read_rule_json,
    run_add_rule,
    run_delete_rule,
    run_rules,
    split_a1,
    tab_sheet_ids,
)

TABS = {"Sheet1": 0, "Q3 Budget": 42}

GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}

RULE = {
    "ranges": [
        {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 27}
    ],
    "booleanRule": {
        "condition": {
            "type": "CUSTOM_FORMULA",
            "values": [{"userEnteredValue": '=$F2="Rejected"'}],
        },
        "format": {"textFormat": {"strikethrough": True, "foregroundColor": GREY}},
    },
}


def meta(*sheets):
    """A ``spreadsheets.get`` payload from ``(title, sheetId, rules)`` triples."""
    out = []
    for title, sheet_id, rules in sheets:
        entry = {"properties": {"title": title, "sheetId": sheet_id}}
        if rules is not None:  # the API omits the key on a tab with no rules
            entry["conditionalFormats"] = rules
        out.append(entry)
    return {"sheets": out}


# -- A1 <-> GridRange --


class TestColumnIndex:
    @pytest.mark.parametrize(
        "letters, index",
        [("A", 0), ("Z", 25), ("AA", 26), ("az", 51), ("ZZ", 701), ("AAA", 702)],
    )
    def test_letter_to_index(self, letters, index):
        assert column_index(letters) == index

    @pytest.mark.parametrize("bad", ["", "A1", "É"])
    def test_non_letters_raise(self, bad):
        with pytest.raises(ValueError, match="not a column letter"):
            column_index(bad)


class TestSplitA1:
    def test_bare_span(self):
        assert split_a1("A1:C3") == (None, "A1:C3")

    def test_plain_tab(self):
        assert split_a1("Sheet1!A2:AA") == ("Sheet1", "A2:AA")

    def test_quoted_tab_with_space(self):
        assert split_a1("'Q3 Budget'!B:B") == ("Q3 Budget", "B:B")

    def test_quoted_tab_with_bang_and_quote(self):
        assert split_a1("'Hi! O''Brien'!A1") == ("Hi! O'Brien", "A1")


class TestTabSheetIds:
    def test_maps_titles_to_ids(self):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, None), ("Data", 7, None)))
        assert tab_sheet_ids(svc, "sid") == {"Sheet1": 0, "Data": 7}
        assert svc.calls[0] == (
            "spreadsheets.get",
            {"spreadsheetId": "sid", "fields": "sheets.properties(sheetId,title)"},
        )

    def test_omitted_sheet_id_is_zero(self):
        svc = FakeSheetsService(meta={"sheets": [{"properties": {"title": "S"}}]})
        assert tab_sheet_ids(svc, "sid") == {"S": 0}

    def test_no_sheets(self):
        assert tab_sheet_ids(FakeSheetsService(), "sid") == {}


class TestA1ToGridRange:
    def convert(self, range_):
        return a1_to_grid_range(FakeSheetsService(), "sid", range_, tab_ids=TABS)

    def test_bounded_range(self):
        assert self.convert("Sheet1!A1:C10") == {
            "sheetId": 0,
            "startRowIndex": 0,
            "endRowIndex": 10,
            "startColumnIndex": 0,
            "endColumnIndex": 3,
        }

    def test_open_ended_rows_leave_end_unset(self):
        # A bare span targets the first tab; no end row means "to the bottom".
        assert self.convert("A2:AA") == {
            "sheetId": 0,
            "startRowIndex": 1,
            "startColumnIndex": 0,
            "endColumnIndex": 27,
        }

    def test_single_column(self):
        assert self.convert("Sheet1!B:B") == {
            "sheetId": 0,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        }

    def test_rows_only(self):
        assert self.convert("Sheet1!2:5") == {
            "sheetId": 0,
            "startRowIndex": 1,
            "endRowIndex": 5,
        }

    def test_quoted_tab_with_space(self):
        assert self.convert("'Q3 Budget'!A2:B5") == {
            "sheetId": 42,
            "startRowIndex": 1,
            "endRowIndex": 5,
            "startColumnIndex": 0,
            "endColumnIndex": 2,
        }

    def test_single_cell_and_absolute_refs(self):
        expected = {
            "sheetId": 0,
            "startRowIndex": 2,
            "endRowIndex": 3,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        }
        assert self.convert("B3") == expected
        assert self.convert("$B$3") == expected

    @pytest.mark.parametrize("range_", ["Q3 Budget", "'Q3 Budget'", "Q3 Budget!"])
    def test_whole_tab(self, range_):
        assert self.convert(range_) == {"sheetId": 42}

    def test_looks_up_tabs_when_not_given(self):
        svc = FakeSheetsService(meta=meta(("Data", 9, None)))
        assert a1_to_grid_range(svc, "sid", "Data!A1") == {
            "sheetId": 9,
            "startRowIndex": 0,
            "endRowIndex": 1,
            "startColumnIndex": 0,
            "endColumnIndex": 1,
        }
        assert [c[0] for c in svc.calls] == ["spreadsheets.get"]

    def test_unknown_tab_raises(self):
        with pytest.raises(ValueError, match="no tab named 'Nope'"):
            self.convert("Nope!A1")

    def test_no_tabs_raises(self):
        with pytest.raises(ValueError, match="no tabs"):
            a1_to_grid_range(FakeSheetsService(), "sid", "A1", tab_ids={})

    @pytest.mark.parametrize("range_", ["C1:A1", "A5:A2"])
    def test_reversed_range_raises(self, range_):
        with pytest.raises(ValueError, match="ends before it starts"):
            self.convert(range_)

    @pytest.mark.parametrize("range_", ["1A", "A1:", "A1:B2:C3", "A-1"])
    def test_bad_reference_raises(self, range_):
        with pytest.raises(ValueError, match="bad A1 cell reference"):
            self.convert(range_)

    def test_row_zero_raises(self):
        with pytest.raises(ValueError, match="start at 1"):
            self.convert("A0:B2")


class TestGridRangeToA1:
    @pytest.mark.parametrize(
        "grid, a1",
        [
            (
                {
                    "startRowIndex": 0,
                    "endRowIndex": 10,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "A1:C10",
            ),
            (
                {"startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 27},
                "A2:AA",
            ),
            (
                {
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "B3",
            ),
            ({"startColumnIndex": 1, "endColumnIndex": 2}, "B:B"),
            ({"startRowIndex": 1, "endRowIndex": 5}, "2:5"),
            # The API omits zero-valued starts: A1:B5 may come back without them.
            ({"endRowIndex": 5, "endColumnIndex": 2}, "A1:B5"),
            ({"sheetId": 3}, ""),
        ],
    )
    def test_renders(self, grid, a1):
        assert grid_range_to_a1(grid) == a1


# -- colors --


class TestColors:
    @pytest.mark.parametrize("value", ["#999999", "999999", " #999 "])
    def test_hex_to_color(self, value):
        assert hex_to_color(value) == GREY

    def test_primary_channels(self):
        assert hex_to_color("#FF0080") == {
            "red": 1.0,
            "green": 0.0,
            "blue": 128 / 255,
        }

    @pytest.mark.parametrize("bad", ["#12345", "zzzzzz", "", "#99999g"])
    def test_bad_hex_raises(self, bad):
        with pytest.raises(ValueError, match="hex string"):
            hex_to_color(bad)

    def test_round_trip(self):
        assert color_to_hex(hex_to_color("#1a2b3c")) == "#1a2b3c"

    def test_omitted_channels_are_zero(self):
        assert color_to_hex({}) == "#000000"
        assert color_to_hex({"red": 1}) == "#ff0000"


# -- build_formula_rule --


class TestBuildFormulaRule:
    def test_documented_shape(self):
        grid = {"sheetId": 0, "startRowIndex": 1}
        rule = build_formula_rule(
            [grid], '=$F2="Rejected"', strikethrough=True, text_color=GREY
        )
        assert rule == {
            "ranges": [grid],
            "booleanRule": {
                "condition": {
                    "type": "CUSTOM_FORMULA",
                    "values": [{"userEnteredValue": '=$F2="Rejected"'}],
                },
                "format": {
                    "textFormat": {"strikethrough": True, "foregroundColor": GREY},
                },
            },
        }

    def test_unset_options_are_omitted(self):
        rule = build_formula_rule(
            [{"sheetId": 0}], "=TRUE", bold=False, background=GREY
        )
        # bold=False is sent (explicitly off); italic etc. were never set.
        assert rule["booleanRule"]["format"] == {
            "textFormat": {"bold": False},
            "backgroundColor": GREY,
        }

    def test_background_only_has_no_text_format(self):
        rule = build_formula_rule([{"sheetId": 0}], "=TRUE", background=GREY)
        assert rule["booleanRule"]["format"] == {"backgroundColor": GREY}

    def test_all_flags(self):
        rule = build_formula_rule(
            [{"sheetId": 0}], "=TRUE", bold=True, italic=True, underline=True
        )
        assert rule["booleanRule"]["format"]["textFormat"] == {
            "bold": True,
            "italic": True,
            "underline": True,
        }

    def test_no_ranges_raises(self):
        with pytest.raises(ValueError, match="at least one range"):
            build_formula_rule([], "=TRUE", bold=True)

    def test_no_format_raises(self):
        with pytest.raises(ValueError, match="at least one format option"):
            build_formula_rule([{"sheetId": 0}], "=TRUE")

    def test_ranges_on_two_tabs_raise(self):
        with pytest.raises(ValueError, match="all be on one tab"):
            build_formula_rule([{"sheetId": 0}, {"sheetId": 7}], "=TRUE", bold=True)

    def test_omitted_sheet_id_is_the_first_tab(self):
        # The API omits sheetId 0, so {} and {"sheetId": 0} are the same tab.
        ranges = [{}, {"sheetId": 0, "startRowIndex": 1}]
        assert build_formula_rule(ranges, "=TRUE", bold=True)["ranges"] == ranges


# -- list / add / delete --


class TestListConditionalRules:
    def test_rules_across_tabs_with_indices(self):
        other = {"ranges": [{"sheetId": 7}], "gradientRule": {"minpoint": {}}}
        svc = FakeSheetsService(
            meta=meta(
                ("Sheet1", 0, [RULE, RULE]), ("Empty", 3, None), ("Data", 7, [other])
            )
        )
        assert list_conditional_rules(svc, "sid") == [
            {"tab": "Sheet1", "sheet_id": 0, "index": 0, "rule": RULE},
            {"tab": "Sheet1", "sheet_id": 0, "index": 1, "rule": RULE},
            {"tab": "Data", "sheet_id": 7, "index": 0, "rule": other},
        ]
        assert svc.calls[0] == (
            "spreadsheets.get",
            {
                "spreadsheetId": "sid",
                "fields": "sheets(properties(sheetId,title),conditionalFormats)",
            },
        )

    def test_no_rules_anywhere(self):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, None)))
        assert list_conditional_rules(svc, "sid") == []

    def test_omitted_sheet_id_is_zero(self):
        svc = FakeSheetsService(
            meta={
                "sheets": [{"properties": {"title": "S"}, "conditionalFormats": [RULE]}]
            }
        )
        assert list_conditional_rules(svc, "sid")[0]["sheet_id"] == 0


class TestAddDelete:
    def test_add_request_body(self):
        svc = FakeSheetsService(spreadsheetBatchUpdate={"replies": [{}]})
        assert add_conditional_rule(svc, "sid", RULE, index=2) == {"replies": [{}]}
        assert svc.calls == [
            (
                "spreadsheets.batchUpdate",
                {
                    "spreadsheetId": "sid",
                    "body": {
                        "requests": [
                            {"addConditionalFormatRule": {"rule": RULE, "index": 2}}
                        ]
                    },
                },
            )
        ]

    def test_add_defaults_to_first(self):
        svc = FakeSheetsService()
        add_conditional_rule(svc, "sid", RULE)
        request = svc.calls[0][1]["body"]["requests"][0]
        assert request["addConditionalFormatRule"]["index"] == 0

    def test_add_negative_index_raises(self):
        svc = FakeSheetsService()
        with pytest.raises(ValueError, match="non-negative"):
            add_conditional_rule(svc, "sid", RULE, index=-1)
        assert svc.calls == []

    def test_delete_request_body(self):
        svc = FakeSheetsService()
        delete_conditional_rule(svc, "sid", 42, 1)
        assert svc.calls[0][1]["body"] == {
            "requests": [{"deleteConditionalFormatRule": {"sheetId": 42, "index": 1}}]
        }

    def test_delete_negative_index_raises(self):
        svc = FakeSheetsService()
        with pytest.raises(ValueError, match="non-negative"):
            delete_conditional_rule(svc, "sid", 42, -1)
        assert svc.calls == []


# -- read_rule_json --


class TestReadRuleJson:
    def write(self, tmp_path, data):
        path = tmp_path / "rule.json"
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return str(path)

    def test_bare_rule(self, tmp_path):
        assert read_rule_json(self.write(tmp_path, RULE)) == RULE

    def test_unwraps_sheets_rules_entry(self, tmp_path):
        entry = {"tab": "Sheet1", "sheet_id": 0, "index": 0, "rule": RULE}
        assert read_rule_json(self.write(tmp_path, entry)) == RULE

    def test_gradient_rule(self, tmp_path):
        rule = {"ranges": [], "gradientRule": {}}
        assert read_rule_json(self.write(tmp_path, rule)) == rule

    @pytest.mark.parametrize("data", [[RULE], {"ranges": []}, {"rule": [RULE]}])
    def test_wrong_shape_raises(self, tmp_path, data):
        with pytest.raises(ValueError, match="expected one conditional format rule"):
            read_rule_json(self.write(tmp_path, data))

    def test_invalid_json_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            read_rule_json(self.write(tmp_path, "{not json"))


# -- display --


class TestDescribeRule:
    def test_custom_formula(self):
        assert (
            describe_rule(RULE)
            == 'A2:AA  =$F2="Rejected"  [strikethrough, text #999999]'
        )

    def test_other_condition_type_and_several_ranges(self):
        rule = {
            "ranges": [
                {"startColumnIndex": 0, "endColumnIndex": 1},
                {"sheetId": 0},
            ],
            "booleanRule": {
                "condition": {
                    "type": "TEXT_CONTAINS",
                    "values": [{"userEnteredValue": "x"}],
                },
                "format": {"bold": True, "backgroundColor": {"red": 1}},
            },
        }
        assert (
            describe_rule(rule) == "A:A, (whole tab)  TEXT_CONTAINS x  [fill #ff0000]"
        )

    def test_relative_date_and_color_styles(self):
        rule = {
            "ranges": [],
            "booleanRule": {
                "condition": {
                    "type": "DATE_BEFORE",
                    "values": [{"relativeDate": "TODAY"}],
                },
                "format": {
                    "textFormat": {
                        "bold": True,
                        "foregroundColorStyle": {"rgbColor": {"blue": 1}},
                    },
                    "backgroundColorStyle": {"rgbColor": {"green": 1}},
                },
            },
        }
        assert (
            describe_rule(rule)
            == "  DATE_BEFORE TODAY  [bold, text #0000ff, fill #00ff00]"
        )

    def test_theme_color_only_and_empty_format(self):
        rule = {
            "ranges": [{"sheetId": 0}],
            "booleanRule": {
                "condition": {"type": "NOT_BLANK"},
                "format": {
                    "textFormat": {"foregroundColorStyle": {"themeColor": "ACCENT1"}}
                },
            },
        }
        assert describe_rule(rule) == "(whole tab)  NOT_BLANK  [no format]"

    def test_black_text_is_shown(self):
        # The API sends black as an empty Color: every channel omitted.
        rule = {"booleanRule": {"format": {"textFormat": {"foregroundColor": {}}}}}
        assert describe_rule(rule).endswith("[text #000000]")

    def test_gradient(self):
        rule = {
            "ranges": [{"startColumnIndex": 2, "endColumnIndex": 3}],
            "gradientRule": {},
        }
        assert describe_rule(rule) == "C:C  color scale"


class TestFormatRules:
    def test_groups_by_tab(self):
        other = {"ranges": [{"sheetId": 7}], "gradientRule": {}}
        rules = [
            {"tab": "Sheet1", "sheet_id": 0, "index": 0, "rule": RULE},
            {"tab": "Sheet1", "sheet_id": 0, "index": 1, "rule": RULE},
            {"tab": "Data", "sheet_id": 7, "index": 0, "rule": other},
        ]
        line = 'A2:AA  =$F2="Rejected"  [strikethrough, text #999999]'
        assert format_rules(rules) == "\n".join(
            [
                "Sheet1 (sheetId 0)",
                f"  [0] {line}",
                f"  [1] {line}",
                "Data (sheetId 7)",
                "  [0] (whole tab)  color scale",
            ]
        )

    def test_empty(self):
        assert format_rules([]) == ""


# -- run_rules --


class TestRunRules:
    def test_prints_grouped_with_read_scope(self, monkeypatch, capsys):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, [RULE])))
        rec = patch_sheets_service(monkeypatch, svc)
        run_rules("SHEET_ID")
        assert rec["scopes"] is None  # the default read-only scope
        out = capsys.readouterr().out
        assert out.startswith("Sheet1 (sheetId 0)\n  [0] A2:AA")

    def test_json(self, monkeypatch, capsys):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, [RULE])))
        patch_sheets_service(monkeypatch, svc)
        run_rules("SHEET_ID", as_json=True)
        assert json.loads(capsys.readouterr().out) == [
            {"tab": "Sheet1", "sheet_id": 0, "index": 0, "rule": RULE}
        ]

    def test_json_empty_is_empty_list(self, monkeypatch, capsys):
        patch_sheets_service(monkeypatch, FakeSheetsService(meta=meta(("S", 0, None))))
        run_rules("SHEET_ID", as_json=True)
        assert json.loads(capsys.readouterr().out) == []

    def test_no_rules_message(self, monkeypatch, capsys):
        patch_sheets_service(monkeypatch, FakeSheetsService(meta=meta(("S", 0, None))))
        run_rules("SHEET_ID")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "(no conditional format rules)" in captured.err


# -- run_add_rule --


class TestRunAddRule:
    def test_builds_rule_with_write_scope(self, monkeypatch, capsys):
        from gdrives.auth import SHEETS_WRITE_SCOPES

        svc = FakeSheetsService(meta=meta(("Sheet1", 0, None), ("Data", 7, None)))
        rec = patch_sheets_service(monkeypatch, svc)
        run_add_rule(
            "SHEET_ID",
            ranges=["Data!A2:C", "Data!E:E"],
            formula="=$B2>0",
            bold=True,
            background="#ff0000",
            index=1,
        )
        assert rec["scopes"] == SHEETS_WRITE_SCOPES
        # one tab lookup serves both ranges, then one write
        assert [c[0] for c in svc.calls] == [
            "spreadsheets.get",
            "spreadsheets.batchUpdate",
        ]
        (request,) = svc.calls[1][1]["body"]["requests"]
        assert request == {
            "addConditionalFormatRule": {
                "index": 1,
                "rule": {
                    "ranges": [
                        {
                            "sheetId": 7,
                            "startRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 3,
                        },
                        {"sheetId": 7, "startColumnIndex": 4, "endColumnIndex": 5},
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": "=$B2>0"}],
                        },
                        "format": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0},
                        },
                    },
                },
            }
        }
        assert (
            "Added rule at index 1: A2:C, E:E  =$B2>0  [bold, fill #ff0000]"
            in capsys.readouterr().out
        )

    def test_text_color_and_remaining_flags(self, monkeypatch):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, None)))
        patch_sheets_service(monkeypatch, svc)
        run_add_rule(
            "SHEET_ID",
            ranges=["A1"],
            formula="=TRUE",
            italic=True,
            strikethrough=True,
            underline=True,
            text_color="#999999",
        )
        rule = svc.calls[1][1]["body"]["requests"][0]["addConditionalFormatRule"][
            "rule"
        ]
        assert rule["booleanRule"]["format"] == {
            "textFormat": {
                "italic": True,
                "strikethrough": True,
                "underline": True,
                "foregroundColor": GREY,
            }
        }

    def test_replays_rule_json(self, monkeypatch, tmp_path, capsys):
        path = tmp_path / "rule.json"
        path.write_text(
            json.dumps({"tab": "Sheet1", "sheet_id": 0, "index": 3, "rule": RULE})
        )
        svc = FakeSheetsService()
        patch_sheets_service(monkeypatch, svc)
        run_add_rule("SHEET_ID", rule_json=str(path))
        # no tab lookup: the rule's ranges already carry their sheetId
        assert [c[0] for c in svc.calls] == ["spreadsheets.batchUpdate"]
        request = svc.calls[0][1]["body"]["requests"][0]["addConditionalFormatRule"]
        assert request == {"rule": RULE, "index": 0}
        assert "Added rule at index 0: A2:AA" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "extra",
        [
            {"ranges": ["A1"]},
            {"formula": "=TRUE"},
            {"bold": True},
            {"text_color": "#000"},
            {"background": "#000"},
        ],
    )
    def test_rule_json_excludes_builder_options(self, monkeypatch, tmp_path, extra):
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: pytest.fail("must not build a service"),
        )
        with pytest.raises(ValueError, match="cannot be combined"):
            run_add_rule("SHEET_ID", rule_json=str(tmp_path / "r.json"), **extra)

    @pytest.mark.parametrize("kwargs", [{}, {"ranges": ["A1"]}, {"formula": "=TRUE"}])
    def test_needs_range_and_formula(self, monkeypatch, kwargs):
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: pytest.fail("must not build a service"),
        )
        with pytest.raises(ValueError, match="pass --range and --formula"):
            run_add_rule("SHEET_ID", bold=True, **kwargs)

    def test_bad_color_fails_before_any_call(self, monkeypatch):
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: pytest.fail("must not build a service"),
        )
        with pytest.raises(ValueError, match="hex string"):
            run_add_rule("SHEET_ID", ranges=["A1"], formula="=TRUE", text_color="grey")

    def test_no_format_fails_before_any_call(self, monkeypatch):
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: pytest.fail("must not build a service"),
        )
        with pytest.raises(ValueError, match="format option"):
            run_add_rule("SHEET_ID", ranges=["A1"], formula="=TRUE")

    def test_ranges_on_two_tabs_refuse_without_writing(self, monkeypatch):
        svc = FakeSheetsService(meta=meta(("Sheet1", 0, None), ("Data", 7, None)))
        patch_sheets_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="all be on one tab"):
            run_add_rule(
                "SHEET_ID", ranges=["Sheet1!A1", "Data!A1"], formula="=TRUE", bold=True
            )
        assert [c[0] for c in svc.calls] == ["spreadsheets.get"]


# -- run_delete_rule --


class TestRunDeleteRule:
    def service(self):
        # The one read supplies both the tab ids and the rule list.
        return FakeSheetsService(
            meta=meta(
                ("Sheet1", 0, [RULE]),
                ("Data", 7, [RULE, {"ranges": [], "gradientRule": {}}]),
            )
        )

    def test_yes_deletes_on_named_tab(self, monkeypatch, capsys):
        from gdrives.auth import SHEETS_WRITE_SCOPES

        svc = self.service()
        rec = patch_sheets_service(monkeypatch, svc)
        monkeypatch.setattr(
            "typer.confirm",
            lambda *a, **k: pytest.fail("must not prompt with yes=True"),
        )
        run_delete_rule("SHEET_ID", 1, tab="Data", yes=True)
        assert rec["scopes"] == SHEETS_WRITE_SCOPES
        # one read serves the tab lookup and the rule list, then one write
        assert [c[0] for c in svc.calls] == [
            "spreadsheets.get",
            "spreadsheets.batchUpdate",
        ]
        assert svc.calls[0][1]["fields"] == (
            "sheets(properties(sheetId,title),conditionalFormats)"
        )
        assert svc.calls[-1] == (
            "spreadsheets.batchUpdate",
            {
                "spreadsheetId": "SHEET_ID",
                "body": {
                    "requests": [
                        {"deleteConditionalFormatRule": {"sheetId": 7, "index": 1}}
                    ]
                },
            },
        )
        assert "Deleted rule [1] on Data:   color scale" in capsys.readouterr().out

    def test_prompt_shows_rule_and_decline_aborts(self, monkeypatch, capsys):
        svc = self.service()
        patch_sheets_service(monkeypatch, svc)
        prompts = []
        monkeypatch.setattr(
            "typer.confirm", lambda text, **k: prompts.append(text) or False
        )
        run_delete_rule("SHEET_ID", 0)  # default: first tab
        assert prompts == [
            'Delete rule [0] on Sheet1: A2:AA  =$F2="Rejected"  '
            "[strikethrough, text #999999]?"
        ]
        assert "spreadsheets.batchUpdate" not in [c[0] for c in svc.calls]
        assert "Aborted." in capsys.readouterr().err

    def test_accepted_prompt_deletes(self, monkeypatch):
        svc = self.service()
        patch_sheets_service(monkeypatch, svc)
        monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
        run_delete_rule("SHEET_ID", 0)
        assert svc.calls[-1][1]["body"]["requests"] == [
            {"deleteConditionalFormatRule": {"sheetId": 0, "index": 0}}
        ]

    @pytest.mark.parametrize("index", [1, -1])
    def test_index_out_of_range_refuses(self, monkeypatch, index):
        svc = self.service()
        patch_sheets_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="'Sheet1' has 1 rule"):
            run_delete_rule("SHEET_ID", index, yes=True)
        assert "spreadsheets.batchUpdate" not in [c[0] for c in svc.calls]

    def test_unknown_tab_refuses(self, monkeypatch):
        patch_sheets_service(monkeypatch, self.service())
        with pytest.raises(ValueError, match="no tab named 'Nope'"):
            run_delete_rule("SHEET_ID", 0, tab="Nope", yes=True)

    def test_no_tabs_refuses(self, monkeypatch):
        patch_sheets_service(monkeypatch, FakeSheetsService())
        with pytest.raises(ValueError, match="no tabs"):
            run_delete_rule("SHEET_ID", 0, yes=True)
