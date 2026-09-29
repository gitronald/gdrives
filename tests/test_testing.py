"""Tests for gdrives.testing as a caller meets it: importable, with no pytest.

What the fakes model is pinned in test_sheets_grid.py and test_sheets.py.
"""

import subprocess
import sys
from typing import Any

from googleapiclient.errors import HttpError

from gdrives import testing
from gdrives.sheets import pull_values, run_get
from gdrives.testing import (
    FakeSheetGrid,
    FakeSheetsService,
    http_error,
    patch_sheets_service,
    terminal,
)


class Patcher:
    """A stand-in for ``pytest.MonkeyPatch``: ``setattr`` by dotted path."""

    def __init__(self) -> None:
        self.undo: list[tuple[Any, str, Any]] = []

    def setattr(self, target: str, value: Any) -> None:
        import importlib

        module, _, name = target.rpartition(".")
        owner = importlib.import_module(module)
        self.undo.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def restore(self) -> None:
        for owner, name, value in reversed(self.undo):
            setattr(owner, name, value)


class TestModule:
    def test_it_exports_the_fakes_and_their_helpers(self):
        assert sorted(testing.__all__) == [
            "FakeSheetGrid",
            "FakeSheetsService",
            "LINK_BLUE",
            "http_error",
            "patch_sheets_service",
            "terminal",
        ]
        assert all(hasattr(testing, name) for name in testing.__all__)

    def test_importing_it_imports_no_test_framework(self):
        code = (
            "import sys, gdrives.testing; "
            "sys.exit('pytest' in sys.modules or '_pytest' in sys.modules)"
        )
        done = subprocess.run([sys.executable, "-c", code], check=False)
        assert done.returncode == 0

    def test_http_error_is_the_clients_error(self):
        error = http_error(429, "rate")
        assert isinstance(error, HttpError)
        assert (error.resp.status, error.resp.reason) == (429, "rate")


class TestTerminal:
    def test_the_library_sees_what_the_block_says(self, monkeypatch):
        from gdrives import auth

        monkeypatch.setattr(auth, "_is_interactive", lambda: "as it was")
        with terminal():
            assert auth._is_interactive() is True
            with terminal(False):
                assert auth._is_interactive() is False
            assert auth._is_interactive() is True
        assert auth._is_interactive() == "as it was"

    def test_what_was_seen_is_seen_again_after_a_failure(self, monkeypatch):
        from gdrives import auth

        monkeypatch.setattr(auth, "_is_interactive", lambda: "as it was")
        try:
            with terminal(False):
                raise RuntimeError("the test failed")
        except RuntimeError:
            pass
        assert auth._is_interactive() == "as it was"

    def test_a_fallback_is_announced_with_no_terminal(
        self, monkeypatch, tmp_path, capsys
    ):
        from gdrives import auth

        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        (tmp_path / "service_account.json").write_text('{"client_email": "a@b.c"}')
        with terminal(False):
            assert auth.describe_credentials().consent_skipped is True
            auth.announce_credentials()
        assert capsys.readouterr().err.startswith("Credential: service account a@b.c")
        with terminal():
            assert auth.describe_credentials().consent is True


class TestACallersTest:
    """The shape of a test a caller writes against each fake."""

    def test_a_grid_holds_what_was_written(self):
        grid = FakeSheetGrid({"Members": [["id", "name"], ["m1", "Ada"]]})
        assert pull_values(grid, "S", "Members!A1:B2") == [
            ["id", "name"],
            ["m1", "Ada"],
        ]
        assert grid.methods == ["values.get"]

    def test_a_service_replays_a_response(self):
        service = FakeSheetsService(get={"values": [["id"], ["m1"]]})
        assert pull_values(service, "S", "Members!A1:A2") == [["id"], ["m1"]]
        ((method, request),) = service.calls
        assert (method, request["range"]) == ("values.get", "Members!A1:A2")

    def test_a_command_runs_on_a_fake_with_any_patcher(self, capsys):
        patcher = Patcher()
        grid = FakeSheetGrid({"Members": [["id", "name"], ["m1", "Ada"]]})
        try:
            rec = patch_sheets_service(patcher, grid)
            run_get("SHEET", "Members!A1:B2")
        finally:
            patcher.restore()
        assert rec == {"scopes": None}
        assert "m1" in capsys.readouterr().out
