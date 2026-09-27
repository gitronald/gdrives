"""Tests for gdrives.sheets.files record files: CSV, TSV, and JSON by extension."""

import json

import pytest

from gdrives.sheets import Records, read_records, write_records, write_values_csv

COLUMNS = ["id", "name", "paid", "joined"]
ROWS = [
    {"id": "007", "name": "Alex Doe", "paid": "TRUE", "joined": "2026-01-15"},
    {"id": "12", "name": "", "paid": "FALSE", "joined": ""},
]
TYPES = {"id": "int", "paid": "bool", "joined": "date"}


class TestDelimited:
    @pytest.mark.parametrize("suffix", [".csv", ".tsv", ".CSV"])
    def test_round_trip_keeps_every_cell_as_written(self, tmp_path, suffix):
        path = tmp_path / f"members{suffix}"
        write_records(path, COLUMNS, ROWS)
        assert read_records(path) == Records(COLUMNS, ROWS)

    def test_csv_bytes(self, tmp_path):
        path = tmp_path / "m.csv"
        write_records(path, ["id", "note"], [{"id": "1", "note": "a, b"}])
        assert path.read_bytes() == b'id,note\r\n1,"a, b"\r\n'

    def test_tsv_uses_tabs(self, tmp_path):
        path = tmp_path / "m.tsv"
        write_records(path, ["id", "note"], [{"id": "1", "note": "x"}])
        assert path.read_bytes() == b"id\tnote\r\n1\tx\r\n"

    def test_types_do_not_change_a_delimited_file(self, tmp_path):
        path = tmp_path / "m.csv"
        write_records(path, COLUMNS, ROWS, types=TYPES)
        assert read_records(path).rows[0]["id"] == "007"

    def test_bom_is_written_on_request_and_accepted_on_read(self, tmp_path):
        path = tmp_path / "m.csv"
        write_records(path, ["id"], [{"id": "1"}], bom=True)
        assert path.read_bytes() == b"\xef\xbb\xbfid\r\n1\r\n"
        assert read_records(path) == Records(["id"], [{"id": "1"}])

    def test_no_bom_by_default(self, tmp_path):
        path = tmp_path / "m.csv"
        write_records(path, ["id"], [])
        assert path.read_bytes() == b"id\r\n"

    def test_header_only_file_keeps_its_columns(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name\n")
        assert read_records(path) == Records(["id", "name"], [])

    def test_empty_file(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("")
        assert read_records(path) == Records([], [])

    def test_short_rows_are_padded(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name,note\n1,Alex\n")
        assert read_records(path).rows == [{"id": "1", "name": "Alex", "note": ""}]

    def test_blank_rows_are_skipped(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name\n\n1,Alex\n,\n2,Sam\n")
        assert read_records(path).rows == [
            {"id": "1", "name": "Alex"},
            {"id": "2", "name": "Sam"},
        ]

    def test_row_wider_than_header_raises(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name\n1,Alex\n2,Sam,extra\n")
        with pytest.raises(ValueError, match=r"m\.csv, row 3: 3 cells but 2 columns"):
            read_records(path)

    def test_repeated_header_raises(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name,id\n")
        with pytest.raises(ValueError, match=r"repeated column name\(s\) \['id'\]"):
            read_records(path)

    def test_blank_header_raises(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,,name\n")
        with pytest.raises(ValueError, match="blank column name"):
            read_records(path)

    @pytest.mark.parametrize(
        ("name", "text"),
        [("m.csv", "id , name\n1,Alex\n"), ("m.tsv", "id \t name\n1\tAlex\n")],
    )
    def test_header_names_are_stripped(self, tmp_path, name, text):
        # A tab's header cells are stripped, so a padded name must match them.
        path = tmp_path / name
        path.write_text(text)
        assert read_records(path) == Records(
            ["id", "name"], [{"id": "1", "name": "Alex"}]
        )

    def test_cells_are_not_stripped(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name\n1, Alex \n")
        assert read_records(path).rows == [{"id": "1", "name": " Alex "}]

    def test_header_names_equal_once_stripped_raise(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id,name,id \n")
        with pytest.raises(ValueError, match=r"repeated column name\(s\) \['id'\]"):
            read_records(path)

    def test_whitespace_only_header_raises(self, tmp_path):
        path = tmp_path / "m.csv"
        path.write_text("id, ,name\n")
        with pytest.raises(ValueError, match="blank column name"):
            read_records(path)

    def test_write_value_grid_bom(self, tmp_path):
        # The grid writer gained the same option; its default is unchanged.
        path = tmp_path / "grid.csv"
        write_values_csv(str(path), [["a"]], bom=True)
        assert path.read_bytes() == b"\xef\xbb\xbfa\r\n"


class TestJson:
    def test_writes_typed_values_in_column_order(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, COLUMNS, ROWS, types=TYPES)
        assert path.read_text(encoding="utf-8") == (
            "[\n"
            "  {\n"
            '    "id": 7,\n'
            '    "name": "Alex Doe",\n'
            '    "paid": true,\n'
            '    "joined": "2026-01-15"\n'
            "  },\n"
            "  {\n"
            '    "id": 12,\n'
            '    "name": null,\n'
            '    "paid": false,\n'
            '    "joined": null\n'
            "  }\n"
            "]\n"
        )

    def test_key_order_is_column_order_not_alphabetical(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, ["z", "a"], [{"a": "1", "z": "2"}])
        assert list(json.loads(path.read_text())[0]) == ["z", "a"]

    def test_rewrite_is_byte_stable(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, COLUMNS, ROWS, types=TYPES)
        first = path.read_bytes()
        records = read_records(path)
        write_records(path, records.columns, records.rows, types=TYPES)
        assert path.read_bytes() == first

    def test_round_trip_to_canonical_strings(self, tmp_path):
        path = tmp_path / "m.json"
        rows = [{"n": "3", "x": "2.5", "b": "TRUE", "at": "2026-01-15 10:30:00"}]
        types = {"n": "int", "x": "float", "b": "bool", "at": "datetime"}
        write_records(path, ["n", "x", "b", "at"], rows, types=types)
        assert read_records(path) == Records(["n", "x", "b", "at"], rows)

    def test_integer_valued_float_reads_back_canonical(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, ["x"], [{"x": "3"}], types={"x": "float"})
        assert path.read_text() == '[\n  {\n    "x": 3.0\n  }\n]\n'
        assert read_records(path).rows == [{"x": "3"}]

    def test_untyped_columns_are_strings(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, ["id"], [{"id": "007"}])
        assert json.loads(path.read_text()) == [{"id": "007"}]

    def test_non_ascii_is_written_as_is(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, ["name"], [{"name": "Zoë"}])
        assert '"Zoë"' in path.read_text(encoding="utf-8")

    def test_empty_rows(self, tmp_path):
        path = tmp_path / "m.json"
        write_records(path, ["id"], [])
        assert path.read_text() == "[]\n"
        assert read_records(path) == Records([], [])

    def test_cell_that_does_not_parse_as_its_type_raises(self, tmp_path):
        path = tmp_path / "m.json"
        with pytest.raises(ValueError, match=r"m\.json: 'x' is not a valid int"):
            write_records(path, ["n"], [{"n": "x"}], types={"n": "int"})
        assert not path.exists()

    def test_non_finite_float_raises(self, tmp_path):
        path = tmp_path / "m.json"
        with pytest.raises(ValueError, match=r"m\.json: Out of range float"):
            write_records(path, ["x"], [{"x": "inf"}], types={"x": "float"})
        assert not path.exists()

    def test_bom_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="only to .csv and .tsv"):
            write_records(tmp_path / "m.json", ["id"], [], bom=True)

    def test_read_columns_are_every_key_in_first_seen_order(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps([{"b": 1, "a": None}, {"c": 2.0, "a": "x"}]))
        assert read_records(path) == Records(
            ["b", "a", "c"],
            [{"b": "1", "a": "", "c": ""}, {"b": "", "a": "x", "c": "2"}],
        )

    def test_read_skips_blank_objects(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps([{"a": "1"}, {"a": None}, {}]))
        assert read_records(path).rows == [{"a": "1"}]

    def test_read_accepts_a_bom(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_bytes(b'\xef\xbb\xbf[{"a": true}]')
        assert read_records(path).rows == [{"a": "TRUE"}]

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("[{")
        with pytest.raises(ValueError, match=r"m\.json: not valid JSON"):
            read_records(path)

    def test_not_an_array(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('{"a": 1}')
        with pytest.raises(ValueError, match="expected a JSON array of objects"):
            read_records(path)

    def test_item_not_an_object(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('[{"a": 1}, 2]')
        with pytest.raises(ValueError, match="item 1 is not an object"):
            read_records(path)

    @pytest.mark.parametrize("nested", [[1], {"k": 1}])
    def test_nested_value(self, tmp_path, nested):
        path = tmp_path / "m.json"
        path.write_text(json.dumps([{"a": nested}]))
        with pytest.raises(ValueError, match="item 0, 'a': nested values"):
            read_records(path)

    def test_blank_key_name_raises(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('[{"": 1}]')
        with pytest.raises(ValueError, match="blank column name"):
            read_records(path)

    def test_key_names_are_stripped(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps([{"id ": 1, " name": "Alex"}, {"id": 2}]))
        assert read_records(path) == Records(
            ["id", "name"],
            [{"id": "1", "name": "Alex"}, {"id": "2", "name": ""}],
        )

    def test_whitespace_only_key_name_raises(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('[{" ": 1}]')
        with pytest.raises(ValueError, match="blank column name"):
            read_records(path)

    def test_key_names_equal_once_stripped_raise(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('[{"id": 1}, {"id": 2, "id ": 3}]')
        with pytest.raises(ValueError, match="item 1 repeats column name 'id'"):
            read_records(path)


class TestWriteRecords:
    def test_missing_cell_is_written_blank(self, tmp_path):
        path = tmp_path / "m.csv"
        write_records(path, ["id", "name"], [{"id": "1"}])
        assert read_records(path).rows == [{"id": "1", "name": ""}]

    def test_cell_outside_columns_raises(self, tmp_path):
        path = tmp_path / "m.csv"
        with pytest.raises(ValueError, match=r"record 2 has unknown columns \['x'\]"):
            write_records(path, ["id"], [{"id": "1"}, {"id": "2", "x": "y"}])
        assert not path.exists()

    def test_repeated_columns_raise(self, tmp_path):
        with pytest.raises(ValueError, match="repeated column name"):
            write_records(tmp_path / "m.csv", ["id", "id"], [])

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "m.json"
        write_records(path, ["id"], [{"id": "1"}])
        assert read_records(path).rows == [{"id": "1"}]

    def test_accepts_a_string_path(self, tmp_path):
        path = str(tmp_path / "m.csv")
        write_records(path, ["id"], [{"id": "1"}])
        assert read_records(path).rows == [{"id": "1"}]

    def test_failed_write_keeps_the_previous_file(self, tmp_path, monkeypatch):
        path = tmp_path / "m.json"
        path.write_text("old\n")

        def fail(self, destination):
            raise OSError("disk full")

        monkeypatch.setattr("pathlib.Path.replace", fail)
        with pytest.raises(OSError, match="disk full"):
            write_records(path, ["id"], [{"id": "1"}])
        assert path.read_text() == "old\n"
        assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


class TestUnsupportedExtension:
    @pytest.mark.parametrize("name", ["m.xlsx", "m", "m.txt"])
    def test_read_and_write_refuse(self, tmp_path, name):
        path = tmp_path / name
        with pytest.raises(ValueError, match="unsupported record file type"):
            read_records(path)
        with pytest.raises(ValueError, match="unsupported record file type"):
            write_records(path, ["id"], [])
        assert not path.exists()
