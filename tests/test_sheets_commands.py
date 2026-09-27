"""Tests for the sheets-sync, sheets-pull, and sheets-push commands.

Each test invokes the real Typer app with ``CliRunner``, against
``FakeSheetGrid`` and a config file written under ``tmp_path``, so it covers
the wiring in ``gdrives.cli`` and the ``run_*`` entry points in
``gdrives.sheets.commands`` together. ``build_sheets_service`` and
``describe_credentials`` are patched at their source (``gdrives.auth``), since
the entry points import them lazily; the config's spreadsheet is a bare file
ID, so resolving it makes no request.
"""

import copy
import json
from pathlib import Path

import pytest
from helpers import FakeSheetGrid, http_error, plain
from typer.testing import CliRunner

from gdrives.auth import SHEETS_WRITE_SCOPES, CredentialInfo, build_sheets_service
from gdrives.cli import app
from gdrives.sheets import CONFIG_NAME, read_records, run_pull, write_values_csv

HEADER = ["member_id", "name", "status"]
ROWS = [["m1", "Ada", "active"], ["m2", "Bo", "active"]]
READS = {"spreadsheets.get", "values.get", "values.batchGet"}
CREDENTIAL = "Credential: service account sync-bot@example.com (key key.json)"

CONFIG = {
    "roster": {
        "spreadsheet": "SHEET",
        "tabs": {
            "Members": {"local": "data/members.csv", "key": ["member_id"]},
            "Summary": {"mode": "push", "local": "output/summary.csv"},
            "Totals": {"mode": "pull", "local": "data/totals.csv"},
        },
    }
}


