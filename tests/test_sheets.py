"""Tests for gdrives.sheets — value ops, source resolution, CSV, and entry points.

The core helpers run against ``FakeSheetsService`` (tests/helpers.py), which
records each ``(method, kwargs)`` call and returns a preset response, so tests
assert both the exact request shape and the parsed result. The ``run_*`` entry
points patch ``build_sheets_service`` at its source (``gdrives.auth``) since they
import it lazily.
"""

import pytest
from helpers import FakeSheetsService

from gdrives.sheets import (
    a1_quote,
    append_values,
    batch_update_values,
    clear_values,
    column_letter,
    find_rows,
    format_values,
    list_tabs,
    parse_pairs,
    pull_values,
    read_values_csv,
    resolve_spreadsheet_id,
    run_append,
    run_clear,
    run_get,
    run_set,
    run_update,
    set_by_match,
    update_values,
    write_values_csv,
)

# -- pull_values --


class TestPullValues:
    def test_returns_values(self):
        svc = FakeSheetsService(get={"values": [["a", "b"], ["1", "2"]]})
        assert pull_values(svc, "sid", "Sheet1!A1:B2") == [["a", "b"], ["1", "2"]]
        assert svc.calls == [
            ("values.get", {"spreadsheetId": "sid", "range": "Sheet1!A1:B2"})
        ]

    def test_empty_range_returns_empty_list(self):
        # Sheets omits "values" entirely for an empty range.
        svc = FakeSheetsService(get={})
        assert pull_values(svc, "sid", "Sheet1!Z1:Z9") == []


# -- update_values --


class TestUpdateValues:
    def test_default_input_option_is_user_entered(self):
        svc = FakeSheetsService(update={"updatedCells": 4})
        result = update_values(svc, "sid", "A1:B2", [["a", "b"], ["1", "2"]])
        assert result == {"updatedCells": 4}
        assert svc.calls == [
            (
                "values.update",
                {
                    "spreadsheetId": "sid",
                    "range": "A1:B2",
                    "valueInputOption": "USER_ENTERED",
                    "body": {"values": [["a", "b"], ["1", "2"]]},
                },
            )
        ]

    def test_raw_input_option(self):
        svc = FakeSheetsService()
        update_values(svc, "sid", "A1", [["=SUM(1,2)"]], input_option="RAW")
        assert svc.calls[0][1]["valueInputOption"] == "RAW"


# -- append_values --


class TestAppendValues:
    def test_appends_with_body_and_option(self):
        svc = FakeSheetsService(append={"updates": {"updatedRows": 1}})
        result = append_values(svc, "sid", "Sheet1!A1", [["x", "y"]])
        assert result == {"updates": {"updatedRows": 1}}
        assert svc.calls == [
            (
                "values.append",
                {
                    "spreadsheetId": "sid",
                    "range": "Sheet1!A1",
                    "valueInputOption": "USER_ENTERED",
                    "body": {"values": [["x", "y"]]},
                },
            )
        ]


# -- clear_values --


class TestClearValues:
    def test_clears_with_empty_body(self):
        svc = FakeSheetsService(clear={"clearedRange": "Sheet1!A1:C10"})
        result = clear_values(svc, "sid", "Sheet1!A1:C10")
        assert result == {"clearedRange": "Sheet1!A1:C10"}
        assert svc.calls == [
            (
                "values.clear",
                {"spreadsheetId": "sid", "range": "Sheet1!A1:C10", "body": {}},
            )
        ]


# -- list_tabs --


class TestListTabs:
    def test_returns_titles_in_order(self):
        svc = FakeSheetsService(
            meta={
                "sheets": [
                    {"properties": {"title": "Summary"}},
                    {"properties": {"title": "Data"}},
                ]
            }
        )
        assert list_tabs(svc, "sid") == ["Summary", "Data"]
        assert svc.calls == [
            (
                "spreadsheets.get",
                {"spreadsheetId": "sid", "fields": "sheets.properties.title"},
            )
        ]

    def test_no_sheets_returns_empty(self):
        svc = FakeSheetsService(meta={})
        assert list_tabs(svc, "sid") == []


# -- resolve_spreadsheet_id --


