"""Tests for gdrives.cli — command wiring, validation, and error handling.

Typer's ``@app.command()`` returns the wrapped function unchanged, so each
command is called directly with plain Python defaults; the lazily-imported
delegates (run/ls/resolve/build_drive_service) are patched at their source.
"""

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError, TransportError
from helpers import plain
from httplib2 import ServerNotFoundError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError
from typer.testing import CliRunner

from gdrives import cli
from gdrives.files import IncompleteSearchError
from gdrives.resolve import DrivePathError

DRIVE = {
    "url": "https://drive.google.com/drive/my-drive",
    "type": "user",
    "name": "My Drive",
    "id": "root",
}


class TestVersion:
    def test_prints_the_installed_version_and_exits_0(self):
        result = CliRunner().invoke(cli.app, ["--version"])
        assert result.exit_code == 0
        assert result.output == f"gdrives {version('gdrives')}\n"

    def test_needs_no_command_and_runs_none(self, monkeypatch):
        def ran(*args, **kwargs):
            raise AssertionError("a command ran")

        monkeypatch.setattr("gdrives.auth.build_drive_service", ran)
        result = CliRunner().invoke(cli.app, ["--version", "show-drives"])
        assert result.exit_code == 0
        assert result.output == f"gdrives {version('gdrives')}\n"

    def test_help_keeps_the_app_description_and_lists_the_option(self):
        result = CliRunner().invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        assert "Google Drive file management tools." in plain(result.output)
        assert "--version" in plain(result.output)


