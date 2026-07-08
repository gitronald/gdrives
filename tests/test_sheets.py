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
    append_values,
    clear_values,
    format_values,
    list_tabs,
    pull_values,
    read_values_csv,
    resolve_spreadsheet_id,
    run_append,
    run_clear,
    run_get,
    run_update,
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
