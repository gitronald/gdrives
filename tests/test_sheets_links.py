"""Tests for sweep_url_links, format_sweep, and the sheets-links command.

The library runs against ``FakeSheetGrid``. The command tests invoke the real
Typer app with ``CliRunner`` and a config under ``tmp_path``, with
``build_sheets_service`` and ``describe_credentials`` patched at their source.
"""

import json
from pathlib import Path

import pytest
from helpers import FakeSheetGrid, http_error
from typer.testing import CliRunner

from gdrives.auth import SHEETS_WRITE_SCOPES, CredentialInfo
from gdrives.cli import app
from gdrives.sheets import (
    CONFIG_NAME,
    LinkSweep,
    TabLinks,
    UrlLinkProblem,
    ensure_tabs,
    format_sweep,
    sweep_url_links,
    url_link_problems,
)

BLUE = "#1155cc"
GREEN = "#33aa55"
STRUCTURE = "spreadsheets.batchUpdate"
HINT = "Preview only; rerun with --apply to write.\n"


def club():
    """A roster spreadsheet: two tabs of URL cells the API linked, and a notes tab.

    ``Members`` and ``Dues`` hold pasted URLs, which the API links in blue
    (``#1155cc``) and underlines. ``Notes`` has no header row, so it holds no named
    column.
    """
    return FakeSheetGrid(
        {
            "Members": [
                ["name", "site"],
                ["Ada", "https://ada.example"],
                ["Bo", "https://bo.example"],
            ],
            "Dues": [["name", "invoice"], ["Ada", "https://pay.example/1"]],
            "Notes": [[], ["https://notes.example"]],
        }
    )


def reasons(tab):
    return [(p.row, p.column, p.reasons) for p in tab.problems]


