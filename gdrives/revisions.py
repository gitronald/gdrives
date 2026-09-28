"""List and download a file's Drive revisions, read-only.

Everything here runs on the default `drive.readonly` scope: `revisions.list`
and `revisions.get` read a revision's metadata, `files.get` reads the file's
current MIME type (to decide binary vs. native), and a native revision's
bytes come from a GET of its own `exportLinks` entry rather than from Drive's
usual `files.export`, which only ever serves the head revision. Nothing here
restores, pins, or deletes a revision.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from gdrives.export import NATIVE_EXPORTS
from gdrives.files import GOOGLE_MIME_TYPES, Service, get_file_metadata, is_native
from gdrives.local import atomic_output, safe_filename
from gdrives.sheets.retry import with_retry

# revisions.list's largest page.
PAGE_SIZE = 1000

# The fields a revision needs to build a Revision without a second call.
# lastModifyingUser, size, keepForever, and exportLinks come back only when
# named here (or with fields="*"): a native file's revision omits size and
# keepForever, a binary file's omits exportLinks.
_REVISION_FIELDS = (
    "id, modifiedTime, lastModifyingUser(displayName, emailAddress), "
    "mimeType, size, keepForever, exportLinks"
)
LIST_FIELDS = f"nextPageToken, revisions({_REVISION_FIELDS})"

# Extension -> export MIME type(s) among a native revision's exportLinks
# keys. A format Drive spells two ways (an older and a newer MIME string for
# the same download) lists both; either is a match.
EXPORT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "docx": (NATIVE_EXPORTS["gdoc"][1],),
    "xlsx": (NATIVE_EXPORTS["gsheet"][1],),
    "pptx": (NATIVE_EXPORTS["gslides"][1],),
    "pdf": ("application/pdf",),
    "csv": ("text/csv",),
    "tsv": ("text/tab-separated-values",),
    "txt": ("text/plain",),
    "html": ("text/html",),
    "rtf": ("application/rtf",),
    "odt": ("application/vnd.oasis.opendocument.text",),
    "ods": (
        "application/x-vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.spreadsheet",
    ),
    "epub": ("application/epub+zip",),
    "zip": ("application/zip",),
    "md": ("text/markdown", "text/x-markdown"),
}

# Google-native label -> default export extension (no leading dot), derived
# from the canonical export table so this and `download.py`/`export.py`
# never drift.
_DEFAULT_EXTENSION = {
    label: ext.lstrip(".") for label, (ext, _mime) in NATIVE_EXPORTS.items()
}


@dataclass(frozen=True)
class Revision:
    """One revision of a Drive file, as much as `revisions.list`/`.get` give.

    ``size`` and ``keep_forever`` are binary-file fields; ``export_links`` is a
    native-file field, mapping each offered export MIME type to a fetchable
    URL. A field the revision's kind does not carry is ``None``.
    """

    id: str
    modified_time: str
    modified_by: str
    mime_type: str
    size: int | None
    keep_forever: bool | None
    export_links: dict[str, str] | None


def _modified_by(user: dict[str, Any]) -> str:
    """Format `lastModifyingUser` for display.

    A service account's `displayName` is its own email address; showing both
    would repeat it, so an identical pair collapses to the one string.
    """
    display = user.get("displayName", "")
    email = user.get("emailAddress", "")
    if not email or email == display:
        return display or email
    return f"{display} ({email})" if display else email


def _revision_from_api(data: dict[str, Any]) -> Revision:
    size = data.get("size")
    return Revision(
        id=data["id"],
        modified_time=data.get("modifiedTime", ""),
        modified_by=_modified_by(data.get("lastModifyingUser") or {}),
        mime_type=data.get("mimeType", ""),
        size=int(size) if size is not None else None,
        keep_forever=data.get("keepForever"),
        export_links=data.get("exportLinks"),
    )


def list_revisions(service: Service, file_id: str) -> list[Revision]:
    """Page through every revision of a file, oldest first, as the API returns them.

    Retried on 429 and 5xx (see :func:`gdrives.sheets.retry.with_retry`'s
    default statuses): the call is idempotent, and a live run has hit one
    transient 500 that succeeded on retry.
    """
    revisions: list[Revision] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "fileId": file_id,
            "fields": LIST_FIELDS,
            "pageSize": PAGE_SIZE,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        response = with_retry(lambda: service.revisions().list(**kwargs).execute())
        revisions.extend(_revision_from_api(r) for r in response.get("revisions", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return revisions


def _get_revision(service: Service, file_id: str, revision_id: str) -> Revision:
    """Fetch one revision's metadata by ID (`revisions.get`, never `get_media`)."""
    data = (
        service.revisions()
        .get(fileId=file_id, revisionId=revision_id, fields=_REVISION_FIELDS)
        .execute()
    )
    return _revision_from_api(data)


def _default_format(mime_type: str) -> str:
    """The export extension (no leading dot) `download.py`/`export.py` use for a
    native type, or a clear error for a native type with no export format."""
    label = GOOGLE_MIME_TYPES.get(mime_type)
    if label is None or label not in _DEFAULT_EXTENSION:
        raise ValueError(f"{mime_type!r} has no export format")
    return _DEFAULT_EXTENSION[label]


