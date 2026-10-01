# ruff: noqa: E501  (recorded requests hold long field masks verbatim)
"""Default runs of a sync, a pull, and a push, and the requests each made.

The expected requests were recorded from these runs before the `render` tab
setting existed, and are kept as they were recorded: a run that does not set
`render` must send exactly them. Do not regenerate them.
"""

from datetime import date

from gdrives.sheets import (
    CONFIG_NAME,
    parse_config,
    pull_tab,
    push_tab,
    sync_tab,
    write_values_csv,
)
from gdrives.testing import FakeSheetGrid

HEADER = ["id", "name", "when"]


def _target(tmp_path, tabs):
    data = {"t": {"spreadsheet": "S", "tabs": tabs}}
    return parse_config(data, tmp_path / CONFIG_NAME).target("t")


def default_sync(tmp_path):
    """A sync that pushes, appends, adds and drops a column, and sets widths."""
    target = _target(
        tmp_path,
        {
            "T": {
                "local": "local.csv",
                "key": ["id"],
                "schema": {"when": {"type": "date"}},
                "widths": {"name": 120},
            }
        },
    )
    tab = target.tabs[0]
    header = [*HEADER, "note"]
    write_values_csv(
        str(tab.local),
        [
            header,
            ["a", "Ada", "2024-01-02", "x"],
            ["b", "Bob", "2024-02-03", ""],
            ["c", "Cy", "2024-03-04", "z"],
        ],
    )
    base = target.base_path(tab)
    base.parent.mkdir(parents=True)
    write_values_csv(
        str(base),
        [header, ["a", "Ada", "2024-01-02", ""], ["b", "Bo", "2024-02-03", ""]],
    )
    grid = FakeSheetGrid(
        {
            "T": [
                [*HEADER, "old"],
                ["a", "Ada", date(2024, 1, 2), 1],
                ["b", "Bo", date(2024, 2, 3), 2],
            ]
        }
    )
    sync_tab(grid, "S", target, tab, apply=True, add_missing=True, drop_extra=True)
    return grid


def default_created(tmp_path):
    """A sync that creates its tab and clears the links it writes."""
    target = _target(
        tmp_path, {"T": {"local": "local.csv", "key": ["id"], "clear_links": True}}
    )
    tab = target.tabs[0]
    write_values_csv(str(tab.local), [HEADER, ["a", "x.org", "2024-01-02"]])
    grid = FakeSheetGrid({"Other": []})
    sync_tab(grid, "S", target, tab, apply=True)
    return grid


def default_pull(tmp_path):
    """A pull of a tab with a declared date column and a number."""
    target = _target(
        tmp_path,
        {
            "T": {
                "mode": "pull",
                "local": "local.csv",
                "key": ["id"],
                "schema": {"when": {"type": "date"}},
            }
        },
    )
    grid = FakeSheetGrid({"T": [[*HEADER, "amt"], ["a", "Ada", date(2024, 1, 2), 0.5]]})
    pull_tab(grid, "S", target.tabs[0], apply=True)
    return grid


def default_push(tmp_path):
    """A push over an existing tab, with widths and links cleared."""
    target = _target(
        tmp_path,
        {
            "T": {
                "mode": "push",
                "local": "local.csv",
                "key": ["id"],
                "widths": {"name": 120},
                "clear_links": True,
            }
        },
    )
    tab = target.tabs[0]
    write_values_csv(
        str(tab.local), [HEADER, ["a", "Ada", "2024-01-02"], ["b", "Bo", "x.org"]]
    )
    grid = FakeSheetGrid({"T": [HEADER, ["a", "Ada", date(2024, 1, 2)]]})
    push_tab(grid, "S", tab, apply=True)
    return grid


# -- recorded before the render setting existed; do not regenerate --