class TestResolveSpreadsheetId:
    def test_url_extracts_id(self):
        url = "https://docs.google.com/spreadsheets/d/SHEET123/edit#gid=0"
        assert resolve_spreadsheet_id(url) == "SHEET123"

    def test_bare_id_returned_as_is(self):
        assert resolve_spreadsheet_id("SHEET123") == "SHEET123"

    def test_path_uses_resolve_path(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda path, service=None, *, allow_files=False: (
                rec.update(path=path, allow_files=allow_files) or "RESOLVED"
            ),
        )
        assert resolve_spreadsheet_id("My Drive/budget") == "RESOLVED"
        assert rec == {"path": "My Drive/budget", "allow_files": True}


# -- CSV interchange --


class TestCsvInterchange:
    def test_round_trip(self, tmp_path):
        rows = [["name", "score"], ["alice", "10"], ["bob", "20"]]
        path = tmp_path / "data.csv"
        write_values_csv(str(path), rows)
        assert read_values_csv(str(path)) == rows

    def test_tsv_delimiter(self, tmp_path):
        rows = [["a", "b"], ["1", "2"]]
        path = tmp_path / "data.tsv"
        write_values_csv(str(path), rows, delimiter="\t")
        assert "a\tb" in path.read_text()
        assert read_values_csv(str(path), delimiter="\t") == rows

    def test_creates_missing_parent_dir(self, tmp_path):
        path = tmp_path / "new" / "sub" / "out.csv"
        write_values_csv(str(path), [["x"]])
        assert read_values_csv(str(path)) == [["x"]]


# -- format_values --


class TestFormatValues:
    def test_empty_is_blank(self):
        assert format_values([]) == ""

    def test_aligns_columns(self):
        # Interior columns pad to width; trailing whitespace is stripped per row.
        out = format_values([["name", "n"], ["alice", "10"]])
        assert out == "name   n\nalice  10"

    def test_pads_ragged_rows(self):
        # Sheets truncates rows at the last non-empty cell; short rows still align.
        out = format_values([["a", "b", "c"], ["1"]])
        assert out.splitlines() == ["a  b  c", "1"]


# -- run_get --


class TestRunGet:
    def test_default_range_uses_first_tab(self, monkeypatch, capsys):
        svc = FakeSheetsService(
            meta={"sheets": [{"properties": {"title": "Sheet1"}}]},
            get={"values": [["a", "b"]]},
        )
        rec = {}
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: rec.update(scopes=scopes) or svc,
        )
        run_get("SHEET_ID")
        # Read-only: default scope (None -> read-only inside build_sheets_service).
        assert rec["scopes"] is None
        # list_tabs (meta) then values.get on the first tab.
        assert svc.calls[0][0] == "spreadsheets.get"
        assert svc.calls[1] == (
            "values.get",
            {"spreadsheetId": "SHEET_ID", "range": "Sheet1"},
        )
        assert "a  b" in capsys.readouterr().out

    def test_explicit_range_skips_tab_lookup(self, monkeypatch, capsys):
        svc = FakeSheetsService(get={"values": [["x"]]})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        run_get("SHEET_ID", "Data!A1:A1")
        assert [c[0] for c in svc.calls] == ["values.get"]

    def test_output_writes_csv(self, monkeypatch, tmp_path, capsys):
        svc = FakeSheetsService(get={"values": [["a", "b"], ["1", "2"]]})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        out = tmp_path / "out.csv"
        run_get("SHEET_ID", "A1:B2", output=str(out))
        assert read_values_csv(str(out)) == [["a", "b"], ["1", "2"]]
        assert "Wrote 2 row(s)" in capsys.readouterr().err

    def test_delimited_stdout_when_not_aligned(self, monkeypatch, capsys):
        svc = FakeSheetsService(get={"values": [["a", "b"]]})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        run_get("SHEET_ID", "A1:B1", aligned=False)
        assert "a,b" in capsys.readouterr().out

    def test_empty_range_message(self, monkeypatch, capsys):
        svc = FakeSheetsService(get={})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        run_get("SHEET_ID", "Z1:Z9")
        assert "(empty range)" in capsys.readouterr().err

    def test_no_tabs_raises(self, monkeypatch):
        svc = FakeSheetsService(meta={})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        with pytest.raises(ValueError, match="no tabs"):
            run_get("SHEET_ID")


# -- run_update / run_append (write scope + CSV input) --


