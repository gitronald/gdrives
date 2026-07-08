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