DEFAULT_SYNC_CALLS = [
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.batchGet",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!C:C"],
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "SERIAL_NUMBER",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": 0,
                                "dimension": "COLUMNS",
                                "startIndex": 3,
                                "endIndex": 4,
                            },
                            "inheritFromBefore": True,
                        }
                    },
                    {
                        "updateCells": {
                            "start": {"sheetId": 0, "rowIndex": 0, "columnIndex": 3},
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "note"}}
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                        }
                    },
                ]
            },
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": 0,
                                "dimension": "COLUMNS",
                                "startIndex": 4,
                                "endIndex": 5,
                            }
                        }
                    }
                ]
            },
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.batchGet",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!C:C"],
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "SERIAL_NUMBER",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.batchGet",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!C:C"],
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "SERIAL_NUMBER",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "valueInputOption": "RAW",
                "data": [
                    {"range": "'T'!D2", "values": [["x"]]},
                    {"range": "'T'!B3", "values": [["Bob"]]},
                ],
            },
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "updateCells": {
                            "start": {"sheetId": 0, "rowIndex": 3, "columnIndex": 0},
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "c"}},
                                        {"userEnteredValue": {"stringValue": "Cy"}},
                                        {
                                            "userEnteredValue": {
                                                "stringValue": "2024-03-04"
                                            }
                                        },
                                        {"userEnteredValue": {"stringValue": "z"}},
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                        }
                    }
                ]
            },
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.batchGet",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!C:C"],
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "SERIAL_NUMBER",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": 0,
                                "dimension": "COLUMNS",
                                "startIndex": 1,
                                "endIndex": 2,
                            },
                            "properties": {"pixelSize": 120},
                            "fields": "pixelSize",
                        }
                    }
                ]
            },
        },
    ),
]

DEFAULT_CREATED_CALLS = [
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {"requests": [{"addSheet": {"properties": {"title": "T"}}}]},
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": 1,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": 3,
                            },
                            "inheritFromBefore": False,
                        }
                    },
                    {
                        "updateCells": {
                            "start": {"sheetId": 1, "rowIndex": 0, "columnIndex": 0},
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "id"}},
                                        {"userEnteredValue": {"stringValue": "name"}},
                                        {"userEnteredValue": {"stringValue": "when"}},
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                        }
                    },
                ]
            },
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "updateCells": {
                            "start": {"sheetId": 1, "rowIndex": 1, "columnIndex": 0},
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "a"}},
                                        {"userEnteredValue": {"stringValue": "x.org"}},
                                        {
                                            "userEnteredValue": {
                                                "stringValue": "2024-01-02"
                                            }
                                        },
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue,userEnteredFormat.textFormat.link",
                        }
                    }
                ]
            },
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!A:C"],
            "includeGridData": True,
            "fields": "sheets(data(rowData(values(hyperlink,textFormatRuns(format(link))))))",
        },
    ),
]

DEFAULT_PULL_CALLS = [
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.batchGet",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!C:C"],
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "SERIAL_NUMBER",
        },
    ),
]

DEFAULT_PUSH_CALLS = [
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "values.update",
        {
            "spreadsheetId": "S",
            "range": "'T'!A1:C3",
            "valueInputOption": "RAW",
            "body": {
                "values": [
                    ["id", "name", "when"],
                    ["a", "Ada", "2024-01-02"],
                    ["b", "Bo", "x.org"],
                ]
            },
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!A:C"],
            "includeGridData": True,
            "fields": "sheets(data(rowData(values(hyperlink,textFormatRuns(format(link))))))",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": 0,
                                "startColumnIndex": 0,
                                "endColumnIndex": 3,
                            },
                            "cell": {},
                            "fields": "userEnteredFormat.textFormat.link",
                        }
                    }
                ]
            },
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "ranges": ["'T'!A:C"],
            "includeGridData": True,
            "fields": "sheets(data(rowData(values(hyperlink,textFormatRuns(format(link))))))",
        },
    ),
    (
        "values.get",
        {
            "spreadsheetId": "S",
            "range": "'T'!1:1",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        },
    ),
    (
        "spreadsheets.get",
        {
            "spreadsheetId": "S",
            "fields": "sheets.properties(sheetId,title,gridProperties)",
        },
    ),
    (
        "spreadsheets.batchUpdate",
        {
            "spreadsheetId": "S",
            "body": {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": 0,
                                "dimension": "COLUMNS",
                                "startIndex": 1,
                                "endIndex": 2,
                            },
                            "properties": {"pixelSize": 120},
                            "fields": "pixelSize",
                        }
                    }
                ]
            },
        },
    ),
]
