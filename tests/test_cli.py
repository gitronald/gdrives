"""Tests for gdrives.cli — command wiring, validation, and error handling.

Typer's ``@app.command()`` returns the wrapped function unchanged, so each
command is called directly with plain Python defaults; the lazily-imported
delegates (run/ls/resolve/build_drive_service) are patched at their source.
"""

import pytest

from gdrives import cli
from gdrives.resolve import DrivePathError


class TestExport:
    def test_delegates_to_run(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.export.run", lambda source, output: rec.update(s=source, o=output)
        )
        cli.export("https://docs.google.com/document/d/X/edit", "out.docx")
        assert rec == {
            "s": "https://docs.google.com/document/d/X/edit",
            "o": "out.docx",
        }

    def test_value_error_exits_1(self, monkeypatch, capsys):
        def boom(source, output):
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

        def boom(source, output):
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
            lambda source, output_dir, *, depth, yes: rec.update(
                s=source, od=output_dir, depth=depth, yes=yes
            ),
        )
        cli.download("My Drive/refs", output_dir="out", depth=2, yes=True)
        assert rec == {"s": "My Drive/refs", "od": "out", "depth": 2, "yes": True}

    def test_path_error_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise DrivePathError("folder 'missing' not found in Drive")

        monkeypatch.setattr("gdrives.download.run", boom)
        with pytest.raises(SystemExit) as exc:
            cli.download("My Drive/missing")
        assert exc.value.code == 1
        assert "Error: folder 'missing' not found" in capsys.readouterr().err


class TestLs:
    def test_shared_with_me_and_drive_id_mutually_exclusive(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.ls(drive_id="abc", shared_with_me=True)
        assert exc.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().err

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

    def test_shared_all_items(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(a=a, k=k))
        cli.ls(shared_with_me=True, save_as=["map.md"])
        assert rec["k"]["shared_with_me"] is True
        assert rec["k"]["save_as"] == ["map.md"]

    def test_shared_with_path_resolves(self, monkeypatch):
        rec = {}
        monkeypatch.setattr("gdrives.resolve.resolve_shared_path", lambda p: "SID")
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(a=a, k=k))
        cli.ls(path="Shared/sub", shared_with_me=True, depth=3)
        assert rec["a"] == ("SID",)
        assert rec["k"]["depth"] == 3

    def test_path_resolution(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda *a, **k: rec.update(path=a[0]) or "FID",
        )
        monkeypatch.setattr("gdrives.listing.ls", lambda *a, **k: rec.update(fid=a[0]))
        cli.ls(path="My Drive/projects")
        assert rec["path"] == "My Drive/projects"
        assert rec["fid"] == "FID"

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


class TestSheetsGet:
    def test_delegates_aligned_by_default(self, monkeypatch):
        rec = {}
        monkeypatch.setattr(
            "gdrives.sheets.run_get",
            lambda source, range_, *, output, delimiter, aligned: rec.update(
                s=source, r=range_, o=output, d=delimiter, a=aligned
            ),
        )
        cli.sheets_get("SID", "Sheet1!A1:B2")
        assert rec == {"s": "SID", "r": "Sheet1!A1:B2", "o": None, "d": ",", "a": True}

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
