"""Tests for a target's ``defaults``: tab fields given to every tab that lacks its own.

A default is checked as the tab field is, naming the target's ``defaults``,
and is applied only to a tab it does not contradict.
"""

import pytest

from gdrives.sheets import TARGET_DEFAULTS, ConfigError, TabConfig, parse_config
from gdrives.sheets.config import CONFIG_NAME

PLAIN = {"local": "m.csv", "key": ["id"]}


def target_of(tmp_path, tabs, **fields):
    data = {"t": {"spreadsheet": "S", "tabs": tabs} | fields}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def problems_of(tmp_path, tabs, **fields):
    with pytest.raises(ConfigError) as caught:
        target_of(tmp_path, tabs, **fields)
    return caught.value.problems


class TestApplied:
    def test_the_five_fields_are_the_defaults_a_target_takes(self):
        assert TARGET_DEFAULTS == {
            "link_urls",
            "strict_schema",
            "newline",
            "render",
            "blank_keys",
        }

    def test_each_default_reaches_a_tab_that_lacks_it(self, tmp_path):
        defaults = {
            "link_urls": {"color": "#0000ff"},
            "strict_schema": "local",
            "newline": "crlf",
            "render": "formatted",
            "blank_keys": "partial",
        }
        (tab,) = target_of(tmp_path, {"T": PLAIN}, defaults=defaults).tabs
        assert tab.link_urls == "#0000ff"
        assert tab.strict_schema == "local"
        assert tab.newline == "crlf"
        assert tab.render == "formatted"
        assert tab.blank_keys == "partial"

    def test_a_field_the_tab_sets_is_its_own_even_at_the_built_in_value(self, tmp_path):
        tab = PLAIN | {"newline": "lf", "blank_keys": "refuse", "strict_schema": False}
        defaults = {"newline": "crlf", "blank_keys": "partial", "strict_schema": True}
        (found,) = target_of(tmp_path, {"T": tab}, defaults=defaults).tabs
        assert (found.newline, found.blank_keys) == ("lf", "refuse")
        assert found.strict_schema is False

    def test_no_defaults_leaves_the_built_in_values(self, tmp_path):
        (tab,) = target_of(tmp_path, {"T": PLAIN}).tabs
        assert tab == TabConfig(
            "T", tab.local, key=("id",), schema=tab.schema, hooks=tab.hooks
        )
        assert (tab.newline, tab.render, tab.link_urls) == ("lf", "unformatted", None)

    def test_a_default_skips_a_tab_it_would_contradict(self, tmp_path):
        tabs = {
            "Pulled": {"mode": "pull", "local": "p.csv"},
            "Cleared": PLAIN | {"local": "c.csv", "clear_links": True},
            "Json": {"local": "m.json", "key": ["id"]},
            "Typed": PLAIN | {"local": "t.csv", "typed_writes": True},
            "Pushed": {"mode": "push", "local": "s.csv"},
        }
        defaults = {
            "link_urls": {"color": "#0000ff"},
            "newline": "crlf",
            "render": "formatted",
        }
        pulled, cleared, json_tab, typed, pushed = target_of(
            tmp_path, tabs, defaults=defaults
        ).tabs
        assert pulled.link_urls is None and pulled.newline == "crlf"
        assert cleared.link_urls is None and cleared.clear_links is True
        assert json_tab.newline == "lf" and json_tab.link_urls == "#0000ff"
        assert typed.render == "unformatted" and typed.typed_writes is True
        assert typed.newline == "crlf"
        assert pushed.link_urls == "#0000ff" and pushed.render == "formatted"

    def test_an_unformatted_default_is_given_to_a_typed_tab(self, tmp_path):
        tab = PLAIN | {"typed_writes": True}
        defaults = {"render": "unformatted"}
        (found,) = target_of(tmp_path, {"T": tab}, defaults=defaults).tabs
        assert found.render == "unformatted"

    def test_a_default_can_be_overridden_by_a_tab_error(self, tmp_path):
        tab = PLAIN | {"newline": "cr"}
        found = problems_of(tmp_path, {"T": tab}, defaults={"newline": "crlf"})
        assert found == [
            "target 't', tab 'T': 'newline' must be one of ['crlf', 'lf'], not 'cr'"
        ]


class TestRefused:
    @pytest.mark.parametrize(
        ("defaults", "problem"),
        [
            ("crlf", "'defaults' must be an object naming one or more of"),
            ({}, "'defaults' must be an object naming one or more of"),
        ],
    )
    def test_defaults_that_is_not_an_object_of_fields(
        self, tmp_path, defaults, problem
    ):
        (found,) = problems_of(tmp_path, {"T": PLAIN}, defaults=defaults)
        assert found.startswith("target 't': " + problem)

    def test_any_other_name_is_a_problem(self, tmp_path):
        defaults = {"widths": {"a": 1}, "bom": True, "hooks": {}}
        assert problems_of(tmp_path, {"T": PLAIN}, defaults=defaults) == [
            "target 't', 'defaults': unknown field(s) ['bom', 'hooks', 'widths']; "
            "defaults: ['blank_keys', 'link_urls', 'newline', 'render', "
            "'strict_schema']"
        ]

    @pytest.mark.parametrize(
        ("defaults", "problem"),
        [
            ({"newline": "cr"}, "'newline' must be one of ['crlf', 'lf'], not 'cr'"),
            ({"newline": 3}, "'newline' must be one of ['crlf', 'lf'], not 3"),
            (
                {"blank_keys": "some"},
                "'blank_keys' must be one of ['partial', 'refuse'], not 'some'",
            ),
            (
                {"render": "raw"},
                "'render' must be one of ['formatted', 'unformatted'], not 'raw'",
            ),
            (
                {"strict_schema": "yes"},
                "'strict_schema' must be true, false, or 'local'",
            ),
            (
                {"link_urls": "#0000ff"},
                "'link_urls' must be an object with one field, 'color'",
            ),
            (
                {"link_urls": {"color": "blue"}},
                "'link_urls' color must be '#rrggbb', not 'blue'",
            ),
        ],
    )
    def test_a_default_is_checked_as_the_tab_field_is(
        self, tmp_path, defaults, problem
    ):
        assert problems_of(tmp_path, {"T": PLAIN}, defaults=defaults) == [
            "target 't', 'defaults': " + problem
        ]
