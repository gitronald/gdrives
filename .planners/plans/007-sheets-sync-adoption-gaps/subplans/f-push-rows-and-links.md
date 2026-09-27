---
status: draft
branch:
---

# 007f — Add push_rows, written cells, and link formatting

Part of [007](../plan.md).

## `push_rows`

```python
def push_rows(
    service,
    spreadsheet_id,
    title,
    columns,
    rows,
    *,
    key=(),
    input_option=RAW,
    apply=False,
    schema=None,
    validate=None,
    widths=None,
    clear_links=False,
) -> TabReport: ...
```

The body of `push_tab` from the point where the local file has been read: the preview of
what the tab loses, the re-read guard, the single write over the old extent, the grid
growth, and the read-back. `push_tab` becomes: read the store, call `push_rows`.

## Link formatting (`structure.py`)

Sheets formats text that looks like a URL or a domain as a link when it is written, under
`RAW` input too. For a tab meant to hold plain text that is a change nobody asked for.

- `clear_link_format(service, spreadsheet_id, tab, *, columns=None, rows=None)` clears
  only `userEnteredFormat.textFormat.link` over the named columns (every named column by
  default), in one `repeatCell` request per run of adjacent columns. Every other format
  is left alone.
- `linked_cells(service, spreadsheet_id, tab)` returns the cells that render as links, by
  row number and column name.
- `push_rows(clear_links=True)` runs the first after the write and the second as part of
  the read-back, raising `ReadBackError` when a link remains.