class TestExport:
    def test_delegates_to_run(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.run",
            lambda source, output, newline=None: rec.update(s=source, o=output),
        )
        cli.export("https://docs.google.com/document/d/X/edit", "out.docx")
        assert rec == {
            "s": "https://docs.google.com/document/d/X/edit",
            "o": "out.docx",
        }

    def test_newline_option_is_passed_on(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.run",
            lambda source, output, newline: rec.update(n=newline),
        )
        result = CliRunner().invoke(
            cli.app, ["export", "SHEET", "-o", "out.csv", "--newline", "lf"]
        )
        assert result.exit_code == 0
        assert rec == {"n": "lf"}

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(source, output, newline=None):
            raise ValueError("Unsupported output extension '.bad'")

        monkeypatch.setattr("gdrives.export.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.export("src", "out.bad")
        assert exc.value.code == 1
        assert "Error: Unsupported output extension" in capsys.readouterr().err

    def test_http_error_exits_1(self, monkeypatch, capsys):
        # The shared error seam turns a Drive API HttpError into a clean message
        # + exit 1 instead of a raw traceback (covers every command).
        from googleapiclient.errors import HttpError

        class FakeResp:
            status = 404
            reason = "Not Found"

        def boom(source, output, newline=None):
            raise HttpError(FakeResp(), b"")

        monkeypatch.setattr("gdrives.export.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.export("https://docs.google.com/document/d/X/edit", "out.docx")
        assert exc.value.code == 1
        assert "Drive API request failed" in capsys.readouterr().err


class TestDownload:
    def test_delegates_with_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.download.run",
            lambda source, output_dir, *, depth, yes, skip_existing: rec.update(
                s=source, od=output_dir, depth=depth, yes=yes, skip=skip_existing
            ),
        )
        cli.download("My Drive/refs", output_dir="out", depth=2, yes=True)
        assert rec == {
            "s": "My Drive/refs",
            "od": "out",
            "depth": 2,
            "yes": True,
            "skip": False,
        }
        cli.download("1AbC", skip_existing=True)
        assert rec["s"] == "1AbC" and rec["skip"] is True

    def test_partial_failure_exits_1_with_the_list(self, monkeypatch, capsys):
        from gdrives.download import DownloadError

        def boom(*a, **k):
            raise DownloadError("1 item(s) failed to download:\n  Big: 403")

        monkeypatch.setattr("gdrives.download.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.download("My Drive/refs")
        assert exc.value.code == 1
        assert capsys.readouterr().err == (
            "Error: 1 item(s) failed to download:\n  Big: 403\n"
        )

    def test_path_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise DrivePathError("folder 'missing' not found in Drive")

        monkeypatch.setattr("gdrives.download.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.download("My Drive/missing")
        assert exc.value.code == 1
        assert "Error: folder 'missing' not found" in capsys.readouterr().err


class TestLs:
    @pytest.fixture(autouse=True)
    def service(self, monkeypatch):
        """The one Drive service ls builds, shared by resolution and listing."""
        service = object()
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: service)
        return service

    def test_shared_with_me_and_drive_id_mutually_exclusive(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.ls(drive_id="abc", shared_with_me=True)
        assert exc.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().err

    def test_argument_errors_escape_control_characters(self, capsys):
        # Argument errors go through the same seam as every other failure.
        with pytest.raises(SystemExit) as exc:
            cli.ls(path="My Drive", save_as=["bad\x1b[2J.txt"])
        assert exc.value.code == 1
        assert capsys.readouterr().err == (
            "Error: --save-as must end in .md or .csv: bad\\x1b[2J.txt\n"
        )

    def test_bad_save_as_extension_is_named(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.ls(path="My Drive", save_as=["map.md", "data.txt"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "must end in .md or .csv" in err
        assert "data.txt" in err

    def test_shared_all_items_rejects_depth(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.ls(shared_with_me=True, depth=2)
        assert exc.value.code == 1
        assert "--depth is not supported" in capsys.readouterr().err

    def test_shared_all_items(self, monkeypatch, service):
        rec = {}
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(a=a, k=k))
        cli.ls(shared_with_me=True, save_as=["map.md"])
        assert rec["k"]["shared_with_me"] is True
        assert rec["k"]["save_as"] == ["map.md"]
        assert rec["k"]["service"] is service

    def test_shared_with_path_resolves(self, monkeypatch, service):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_shared_path",
            lambda p, svc: rec.update(svc=svc) or "SID",
        )
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(a=a, k=k))
        cli.ls(path="Shared/sub", shared_with_me=True, depth=3)
        assert rec["a"] == ("SID",)
        assert rec["k"]["depth"] == 3
        assert rec["svc"] is service and rec["k"]["service"] is service

    def test_path_resolution_and_listing_share_one_service(self, monkeypatch, service):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda path, svc: rec.update(path=path, resolve_svc=svc) or "FID",
        )
        monkeypatch.setattr(
            "gdrives.listing.ls",
            lambda *a, **k: rec.update(fid=a[0], list_svc=k["service"]),
        )
        cli.ls(path="My Drive/projects")
        assert rec["path"] == "My Drive/projects"
        assert rec["fid"] == "FID"
        assert rec["resolve_svc"] is service and rec["list_svc"] is service

    def test_save_as_extension_is_case_insensitive(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.resolve.resolve_path", lambda *a: "FID")
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(k=k))
        cli.ls(path="My Drive", save_as=["MAP.MD", "Data.CSV"])
        assert rec["k"]["save_as"] == ["MAP.MD", "Data.CSV"]

    def test_default_path_is_my_drive(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda *a, **k: rec.update(path=a[0]) or "ROOT",
        )
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: None)
        cli.ls()
        assert rec["path"] == "My Drive"

    def test_drive_id_skips_resolution(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda *a, **k: pytest.fail("must not resolve when --drive-id given"),
        )
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(fid=a[0]))
        cli.ls(drive_id="DID")
        assert rec["fid"] == "DID"

    def test_path_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise DrivePathError("folder 'missing' not found in Drive")

        monkeypatch.setattr("gdrives.resolve.resolve_path", boom)
        with pytest.raises(SystemExit) as exc:
            cli.ls(path="My Drive/missing")
        assert exc.value.code == 1
        assert "Error: folder 'missing' not found" in capsys.readouterr().err


class TestShowDrives:
    def test_fetches_saves_and_prints(self, mock_service, monkeypatch, capsys):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        drives = [
            {"id": "root", "type": "personal", "name": "My Drive", "url": "u1"},
            {"id": "sd", "type": "shared", "name": "Team", "url": "u2-longer"},
        ]
        monkeypatch.setattr("gdrives.drives.fetch", lambda s: drives)
        saved = {}
        monkeypatch.setattr("gdrives.drives.save", lambda d: saved.update(d=d))
        cli.show_drives()
        out = capsys.readouterr()
        assert saved["d"] == drives
        assert "My Drive (root)" in out.out
        assert "Team (sd)" in out.out
        assert "Saved to" in out.err

    def test_drive_names_are_escaped(self, mock_service, monkeypatch, capsys):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: mock_service)
        drives = [{"id": "sd", "type": "shared", "name": "\x1b[2JTeam", "url": "u"}]
        monkeypatch.setattr("gdrives.drives.fetch", lambda s: drives)
        monkeypatch.setattr("gdrives.drives.save", lambda d: None)
        cli.show_drives()
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "\\x1b[2JTeam (sd)" in out


