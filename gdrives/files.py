"""Drive API wrappers and file helpers."""

import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

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

# Fields requested from files.list — see docs/drive-api.md. incompleteSearch is
# how the API flags a page that may be missing results (see paginate_files).
LIST_FIELDS = (
    "nextPageToken, incompleteSearch, "
    "files(id, name, mimeType, size, webViewLink, "
    "modifiedTime, owners(displayName, emailAddress), "
    "sharingUser(displayName, emailAddress))"
)

# files.list's largest page: a 5,000-item folder takes 5 calls instead of 50.
PAGE_SIZE = 1000


class IncompleteSearchError(Exception):
    """Raised when files.list reports that some results may be missing."""


def strip_url_suffix(url: str) -> str:
    """Remove query params and trailing /edit or /view from Drive URLs.

    Preserves query params for non-Drive URLs (e.g., Google Maps ?mid=).
    """
    parsed = urlsplit(url)
    if parsed.hostname not in {"drive.google.com", "docs.google.com"}:
        return url
    path = parsed.path.removesuffix("/edit").removesuffix("/view")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", parsed.fragment))


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
    return f.get("mimeType") == "application/vnd.google-apps.folder"


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
    extension = Path(f["name"]).suffix.lstrip(".")
    # Keep ordinary extensions distinct from the reserved Workspace labels.
    if extension in GOOGLE_MIME_TYPES.values():
        return "." + extension
    return extension or "unknown"


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


def _folders_first(f: DriveFile) -> tuple[bool, str, str, str]:
    """Sort key placing folders before files, then case-insensitively by name.

    Ties fall back to the exact name and then the file ID, so items that share a
    name (Drive allows duplicates) come back in the same order on every call:
    files.list promises no order, and ``download --skip-existing`` relies on a
    rerun seeing duplicates in the order the first run did.
    """
    return (not is_folder(f), f["name"].lower(), f["name"], f["id"])


def paginate_files(
    service: Service, query: str, fields: str, corpora: str
) -> list[DriveFile]:
    """Paginate through a files.list query and return all results.

    Drive marks a page ``incompleteSearch`` when it could not search every drive
    in the corpus, which an ``allDrives`` query can hit, so results may be
    missing. A listing short of items would pass for complete: an ``ls`` that
    leaves files out, a ``download`` that skips them, or a "not found" that
    ``mv`` takes for a new name. So that raises IncompleteSearchError instead.
    ``fields`` must request ``incompleteSearch``, as :data:`LIST_FIELDS` does.
    """
    items: list[DriveFile] = []
    page_token = None
    while True:
        kwargs = {
            "q": query,
            "fields": fields,
            "corpora": corpora,
            "pageSize": PAGE_SIZE,
        }
        if corpora == "allDrives":
            kwargs["includeItemsFromAllDrives"] = True
            kwargs["supportsAllDrives"] = True
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.files().list(**kwargs).execute()
        if results.get("incompleteSearch"):
            raise IncompleteSearchError(
                "Drive reported an incomplete search, so this listing may be "
                "missing items; nothing was done with it"
            )
        items.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return items


def get_file_metadata(
    service: Service, file_id: str, *, fields: str = "id, name, mimeType"
) -> DriveFile:
    """Fetch metadata for one Drive file or folder.

    Defaults to the id, name, and mimeType that callers dispatching on file kind
    need; ``fields`` widens it (``mv`` also asks for ``parents`` and ``driveId``).
    Includes supportsAllDrives so IDs in shared drives resolve.
    """
    return (
        service.files()
        .get(fileId=file_id, fields=fields, supportsAllDrives=True)
        .execute()
    )


def list_children(
    service: Service, folder_id: str, *, corpora: str = "allDrives"
) -> list[DriveFile]:
    """List all children of a Drive folder, sorted folders-first."""
    query = f"'{escape_query_value(folder_id)}' in parents and trashed = false"
    items = paginate_files(service, query, LIST_FIELDS, corpora)
    return sorted(items, key=_folders_first)


