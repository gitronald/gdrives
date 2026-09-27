"""Tests for gdrives.sheets.cells: canonical strings, types, keys, and schemas.

All pure functions, so the tests are exhaustive over the documented cases.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from gdrives.sheets import (
    COLUMN_TYPES,
    ColumnSchema,
    Problem,
    from_cell,
    index_rows,
    normalize_key,
    problems,
    row_key,
    to_cell,
)


class TestToCell:
    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (None, ""),
            (True, "TRUE"),
            (False, "FALSE"),
            (3.0, "3"),
            (-2.0, "-2"),
            (0.0, "0"),
            (2.5, "2.5"),
            (1e-07, "1e-07"),
            (1e20, "100000000000000000000"),
            (float("inf"), "inf"),
            (7, "7"),
            (0, "0"),
            ("007", "007"),
            ("", ""),
            (" padded ", " padded "),
            (date(2026, 1, 15), "2026-01-15"),
            (datetime(2026, 1, 15, 10, 30), "2026-01-15 10:30:00"),
        ],
    )
    def test_canonical_string(self, value, text):
        assert to_cell(value) == text

    def test_bool_is_not_treated_as_an_int(self):
        # bool subclasses int; it must still render as Sheets shows it.
        assert to_cell(True) != "1"


class TestFromCell:
    @pytest.mark.parametrize("type_", sorted(COLUMN_TYPES))
    def test_blank_is_none_under_every_type(self, type_):
        assert from_cell("", type_) is None

    def test_str_is_the_default_and_keeps_text_verbatim(self):
        assert from_cell(" 007 ") == " 007 "
        assert from_cell("TRUE") == "TRUE"

    @pytest.mark.parametrize(
        ("text", "type_", "value"),
        [
            ("42", "int", 42),
            ("-3", "int", -3),
            ("007", "int", 7),
            ("2.5", "float", 2.5),
            ("3", "float", 3.0),
            ("1e-07", "float", 1e-07),
            ("TRUE", "bool", True),
            ("FALSE", "bool", False),
            ("true", "bool", True),
            ("False", "bool", False),
            ("2026-01-15", "date", date(2026, 1, 15)),
            ("2026-01-15 10:30:00", "datetime", datetime(2026, 1, 15, 10, 30)),
            ("2026-01-15T10:30:00", "datetime", datetime(2026, 1, 15, 10, 30)),
        ],
    )
    def test_parses(self, text, type_, value):
        parsed = from_cell(text, type_)
        assert parsed == value
        assert type(parsed) is type(value)

    @pytest.mark.parametrize(
        ("text", "type_"),
        [
            ("3.0", "int"),
            ("1_000", "int"),
            (" 3", "int"),
            ("3 ", "int"),
            ("+3", "int"),
            ("three", "int"),
            ("1_000.5", "float"),
            (" 2.5", "float"),
            ("abc", "float"),
            ("yes", "bool"),
            ("1", "bool"),
            (" TRUE", "bool"),
            ("2026-13-01", "date"),
            ("2026-01-15 10:30:00", "date"),
            ("Jan 15", "date"),
            ("noon", "datetime"),
            (" 2026-01-15", "datetime"),
        ],
    )
    def test_refuses_text_that_is_not_the_type(self, text, type_):
        with pytest.raises(ValueError, match=f"is not a valid {type_}"):
            from_cell(text, type_)

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="unknown column type 'number'"):
            from_cell("1", "number")

    def test_unknown_type_is_refused_even_for_a_blank(self):
        with pytest.raises(ValueError, match="unknown column type"):
            from_cell("", "number")

    @pytest.mark.parametrize(
        ("value", "type_"),
        [
            ("text", "str"),
            (" spaced ", "str"),
            (0, "int"),
            (-12, "int"),
            (10**20, "int"),
            (3.0, "float"),
            (2.5, "float"),
            (-0.125, "float"),
            (1e-07, "float"),
            (1e20, "float"),
            (True, "bool"),
            (False, "bool"),
            (date(2026, 1, 15), "date"),
            (datetime(2026, 1, 15, 10, 30), "datetime"),
            (datetime(2026, 1, 15, 10, 30, 5, 123456), "datetime"),
            (
                datetime(2026, 1, 15, 10, 30, tzinfo=timezone(timedelta(hours=-7))),
                "datetime",
            ),
            (None, "str"),
            (None, "int"),
            (None, "float"),
            (None, "bool"),
            (None, "date"),
            (None, "datetime"),
        ],
    )
    def test_round_trips_what_to_cell_writes(self, value, type_):
        assert from_cell(to_cell(value), type_) == value


class TestColumnTypes:
    def test_declared_names(self):
        assert COLUMN_TYPES == {"str", "int", "float", "bool", "date", "datetime"}


class TestRowKeys:
    @pytest.mark.parametrize(
        ("text", "normal"),
        [
            ("C300", "C300"),
            ("C300 ", "C300"),
            ("  C300", "C300"),
            ("Jane  Doe", "Jane Doe"),
            ("Jane\t Doe\n", "Jane Doe"),
            ("Jane\u00a0Doe", "Jane Doe"),  # a no-break space pasted from the web
            ("   ", ""),
            ("", ""),
        ],
    )
    def test_normalize_key(self, text, normal):
        assert normalize_key(text) == normal

    def test_row_key_follows_key_order(self):
        record = {"id": " 7", "year": "2026 ", "name": "x"}
        assert row_key(record, ["year", "id"]) == ("2026", "7")

    def test_row_key_missing_column_is_blank(self):
        assert row_key({"id": "7"}, ["id", "year"]) == ("7", "")

    def test_row_key_does_not_rewrite_the_record(self):
        record = {"id": "C300 "}
        row_key(record, ["id"])
        assert record == {"id": "C300 "}


class TestIndexRows:
    def test_maps_keys_to_positions(self):
        rows = [{"id": "a"}, {"id": "b"}]
        assert index_rows(rows, ["id"], side="local") == {("a",): 1, ("b",): 2}

    def test_maps_keys_to_given_numbers(self):
        rows = [{"id": "a"}, {"id": "b"}]
        found = index_rows(rows, ["id"], side="tab", numbers=[2, 5])
        assert found == {("a",): 2, ("b",): 5}

    def test_keys_are_normalized(self):
        rows = [{"id": "C300 "}]
        assert index_rows(rows, ["id"], side="local") == {("C300",): 1}

    def test_composite_key(self):
        rows = [{"y": "2025", "id": "1"}, {"y": "2026", "id": "1"}]
        found = index_rows(rows, ["y", "id"], side="local")
        assert found == {("2025", "1"): 1, ("2026", "1"): 2}

    def test_whitespace_variants_are_one_duplicate_key(self):
        rows = [{"id": "C300"}, {"id": "C300 "}]
        with pytest.raises(
            ValueError, match=r"^local: duplicate key \('C300',\) in rows \[1, 2\]$"
        ):
            index_rows(rows, ["id"], side="local")

    def test_every_duplicate_is_listed(self):
        rows = [{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "b"}, {"id": "a"}]
        with pytest.raises(ValueError) as raised:
            index_rows(rows, ["id"], side="tab 'T'", numbers=[2, 3, 4, 5, 6])
        assert str(raised.value) == (
            "tab 'T': duplicate key ('a',) in rows [2, 4, 6]; "
            "duplicate key ('b',) in rows [3, 5]"
        )

    def test_blank_key_rows_are_listed(self):
        rows = [{"id": ""}, {"id": "a"}, {"id": "  "}, {"name": "no id"}]
        with pytest.raises(ValueError) as raised:
            index_rows(rows, ["id"], side="local")
        assert str(raised.value) == "local: blank key ['id'] in rows [1, 3, 4]"

    def test_a_partly_blank_composite_key_is_blank(self):
        rows = [{"y": "2026", "id": ""}]
        with pytest.raises(ValueError, match=r"blank key \['y', 'id'\] in rows \[1\]"):
            index_rows(rows, ["y", "id"], side="local")

    def test_blank_and_duplicate_reported_together(self):
        rows = [{"id": ""}, {"id": "a"}, {"id": "a"}]
        with pytest.raises(ValueError) as raised:
            index_rows(rows, ["id"], side="local")
        assert str(raised.value) == (
            "local: blank key ['id'] in rows [1]; duplicate key ('a',) in rows [2, 3]"
        )

    def test_no_key_columns_is_refused(self):
        with pytest.raises(ValueError, match="local: no key columns"):
            index_rows([{"id": "a"}], [], side="local")

    def test_empty_rows(self):
        assert index_rows([], ["id"], side="local") == {}


class TestColumnSchema:
    def test_defaults(self):
        schema = ColumnSchema()
        assert (schema.type, schema.required, schema.allowed) == ("str", False, None)

    def test_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type 'money'"):
            ColumnSchema(type="money")


class TestProblems:
    def test_clean_rows_have_no_problems(self):
        rows = [{"id": "1", "paid": "TRUE", "status": "active"}]
        schema = {
            "id": ColumnSchema(type="int", required=True),
            "paid": ColumnSchema(type="bool"),
            "status": ColumnSchema(allowed=["active", "closed"]),
        }
        assert problems(rows, schema, tab="Members", key=["id"]) == []

    def test_reports_every_problem_in_row_then_column_order(self):
        rows = [
            {"id": "1", "paid": "maybe", "status": "active"},
            {"id": "", "paid": "TRUE", "status": "gone"},
        ]
        schema = {
            "id": ColumnSchema(required=True),
            "paid": ColumnSchema(type="bool"),
            "status": ColumnSchema(allowed=["active", "closed"]),
        }
        found = problems(rows, schema, tab="Members", key=["id"])
        assert found == [
            Problem(
                "Members", 1, ("1",), "paid", "maybe", "'maybe' is not a valid bool"
            ),
            Problem("Members", 2, ("",), "id", "", "is required"),
            Problem(
                "Members",
                2,
                ("",),
                "status",
                "gone",
                "'gone' is not one of ['active', 'closed']",
            ),
        ]

    def test_blank_optional_cell_is_fine_under_any_type_and_allowed(self):
        rows = [{"n": "", "s": ""}]
        schema = {"n": ColumnSchema(type="int"), "s": ColumnSchema(allowed=["x"])}
        assert problems(rows, schema, tab="T") == []

    def test_missing_column_counts_as_blank(self):
        found = problems([{"id": "1"}], {"name": ColumnSchema(required=True)}, tab="T")
        assert [(p.column, p.text, p.reason) for p in found] == [
            ("name", "", "is required")
        ]

    def test_allowed_values_compare_as_canonical_strings(self):
        # Typed allowed values (as a JSON config holds them) match their cells.
        schema = {
            "level": ColumnSchema(type="int", allowed=[1, 2]),
            "flag": ColumnSchema(type="bool", allowed=[True]),
        }
        assert problems([{"level": "2", "flag": "TRUE"}], schema, tab="T") == []
        found = problems([{"level": "3", "flag": "FALSE"}], schema, tab="T")
        assert [p.reason for p in found] == [
            "'3' is not one of ['1', '2']",
            "'FALSE' is not one of ['TRUE']",
        ]

    def test_type_is_checked_before_allowed(self):
        schema = {"n": ColumnSchema(type="int", allowed=[1])}
        found = problems([{"n": "x"}], schema, tab="T")
        assert [p.reason for p in found] == ["'x' is not a valid int"]

    def test_problem_text_names_tab_key_column(self):
        rows = [{"id": "C300", "n": "x"}]
        schema = {"n": ColumnSchema(type="int")}
        found = problems(rows, schema, tab="Members", key=["id"])
        assert str(found[0]) == (
            "Members: key ('C300',), column 'n': 'x' is not a valid int"
        )

    def test_without_a_key_rows_are_named_by_position(self):
        found = problems(
            [{"n": "1"}, {"n": "x"}], {"n": ColumnSchema(type="int")}, tab="T"
        )
        assert found[0].key == ()
        assert found[0].row == 2
        assert str(found[0]) == "T: row 2, column 'n': 'x' is not a valid int"