class TestSheetsGet:
    def test_delegates_aligned_by_default(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_get",
            lambda source, range_, *, output, delimiter, aligned, escape_formulas: (
                rec.update(
                    s=source,
                    r=range_,
                    o=output,
                    d=delimiter,
                    a=aligned,
                    e=escape_formulas,
                )
            ),
        )
        cli.sheets_get("SID", "Sheet1!A1:B2")
        assert rec == {
            "s": "SID",
            "r": "Sheet1!A1:B2",
            "o": None,
            "d": ",",
            "a": True,
            "e": False,
        }
        cli.sheets_get("SID", output="out.csv", escape_formulas=True)
        assert rec["e"] is True

    def test_tsv_sets_delimiter_and_unaligns(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.sheets.run_get", lambda *a, **k: rec.update(k=k))
        cli.sheets_get("SID", "A1:B2", tsv_out=True)
        assert rec["k"]["delimiter"] == "\t"
        assert rec["k"]["aligned"] is False

    def test_csv_prints_unaligned_with_comma(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.sheets.run_get", lambda *a, **k: rec.update(k=k))
        cli.sheets_get("SID", "A1:B2", csv_out=True)
        assert rec["k"]["delimiter"] == ","
        assert rec["k"]["aligned"] is False

    def test_csv_and_tsv_mutually_exclusive(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.sheets_get("SID", csv_out=True, tsv_out=True)
        assert exc.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().err

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValueError("spreadsheet has no tabs to read")

        monkeypatch.setattr("gdrives.sheets.run_get", boom)
        with pytest.raises(SystemExit) as exc:
            cli.sheets_get("SID")
        assert exc.value.code == 1
        assert "Error: spreadsheet has no tabs" in capsys.readouterr().err


class TestSheetsUpdate:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_update",
            lambda source, range_, values_file, *, raw: rec.update(
                s=source, r=range_, vf=values_file, raw=raw
            ),
        )
        cli.sheets_update("SID", "Sheet1!A1:B2", values_file="data.csv", raw=True)
        assert rec == {"s": "SID", "r": "Sheet1!A1:B2", "vf": "data.csv", "raw": True}

    def test_path_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise DrivePathError("file 'missing' not found in Drive")

        monkeypatch.setattr("gdrives.sheets.run_update", boom)
        with pytest.raises(SystemExit) as exc:
            cli.sheets_update("My Drive/missing", "A1", values_file="d.csv")
        assert exc.value.code == 1
        assert "Error: file 'missing' not found" in capsys.readouterr().err


class TestSheetsAppend:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_append",
            lambda source, range_, values_file, *, raw: rec.update(
                s=source, r=range_, vf=values_file, raw=raw
            ),
        )
        cli.sheets_append("SID", "Sheet1!A1", values_file="data.csv")
        assert rec == {"s": "SID", "r": "Sheet1!A1", "vf": "data.csv", "raw": False}


class TestSheetsClear:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_clear",
            lambda source, range_, *, yes: rec.update(s=source, r=range_, yes=yes),
        )
        cli.sheets_clear("SID", "Sheet1!A1:C10", yes=True)
        assert rec == {"s": "SID", "r": "Sheet1!A1:C10", "yes": True}


class TestSheetsSet:
    def test_parses_match_and_set_pairs(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_set",
            lambda source, match, updates, *, tab, raw, allow_multiple: rec.update(
                s=source, m=match, u=updates, tab=tab, raw=raw, all=allow_multiple
            ),
        )
        cli.sheets_set(
            "SID",
            match=["year=2026", "id=C300"],
            set_=["status=paid", "amount=250"],
            tab="Sheet1",
            all_=True,
            raw=True,
        )
        assert rec == {
            "s": "SID",
            "m": {"year": "2026", "id": "C300"},
            "u": {"status": "paid", "amount": "250"},
            "tab": "Sheet1",
            "raw": True,
            "all": True,
        }

    def test_bad_pair_exits_1(self, monkeypatch, capsys):
        # A --set without '=' is a ValueError, surfaced by the shared error seam.
        monkeypatch.setattr(
            "gdrives.sheets.run_set", lambda *a, **k: pytest.fail("must not run")
        )
        with pytest.raises(SystemExit) as exc:
            cli.sheets_set("SID", match=["id=C300"], set_=["noequals"])
        assert exc.value.code == 1
        assert "COLUMN=VALUE" in capsys.readouterr().err


class TestSheetsRules:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_rules",
            lambda source, *, as_json: rec.update(s=source, j=as_json),
        )
        cli.sheets_rules("SID", as_json=True)
        assert rec == {"s": "SID", "j": True}