class TestSweepUrlLinks:
    def test_a_preview_checks_every_tab_and_writes_nothing(self):
        grid = club()
        sweep = sweep_url_links(grid, "S", color=BLUE)
        assert [tab.tab for tab in sweep.tabs] == ["Members", "Dues", "Notes"]
        members, dues, notes = sweep.tabs
        assert reasons(members) == [
            (2, "site", ("underline",)),
            (3, "site", ("underline",)),
        ]
        assert len(dues.problems) == 1
        assert notes.skipped == "no named columns in the first row"
        assert not sweep.apply and sweep.pending and sweep.exit_code == 2
        assert all(not tab.applied for tab in sweep.tabs)
        assert STRUCTURE not in grid.methods

    def test_apply_fixes_the_cells_and_a_second_sweep_is_clean(self):
        grid = club()
        sweep = sweep_url_links(grid, "S", color=BLUE, apply=True)
        members, dues, notes = sweep.tabs
        assert members.applied and dues.applied and not notes.applied
        assert sweep.apply and not sweep.pending and sweep.exit_code == 0
        assert grid.methods.count(STRUCTURE) == 2
        assert url_link_problems(grid, "S", "Members", color=BLUE) == []
        again = sweep_url_links(grid, "S", color=BLUE, apply=True)
        assert [tab.problems for tab in again.tabs] == [(), (), ()]
        assert again.exit_code == 0
        assert grid.methods.count(STRUCTURE) == 2

    def test_named_tabs_only_and_once(self):
        grid = club()
        sweep = sweep_url_links(grid, "S", ["Dues", "Dues"], color=BLUE)
        assert [tab.tab for tab in sweep.tabs] == ["Dues"]

    def test_a_colour_for_each_tab(self):
        grid = club()
        sweep = sweep_url_links(
            grid,
            "S",
            ["Members", "Dues"],
            color={"Members": BLUE, "Dues": GREEN, "Unused": "#000000"},
            apply=True,
        )
        assert [tab.color for tab in sweep.tabs] == [BLUE, GREEN]
        assert url_link_problems(grid, "S", "Dues", color=GREEN) == []
        assert url_link_problems(grid, "S", "Dues", color=BLUE) != []

    def test_a_tab_the_spreadsheet_lacks_is_refused_before_any_write(self):
        grid = club()
        with pytest.raises(ValueError) as raised:
            sweep_url_links(grid, "S", ["Members", "Gone"], color=BLUE, apply=True)
        assert str(raised.value) == (
            "no tab(s) ['Gone'] in the spreadsheet; tabs: ['Members', 'Dues', 'Notes']"
        )
        assert grid.methods == ["spreadsheets.get"]

    @pytest.mark.parametrize("color", ["blue", "#12345", "#1155cg", {"Members": "red"}])
    def test_a_colour_that_is_not_rrggbb_makes_no_request(self, color):
        grid = club()
        with pytest.raises(ValueError, match="'#rrggbb'"):
            sweep_url_links(grid, "S", color=color)
        assert grid.calls == []

    def test_a_tab_without_a_colour_is_refused(self):
        grid = club()
        with pytest.raises(ValueError, match=r"no colour for tab\(s\) \['Dues'\]"):
            sweep_url_links(grid, "S", ["Dues"], color={"Members": BLUE})
        assert grid.calls == []
        with pytest.raises(ValueError, match=r"no colour for tab\(s\)"):
            sweep_url_links(grid, "S", color={"Members": BLUE})
        assert grid.methods == ["spreadsheets.get"]

    def test_a_tab_that_holds_no_named_column_is_skipped_not_failed(self):
        grid = FakeSheetGrid(
            {"Empty": [], "Blank": [["", ""], ["https://x.example"]], "Chart": [[]]}
        )
        sweep = sweep_url_links(grid, "S", color=BLUE, apply=True)
        assert [tab.skipped for tab in sweep.tabs] == [
            "no named columns in the first row"
        ] * 3
        assert sweep.exit_code == 0 and not sweep.pending
        assert STRUCTURE not in grid.methods

    def test_a_failed_tab_is_reported_and_the_sweep_goes_on(self):
        grid = club()
        grid.write("Members", [["name", "site", "site"]])
        sweep = sweep_url_links(grid, "S", color=BLUE, apply=True)
        members, dues, _ = sweep.tabs
        assert members.error == "tab 'Members': header repeats ['site', 'site']"
        assert members.problems == () and not members.applied
        assert dues.applied
        assert sweep.exit_code == 1

    def test_an_api_error_fails_only_its_tab(self):
        grid = club()
        grid.fail("values.get", http_error(403, "forbidden"))
        sweep = sweep_url_links(grid, "S", color=BLUE)
        members, dues, _ = sweep.tabs
        assert "forbidden" in (members.error or "")
        assert dues.error is None and len(dues.problems) == 1
        assert sweep.exit_code == 1

    def test_a_read_back_that_finds_a_cell_wrong_fails_its_tab(self):
        grid = club()
        grid.edit_externally(
            lambda g: g.format("Members", 3, 2).update(underline=True),
            before="spreadsheets.get",
            occurrence=3,
        )
        sweep = sweep_url_links(grid, "S", ["Members"], color=BLUE, apply=True)
        (members,) = sweep.tabs
        assert members.error is not None
        assert "the read-back found URL cells the fix did not link" in members.error
        assert len(members.problems) == 2 and not members.applied
        assert not members.pending and members.exit_code == 1

    def test_no_tabs_is_a_clean_sweep(self):
        sweep = LinkSweep(())
        assert sweep.exit_code == 0 and not sweep.pending
        assert format_sweep(sweep) == ""


class TestFormatSweep:
    def test_each_state_of_a_tab(self):
        cell = UrlLinkProblem(2, "site", "https://ada.example", ("color", "runs"))
        sweep = LinkSweep(
            (
                TabLinks("Members", BLUE, (cell,)),
                TabLinks("Dues", BLUE, (cell,), applied=True),
                TabLinks("Clean", GREEN),
                TabLinks("Notes", GREEN, skipped="no named columns in the first row"),
                TabLinks("Bad", GREEN, (cell,), error="boom"),
            )
        )
        assert format_sweep(sweep) == "\n".join(
            [
                "tab 'Members' (preview, colour #1155cc)",
                "  row 2, column 'site': color, runs: https://ada.example",
                "  to fix: 1 URL cell(s)",
                "",
                "tab 'Dues' (preview, colour #1155cc)",
                "  row 2, column 'site': color, runs: https://ada.example",
                "  fixed: 1 URL cell(s) linked",
                "",
                "tab 'Clean' (preview, colour #33aa55)",
                "  every URL cell follows the rule",
                "",
                "tab 'Notes' (preview, colour #33aa55)",
                "  skipped: no named columns in the first row",
                "",
                "tab 'Bad' (preview, colour #33aa55)",
                "  row 2, column 'site': color, runs: https://ada.example",
                "  error: boom",
            ]
        )

    def test_the_run_is_named_and_sheet_text_is_made_printable(self):
        cell = UrlLinkProblem(2, "si\x1bte", "https://a.example/\x07", ("no_link",))
        text = format_sweep(LinkSweep((TabLinks("T\x1b[2J", BLUE, (cell,)),), True))
        assert "\x1b" not in text and "\x07" not in text
        assert text.startswith("tab 'T\\x1b[2J' (apply, colour #1155cc)")


