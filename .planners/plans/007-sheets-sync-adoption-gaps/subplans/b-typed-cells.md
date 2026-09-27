---
status: draft
branch:
---

# 007b — Add the typed codec, Python column types, and typed dates

Part of [007](../plan.md). Step 2 of the umbrella's implementation order. It is pure
except for the typed date read, and [`c-merge-additions.md`](c-merge-additions.md) and
[`e-stores.md`](e-stores.md) build on it.

Notes applied: R2, R4, R9, R13, M5.

## Typed codec (`cells.py`)

`read_records` and `write_records` already convert typed JSON values to canonical strings
and back. Only the writer does it in a private helper, `_json_value` (`files.py:166`).
The reader calls `to_cell` inline (`files.py:143`), after refusing nested values and
tidying the column names [R4]. Make the conversion public, for rows that never touch a
file:

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
- `encode_rows` refuses a `list` or `dict` value, naming the row position and the
  column, as the JSON reader does (`files.py:132`). `to_cell` alone would write its
  `str()` [R4]. It also refuses a row holding a column that `columns` does not name,
  as `write_records` does.
- Column names are used as given. A file's header is stripped because a person typed
  it; a row built in code has the names its code gave it.
- `decode_rows` applies `from_cell` per column, `str` for a column `types` does not name.
  It raises one ValueError listing every cell that does not parse, each by row position
  and column.
- `decode_rows(encode_rows(rows), types) == rows` for rows whose values match `types`,
  with two exceptions [R13]: a value of `""` decodes to `None`, and a column a row
  lacks comes back holding `None`. `from_cell`'s docstring makes the same claim without
  them (`cells.py:55`) and is corrected in this step.
- The JSON reader and writer in `files.py` are rewritten over these two functions, so
  there is one conversion. A `date` or `datetime` column still writes its canonical string
  to JSON, which has no date type. The reader keeps its own checks, which run before
  `encode_rows`: the item index in its messages, the stripped column names, and the
  refusal of a name repeated after stripping.
- The writer's message changes [R2]. Today `write_records` raises on the first cell
  that does not parse, prefixed with the path (`files.py:219-230`). Over `decode_rows`
  it lists every such cell by row position, still prefixed with the path. It is a
  visible change for a caller that matches the text.

## Python types as column types

`from_cell`, `decode_rows`, and `write_records(types=)` accept the classes
`str`, `int`, `float`, `bool`, `date`, and `datetime` as well as their names.
`ColumnSchema.type` is stored as the name, so equality, `TabConfig.types`, and the config
file are unchanged. The config file accepts names only, since JSON has no classes.

`ColumnSchema` is a frozen dataclass with `type: str` (`cells.py:167`), and a dataclass
field has one annotation for the constructor and the attribute. Accepting a class in the
constructor would make the attribute `str | type`, and that flows into
`TabConfig.types -> dict[str, str]` under pyrefly's strict preset [R9]. So the field is
not widened (decision 9):

- `column_type(name_or_class) -> str` is public. It returns the name for a name or a
  class, and raises the ValueError `_check_type` raises for anything else.
- Classes are matched by identity, not `isinstance` or `issubclass`: `bool` is a
  subclass of `int` and `datetime` of `date`, and each must map to its own name.
- `ColumnSchema.of(type_, *, required=False, allowed=None)` is a second constructor that
  takes a name or a class and stores the name. `ColumnSchema(type=int)` stays a type
  error and a ValueError.
- `from_cell`, `decode_rows`, and `write_records` take `str | type` and call
  `column_type` first.

## Typed dates read from the sheet [M5]

`read_tab` reads with `UNFORMATTED_VALUE` and `FORMATTED_STRING` (`table.py:80-86`), so
a date cell reads as its display text, and `from_cell` takes ISO 8601 only
(`cells.py:87-89`). A column of sheet-native dates cannot be declared `date` or
`datetime` unless the sheet happens to display ISO 8601. The timestamp column of a
form-responses sheet does not, so its schema check fails on every row. The codec above
and the normalized comparison of step c both assume a typed column's cells parse.

- For a column declared `date` or `datetime`, the cell is read as a serial number and
  converted to ISO 8601, so the local value does not depend on display format or locale.
- Conversion is by declaration, never by guess. A number in an undeclared column
  cannot be told from a date serial, and is left alone.
- One request carries one `dateTimeRenderOption`, so a tab with a declared date column
  costs a second read, of the declared columns only (`Tab!C:C`, one `values.batchGet`
  with `SERIAL_NUMBER`). Reading the whole tab that way would turn every undeclared date
  column into numbers (decision 11). A tab with no declared date column makes the one
  read it makes today.
- `read_tab(..., types=None)` and `parse_tab(..., serials=None)`. `types` names the
  typed columns; `serials` is the second grid, for a caller that read it itself. The
  `Table` records `types`, so the re-read guard and the read-back in `apply_plan` read
  the same way.
- `serial_to_cell(number, type_) -> str` in `cells.py`, pure. The epoch is 1899-12-30.
  A `date` column takes a whole serial and refuses one with a fraction, which the
  schema check then reports. A `datetime` column rounds to the millisecond and writes
  what `to_cell` writes for a `datetime`, with no fraction when it is zero.
- A cell holding text in a declared column stays as it is: a sync writes `RAW` strings,
  so an ISO date it pushed is text on the sheet and reads back as written. A column can
  hold both, and both arrive as ISO 8601.
- Serials carry no time zone. The values are naive, in the spreadsheet's own zone.
- `plan_tab` and `pull_tab` pass the tab's schema types. `pull_all_tabs` has no schema
  and is unchanged.

**First task: a live check.** Read a date cell, a date-time cell, and an ISO string in
one column with `SERIAL_NUMBER`, and record in the Log what each returns and how many
digits the date-time carries. The design above assumes text comes back as text. If it
does not, stop and revise this section before building on it.

A base saved from display text reports each typed date cell as a sheet edit on the
first run after the upgrade, and folds the ISO value in. It is a visible change, and
the guide's section on moving over says so
([`h-docs-and-release.md`](h-docs-and-release.md)).

## Tests

- The codec round trip for each type, both exceptions, and each refusal: a nested
  value, an unknown column, an unknown type, and a cell that does not parse, with every
  such cell listed.
- `column_type` for each name and class, `bool` and `datetime` among them, and a class
  that is none of the six.
- The JSON reader and writer tests pass unchanged except the one that pins the
  writer's message.
- `serial_to_cell`: a whole serial, a fraction in a `date` column, midnight, a
  millisecond, and a serial before the epoch.
- `parse_tab` with `serials`: a typed column of mixed serials and text, a typed column
  the tab lacks, and a `serials` grid shorter than the values grid.
- `FakeSheetGrid` gains a `SERIAL_NUMBER` read: a cell seeded as a date returns its
  serial, and a string returns itself.
- Live: the first task's cells, kept as one test.

## Config and docs in this step

- No new config field: `schema` already declares the types.
- The guide's section on how cells are read says what a declared date column reads as.
- Changelog: the codec, `column_type`, and `ColumnSchema.of` under Added; typed dates
  and the writer's message under Changed.