class TestSheetsAddRule:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_add_rule", lambda source, **k: rec.update(s=source, k=k)
        )
        cli.sheets_add_rule(
            "SID",
            range_=["Sheet1!A2:AA", "Sheet1!C:C"],
            formula="=$F2=1",
            strikethrough=True,
            text_color="#999999",
            index=2,
        )
        assert rec == {
            "s": "SID",
            "k": {
                "ranges": ["Sheet1!A2:AA", "Sheet1!C:C"],
                "formula": "=$F2=1",
                "bold": False,
                "italic": False,
                "strikethrough": True,
                "underline": False,
                "text_color": "#999999",
                "background": None,
                "rule_json": None,
                "index": 2,
            },
        }

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValueError("pass --range and --formula, or --rule-json")

        monkeypatch.setattr("gdrives.sheets.run_add_rule", boom)
        with pytest.raises(SystemExit) as exc:
            cli.sheets_add_rule("SID")
        assert exc.value.code == 1
        assert "Error: pass --range" in capsys.readouterr().err


class TestSheetsDeleteRule:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_delete_rule",
            lambda source, index, *, tab, yes: rec.update(
                s=source, i=index, tab=tab, yes=yes
            ),
        )
        cli.sheets_delete_rule("SID", index=1, tab="Data", yes=True)
        assert rec == {"s": "SID", "i": 1, "tab": "Data", "yes": True}


class TestDocsGet:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_get",
            lambda source, *, tab, output, as_json: rec.update(
                s=source, tab=tab, o=output, j=as_json
            ),
        )
        cli.docs_get("DID", tab="Notes", output="out.txt", as_json=True)
        assert rec == {"s": "DID", "tab": "Notes", "o": "out.txt", "j": True}

    def test_defaults(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.docs.run_get", lambda *a, **k: rec.update(k=k))
        cli.docs_get("DID")
        assert rec["k"] == {"tab": None, "output": None, "as_json": False}

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValueError("tab 'nope' not found; tabs: 'Tab 1' (t.0)")

        monkeypatch.setattr("gdrives.docs.run_get", boom)
        with pytest.raises(SystemExit) as exc:
            cli.docs_get("DID", tab="nope")
        assert exc.value.code == 1
        assert "Error: tab 'nope' not found" in capsys.readouterr().err


class TestDocsUpdate:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_update",
            lambda source, text_file, *, tab, yes: rec.update(
                s=source, tf=text_file, tab=tab, yes=yes
            ),
        )
        cli.docs_update("DID", text_file="body.txt", tab="t.1", yes=True)
        assert rec == {"s": "DID", "tf": "body.txt", "tab": "t.1", "yes": True}

    def test_path_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise DrivePathError("file or folder 'missing' not found in Drive")

        monkeypatch.setattr("gdrives.docs.run_update", boom)
        with pytest.raises(SystemExit) as exc:
            cli.docs_update("My Drive/missing", text_file="body.txt")
        assert exc.value.code == 1
        assert "Error: file or folder 'missing' not found" in capsys.readouterr().err