class TestRunWrite:
    def _patch_service(self, monkeypatch, svc):
        rec = {}
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: rec.update(scopes=scopes) or svc,
        )
        return rec

    def test_update_reads_csv_and_requests_write_scope(
        self, monkeypatch, tmp_path, capsys
    ):
        from gdrives.auth import SHEETS_WRITE_SCOPES

        svc = FakeSheetsService(
            update={"updatedCells": 4, "updatedRange": "Sheet1!A1:B2"}
        )
        rec = self._patch_service(monkeypatch, svc)
        csv_path = tmp_path / "data.csv"
        write_values_csv(str(csv_path), [["a", "b"], ["1", "2"]])
        run_update("SHEET_ID", "Sheet1!A1:B2", str(csv_path))
        assert rec["scopes"] == SHEETS_WRITE_SCOPES
        assert svc.calls[0] == (
            "values.update",
            {
                "spreadsheetId": "SHEET_ID",
                "range": "Sheet1!A1:B2",
                "valueInputOption": "USER_ENTERED",
                "body": {"values": [["a", "b"], ["1", "2"]]},
            },
        )
        assert "Updated 4 cell(s) in Sheet1!A1:B2" in capsys.readouterr().out

    def test_update_raw_flag_selects_raw(self, monkeypatch, tmp_path):
        svc = FakeSheetsService()
        self._patch_service(monkeypatch, svc)
        csv_path = tmp_path / "data.csv"
        write_values_csv(str(csv_path), [["=SUM(1,2)"]])
        run_update("SHEET_ID", "A1", str(csv_path), raw=True)
        assert svc.calls[0][1]["valueInputOption"] == "RAW"

    def test_append_reports_rows(self, monkeypatch, tmp_path, capsys):
        svc = FakeSheetsService(
            append={"updates": {"updatedRows": 2, "updatedRange": "Sheet1!A3:B4"}}
        )
        self._patch_service(monkeypatch, svc)
        csv_path = tmp_path / "data.csv"
        write_values_csv(str(csv_path), [["a", "b"], ["c", "d"]])
        run_append("SHEET_ID", "Sheet1!A1", str(csv_path))
        assert svc.calls[0][0] == "values.append"
        assert "Appended 2 row(s) to Sheet1!A3:B4" in capsys.readouterr().out


# -- run_clear (confirmation) --


class TestRunClear:
    def test_yes_skips_confirm_and_clears(self, monkeypatch, capsys):
        svc = FakeSheetsService(clear={"clearedRange": "Sheet1!A1:C10"})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        monkeypatch.setattr(
            "typer.confirm",
            lambda *a, **k: pytest.fail("must not prompt with yes=True"),
        )
        run_clear("SHEET_ID", "Sheet1!A1:C10", yes=True)
        assert svc.calls[0][0] == "values.clear"
        assert "Cleared Sheet1!A1:C10" in capsys.readouterr().out

    def test_declined_confirm_aborts_without_clearing(self, monkeypatch, capsys):
        svc = FakeSheetsService()
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
        run_clear("SHEET_ID", "Sheet1!A1:C10")
        assert svc.calls == []
        assert "Aborted." in capsys.readouterr().err

    def test_accepted_confirm_clears(self, monkeypatch):
        svc = FakeSheetsService(clear={"clearedRange": "A1:B2"})
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
        run_clear("SHEET_ID", "A1:B2")
        assert svc.calls[0][0] == "values.clear"


# -- column_letter / a1_quote --


class TestColumnLetter:
    @pytest.mark.parametrize(
        "index, letter",
        [(0, "A"), (25, "Z"), (26, "AA"), (51, "AZ"), (701, "ZZ"), (702, "AAA")],
    )
    def test_index_to_letter(self, index, letter):
        assert column_letter(index) == letter

    def test_negative_index_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            column_letter(-1)


class TestA1Quote:
    def test_plain_name(self):
        assert a1_quote("Sheet1") == "'Sheet1'"

    def test_name_with_space(self):
        assert a1_quote("Q3 Budget") == "'Q3 Budget'"

    def test_embedded_quote_is_doubled(self):
        assert a1_quote("O'Brien") == "'O''Brien'"


# -- find_rows --

GRID = [
    ["id", "name", "status", "amount"],
    ["A100", "Ada", "pending", "0"],
    ["B200", "Grace", "pending", "0"],
    ["C300", "Alan", "pending", "0"],
]


