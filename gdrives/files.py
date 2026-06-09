"""Drive API wrappers and file helpers."""

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

# The googleapiclient Drive client (a discovery `Resource`) ships no type
# stubs; alias it to Any so callers can annotate the `service` parameter.
Service = Any
# A Drive API file or metadata object (JSON deserialized to a dict).
DriveFile = dict[str, Any]

# Maps Google Workspace MIME types to short labels
GOOGLE_MIME_TYPES = {
    "application/vnd.google-apps.folder": "folder",
    "application/vnd.google-apps.document": "gdoc",
    "application/vnd.google-apps.spreadsheet": "gsheet",
    "application/vnd.google-apps.presentation": "gslides",
    "application/vnd.google-apps.form": "gform",
    "application/vnd.google-apps.drawing": "gdrawing",
    "application/vnd.google-apps.site": "gsite",
    "application/vnd.google-apps.jam": "gjam",
    "application/vnd.google-apps.script": "gscript",
    "application/vnd.google.colaboratory": "colab",
    "application/vnd.google-apps.map": "gmap",
    "application/vnd.google-apps.shortcut": "shortcut",
}

# Fields requested from files.list — see docs/drive-api.md
LIST_FIELDS = (
    "nextPageToken, "
    "files(id, name, mimeType, size, webViewLink, "
    "modifiedTime, owners(displayName, emailAddress), "
    "sharingUser(displayName, emailAddress))"
)


def strip_url_suffix(url: str) -> str:
    """Remove query params and trailing /edit or /view from Drive URLs.

    Preserves query params for non-Drive URLs (e.g., Google Maps ?mid=).
    """
    if "drive.google.com" in url or "docs.google.com" in url:
        url = url.split("?")[0]
    for suffix in ("/edit", "/view"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def extract_drive_id(url_or_id: str) -> str:
    """Extract a Drive file or folder ID from a URL, or return as-is if already an ID.

    Handles file URLs (``/d/<id>``), folder URLs (``/folders/<id>``), and the
    legacy ``open?id=<id>`` / ``uc?id=<id>`` query forms. Raises ValueError when
    the input is clearly a URL but no ID can be parsed.
    """
    m = re.search(r"/(?:folders|d)/([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    if url_or_id.startswith(("http://", "https://")):
        raise ValueError(f"could not parse a Drive ID from URL: {url_or_id}")
    return url_or_id


def escape_query_value(value: str) -> str:
    """Escape a value for safe interpolation into a Drive API ``q`` query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def is_folder(f: DriveFile) -> bool:
    return file_type(f) == "folder"


def is_native(f: DriveFile) -> bool:
    """Return True for Google-native files (Docs, Sheets, Slides, Forms, etc.)."""
    return f.get("mimeType", "").startswith("application/vnd.google-apps.")


def modified_date(f: DriveFile) -> str:
    """Extract date from modifiedTime (ISO 8601 -> YYYY-MM-DD)."""
    ts = f.get("modifiedTime")
    if not ts:
        return ""
    return datetime.fromisoformat(ts).strftime("%Y-%m-%d")


def file_type(f: DriveFile) -> str:
    """Return a short type label for a Drive file."""
    mime = f.get("mimeType", "")
    if mime in GOOGLE_MIME_TYPES:
        return GOOGLE_MIME_TYPES[mime]
    return Path(f["name"]).suffix.lstrip(".") or "unknown"


def file_url(f: DriveFile) -> str:
    """Return a clean URL for a Drive file or folder."""
    if is_folder(f):
        return f"https://drive.google.com/drive/folders/{f['id']}"
    url = f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}")
    return strip_url_suffix(url)


def owner_email(f: DriveFile) -> str:
    """Extract the primary owner's email from a Drive file dict."""
    owners = f.get("owners") or []
    return owners[0].get("emailAddress", "") if owners else ""


def shared_by(f: DriveFile) -> str:
    """Extract the sharing user's email from a Drive file dict (empty if none)."""
    return (f.get("sharingUser") or {}).get("emailAddress", "")


def paginate_files(
    service: Service, query: str, fields: str, corpora: str
) -> list[DriveFile]:
    """Paginate through a files.list query and return all results."""
    items: list[DriveFile] = []
    page_token = None
    while True:
        kwargs = {
            "q": query,
            "fields": fields,
            "corpora": corpora,
            "pageSize": 100,
        }
        if corpora == "allDrives":
            kwargs["includeItemsFromAllDrives"] = True
            kwargs["supportsAllDrives"] = True
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.files().list(**kwargs).execute()
        items.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return items


def list_children(
    service: Service, folder_id: str, *, corpora: str = "allDrives"
) -> list[DriveFile]:
    """List all children of a Drive folder, sorted folders-first."""
    query = f"'{escape_query_value(folder_id)}' in parents and trashed = false"
    items = paginate_files(service, query, LIST_FIELDS, corpora)
    return sorted(items, key=lambda f: (not is_folder(f), f["name"].lower()))


def list_shared_with_me(service: Service, name: str | None = None) -> list[DriveFile]:
    """List items shared with the authenticated user.

    Args:
        service: Drive API service instance.
        name: Optional name filter (server-side, case-insensitive exact match).
    """
    query = "sharedWithMe=true and trashed=false"
    if name:
        query += f" and name='{escape_query_value(name)}'"
    items = paginate_files(service, query, LIST_FIELDS, "user")
    return sorted(items, key=lambda f: (not is_folder(f), f["name"].lower()))


class WalkItem(NamedTuple):
    """One entry yielded by ``walk_tree``.

    ``file`` is the raw Drive API dict. ``ancestors`` is the chain of *raw*
    (un-sanitized) folder names from the walk root down to this entry's parent,
    root-first — each consumer joins or sanitizes it as it needs (listing joins
    raw; download sanitizes each component). ``depth`` is the 0-based nesting
    level (``== len(ancestors)``), the number listing uses for markdown indent —
    distinct from the 1-based recursion gate inside ``walk_tree``. ``descended``
    only matters for folders: ``True`` means the walk recursed into it (its
    descendants follow) and ``False`` means it hit the depth limit (no
    descendants follow); it is always ``False`` for non-folder entries.
    """

    file: DriveFile
    ancestors: tuple[str, ...]
    depth: int
    descended: bool


def walk_tree(
    service: Service,
    folder_id: str,
    *,
    depth: int | None = None,
    _ancestors: tuple[str, ...] = (),
) -> Iterator[WalkItem]:
    """Yield every descendant of a folder, folders-first, in depth-first order.

    The single depth-limited recursive walk shared by ``listing.collect`` and
    ``download``. Depth semantics match ``gdrives ls``: ``depth=1`` is flat
    (direct children only), ``depth=2`` descends one level, ``depth=None`` is
    unlimited. A folder within the depth budget is yielded with
    ``descended=True`` immediately followed by its descendants; a depth-limited
    folder is yielded with ``descended=False`` and no descendants. Folders-first
    ordering comes from ``list_children``; this function does not re-sort.
    """
    level = len(_ancestors)
    for f in list_children(service, folder_id):
        if is_folder(f):
            descend = depth is None or level + 1 < depth
            yield WalkItem(file=f, ancestors=_ancestors, depth=level, descended=descend)
            if descend:
                yield from walk_tree(
                    service,
                    f["id"],
                    depth=depth,
                    _ancestors=_ancestors + (f["name"],),
                )
        else:
            yield WalkItem(file=f, ancestors=_ancestors, depth=level, descended=False)