def _offered_extensions(export_links: dict[str, str]) -> list[str]:
    return sorted(
        ext
        for ext, mimes in EXPORT_EXTENSIONS.items()
        if any(mime in export_links for mime in mimes)
    )


def _export_mime(export_links: dict[str, str], fmt: str) -> str:
    """The export link key for extension `fmt`, or a refusal listing what is offered."""
    for mime in EXPORT_EXTENSIONS.get(fmt, ()):
        if mime in export_links:
            return mime
    offered = ", ".join(_offered_extensions(export_links)) or "none"
    raise ValueError(f"revision has no {fmt!r} export; offered: {offered}")


def _fetch_export_link(service: Service, link: str) -> bytes:
    """GET an export link, checking the status: a 429 or 401 body is HTML, never bytes.

    Retried on 429 and 5xx, since export links are rate limited separately
    from the rest of the API and have been seen to answer a burst of fetches
    with a 429. Any other non-200 status raises a clear `HttpError` naming it,
    and its (HTML) body is never returned.
    """

    def attempt() -> bytes:
        response, content = service._http.request(link, "GET")
        if response.status != 200:
            raise HttpError(response, content, uri=link)
        return content

    return with_retry(attempt)


def _target_path(output: str, stem: str, revision_id: str, ext: str) -> Path:
    """The file `download_revision` writes to.

    `output` is used as given when it names an existing directory; otherwise
    it is taken as the file path itself (its parent is created if missing, as
    `export_file` does for `-o`).
    """
    out = Path(output)
    if out.is_dir():
        return out / f"{stem}-{revision_id}{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def download_revision(
    service: Service,
    file_id: str,
    revision_id: str,
    output: str,
    *,
    mime_type: str | None = None,
) -> Path:
    """Download one revision of a file to `output` (a file path or a directory).

    The file's current MIME type decides how: a native Google file (Doc,
    Sheet, Slides) is fetched from the revision's own `exportLinks`, in the
    format named by `mime_type` (an extension such as ``"xlsx"``, default the
    one `download.py`/`export.py` use for the type); anything else is fetched
    as stored, via `revisions.get_media`, and `mime_type` must be left unset.

    For a directory `output`, the local name is the file's name, the revision
    ID, and the format's extension, joined by a hyphen.
    """
    meta = get_file_metadata(service, file_id)
    stem = Path(safe_filename(meta["name"])).stem or safe_filename(meta["name"])

    if is_native(meta):
        revision = _get_revision(service, file_id, revision_id)
        export_links = revision.export_links or {}
        fmt = mime_type or _default_format(meta["mimeType"])
        mime = _export_mime(export_links, fmt)
        content = _fetch_export_link(service, export_links[mime])
        target = _target_path(output, stem, revision_id, f".{fmt}")
        with atomic_output(target) as fh:
            fh.write(content)
        return target

    if mime_type is not None:
        raise ValueError("--format only applies to a native Google file")

    ext = Path(safe_filename(meta["name"])).suffix
    target = _target_path(output, stem, revision_id, ext)
    request = service.revisions().get_media(fileId=file_id, revisionId=revision_id)
    with atomic_output(target) as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target


def _print_table(revisions: list[Revision]) -> None:
    """Print revisions as aligned columns: id, modified time, modified by, size."""
    from gdrives.local import printable

    header = ("id", "modified time", "modified by", "size")
    rows = [
        (
            r.id,
            r.modified_time,
            printable(r.modified_by),
            "" if r.size is None else str(r.size),
        )
        for r in revisions
    ]
    widths = [
        max(len(header[i]), *(len(row[i]) for row in rows)) if rows else len(header[i])
        for i in range(len(header))
    ]
    print("  ".join(title.ljust(widths[i]) for i, title in enumerate(header)))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def run(
    source: str,
    *,
    download: str | None = None,
    output: str = ".",
    format: str | None = None,
    as_json: bool = False,
) -> None:
    """List a file's revisions, or download one of them.

    `source` is a Drive URL, a bare file ID, or a Drive path, as for
    `download`. With `download` unset, every revision is listed (`as_json`
    prints them as a raw JSON list instead of aligned columns). With
    `download` set to a revision ID, that revision is fetched to `output` (a
    file path or a directory, default the current directory); `format` names
    the export extension for a native file and is refused for any other kind.
    """
    from dataclasses import asdict

    from gdrives.auth import build_drive_service
    from gdrives.local import printable
    from gdrives.resolve import resolve_and_report

    service = build_drive_service()
    file_id = resolve_and_report(source, "File", service)

    if download is not None:
        target = download_revision(service, file_id, download, output, mime_type=format)
        print(f"Downloaded revision {download} to {printable(str(target))}")
        return

    revisions = list_revisions(service, file_id)
    if as_json:
        import json

        print(json.dumps([asdict(r) for r in revisions], indent=2))
        return
    _print_table(revisions)