class TestFindRows:
    def test_single_column_match(self):
        assert find_rows(GRID, {"id": "C300"}) == [4]  # header is row 1

    def test_matches_all_pending(self):
        assert find_rows(GRID, {"status": "pending"}) == [2, 3, 4]

    def test_composite_and_key(self):
        grid = [["year", "id"], ["2025", "C300"], ["2026", "C300"]]
        assert find_rows(grid, {"year": "2026", "id": "C300"}) == [3]

    def test_no_match_returns_empty(self):
        assert find_rows(GRID, {"id": "ZZZ"}) == []

    def test_empty_match_matches_all_data_rows(self):
        assert find_rows(GRID, {}) == [2, 3, 4]

    def test_empty_grid(self):
        assert find_rows([], {"id": "x"}) == []

    def test_ragged_missing_cell_is_empty_string(self):
        grid = [["a", "b", "c"], ["1"]]  # row 2 truncated at last non-empty cell
        assert find_rows(grid, {"b": ""}) == [2]
        assert find_rows(grid, {"c": "z"}) == []

    def test_unknown_column_raises(self):
        with pytest.raises(ValueError, match="not in header"):
            find_rows(GRID, {"missing": "x"})


# -- batch_update_values --


class TestBatchUpdateValues:
    def test_builds_data_body(self):
        svc = FakeSheetsService(batchUpdate={"totalUpdatedCells": 2})
        data = [("S!A1", [["x"]]), ("S!B2", [["y"]])]
        result = batch_update_values(svc, "sid", data)
        assert result == {"totalUpdatedCells": 2}
        method, kwargs = svc.calls[0]
        assert method == "values.batchUpdate"
        assert kwargs["spreadsheetId"] == "sid"
        assert kwargs["body"] == {
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": "S!A1", "values": [["x"]]},
                {"range": "S!B2", "values": [["y"]]},
            ],
        }

    def test_raw_option(self):
        svc = FakeSheetsService()
        batch_update_values(svc, "sid", [("S!A1", [["=1"]])], input_option="RAW")
        assert svc.calls[0][1]["body"]["valueInputOption"] == "RAW"


# -- set_by_match --


class TestSetByMatch:
    def test_single_row_multiple_columns(self):
        svc = FakeSheetsService(
            get={"values": GRID}, batchUpdate={"totalUpdatedCells": 2}
        )
        result = set_by_match(
            svc, "sid", "Sheet1", {"id": "C300"}, {"status": "paid", "amount": "250"}
        )
        assert result == {"rows": [4], "updated_cells": 2}
        assert svc.calls[0][0] == "values.get"
        assert svc.calls[0][1]["range"] == "'Sheet1'"  # reads via quoted tab name
        body = svc.calls[1][1]["body"]
        assert body["data"] == [
            {"range": "'Sheet1'!C4", "values": [["paid"]]},
            {"range": "'Sheet1'!D4", "values": [["250"]]},
        ]

    def test_composite_key(self):
        grid = [
            ["year", "id", "status"],
            ["2025", "C300", "old"],
            ["2026", "C300", "x"],
        ]
        svc = FakeSheetsService(
            get={"values": grid}, batchUpdate={"totalUpdatedCells": 1}
        )
        result = set_by_match(
            svc, "sid", "S", {"year": "2026", "id": "C300"}, {"status": "paid"}
        )
        assert result["rows"] == [3]
        assert svc.calls[1][1]["body"]["data"] == [
            {"range": "'S'!C3", "values": [["paid"]]}
        ]

    def test_no_match_refuses(self):
        svc = FakeSheetsService(get={"values": GRID})
        with pytest.raises(ValueError, match="no row matching"):
            set_by_match(svc, "sid", "S", {"id": "ZZZ"}, {"status": "x"})
        assert [c[0] for c in svc.calls] == ["values.get"]  # never wrote

    def test_multiple_matches_refuses_without_all(self):
        grid = [["id", "v"], ["X", "1"], ["X", "2"]]
        svc = FakeSheetsService(get={"values": grid})
        with pytest.raises(ValueError, match="matches rows"):
            set_by_match(svc, "sid", "S", {"id": "X"}, {"v": "9"})
        assert [c[0] for c in svc.calls] == ["values.get"]

    def test_multiple_matches_with_allow_multiple(self):
        grid = [["id", "v"], ["X", "1"], ["X", "2"]]
        svc = FakeSheetsService(
            get={"values": grid}, batchUpdate={"totalUpdatedCells": 2}
        )
        result = set_by_match(
            svc, "sid", "S", {"id": "X"}, {"v": "9"}, allow_multiple=True
        )
        assert result["rows"] == [2, 3]
        assert svc.calls[1][1]["body"]["data"] == [
            {"range": "'S'!B2", "values": [["9"]]},
            {"range": "'S'!B3", "values": [["9"]]},
        ]

    def test_unknown_target_column_refuses(self):
        svc = FakeSheetsService(get={"values": GRID})
        with pytest.raises(ValueError, match="target column"):
            set_by_match(svc, "sid", "S", {"id": "C300"}, {"nope": "x"})

    def test_empty_tab_refuses(self):
        svc = FakeSheetsService(get={})
        with pytest.raises(ValueError, match="empty"):
            set_by_match(svc, "sid", "S", {"id": "C300"}, {"status": "x"})

    def test_no_columns_to_set_refuses(self):
        svc = FakeSheetsService(get={"values": GRID})
        with pytest.raises(ValueError, match="no columns to set"):
            set_by_match(svc, "sid", "S", {"id": "C300"}, {})

    def test_raw_input_option(self):
        svc = FakeSheetsService(get={"values": GRID}, batchUpdate={})
        set_by_match(
            svc, "sid", "S", {"id": "C300"}, {"status": "=A1"}, input_option="RAW"
        )
        assert svc.calls[1][1]["body"]["valueInputOption"] == "RAW"


