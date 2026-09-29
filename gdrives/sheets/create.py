"""Create a native spreadsheet in a Drive folder, and name its tabs.

The file is created through the Drive API (``files.create`` with the
spreadsheet MIME type and the folder as its parent), since the Sheets API
creates in the root of My Drive only. A new spreadsheet has one tab. When
tabs are named, that tab takes the first title and the others are added
after it, so nothing is deleted.

A spreadsheet can also start from a local workbook (``.xlsx`` or ``.csv``):
the file is uploaded as the media body of the same ``files.create``, with the
spreadsheet MIME type in the metadata, and Drive converts it. The workbook
names its own tabs, so no tabs are named.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gdrives.files import Service
from gdrives.sheets.values import batch_update_spreadsheet, tab_sheet_ids
from gdrives.upload import UploadError, check_local, resumable_media, send_upload

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# The workbook formats Drive converts here, by extension, with the type sent.
SOURCE_MIMES: Mapping[str, str] = MappingProxyType(
    {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
    }
)


def spreadsheet_url(spreadsheet_id: str) -> str:
    """The URL a spreadsheet opens at."""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def check_tabs(tabs: Sequence[str]) -> list[str]:
    """Return the tab titles to name, each once, refusing a blank one."""
    if any(not title.strip() for title in tabs):
        raise ValueError(f"blank tab title in {list(tabs)}")
    return list(dict.fromkeys(tabs))


def check_source(source: str | Path) -> tuple[Path, str]:
    """Return a workbook's path and the MIME type it is sent as.

    The extension decides, without regard to case, and only ``.xlsx`` and
    ``.csv`` are taken. The file is checked as ``upload`` checks its own.
    """
    path = Path(source)
    mime = SOURCE_MIMES.get(path.suffix.lower())
    if mime is None:
        taken = ", ".join(SOURCE_MIMES)
        raise ValueError(
            f"'{path}' is not a workbook this command converts; use {taken}"
        )
    return check_local(str(path)), mime


def name_tabs(service: Service, spreadsheet_id: str, tabs: Sequence[str]) -> None:
    """Give a new spreadsheet the tabs named, in order, in one request.

    The spreadsheet's first tab is renamed to the first title, and the others
    are added after it. The first tab is found by reading the spreadsheet,
    since its title follows the account's language. It is for a spreadsheet
    just created: a tab that is already there under one of the other titles
    is refused by the API.
    """
    titles = check_tabs(tabs)
    if not titles:
        return
    existing = tab_sheet_ids(service, spreadsheet_id)
    if not existing:
        raise ValueError("spreadsheet has no tabs")
    first, sheet_id = next(iter(existing.items()))
    requests: list[dict[str, Any]] = []
    if titles[0] != first:
        properties = {"sheetId": sheet_id, "title": titles[0]}
        requests.append(
            {"updateSheetProperties": {"properties": properties, "fields": "title"}}
        )
    requests.extend(
        {"addSheet": {"properties": {"title": title}}} for title in titles[1:]
    )
    if requests:
        batch_update_spreadsheet(service, spreadsheet_id, requests)


def create_spreadsheet(
    drive: Service,
    sheets: Service,
    title: str,
    *,
    folder_id: str | None = None,
    tabs: Sequence[str] = (),
    source: str | Path | None = None,
) -> str:
    """Create a native spreadsheet and return its file ID.

    ``drive`` and ``sheets`` are the two services, both with the ``drive``
    scope. The spreadsheet goes in ``folder_id``, or in the root of My Drive
    with none. With ``tabs``, its one tab is renamed to the first title and
    the others are added after it (:func:`name_tabs`); with none, that tab is
    left as it is. Drive permits duplicate names, so a file of the same name
    in the folder is no obstacle, and none is looked for. The title and the
    tabs are checked before anything is created.

    With ``source``, a local ``.xlsx`` or ``.csv`` file (:func:`check_source`),
    the workbook is uploaded as the spreadsheet's content, by the resumable
    upload ``upload`` uses, and Drive converts it. The workbook names its own
    tabs, so ``tabs`` is refused with it. Drive answers with the file as
    uploaded when it refuses the conversion, so the created file's type is
    checked, and an ``UploadError`` names its ID when it is not a spreadsheet.
    """
    if not title.strip():
        raise ValueError("title must not be empty")
    titles = check_tabs(tabs)
    if source is not None and titles:
        raise ValueError("a workbook names its own tabs; pass source or tabs, not both")
    workbook = check_source(source) if source is not None else None
    body: dict[str, Any] = {"name": title, "mimeType": SPREADSHEET_MIME}
    if folder_id is not None:
        body["parents"] = [folder_id]
    if workbook is not None:
        path, mime = workbook
        request = drive.files().create(
            body=body,
            media_body=resumable_media(path, mime),
            fields="id, mimeType",
            supportsAllDrives=True,
        )
        created = send_upload(request)
        if created.get("mimeType") != SPREADSHEET_MIME:
            raise UploadError(
                f"'{title}' ({created['id']}) was created but not converted: "
                f"its type is {created.get('mimeType')}, not a spreadsheet"
            )
        return created["id"]
    created = (
        drive.files().create(body=body, fields="id", supportsAllDrives=True).execute()
    )
    name_tabs(sheets, created["id"], titles)
    return created["id"]