def get_folder(service: Service, folder_id: str) -> DriveFile:
    """Fetch the folder a write goes in, named by its ID.

    Refuses what is not a folder, and a folder in the trash: a file written
    there is one nobody sees. A folder found by its path needs no such check,
    since a listing leaves out what is trashed.
    """
    folder = get_file_metadata(service, folder_id, fields="id, name, mimeType, trashed")
    if not is_folder(folder):
        raise ValueError(f"destination '{folder['name']}' is not a folder")
    if folder.get("trashed"):
        raise ValueError(
            f"destination '{folder['name']}' ({folder['id']}) is in the trash"
        )
    return folder


def _by_id(f: DriveFile) -> str:
    """Sort key: files.list promises no order, and a message lists the same one."""
    return f["id"]


def find_named(
    service: Service,
    folder_id: str,
    name: str,
    *,
    fields: str = "id, name, mimeType",
) -> list[DriveFile]:
    """List the files named ``name`` in a folder, ordered by ID.

    Names compare without regard to case, as path resolution compares them.
    A folder of that name is not a match: it is not a file, and Drive lets a
    file share its name. ``fields`` names what is asked of each file.
    """
    query = (
        f"'{escape_query_value(folder_id)}' in parents "
        f"and name = '{escape_query_value(name)}' and trashed = false"
    )
    listed = f"nextPageToken, incompleteSearch, files({fields})"
    found = paginate_files(service, query, listed, "allDrives")
    matches = [
        f for f in found if f["name"].lower() == name.lower() and not is_folder(f)
    ]
    return sorted(matches, key=_by_id)


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
    return sorted(items, key=_folders_first)


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
    ``cycle`` marks the other reason a folder is not descended into: it is one
    of its own ancestors.
    """

    file: DriveFile
    ancestors: tuple[str, ...]
    depth: int
    descended: bool
    cycle: bool = False


def walk_tree(
    service: Service,
    folder_id: str,
    *,
    depth: int | None = None,
    _ancestors: tuple[str, ...] = (),
    _ancestor_ids: frozenset[str] = frozenset(),
) -> Iterator[WalkItem]:
    """Yield every descendant of a folder, folders-first, in depth-first order.

    The single depth-limited recursive walk shared by ``listing.collect`` and
    ``download``. Depth semantics match ``gdrives ls``: ``depth=1`` is flat
    (direct children only), ``depth=2`` descends one level, ``depth=None`` is
    unlimited. A folder within the depth budget is yielded with
    ``descended=True`` immediately followed by its descendants; a depth-limited
    folder is yielded with ``descended=False`` and no descendants. Folders-first
    ordering comes from ``list_children``; this function does not re-sort.

    A folder that is one of its own ancestors (a cycle, which legacy
    multi-parent items make possible) is yielded without being descended into,
    with a warning, so an unlimited walk always ends.
    """
    if depth is not None and depth < 1:
        raise ValueError("depth must be at least 1")
    level = len(_ancestors)
    path_ids = _ancestor_ids | {folder_id}
    for f in list_children(service, folder_id):
        if is_folder(f):
            descend = depth is None or level + 1 < depth
            cycle = descend and f["id"] in path_ids
            if cycle:
                logger.warning(
                    "folder %r (%s) contains itself; not descending into it again",
                    f["name"],
                    f["id"],
                )
                descend = False
            yield WalkItem(
                file=f,
                ancestors=_ancestors,
                depth=level,
                descended=descend,
                cycle=cycle,
            )
            if descend:
                yield from walk_tree(
                    service,
                    f["id"],
                    depth=depth,
                    _ancestors=_ancestors + (f["name"],),
                    _ancestor_ids=path_ids,
                )
        else:
            yield WalkItem(file=f, ancestors=_ancestors, depth=level, descended=False)