class TestDocsAppend:
    def test_delegates_text(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_append",
            lambda source, *, text, text_file, tab: rec.update(
                s=source, t=text, tf=text_file, tab=tab
            ),
        )
        cli.docs_append("DID", text="more")
        assert rec == {"s": "DID", "t": "more", "tf": None, "tab": None}

    def test_delegates_text_file(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.docs.run_append", lambda *a, **k: rec.update(k=k))
        cli.docs_append("DID", text_file="more.txt", tab="Notes")
        assert rec["k"] == {"text": None, "text_file": "more.txt", "tab": "Notes"}

    def test_exclusivity_error_exits_1(self, monkeypatch, capsys):
        # run_append raises ValueError for both/neither; the seam renders it.
        def boom(*a, **k):
            raise ValueError("pass exactly one of --text or --text-file")

        monkeypatch.setattr("gdrives.docs.run_append", boom)
        with pytest.raises(SystemExit) as exc:
            cli.docs_append("DID")
        assert exc.value.code == 1
        assert "exactly one of --text or --text-file" in capsys.readouterr().err


class TestDocsReplace:
    def test_delegates_with_inverted_case_flag(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_replace",
            lambda source, find, replace, *, match_case, tab, allow_multiple: (
                rec.update(
                    s=source,
                    f=find,
                    r=replace,
                    mc=match_case,
                    tab=tab,
                    all=allow_multiple,
                )
            ),
        )
        cli.docs_replace(
            "DID", find="old", replace="new", ignore_case=True, all_=True, tab="t.1"
        )
        assert rec == {
            "s": "DID",
            "f": "old",
            "r": "new",
            "mc": False,
            "tab": "t.1",
            "all": True,
        }

    def test_defaults_are_case_sensitive_single_match(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.docs.run_replace", lambda *a, **k: rec.update(k=k))
        cli.docs_replace("DID", find="old", replace="")
        assert rec["k"] == {"match_case": True, "tab": None, "allow_multiple": False}

    def test_refusal_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValueError(
                "'old' occurs 3 times; pass --all to replace every occurrence"
            )

        monkeypatch.setattr("gdrives.docs.run_replace", boom)
        with pytest.raises(SystemExit) as exc:
            cli.docs_replace("DID", find="old", replace="new")
        assert exc.value.code == 1
        assert "pass --all" in capsys.readouterr().err


class TestDocsClear:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_clear",
            lambda source, *, tab, yes: rec.update(s=source, tab=tab, yes=yes),
        )
        cli.docs_clear("DID", tab="t.1", yes=True)
        assert rec == {"s": "DID", "tab": "t.1", "yes": True}


class TestDocsCreate:
    def test_delegates(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.docs.run_create",
            lambda title, *, text_file: rec.update(t=title, tf=text_file),
        )
        cli.docs_create(title="Notes", text_file="body.txt")
        assert rec == {"t": "Notes", "tf": "body.txt"}

    def test_http_error_exits_1(self, monkeypatch, capsys):
        from helpers import http_error

        def boom(*a, **k):
            raise http_error(403, "Forbidden")

        monkeypatch.setattr("gdrives.docs.run_create", boom)
        with pytest.raises(SystemExit) as exc:
            cli.docs_create(title="Notes")
        assert exc.value.code == 1
        assert "Drive API request failed" in capsys.readouterr().err


class TestYesFlag:
    def test_every_confirming_command_shares_the_flag(self):
        import typer.core
        import typer.main

        group = typer.main.get_command(cli.app)
        assert isinstance(group, typer.main.TyperGroup)
        for name in (
            "download",
            "sheets-clear",
            "sheets-delete-rule",
            "docs-update",
            "docs-clear",
        ):
            command = group.commands[name]
            (param,) = [p for p in command.params if p.name == "yes"]
            assert isinstance(param, typer.core.TyperOption)
            assert param.opts == ["-y", "--yes"], name
            assert param.help == "Skip the confirmation prompt", name


class TestMv:
    def test_delegates_with_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.mv.run",
            lambda source, dest, *, source_id, dest_id, name, dry_run: rec.update(
                s=source, d=dest, sid=source_id, did=dest_id, n=name, dry=dry_run
            ),
        )
        cli.mv("My Drive/notes.txt", "My Drive/archive", dry_run=True)
        assert rec == {
            "s": "My Drive/notes.txt",
            "d": "My Drive/archive",
            "sid": None,
            "did": None,
            "n": None,
            "dry": True,
        }

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValueError("pass exactly one of SOURCE or --source-id")

        monkeypatch.setattr("gdrives.mv.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.mv()
        assert exc.value.code == 1
        assert "Error: pass exactly one of SOURCE" in capsys.readouterr().err


def test_ls_rejects_conflicting_targets_before_resolution(monkeypatch, capsys):
    monkeypatch.setattr(
        "gdrives.resolve.resolve_path", lambda *a, **k: pytest.fail("must not resolve")
    )
    with pytest.raises(SystemExit) as exc:
        cli.ls(path="My Drive", drive_id="OTHER")
    assert exc.value.code == 1
    assert "PATH and --drive-id are mutually exclusive" in capsys.readouterr().err


class TestCliErrors:
    """Every failure class a command can hit ends as 'Error: ...' and exit 1."""

    @pytest.mark.parametrize(
        "error, message",
        [
            (
                RefreshError("invalid_grant"),
                "Error: authentication failed: invalid_grant",
            ),
            (
                TransportError("connection reset"),
                "Error: authentication failed: connection reset",
            ),
            (
                AccessDeniedError(description="The user denied access"),
                "Error: authentication failed: (access_denied) The user denied access",
            ),
            (
                ServerNotFoundError("Unable to find the server at x"),
                "Error: could not reach Google: Unable to find the server at x",
            ),
            (
                IncompleteSearchError("may be missing items"),
                "Error: may be missing items",
            ),
            (OSError("disk full"), "Error: disk full"),
        ],
        ids=["refresh", "transport", "consent-denied", "offline", "incomplete", "os"],
    )
    def test_error_becomes_a_message(self, capsys, error, message):
        with pytest.raises(SystemExit) as raised:
            with cli._cli_errors():
                raise error
        assert raised.value.code == 1
        assert capsys.readouterr().err == message + "\n"

    def test_control_characters_are_escaped_but_lines_kept(self, capsys):
        with pytest.raises(SystemExit):
            with cli._cli_errors():
                raise ValueError("multiple items named 'a':\n  file  \x1b]0;x\x07a")
        assert capsys.readouterr().err == (
            "Error: multiple items named 'a':\n  file  \\x1b]0;x\\x07a\n"
        )


class TestLogin:
    """``gdrives login`` forces a consent and reports the credential."""

    @pytest.fixture
    def consent(self, monkeypatch):
        """Fake the consent and the description; record what was asked."""
        from gdrives.auth import CredentialInfo

        rec: dict[str, Any] = {"described": []}

        def authenticate_oauth(scopes=None, *, force=False, timeout=None):
            rec.update(scopes=scopes, force=force, timeout=timeout)
            rec["info"] = CredentialInfo(kind="oauth", source=Path("token.json"))

        def describe(scopes=None, *, force=False):
            rec["described"].append((scopes, force))
            return rec["info"]

        rec["info"] = CredentialInfo(
            kind="oauth", consent=True, source=Path("secrets.json")
        )
        monkeypatch.setattr("gdrives.auth.authenticate_oauth", authenticate_oauth)
        monkeypatch.setattr("gdrives.auth.describe_credentials", describe)
        return rec

    def test_grants_read_access_by_default(self, consent):
        from gdrives.auth import SCOPES

        result = CliRunner().invoke(cli.app, ["login"])
        assert result.exit_code == 0
        assert (consent["scopes"], consent["force"]) == (SCOPES, True)
        assert consent["timeout"] == 300
        assert result.stderr == (
            "Credential: OAuth, after an interactive consent (client secrets.json)\n"
            "Credential: OAuth token token.json\n"
        )
        assert consent["described"] == [(SCOPES, True), (SCOPES, False)]

    @pytest.mark.parametrize(
        ("name", "scope"),
        [
            ("read", "drive.readonly"),
            ("sheets", "spreadsheets"),
            ("docs", "documents"),
            ("drive", "drive"),
        ],
    )
    def test_scope_names(self, consent, name, scope):
        result = CliRunner().invoke(
            cli.app, ["login", "--scope", name, "--timeout", "5"]
        )
        assert result.exit_code == 0
        assert consent["scopes"] == [f"https://www.googleapis.com/auth/{scope}"]
        assert consent["timeout"] == 5

    def test_a_token_already_cached_is_reported_once(self, consent):
        from gdrives.auth import CredentialInfo

        consent["info"] = CredentialInfo(kind="oauth", source=Path("token.json"))
        result = CliRunner().invoke(cli.app, ["login"])
        assert result.exit_code == 0
        assert result.stderr == "Credential: OAuth token token.json\n"

    @pytest.mark.parametrize(
        "args", [["--scope", "everything"], ["--timeout", "0"]], ids=["scope", "time"]
    )
    def test_a_bad_option_is_a_usage_error(self, consent, args):
        result = CliRunner().invoke(cli.app, ["login", *args])
        assert result.exit_code == 2
        assert "scopes" not in consent

    def test_a_consent_that_cannot_finish_exits_1(self, monkeypatch, consent):
        from gdrives.auth import ConsentError

        def timed_out(scopes=None, *, force=False, timeout=None):
            raise ConsentError("no consent within 5 seconds; no token was written")

        monkeypatch.setattr("gdrives.auth.authenticate_oauth", timed_out)
        result = CliRunner().invoke(cli.app, ["login", "--timeout", "5"])
        assert result.exit_code == 1
        assert result.stderr.endswith(
            "Error: no consent within 5 seconds; no token was written\n"
        )


class TestLoginReadsTheTokenBack:
    """The last line of ``gdrives login`` is what the token files then hold."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        """A config dir with client secrets, no terminal, and a faked consent."""
        from gdrives import auth

        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        consented = MagicMock()
        consented.to_json.return_value = json.dumps(
            {"refresh_token": "new", "scopes": auth.DOCS_WRITE_SCOPES}
        )
        flow = MagicMock()
        flow.run_local_server.return_value = consented
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: flow,
        )

        def load(path, scopes=None):
            # As the real loader does, refuse a file that is not a token.
            json.loads(Path(path).read_text())
            return MagicMock(valid=True, expired=False)

        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file", load
        )
        return tmp_path

    def test_a_saved_token_is_reported(self, config):
        result = CliRunner().invoke(cli.app, ["login", "--scope", "docs"])
        token = config / "gdrives_token_documents.json"
        assert result.exit_code == 0
        assert result.stderr.endswith(f"Credential: OAuth token {token}\n")
        assert token.exists()

    def test_a_token_that_was_not_saved_exits_1(self, config):
        # The one place for a docs token holds something a consent must not
        # replace, so the consent's token is not saved.
        token = config / "gdrives_token_documents.json"
        token.write_text("not json {{{")
        result = CliRunner().invoke(cli.app, ["login", "--scope", "docs"])
        assert result.exit_code == 1
        assert result.stderr.endswith(
            "Error: the consent finished, but its token was not saved, so the "
            "next command would ask again\n"
        )
        assert "Credential: Application Default Credentials" not in result.stderr
        assert token.read_text() == "not json {{{"


class TestCommandsAnnounceAWait:
    """Any command says so before its authentication waits on a person."""

    @pytest.fixture
    def waiting(self, monkeypatch):
        from gdrives.auth import CredentialInfo

        info = CredentialInfo(kind="oauth", refresh=True, source=Path("token.json"))
        monkeypatch.setattr(
            "gdrives.auth.describe_credentials",
            lambda scopes=None, *, force=False: info,
        )
        monkeypatch.setattr(
            "gdrives.auth.authenticate", lambda scopes=None, *, force=False: "creds"
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build", lambda api, version, credentials: api
        )
        monkeypatch.setattr("gdrives.drives.fetch", lambda service: [DRIVE])
        monkeypatch.setattr("gdrives.drives.save", lambda drives: None)

    def test_a_read_command_outside_sheets(self, waiting):
        result = CliRunner().invoke(cli.app, ["show-drives"])
        assert result.exit_code == 0
        assert result.stderr.startswith(
            "Credential: OAuth token token.json, refreshed first\n"
        )

    def test_nothing_is_announced_once_the_command_is_over(self, waiting, capsys):
        from gdrives import auth

        CliRunner().invoke(cli.app, ["show-drives"])
        auth._credentials.cache_clear()
        auth.build_drive_service()
        assert capsys.readouterr().err == ""


class TestRevisions:
    def test_delegates_with_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.revisions.run",
            lambda source, *, download, output, format, as_json: rec.update(
                s=source, dl=download, o=output, f=format, j=as_json
            ),
        )
        cli.revisions(
            "My Drive/x", download="R1", output="out", format_="csv", as_json=True
        )
        assert rec == {"s": "My Drive/x", "dl": "R1", "o": "out", "f": "csv", "j": True}

    def test_output_without_download_is_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.revisions("My Drive/x", output="out")
        assert exc.value.code == 1
        assert "require --download" in capsys.readouterr().err

    def test_format_without_download_is_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.revisions("My Drive/x", format_="csv")
        assert exc.value.code == 1
        assert "require --download" in capsys.readouterr().err

    def test_lists_via_cli_runner(self, monkeypatch):
        from gdrives.revisions import Revision

        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: object())
        monkeypatch.setattr("gdrives.resolve.resolve_and_report", lambda *a, **k: "FID")
        monkeypatch.setattr(
            "gdrives.revisions.list_revisions",
            lambda service, file_id: [
                Revision(
                    id="1",
                    modified_time="2026-01-01T00:00:00.000Z",
                    modified_by="me",
                    mime_type="application/pdf",
                    size=42,
                    keep_forever=False,
                    export_links=None,
                )
            ],
        )
        result = CliRunner().invoke(cli.app, ["revisions", "My Drive/x"])
        assert result.exit_code == 0
        assert "1" in result.stdout
        assert "me" in result.stdout
        assert "42" in result.stdout

    def test_lists_as_json_via_cli_runner(self, monkeypatch):
        from gdrives.revisions import Revision

        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: object())
        monkeypatch.setattr("gdrives.resolve.resolve_and_report", lambda *a, **k: "FID")
        monkeypatch.setattr(
            "gdrives.revisions.list_revisions",
            lambda service, file_id: [
                Revision(
                    id="1",
                    modified_time="2026-01-01T00:00:00.000Z",
                    modified_by="me",
                    mime_type="application/pdf",
                    size=42,
                    keep_forever=False,
                    export_links=None,
                )
            ],
        )
        result = CliRunner().invoke(cli.app, ["revisions", "My Drive/x", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == [
            {
                "id": "1",
                "modified_time": "2026-01-01T00:00:00.000Z",
                "modified_by": "me",
                "mime_type": "application/pdf",
                "size": 42,
                "keep_forever": False,
                "export_links": None,
            }
        ]

    def test_downloads_via_cli_runner(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gdrives.auth.build_drive_service", lambda: object())
        monkeypatch.setattr("gdrives.resolve.resolve_and_report", lambda *a, **k: "FID")
        target = tmp_path / "out.pdf"
        monkeypatch.setattr(
            "gdrives.revisions.download_revision",
            lambda service, file_id, revision_id, output, **k: target,
        )
        result = CliRunner().invoke(
            cli.app, ["revisions", "My Drive/x", "--download", "R1"]
        )
        assert result.exit_code == 0
        assert "R1" in result.stdout
        assert str(target) in result.stdout


class TestUpload:
    def test_delegates_with_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.upload.run",
            lambda local, dest, **options: rec.update(l=local, d=dest, **options),
        )
        cli.upload("report.pdf", "My Drive/reports", dry_run=True)
        assert rec == {
            "l": "report.pdf",
            "d": "My Drive/reports",
            "dest_id": None,
            "file_id": None,
            "name": None,
            "mime_type": None,
            "dry_run": True,
            "replace": True,
        }

    def test_no_replace_is_passed_as_replace_false(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.upload.run",
            lambda local, dest, **options: rec.update(options),
        )
        result = CliRunner().invoke(
            cli.app, ["upload", "out.pdf", "My Drive/reports", "--no-replace"]
        )
        assert result.exit_code == 0
        assert rec["replace"] is False

    def test_a_file_that_reads_back_different_exits_1(self, monkeypatch, capsys):
        from gdrives.upload import UploadError

        def boom(*a, **k):
            raise UploadError("'report.pdf' (F) does not read back as the local file")

        monkeypatch.setattr("gdrives.upload.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.upload("report.pdf", "My Drive/reports")
        assert exc.value.code == 1
        assert "Error: 'report.pdf' (F) does not read back" in capsys.readouterr().err

    def test_parses_its_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.upload.run",
            lambda local, dest, **options: rec.update(l=local, d=dest, **options),
        )
        result = CliRunner().invoke(
            cli.app,
            ["upload", "out.pdf", "--file-id", "F", "--mime-type", "application/pdf"],
        )
        assert result.exit_code == 0
        assert (rec["l"], rec["d"], rec["file_id"]) == ("out.pdf", None, "F")
        assert rec["mime_type"] == "application/pdf"


class TestSheetsCreate:
    def test_delegates_with_options(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_create",
            lambda title, **options: rec.update(t=title, **options),
        )
        result = CliRunner().invoke(
            cli.app,
            ["sheets-create", "--title", "Roster", "--folder", "My Drive/reports"]
            + ["--tab", "Members", "--tab", "Dues", "--dry-run"],
        )
        assert result.exit_code == 0
        assert rec == {
            "t": "Roster",
            "folder": "My Drive/reports",
            "folder_id": None,
            "tabs": ["Members", "Dues"],
            "source": None,
            "dry_run": True,
        }

    def test_from_stands_in_for_the_title(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_create",
            lambda title, **options: rec.update(t=title, **options),
        )
        result = CliRunner().invoke(
            cli.app, ["sheets-create", "--from", "book.xlsx", "--folder-id", "D"]
        )
        assert result.exit_code == 0
        assert rec["t"] is None
        assert rec["source"] == "book.xlsx"

    def test_no_title_and_no_from_is_a_usage_error(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("ran")

        monkeypatch.setattr("gdrives.sheets.run_create", boom)
        result = CliRunner().invoke(cli.app, ["sheets-create", "--folder-id", "D"])
        assert result.exit_code == 2
        assert "--title" in plain(result.output)

    def test_no_tab_is_no_tabs(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_create",
            lambda title, **options: rec.update(t=title, **options),
        )
        cli.sheets_create(title="Roster", folder_id="D")
        assert rec["tabs"] == ()

    def test_http_error_exits_1(self, monkeypatch, capsys):
        from helpers import http_error

        def boom(*a, **k):
            raise http_error(403, "Forbidden")

        monkeypatch.setattr("gdrives.sheets.run_create", boom)
        with pytest.raises(SystemExit) as exc:
            cli.sheets_create(title="Roster")
        assert exc.value.code == 1
        assert "Drive API request failed" in capsys.readouterr().err