# -- parse_pairs --


class TestParsePairs:
    def test_parses_pairs(self):
        assert parse_pairs(["a=1", "b=2"], "--set") == {"a": "1", "b": "2"}

    def test_splits_on_first_equals(self):
        assert parse_pairs(["k=x=y"], "--set") == {"k": "x=y"}

    def test_strips_column_keeps_value(self):
        assert parse_pairs(["  id =C300"], "--match") == {"id": "C300"}

    def test_last_wins_on_repeat(self):
        assert parse_pairs(["a=1", "a=2"], "--set") == {"a": "2"}

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="COLUMN=VALUE"):
            parse_pairs(["noequals"], "--set")

    def test_empty_column_raises(self):
        with pytest.raises(ValueError, match="empty column"):
            parse_pairs(["=v"], "--set")


# -- run_set --


class TestRunSet:
    def test_default_tab_and_write_scope(self, monkeypatch, capsys):
        from gdrives.auth import SHEETS_WRITE_SCOPES

        svc = FakeSheetsService(
            meta={"sheets": [{"properties": {"title": "Sheet1"}}]},
            get={"values": GRID},
            batchUpdate={"totalUpdatedCells": 1},
        )
        rec = {}
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service",
            lambda scopes=None: rec.update(scopes=scopes) or svc,
        )
        run_set("SHEET_ID", {"id": "C300"}, {"status": "paid"})
        assert rec["scopes"] == SHEETS_WRITE_SCOPES
        # list_tabs (default tab) -> read grid -> batch write
        assert [c[0] for c in svc.calls] == [
            "spreadsheets.get",
            "values.get",
            "values.batchUpdate",
        ]
        assert (
            "Set 1 cell(s) across 1 row(s) (row 4) in Sheet1" in capsys.readouterr().out
        )

    def test_explicit_tab_skips_tab_lookup(self, monkeypatch):
        svc = FakeSheetsService(
            get={"values": GRID}, batchUpdate={"totalUpdatedCells": 1}
        )
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        run_set("SHEET_ID", {"id": "C300"}, {"status": "paid"}, tab="Data")
        assert [c[0] for c in svc.calls] == ["values.get", "values.batchUpdate"]
        assert svc.calls[0][1]["range"] == "'Data'"

    def test_no_tabs_raises(self, monkeypatch):
        svc = FakeSheetsService(meta={})  # default tab lookup finds nothing
        monkeypatch.setattr(
            "gdrives.auth.build_sheets_service", lambda scopes=None: svc
        )
        with pytest.raises(ValueError, match="no tabs"):
            run_set("SHEET_ID", {"id": "C300"}, {"status": "paid"})
