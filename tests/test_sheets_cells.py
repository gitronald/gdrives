"""Tests for gdrives.sheets.cells: canonical strings, types, keys, and schemas.

All pure functions, so the tests are exhaustive over the documented cases.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from gdrives.sheets import (
    BLANK_KEYS,
    COLUMN_TYPES,
    ColumnSchema,
    Problem,
    cell_problem,
    column_type,
    decode_rows,
    encode_rows,
    from_cell,
    index_rows,
    normalize_cell,
    normalize_key,
    problems,
    row_key,
    serial_to_cell,
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


class Flag(int):
    """A subclass of a column class, which is not that class."""


CLASSES = [
    (str, "str"),
    (int, "int"),
    (float, "float"),
    (bool, "bool"),
    (date, "date"),
    (datetime, "datetime"),
]


class TestColumnTypes:
    def test_declared_names(self):
        assert COLUMN_TYPES == {"str", "int", "float", "bool", "date", "datetime"}

    @pytest.mark.parametrize("name", sorted(COLUMN_TYPES))
    def test_a_name_is_its_own_type(self, name):
        assert column_type(name) == name

    @pytest.mark.parametrize(("cls", "name"), CLASSES)
    def test_a_class_maps_to_its_own_name(self, cls, name):
        # bool subclasses int and datetime subclasses date: each is itself.
        assert column_type(cls) == name

    @pytest.mark.parametrize(
        "given", ["number", "Int", "", Flag, bytes, type(None), object, 3, None, []]
    )
    def test_anything_else_is_refused(self, given):
        with pytest.raises(ValueError, match="unknown column type"):
            column_type(given)

    @pytest.mark.parametrize(("cls", "name"), CLASSES)
    def test_from_cell_takes_a_class(self, cls, name):
        value = {
            "str": "x",
            "int": 7,
            "float": 2.5,
            "bool": True,
            "date": date(2026, 1, 15),
            "datetime": datetime(2026, 1, 15, 10, 30),
        }[name]
        parsed = from_cell(to_cell(value), cls)
        assert parsed == value and type(parsed) is cls

    def test_from_cell_refuses_a_class_that_is_no_column_type(self):
        with pytest.raises(ValueError, match="unknown column type"):
            from_cell("1", bytes)


class TestNormalizeCell:
    @pytest.mark.parametrize(
        ("text", "type_", "normal"),
        [
            ("true", "bool", "TRUE"),
            ("False", bool, "FALSE"),
            ("3.0", "float", "3"),
            ("3", float, "3"),
            ("2.50", "float", "2.5"),
            ("1E-7", "float", "1e-07"),
            ("007", "int", "7"),
            ("-0", "int", "0"),
            ("2026-01-15T10:30:00", "datetime", "2026-01-15 10:30:00"),
            ("2026-01-15 10:30", "datetime", "2026-01-15 10:30:00"),
            ("2026-01-15", "date", "2026-01-15"),
            (" 007 ", "str", " 007 "),
            ("", "int", ""),
            ("", "str", ""),
        ],
    )
    def test_a_cell_that_parses_takes_the_form_to_cell_writes(
        self, text, type_, normal
    ):
        assert normalize_cell(text, type_) == normal
        assert normalize_cell(normal, type_) == normal

    @pytest.mark.parametrize(
        ("text", "type_"),
        [("3.0", "int"), ("yes", "bool"), (" 3", "float"), ("Jan 15", "date")],
    )
    def test_a_cell_that_does_not_parse_is_unchanged(self, text, type_):
        assert normalize_cell(text, type_) == text

    def test_str_is_the_default(self):
        assert normalize_cell("TRUE ") == "TRUE "

    def test_an_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type"):
            normalize_cell("1", "number")


class TestCellProblem:
    def test_the_reason_a_cell_does_not_fit_or_none(self):
        assert cell_problem("yes", ColumnSchema(type="bool")) == (
            "'yes' is not a valid bool"
        )
        assert cell_problem("", ColumnSchema(required=True)) == "is required"
        assert cell_problem("z", ColumnSchema(allowed=["x", "y"])) == (
            "'z' is not one of ['x', 'y']"
        )
        assert cell_problem("", ColumnSchema(type="int")) is None
        assert cell_problem("7", ColumnSchema(type="int", allowed=[7])) is None

    @pytest.mark.parametrize("text", ["true", "True"])
    def test_a_respelled_bool_is_a_problem_only_under_strict(self, text):
        assert cell_problem(text, ColumnSchema(type="bool")) is None
        assert cell_problem(text, ColumnSchema(type="bool", strict=True)) == (
            f"{text!r} is not TRUE or FALSE, and the column is strict"
        )

    @pytest.mark.parametrize("text", ["TRUE", "FALSE"])
    def test_the_strict_bool_forms_pass(self, text):
        assert cell_problem(text, ColumnSchema(type="bool", strict=True)) is None

    @pytest.mark.parametrize("text", ["20260927", "2026-W39-7"])
    def test_another_iso_date_form_is_a_problem_only_under_strict(self, text):
        assert cell_problem(text, ColumnSchema(type="date")) is None
        assert cell_problem(text, ColumnSchema(type="date", strict=True)) == (
            f"{text!r} is not YYYY-MM-DD, and the column is strict"
        )

    def test_the_strict_date_form_passes(self):
        schema = ColumnSchema(type="date", strict=True)
        assert cell_problem("2026-09-27", schema) is None

    def test_an_invalid_date_is_a_problem_either_way(self):
        problem = "'2026-02-30' is not a valid date"
        assert cell_problem("2026-02-30", ColumnSchema(type="date")) == problem
        assert cell_problem("2026-02-30", ColumnSchema(type="date", strict=True)) == (
            problem
        )

    def test_a_blank_strict_cell_is_checked_by_required_only(self):
        assert cell_problem("", ColumnSchema(type="bool", strict=True)) is None
        schema = ColumnSchema(type="bool", strict=True, required=True)
        assert cell_problem("", schema) == "is required"


class TestSerialToCell:
    """Serial numbers count days from 1899-12-30, as a live read confirmed."""

    DAY = 46292  # 2026-09-27

    @pytest.mark.parametrize(
        ("number", "text"),
        [
            (46292, "2026-09-27"),
            (46292.0, "2026-09-27"),
            (0, "1899-12-30"),
            (1, "1899-12-31"),
            (-1, "1899-12-29"),
            (45000, "2023-03-15"),
        ],
    )
    def test_a_whole_serial_is_a_date(self, number, text):
        assert serial_to_cell(number, "date") == text
        assert serial_to_cell(number, date) == text

    @pytest.mark.parametrize("number", [46292.5, 46292.43767361111, -0.25])
    def test_a_date_refuses_a_time_of_day(self, number):
        with pytest.raises(ValueError, match="is not a valid date serial"):
            serial_to_cell(number, "date")

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (0, "2026-09-27 00:00:00.000"),
            (10 * 3600 + 30 * 60 + 15, "2026-09-27 10:30:15.000"),
            (10 * 3600 + 30 * 60 + 15.123, "2026-09-27 10:30:15.123"),
            (86399.999, "2026-09-27 23:59:59.999"),
            (43200, "2026-09-27 12:00:00.000"),
            (0.1, "2026-09-27 00:00:00.100"),
        ],
    )
    def test_a_datetime_keeps_the_millisecond(self, seconds, text):
        assert serial_to_cell(self.DAY + seconds / 86400, "datetime") == text
        assert serial_to_cell(self.DAY + seconds / 86400, datetime) == text

    def test_every_datetime_has_one_width(self):
        # str() of a datetime drops a zero fraction; the serial form never does.
        texts = [
            serial_to_cell(self.DAY + seconds / 86400, "datetime")
            for seconds in (0, 37815, 37815.12, 37815.123, 86399.999)
        ]
        assert {len(text) for text in texts} == {len("2026-09-27 10:30:15.123")}

    @pytest.mark.parametrize("seconds", [0, 37815, 37815.1, 37815.123, 86399.999])
    def test_a_datetime_reads_back_and_compares_as_to_cell_writes_it(self, seconds):
        text = serial_to_cell(self.DAY + seconds / 86400, "datetime")
        moment = from_cell(text, "datetime")
        assert isinstance(moment, datetime)
        assert moment.isoformat(sep=" ", timespec="milliseconds") == text
        # The form to_cell writes, as old base files hold it, is the same value.
        assert normalize_cell(text, "datetime") == normalize_cell(
            to_cell(moment), "datetime"
        )
        assert serial_to_cell(46292.43767361111, "datetime") == (
            "2026-09-27 10:30:15.000"
        )

    def test_below_a_millisecond_is_rounded_away(self):
        assert serial_to_cell(46292 + 0.0004 / 86400, "datetime") == (
            "2026-09-27 00:00:00.000"
        )
        assert serial_to_cell(46292 + 0.0004 / 86400, "date") == "2026-09-27"

    def test_a_serial_before_the_epoch(self):
        assert serial_to_cell(-1.5, "datetime") == "1899-12-28 12:00:00.000"
        assert serial_to_cell(-693593, "date") == "0001-01-01"

    @pytest.mark.parametrize(
        "number", [True, False, "46292", None, 1e12, -1e12, float("inf"), float("nan")]
    )
    @pytest.mark.parametrize("type_", ["date", "datetime"])
    def test_what_is_not_a_serial_is_refused(self, number, type_):
        with pytest.raises(ValueError, match=f"is not a valid {type_} serial"):
            serial_to_cell(number, type_)

    @pytest.mark.parametrize("type_", ["str", "int", "float", "bool", int])
    def test_only_a_date_or_a_datetime_has_serials(self, type_):
        with pytest.raises(ValueError, match="a serial number is a date or a datetime"):
            serial_to_cell(46292, type_)

    def test_an_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type"):
            serial_to_cell(46292, "day")


class TestEncodeRows:
    def test_every_value_becomes_its_canonical_string(self):
        rows = [
            {
                "id": 7,
                "name": " Ada ",
                "paid": True,
                "amt": 3.0,
                "on": date(2026, 1, 15),
                "at": datetime(2026, 1, 15, 10, 30),
                "note": None,
            }
        ]
        assert encode_rows(rows) == [
            {
                "id": "7",
                "name": " Ada ",
                "paid": "TRUE",
                "amt": "3",
                "on": "2026-01-15",
                "at": "2026-01-15 10:30:00",
                "note": "",
            }
        ]

    def test_columns_default_to_every_key_in_first_seen_order(self):
        rows = [{"b": 1, "a": None}, {"c": 2.0, "a": "x"}]
        assert encode_rows(rows) == [
            {"b": "1", "a": "", "c": ""},
            {"b": "", "a": "x", "c": "2"},
        ]

    def test_columns_fix_the_order_and_fill_blanks(self):
        assert encode_rows([{"a": 1}, {"b": 2}], ["b", "a"]) == [
            {"b": "", "a": "1"},
            {"b": "2", "a": ""},
        ]

    def test_names_are_used_as_given(self):
        assert encode_rows([{" id ": 1}]) == [{" id ": "1"}]

    def test_no_rows(self):
        assert encode_rows([]) == []
        assert encode_rows([], ["a"]) == []

    def test_the_rows_given_are_not_changed(self):
        rows = [{"a": 1}]
        encode_rows(rows, ["a", "b"])
        assert rows == [{"a": 1}]

    def test_every_nested_value_and_unknown_column_is_listed(self):
        rows = [
            {"id": 1, "tags": ["a"]},
            {"id": 2, "tags": None, "meta": {"k": 1}, "extra": "x"},
        ]
        with pytest.raises(ValueError) as refused:
            encode_rows(rows, ["id", "tags", "meta"])
        assert str(refused.value) == (
            "row 1, column 'tags': nested values are not cells; "
            "row 2, column 'meta': nested values are not cells; "
            "row 2 has unknown columns ['extra']"
        )


class TestDecodeRows:
    TYPES = {
        "id": "int",
        "amt": "float",
        "paid": "bool",
        "on": "date",
        "at": "datetime",
    }

    def test_each_column_is_parsed_as_its_type_and_str_by_default(self):
        records = [
            {
                "id": "007",
                "name": " Ada ",
                "amt": "3",
                "paid": "true",
                "on": "2026-01-15",
                "at": "2026-01-15T10:30:00",
            }
        ]
        assert decode_rows(records, self.TYPES) == [
            {
                "id": 7,
                "name": " Ada ",
                "amt": 3.0,
                "paid": True,
                "on": date(2026, 1, 15),
                "at": datetime(2026, 1, 15, 10, 30),
            }
        ]

    def test_classes_are_types_too(self):
        assert decode_rows([{"n": "1", "b": "TRUE"}], {"n": int, "b": bool}) == [
            {"n": 1, "b": True}
        ]

    def test_a_type_for_a_column_no_record_has_is_ignored(self):
        assert decode_rows([{"n": "1"}], {"n": "int", "gone": "date"}) == [{"n": 1}]

    def test_every_cell_that_does_not_parse_is_listed(self):
        records = [{"id": "1", "on": "Jan 15"}, {"id": "x", "on": "2026-01-15"}]
        with pytest.raises(ValueError) as refused:
            decode_rows(records, self.TYPES)
        assert str(refused.value) == (
            "row 1, column 'on': 'Jan 15' is not a valid date; "
            "row 2, column 'id': 'x' is not a valid int"
        )

    @pytest.mark.parametrize("type_", ["number", bytes])
    def test_an_unknown_type_is_refused_whatever_the_rows_hold(self, type_):
        with pytest.raises(ValueError, match="column 'n': unknown column type"):
            decode_rows([], {"n": type_})

    def test_round_trip(self):
        rows = [
            {
                "id": 7,
                "name": "Ada",
                "amt": 2.5,
                "paid": False,
                "on": date(2026, 1, 15),
                "at": datetime(2026, 1, 15, 10, 30, 5, 123000),
            },
            {
                "id": None,
                "name": None,
                "amt": None,
                "paid": None,
                "on": None,
                "at": None,
            },
        ]
        assert decode_rows(encode_rows(rows), self.TYPES) == rows

    def test_the_two_exceptions_to_the_round_trip(self):
        # A blank string is a blank cell, and a column a row lacks is one too.
        rows = [{"id": 1, "name": ""}, {"id": 2}]
        assert decode_rows(encode_rows(rows), {"id": int}) == [
            {"id": 1, "name": None},
            {"id": 2, "name": None},
        ]


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

    def test_the_settings(self):
        assert BLANK_KEYS == {"refuse", "partial"}

    def test_partial_allows_a_blank_component_beside_one_that_is_not(self):
        rows = [
            {"y": "2026", "id": ""},
            {"y": "", "id": "1"},
            {"y": "2026", "id": "1"},
            {"y": "2026"},
        ]
        with pytest.raises(ValueError, match="duplicate key"):
            index_rows(rows, ["y", "id"], side="local", blank_keys="partial")
        found = index_rows(rows[:3], ["y", "id"], side="local", blank_keys="partial")
        assert found == {("2026", ""): 1, ("", "1"): 2, ("2026", "1"): 3}

    def test_partial_refuses_a_row_whose_every_component_is_blank(self):
        rows = [{"y": "2026", "id": ""}, {"y": " ", "id": ""}, {"name": "x"}]
        with pytest.raises(ValueError) as raised:
            index_rows(rows, ["y", "id"], side="local", blank_keys="partial")
        assert str(raised.value) == "local: blank key ['y', 'id'] in rows [2, 3]"

    def test_partial_compares_blank_components_in_duplicates(self):
        rows = [{"y": "2026", "id": ""}, {"y": "2026 ", "id": " "}]
        with pytest.raises(
            ValueError, match=r"duplicate key \('2026', ''\) in rows \[1, 2\]"
        ):
            index_rows(rows, ["y", "id"], side="local", blank_keys="partial")

    def test_with_a_one_column_key_the_settings_are_the_same(self):
        rows = [{"id": ""}, {"id": "a"}]
        for setting in sorted(BLANK_KEYS):
            with pytest.raises(ValueError, match=r"blank key \['id'\] in rows \[1\]"):
                index_rows(rows, ["id"], side="local", blank_keys=setting)

    def test_an_unknown_setting_is_refused(self):
        with pytest.raises(
            ValueError, match=r"blank_keys must be one of \['partial', 'refuse'\]"
        ):
            index_rows([], ["id"], side="local", blank_keys="allow")

    def test_empty_rows(self):
        assert index_rows([], ["id"], side="local") == {}


class TestColumnSchema:
    def test_defaults(self):
        schema = ColumnSchema()
        assert (schema.type, schema.required, schema.allowed) == ("str", False, None)

    def test_present_and_strict_default_to_false(self):
        schema = ColumnSchema()
        assert (schema.present, schema.strict) == (False, False)

    def test_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type 'money'"):
            ColumnSchema(type="money")

    @pytest.mark.parametrize("type_", ["bool", "date"])
    def test_strict_is_accepted_for_bool_and_date(self, type_):
        schema = ColumnSchema(type=type_, strict=True)
        assert schema.strict is True

    @pytest.mark.parametrize("type_", ["str", "int", "float", "datetime"])
    def test_strict_is_refused_for_any_other_type(self, type_):
        with pytest.raises(
            ValueError,
            match=r"strict is only for a column of \['bool', 'date'\]",
        ):
            ColumnSchema(type=type_, strict=True)


class TestColumnSchemaOf:
    @pytest.mark.parametrize(("cls", "name"), CLASSES)
    def test_a_class_is_stored_as_its_name(self, cls, name):
        schema = ColumnSchema.of(cls, required=True, allowed=["x"])
        assert schema == ColumnSchema(type=name, required=True, allowed=["x"])
        assert schema.type == name

    def test_a_name_and_the_defaults(self):
        assert ColumnSchema.of("date") == ColumnSchema(type="date")
        assert ColumnSchema.of() == ColumnSchema()

    def test_an_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown column type"):
            ColumnSchema.of(bytes)

    def test_the_constructor_still_refuses_a_class(self):
        with pytest.raises(ValueError, match="unknown column type"):
            ColumnSchema(type=int)  # pyrefly: ignore[bad-argument-type]

    def test_present_and_strict_pass_through(self):
        schema = ColumnSchema.of("bool", present=True, strict=True)
        assert schema == ColumnSchema(type="bool", present=True, strict=True)


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
