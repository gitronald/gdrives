---
status: draft
branch:
---

# 007b — Add the typed codec, Python column types, and typed dates

Part of [007](../plan.md).

## Typed codec (`cells.py`)

`read_records` and `write_records` already convert typed JSON values to canonical strings
and back, in private helpers. Make the conversion public, for rows that never touch a file:

```python
def encode_rows(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None
) -> list[dict[str, str]]: ...


def decode_rows(
    records: Sequence[Mapping[str, str]], types: Mapping[str, str]
) -> list[dict[str, CellValue]]: ...
```

- `encode_rows` applies `to_cell` to every value. `columns=None` takes every key in
  first-seen order, as `read_records` does for JSON; a column a row lacks is blank.
- `decode_rows` applies `from_cell` per column, `str` for a column `types` does not name.
  It raises one ValueError listing every cell that does not parse, each by row position
  and column.
- `decode_rows(encode_rows(rows), types) == rows` for rows whose values match `types`.
- The JSON reader and writer in `files.py` are rewritten over these two functions, so
  there is one conversion. A `date` or `datetime` column still writes its canonical string
  to JSON, which has no date type.

## Python types as column types

`from_cell`, `ColumnSchema`, `decode_rows`, and `write_records(types=)` accept the classes
`str`, `int`, `float`, `bool`, `date`, and `datetime` as well as their names.
`ColumnSchema.type` is stored as the name, so equality, `TabConfig.types`, and the config
file are unchanged. The config file accepts names only, since JSON has no classes.