class Env:
    """The fake sheet, the project directory, and what the command asked for."""

    def __init__(self, root: Path, grid: FakeSheetGrid) -> None:
        self.root = root
        self.grid = grid
        self.scopes: list[list[str] | None] = []
        self.described: list[list[str] | None] = []

    def invoke(self, *args: str):
        return CliRunner().invoke(app, list(args))

    def writes(self) -> list[str]:
        return [method for method in self.grid.methods if method not in READS]

    def local(self, name: str) -> Path:
        return self.root / name

    def write(self, name: str, rows: list[list[str]]) -> None:
        write_values_csv(str(self.local(name)), rows)

    def rows(self, name: str) -> list[list[str]]:
        return [list(row.values()) for row in read_records(self.local(name)).rows]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A project with the roster config, its files in sync, and a fake sheet."""
    (tmp_path / CONFIG_NAME).write_text(json.dumps(CONFIG), encoding="utf-8")
    grid = FakeSheetGrid(
        {
            "Members": [HEADER, *ROWS],
            "Summary": [["total"], ["2"]],
            "Totals": [["status", "count"], ["active", "2"]],
        }
    )
    project = Env(tmp_path, grid)
    project.write("data/members.csv", [HEADER, *ROWS])
    project.write("sheets-base/roster/Members.csv", [HEADER, *ROWS])
    project.write("output/summary.csv", [["total"], ["2"]])
    monkeypatch.chdir(tmp_path)

    def build(scopes=None):
        project.scopes.append(scopes)
        return grid

    def describe(scopes=None, *, force=False):
        project.described.append(scopes)
        return CredentialInfo(
            kind="service_account",
            identity="sync-bot@example.com",
            source=Path("key.json"),
        )

    monkeypatch.setattr("gdrives.auth.build_sheets_service", build)
    monkeypatch.setattr("gdrives.auth.describe_credentials", describe)
    return project


def assert_no_request(env: Env, result) -> None:
    """Nothing was resolved, authenticated, announced, or sent."""
    assert env.scopes == [] and env.described == [] and env.grid.calls == []
    assert "Spreadsheet ID" not in result.stderr


class TestAWaitIsAnnounced:
    """The credential line when authentication is about to wait on a person."""

    LINE = "Credential: OAuth token token.json, refreshed first\n"

    @pytest.fixture(autouse=True)
    def expired_token(self, env, monkeypatch):
        """The real service builder, with a token that is refreshed first."""
        info = CredentialInfo(kind="oauth", refresh=True, source=Path("token.json"))
        monkeypatch.setattr(
            "gdrives.auth.describe_credentials",
            lambda scopes=None, *, force=False: info,
        )
        monkeypatch.setattr("gdrives.auth.build_sheets_service", build_sheets_service)
        monkeypatch.setattr(
            "gdrives.auth.authenticate", lambda scopes=None, *, force=False: "creds"
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda api, version, credentials: env.grid,
        )

    def test_on_a_preview(self, env):
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 0
        assert result.stderr == "Spreadsheet ID: SHEET\n" + self.LINE
        assert env.writes() == []

    def test_once_with_apply(self, env):
        result = env.invoke("sheets-pull", "roster", "--apply")
        assert result.exit_code == 0
        assert result.stderr == self.LINE + "Spreadsheet ID: SHEET\n"


class TestSheetsSync:
    def test_a_preview_in_sync_exits_0_read_only_and_silent(self, env):
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 0
        assert result.stdout == (
            "sync tab 'Members' (preview)\n"
            f"  local file: {env.local('data/members.csv')}\n"
            "  in sync: nothing to write\n"
        )
        assert result.stderr == "Spreadsheet ID: SHEET\n"
        assert env.scopes == [None] and env.described == []
        assert env.writes() == []

    def test_a_preview_lists_the_changes_and_writes_nothing(self, env):
        env.write("data/members.csv", [HEADER, ["m1", "Ada", "closed"], ROWS[1]])
        files = env.local("data/members.csv").read_bytes()
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 0
        assert "push to the sheet (1):\n    m1 / 'status': 'active' -> 'closed'" in (
            result.stdout
        )
        assert env.scopes == [None] and env.writes() == []
        assert env.local("data/members.csv").read_bytes() == files

    def test_apply_announces_the_credential_and_writes(self, env):
        env.write("data/members.csv", [HEADER, ["m1", "Ada", "closed"], ROWS[1]])
        env.grid.write("Members", [["m2", "Bea"]], row=3)
        result = env.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 0
        assert result.stderr == f"{CREDENTIAL}\nSpreadsheet ID: SHEET\n"
        assert env.scopes == [SHEETS_WRITE_SCOPES]
        assert env.described == [SHEETS_WRITE_SCOPES]
        assert "sync tab 'Members' (apply)" in result.stdout
        assert "  wrote: sheet, local file, base" in result.stdout
        expected = [["m1", "Ada", "closed"], ["m2", "Bea", "active"]]
        assert env.grid.values("Members") == [HEADER, *expected]
        assert env.rows("data/members.csv") == expected
        assert env.rows("sheets-base/roster/Members.csv") == expected

    def test_a_conflict_exits_2_and_the_rest_applies(self, env):
        env.write(
            "data/members.csv", [HEADER, ["m1", "Ada", "closed"], ["m2", "Bo", "x"]]
        )
        env.grid.write("Members", [["m1", "Ada", "paused"]], row=2)
        preview = env.invoke("sheets-sync", "roster")
        assert preview.exit_code == 2
        assert "conflicts, left as they are (1):" in preview.stdout
        assert env.writes() == []
        result = env.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 2
        assert env.grid.values("Members") == [
            HEADER,
            ["m1", "Ada", "paused"],
            ["m2", "Bo", "x"],
        ]

    def test_prefer_resolves_the_conflict(self, env):
        env.write("data/members.csv", [HEADER, ["m1", "Ada", "closed"], ROWS[1]])
        env.grid.write("Members", [["m1", "Ada", "paused"]], row=2)
        result = env.invoke("sheets-sync", "roster", "--apply", "--prefer", "local")
        assert result.exit_code == 0
        assert "kept local (prefer), discarded 'paused'" in result.stdout
        assert env.grid.values("Members")[1] == ["m1", "Ada", "closed"]

    def test_a_failed_tab_exits_1_with_its_error_on_stdout(self, env):
        env.local("data/members.csv").unlink()
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 1
        assert "  error: tab 'Members': local file" in result.stdout
        assert "does not exist" in result.stdout

    def test_adopt_without_apply_previews_the_adoption(self, env):
        env.local("sheets-base/roster/Members.csv").unlink()
        env.write("data/members.csv", [HEADER, *ROWS, ["m3", "Cy", "active"]])
        result = env.invoke("sheets-sync", "roster", "--adopt")
        assert result.exit_code == 0
        assert "  adopt: the local file wins every difference" in result.stdout
        assert "  new rows for the sheet (1): m3" in result.stdout
        assert env.scopes == [None] and env.described == []
        assert env.writes() == []
        assert not env.local("sheets-base/roster/Members.csv").exists()
        applied = env.invoke("sheets-sync", "roster", "--adopt", "--apply")
        assert applied.exit_code == 0
        assert env.grid.values("Members")[-1] == ["m3", "Cy", "active"]
        assert env.local("sheets-base/roster/Members.csv").exists()

    def test_adopt_is_refused_once_a_base_exists(self, env):
        result = env.invoke("sheets-sync", "roster", "--adopt", "--apply")
        assert result.exit_code == 1
        assert "adopt is only for a first sync" in result.stdout
        assert env.writes() == []

    def test_the_structure_options_reach_the_run(self, env):
        env.grid.write("Members", [["member_id", "name", "old"]])
        preview = env.invoke("sheets-sync", "roster", "--add-missing", "--drop-extra")
        assert preview.exit_code == 0
        assert "  columns would be added: 'status'" in preview.stdout
        assert "  columns would be deleted, with their data: 'old'" in preview.stdout
        refused = env.invoke("sheets-sync", "roster")
        assert refused.exit_code == 1
        assert "--add-missing" in refused.stdout

    def test_a_repeated_tab_runs_once(self, env):
        result = env.invoke(
            "sheets-sync", "roster", "--tab", "Members", "--tab", "Members"
        )
        assert result.exit_code == 0
        assert result.stdout.count("sync tab 'Members'") == 1

    def test_a_bad_prefer_is_a_usage_error_before_any_request(self, env):
        result = env.invoke("sheets-sync", "roster", "--prefer", "both")
        assert result.exit_code == 2
        assert "Invalid value for '--prefer'" in plain(result.stderr)
        assert_no_request(env, result)


class TestConfigRefusals:
    """A config, target, or tab problem exits 1, listed in full, before any request."""

    def test_every_config_problem_is_listed(self, env):
        bad = {"roster": {"spreadsheet": "", "tabs": {"Members": {"mode": "both"}}}}
        (env.root / CONFIG_NAME).write_text(json.dumps(bad), encoding="utf-8")
        result = env.invoke("sheets-sync", "roster", "--apply")
        assert result.exit_code == 1
        assert result.stderr.startswith(f"Error: {env.root / CONFIG_NAME}: 3 problems:")
        assert "'spreadsheet' must be a URL" in result.stderr
        assert "'mode' must be one of" in result.stderr
        assert "'local' must be a file path" in result.stderr
        assert result.stdout == ""
        assert_no_request(env, result)

    def test_an_unknown_target_names_the_targets(self, env):
        result = env.invoke("sheets-push", "staff", "--apply")
        assert result.exit_code == 1
        assert "no target 'staff'; targets: ['roster']" in result.stderr
        assert_no_request(env, result)

    def test_unknown_and_wrong_mode_tabs_are_listed_together(self, env):
        result = env.invoke(
            "sheets-sync",
            "roster",
            "--apply",
            "--tab",
            "Members",
            "--tab",
            "Staff",
            "--tab",
            "Dues",
            "--tab",
            "Summary",
        )
        assert result.exit_code == 1
        assert result.stderr == (
            "Error: target 'roster' has no tab(s) ['Staff', 'Dues']\n"
            "tab(s) ['Summary'] of target 'roster' are not sync tabs\n"
            "tabs: 'Members' (sync), 'Summary' (push), 'Totals' (pull)\n"
        )
        assert_no_request(env, result)

    def test_a_target_without_tabs_of_the_mode(self, env):
        config = copy.deepcopy(CONFIG)
        del config["roster"]["tabs"]["Summary"]
        other = env.root / "other.json"
        other.write_text(json.dumps(config), encoding="utf-8")
        result = env.invoke("sheets-push", "roster", "--config", str(other))
        assert result.exit_code == 1
        assert "target 'roster' has no push tabs" in result.stderr
        assert_no_request(env, result)

    def test_a_missing_config_file(self, env):
        result = env.invoke("sheets-sync", "roster", "--config", "nowhere.json")
        assert result.exit_code == 1
        assert "cannot read the file" in result.stderr
        assert_no_request(env, result)

    def test_the_config_is_found_from_a_subdirectory(self, env, monkeypatch):
        (env.root / "notes").mkdir()
        monkeypatch.chdir(env.root / "notes")
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 0
        assert f"local file: {env.local('data/members.csv')}" in result.stdout


class TestSheetsPush:
    def test_a_preview_reports_what_the_sheet_loses(self, env):
        env.write("output/summary.csv", [["total"], ["3"]])
        result = env.invoke("sheets-push", "roster")
        assert result.exit_code == 0
        assert result.stdout.startswith("push tab 'Summary' (preview)\n")
        assert "the sheet holds 1 rows (1 non-blank cells); it would hold 1" in (
            result.stdout
        )
        assert env.scopes == [None] and env.described == []
        assert env.writes() == []

    def test_apply_needs_the_write_scope_and_replaces_the_tab(self, env):
        env.write("output/summary.csv", [["total"], ["3"]])
        result = env.invoke("sheets-push", "roster", "--tab", "Summary", "--apply")
        assert result.exit_code == 0
        assert result.stderr == f"{CREDENTIAL}\nSpreadsheet ID: SHEET\n"
        assert env.scopes == [SHEETS_WRITE_SCOPES]
        assert env.described == [SHEETS_WRITE_SCOPES]
        assert env.grid.values("Summary") == [["total"], ["3"]]


class TestSheetsPull:
    def test_a_preview_writes_no_file(self, env):
        result = env.invoke("sheets-pull", "roster")
        assert result.exit_code == 0
        assert result.stdout.startswith("pull tab 'Totals' (preview)\n")
        assert env.scopes == [None] and env.described == []
        assert not env.local("data/totals.csv").exists()

    def test_apply_writes_the_file_and_stays_read_only(self, env):
        result = env.invoke("sheets-pull", "roster", "--apply")
        assert result.exit_code == 0
        assert result.stderr == f"{CREDENTIAL}\nSpreadsheet ID: SHEET\n"
        assert env.scopes == [None] and env.described == [None]
        assert env.rows("data/totals.csv") == [["active", "2"]]
        assert env.writes() == []

    def test_all_tabs_dumps_every_tab_without_a_config(self, env):
        (env.root / CONFIG_NAME).unlink()
        preview = env.invoke("sheets-pull", "SHEET", "--all-tabs", "-o", "out")
        assert preview.exit_code == 0
        assert not (env.root / "out").exists()
        result = env.invoke(
            "sheets-pull",
            "SHEET",
            "--all-tabs",
            "-o",
            "out",
            "--skip",
            "Members",
            "--format",
            "json",
            "--apply",
        )
        assert result.exit_code == 0
        assert result.stderr == f"{CREDENTIAL}\nSpreadsheet ID: SHEET\n"
        assert env.scopes == [None, None] and env.described == [None]
        assert sorted(p.name for p in (env.root / "out").iterdir()) == [
            "Summary.json",
            "Totals.json",
        ]
        assert json.loads((env.root / "out" / "Totals.json").read_text()) == [
            {"status": "active", "count": "2"}
        ]
        assert env.writes() == []

    def test_all_tabs_with_bom_and_slug(self, env):
        env.grid.tabs[1].title = "Form responses 1"
        result = env.invoke(
            "sheets-pull",
            "SHEET",
            "--all-tabs",
            "-o",
            "out",
            "--bom",
            "--slug",
            "--apply",
        )
        assert result.exit_code == 0, result.stdout
        assert sorted(p.name for p in (env.root / "out").iterdir()) == [
            "form-responses-1.csv",
            "members.csv",
            "totals.csv",
        ]
        assert (env.root / "out" / "totals.csv").read_bytes() == (
            b"\xef\xbb\xbfstatus,count\nactive,2\n"
        )

    def test_bom_with_json_is_refused_before_any_request(self, env):
        result = env.invoke(
            "sheets-pull",
            "SHEET",
            "--all-tabs",
            "-o",
            "out",
            "--format",
            "json",
            "--bom",
        )
        assert result.exit_code == 1
        assert "--bom applies only to --format csv and tsv" in result.stderr
        assert_no_request(env, result)

    def test_all_tabs_defaults_to_csv(self, env):
        result = env.invoke(
            "sheets-pull", "SHEET", "--all-tabs", "-o", "out", "--apply"
        )
        assert result.exit_code == 0
        assert (env.root / "out" / "Members.csv").exists()

    def test_a_tab_that_cannot_be_dumped_exits_1(self, env):
        (env.root / "out" / "Totals.csv").mkdir(parents=True)  # not a file
        result = env.invoke(
            "sheets-pull", "SHEET", "--all-tabs", "-o", "out", "--apply"
        )
        assert result.exit_code == 1
        assert "pull tab 'Totals' (apply)" in result.stdout
        assert "  error: " in result.stdout

    @pytest.mark.parametrize(
        "args, message",
        [
            (["--all-tabs", "-o", "out", "--tab", "Totals"], "with --tab"),
            (["--all-tabs", "-o", "out", "--config", CONFIG_NAME], "with --config"),
            (["--all-tabs"], "--all-tabs needs -o/--output DIR"),
            (["-o", "out"], "-o/--output apply only with --all-tabs"),
            (["--skip", "Members"], "--skip apply only with --all-tabs"),
            (["--format", "tsv"], "--format apply only with --all-tabs"),
            (["--bom"], "--bom apply only with --all-tabs"),
            (["--slug"], "--slug apply only with --all-tabs"),
        ],
        ids=["tab", "config", "no-output", "output", "skip", "format", "bom", "slug"],
    )
    def test_misused_options_are_refused_before_any_request(self, env, args, message):
        result = env.invoke("sheets-pull", "roster", "--apply", *args)
        assert result.exit_code == 1
        assert message in result.stderr
        assert_no_request(env, result)

    def test_an_unknown_format_is_refused_by_the_entry_point(self, env):
        with pytest.raises(ValueError, match="--format must be one of"):
            run_pull("SHEET", all_tabs=True, output="out", file_format="xlsx")
        assert env.scopes == [] and env.grid.calls == []


class TestSheetsWidths:
    def test_the_first_tab_by_default_as_json_read_only(self, env):
        env.grid.tab("Members").widths[:3] = [80, 200, 120]
        result = env.invoke("sheets-widths", "SHEET")
        assert result.exit_code == 0
        assert json.loads(result.stdout) == dict(
            zip(HEADER, [80, 200, 120], strict=True)
        )
        assert list(json.loads(result.stdout)) == HEADER
        assert result.stderr == "Spreadsheet ID: SHEET\n"
        assert env.scopes == [None] and env.writes() == []

    def test_a_named_tab(self, env):
        env.grid.tab("Totals").widths[:2] = [90, 40]
        result = env.invoke("sheets-widths", "SHEET", "--tab", "Totals")
        assert json.loads(result.stdout) == {"status": 90, "count": 40}
        assert env.grid.methods == ["spreadsheets.get"]

    def test_the_output_is_a_tab_s_widths_in_the_config(self, env):
        from gdrives.sheets import parse_config

        result = env.invoke("sheets-widths", "SHEET", "--tab", "Members")
        tab = {"local": "m.csv", "key": ["member_id"]}
        tab["widths"] = json.loads(result.stdout)
        data = {"roster": {"spreadsheet": "S", "tabs": {"Members": tab}}}
        loaded = parse_config(data, env.root / CONFIG_NAME).target("roster")
        assert loaded.tabs[0].widths == dict.fromkeys(HEADER, 100)

    def test_an_unknown_tab_exits_1(self, env):
        result = env.invoke("sheets-widths", "SHEET", "--tab", "Nope")
        assert result.exit_code == 1 and "Error:" in result.stderr


class TestRetryNotices:
    def test_a_command_says_when_a_call_is_retried(self, env, monkeypatch):
        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)
        monkeypatch.setattr("gdrives.sheets.retry.random.random", lambda: 0.0)
        env.grid.fail("values.get", http_error(429, "rate limited"))
        env.grid.fail("values.get", http_error(503, "unavailable"), occurrence=2)
        result = env.invoke("sheets-sync", "roster")
        assert result.exit_code == 0
        assert result.stderr == (
            "Spreadsheet ID: SHEET\n"
            "Sheets API returned 429; retrying in 1s (attempt 2 of 5)\n"
            "Sheets API returned 503; retrying in 2s (attempt 3 of 5)\n"
        )
        assert "in sync: nothing to write" in result.stdout

    def test_the_library_says_nothing_unless_asked(self, env, monkeypatch, capsys):
        from gdrives.sheets import pull_values

        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)
        env.grid.fail("values.get", http_error(429, "rate limited"))
        assert pull_values(env.grid, "S", "'Members'!A1") == [["member_id"]]
        captured = capsys.readouterr()
        assert (captured.out, captured.err) == ("", "")

    @pytest.mark.parametrize(
        "command",
        [
            ["sheets-get", "SHEET", "Members!A1:A1"],
            ["sheets-widths", "SHEET"],
            ["sheets-pull", "roster"],
            ["sheets-push", "roster"],
        ],
    )
    def test_every_sheets_command_runs_inside_the_notices(
        self, env, monkeypatch, command
    ):
        monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)
        monkeypatch.setattr("gdrives.sheets.retry.random.random", lambda: 0.0)
        for method in ("values.get", "spreadsheets.get"):
            env.grid.fail(method, http_error(429, "rate limited"))
        result = env.invoke(*command)
        assert "Sheets API returned 429; retrying in 1s (attempt 2 of 5)" in (
            result.stderr
        )