def config_of(tabs):
    return {"roster": {"spreadsheet": "SHEET", "tabs": tabs}}


CONFIG = config_of(
    {
        "Members": {
            "local": "data/members.csv",
            "key": ["name"],
            "link_urls": {"color": BLUE},
        },
        "Dues": {"mode": "push", "local": "output/dues.csv"},
        "Totals": {"mode": "pull", "local": "data/totals.csv"},
    }
)


class Env:
    def __init__(self, grid: FakeSheetGrid) -> None:
        self.grid = grid
        self.scopes: list[list[str] | None] = []
        self.described: list[list[str] | None] = []

    def invoke(self, *args: str):
        return CliRunner().invoke(app, list(args))

    def assert_no_request(self, result) -> None:
        assert self.scopes == [] and self.described == [] and self.grid.calls == []
        assert "Spreadsheet ID" not in result.stderr


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A project whose config has a link_urls tab, and a fake club spreadsheet."""
    (tmp_path / CONFIG_NAME).write_text(json.dumps(CONFIG), encoding="utf-8")
    grid = club()
    project = Env(grid)
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


class TestSheetsLinksOnASpreadsheet:
    def test_a_preview_reports_and_exits_2(self, env):
        url = "https://docs.google.com/spreadsheets/d/SHEET/edit"
        result = env.invoke("sheets-links", url, "--color", BLUE)
        assert result.exit_code == 2
        assert result.stdout.startswith("tab 'Members' (preview, colour #1155cc)\n")
        assert "  to fix: 2 URL cell(s)" in result.stdout
        assert "tab 'Notes'" in result.stdout and "skipped" in result.stdout
        assert result.stderr == "Spreadsheet ID: SHEET\n" + HINT
        assert env.scopes == [None]
        assert STRUCTURE not in env.grid.methods

    def test_apply_announces_the_credential_and_writes(self, env):
        result = env.invoke("sheets-links", "SHEET", "--color", BLUE, "--apply")
        assert result.exit_code == 0
        assert "  fixed: 2 URL cell(s) linked" in result.stdout
        assert result.stderr == (
            "Credential: service account sync-bot@example.com (key key.json)\n"
            "Spreadsheet ID: SHEET\n"
        )
        assert env.described == [SHEETS_WRITE_SCOPES]
        assert env.scopes == [SHEETS_WRITE_SCOPES]
        assert env.invoke("sheets-links", "SHEET", "--color", BLUE).exit_code == 0

    def test_tabs_are_chosen_with_tab(self, env):
        result = env.invoke("sheets-links", "SHEET", "--color", BLUE, "--tab", "Dues")
        assert result.exit_code == 2
        assert "tab 'Dues'" in result.stdout and "tab 'Members'" not in result.stdout

    def test_a_colour_is_required_before_any_request(self, env):
        result = env.invoke("sheets-links", "SHEET")
        assert result.exit_code == 1
        assert "needs --color" in result.stderr
        env.assert_no_request(result)

    def test_a_bad_colour_makes_no_request(self, env):
        result = env.invoke("sheets-links", "SHEET", "--color", "blue")
        assert result.exit_code == 1 and "'#rrggbb'" in result.stderr
        assert env.grid.calls == []

    def test_a_failed_tab_exits_1(self, env):
        env.grid.write("Dues", [["name", "name"]])
        result = env.invoke("sheets-links", "SHEET", "--color", BLUE)
        assert result.exit_code == 1
        assert "error: tab 'Dues': header repeats ['name', 'name']" in result.stdout

    def test_a_clean_spreadsheet_exits_0_with_no_hint(self, env):
        env.invoke("sheets-links", "SHEET", "--color", BLUE, "--apply")
        result = env.invoke("sheets-links", "SHEET", "--color", BLUE)
        assert result.exit_code == 0 and "Preview only" not in result.stderr


class TestSheetsLinksOnATarget:
    def test_the_tabs_with_link_urls_in_their_colour_and_the_rest_skipped(self, env):
        result = env.invoke("sheets-links", "roster")
        assert result.exit_code == 2
        assert result.stdout.startswith("tab 'Members' (preview, colour #1155cc)")
        assert "tab 'Dues'" not in result.stdout
        assert result.stderr == (
            "Skipping tab 'Dues': no link_urls; pass --color to check it\n"
            "Skipping tab 'Totals': no link_urls; pass --color to check it\n"
            "Spreadsheet ID: SHEET\n" + HINT
        )

    def test_a_color_applies_to_every_tab_and_overrides_the_config(self, env):
        ensure_tabs(env.grid, "SHEET", ["Totals"])
        result = env.invoke("sheets-links", "roster", "--color", "#33aa55")
        assert [
            line for line in result.stdout.splitlines() if line.startswith("tab ")
        ] == [
            "tab 'Members' (preview, colour #33aa55)",
            "tab 'Dues' (preview, colour #33aa55)",
            "tab 'Totals' (preview, colour #33aa55)",
        ]

    def test_tab_and_apply(self, env):
        result = env.invoke("sheets-links", "roster", "--tab", "Members", "--apply")
        assert result.exit_code == 0
        assert "fixed: 2 URL cell(s) linked" in result.stdout
        assert env.scopes == [SHEETS_WRITE_SCOPES]

    def test_a_named_tab_without_link_urls_needs_a_color(self, env):
        result = env.invoke("sheets-links", "roster", "--tab", "Dues")
        assert result.exit_code == 1
        assert "tab(s) ['Dues'] of target 'roster' have no link_urls" in result.stderr
        env.assert_no_request(result)
        ok = env.invoke("sheets-links", "roster", "--tab", "Dues", "--color", BLUE)
        assert ok.exit_code == 2

    def test_an_unknown_tab_is_refused_before_any_request(self, env):
        result = env.invoke("sheets-links", "roster", "--tab", "Gone")
        assert result.exit_code == 1
        assert "target 'roster' has no tab(s) ['Gone']" in result.stderr
        env.assert_no_request(result)

    def test_a_target_with_no_link_urls_needs_a_color(self, env, tmp_path):
        plain_config = config_of({"Dues": {"mode": "push", "local": "d.csv"}})
        (tmp_path / CONFIG_NAME).write_text(json.dumps(plain_config), encoding="utf-8")
        result = env.invoke("sheets-links", "roster")
        assert result.exit_code == 1
        assert "no tab of target 'roster' has link_urls; pass --color" in result.stderr

    def test_config_names_the_file_and_makes_the_source_a_target(self, env, tmp_path):
        other = tmp_path / "elsewhere.json"
        other.write_text(json.dumps(CONFIG), encoding="utf-8")
        (tmp_path / CONFIG_NAME).unlink()
        assert (
            env.invoke("sheets-links", "roster", "--config", str(other)).exit_code == 2
        )
        result = env.invoke("sheets-links", "SHEET", "--config", str(other))
        assert result.exit_code == 1 and "no target 'SHEET'" in result.stderr

    def test_a_bare_word_that_is_no_target_is_a_spreadsheet_id(self, env):
        result = env.invoke("sheets-links", "SHEET", "--color", BLUE)
        assert "Spreadsheet ID: SHEET" in result.stderr and result.exit_code == 2

    def test_a_directory_with_no_config_has_only_spreadsheets(self, env, tmp_path):
        (tmp_path / CONFIG_NAME).unlink()
        result = env.invoke("sheets-links", "roster", "--color", BLUE)
        assert "Spreadsheet ID: roster" in result.stderr

    def test_a_config_that_cannot_be_read_is_an_error(self, env, tmp_path):
        (tmp_path / CONFIG_NAME).write_text("{", encoding="utf-8")
        result = env.invoke("sheets-links", "roster")
        assert result.exit_code == 1 and "not valid JSON" in result.stderr
        env.assert_no_request(result)

    def test_a_url_or_path_is_never_a_target(self, env):
        result = env.invoke("sheets-links", "roster/x", "--color", BLUE)
        assert result.exit_code == 1  # a Drive path, not found in an empty cache

    def test_the_hooks_and_schemas_of_the_config_are_never_imported(
        self, env, tmp_path
    ):
        config = config_of(
            {
                "Members": {
                    "local": "m.csv",
                    "key": ["name"],
                    "link_urls": {"color": BLUE},
                    "schema": "no_such_clubtools:SCHEMA",
                    "hooks": {"validate": "no_such_clubtools:check"},
                }
            }
        )
        (tmp_path / CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
        assert env.invoke("sheets-links", "roster").exit_code == 2

    def test_a_target_whose_spreadsheet_is_a_url(self, env, tmp_path):
        config = config_of(
            {
                "Members": {
                    "local": "m.csv",
                    "key": ["name"],
                    "link_urls": {"color": BLUE},
                }
            }
        )
        config["roster"]["spreadsheet"] = (
            "https://docs.google.com/spreadsheets/d/SHEET/edit"
        )
        (tmp_path / CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
        assert env.invoke("sheets-links", "roster").exit_code == 2
