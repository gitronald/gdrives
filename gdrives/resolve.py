"""Drive path resolution — convert paths to folder/file IDs."""

import sys

from gdrives.auth import build_drive_service
from gdrives.drives import find_drive, load
from gdrives.files import (
    SPREADSHEET_MIME,
    DriveFile,
    Service,
    extract_drive_id,
    is_folder,
    list_children,
    list_shared_with_me,
    owner_email,
)
from gdrives.local import printable


class DrivePathError(Exception):
    """Raised when a Drive path cannot be resolved."""


class AmbiguousPathError(DrivePathError):
    """Raised when a path segment matches more than one Drive item.

    A distinct type because "not found" and "matched several" call for opposite
    responses: a caller may reasonably treat a missing final segment as a name
    to create (``mv`` does), but an ambiguous one must never be guessed at. It
    subclasses DrivePathError so existing handlers keep catching both.
    """


def _ambiguous_error(
    name: str, location: str, matches: list[DriveFile]
) -> DrivePathError:
    """Build the error raised when a name matches more than one Drive item.

    Drive permits duplicate names in one folder; resolution refuses to silently
    pick one. ``location`` names where the clash occurred (e.g. ``"in path"`` or
    ``"in Shared with me"``); each match is listed with its kind, id, and owner.
    """
    lines = [f"multiple items named '{name}' {location}:"]
    for m in matches:
        kind = "folder" if is_folder(m) else "file"
        owner = owner_email(m) or "unknown"
        lines.append(f"  {kind}  {m['name']}  {m['id']}  (owner: {owner})")
    return AmbiguousPathError("\n".join(lines))


def _is_blank_path(path: str) -> bool:
    """True for a path of nothing but slashes and whitespace ("", "/", " / ").

    Such a path names no drive and no item, so resolvers refuse it up front
    rather than looking for a drive named "" (or " ").
    """
    return not path.replace("/", "").strip()


def walk_segments(
    service: Service,
    folder_id: str,
    segments: list[str],
    *,
    allow_files: bool = False,
    corpora: str = "allDrives",
) -> str:
    """Walk path segments from a starting folder, returning the final ID."""
    return _walk(service, folder_id, segments, allow_files, corpora)[0]


def _walk(
    service: Service,
    folder_id: str,
    segments: list[str],
    allow_files: bool,
    corpora: str,
) -> tuple[str, DriveFile | None]:
    """Walk ``segments``, returning the final ID and the listing entry it came from.

    The entry is the child the last segment matched, with the metadata a folder
    listing carries (its ``mimeType`` among it), and None for no segments.
    """
    match: DriveFile | None = None
    for i, segment in enumerate(segments):
        children = list_children(service, folder_id, corpora=corpora)
        is_last = i == len(segments) - 1
        matches = [
            child
            for child in children
            if child["name"].lower() == segment.lower()
            and (is_folder(child) or (is_last and allow_files))
        ]
        if not matches:
            kind = "file or folder" if is_last and allow_files else "folder"
            raise DrivePathError(f"{kind} '{segment}' not found in Drive")
        if len(matches) > 1:
            raise _ambiguous_error(segment, "in path", matches)
        match = matches[0]
        folder_id = match["id"]
    return folder_id, match


def resolve_path(
    path: str, service: Service | None = None, *, allow_files: bool = False
) -> str:
    """Resolve a drive path like 'My Drive/projects' to a folder ID.

    The first segment is matched against the drive cache. Remaining
    segments are walked via the API.

    Args:
        path: Drive path (e.g. 'My Drive/projects/file.docx').
        service: Drive API service instance.
        allow_files: If True, the final segment can match files or folders.
            If False (default), all segments must be folders.
    """
    service, drive_id, parts = _drive_root(path, service)
    return walk_segments(service, drive_id, parts, allow_files=allow_files)


def _drive_root(path: str, service: Service | None) -> tuple[Service, str, list[str]]:
    """Match a path's leading drive against the cache.

    Returns the service (built read-only when none is given), the drive's ID,
    and the segments left to walk.
    """
    if _is_blank_path(path):
        # Without this, "" or "/" would look for a drive named "" and blame a
        # missing cache ("Run 'gdrives show-drives' first").
        raise DrivePathError("Drive path must not be empty")
    service = service or build_drive_service()

    # Load the drive cache once, then try progressively longer prefixes against it.
    drives = load()
    parts = path.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        entry = find_drive(drives, "/".join(parts[:i]))
        if entry:
            return service, entry["id"], parts[i:]

    raise DrivePathError(
        f"No drive matching '{parts[0]}' in cache. Run 'gdrives show-drives' first."
    )


def resolve_shared_path(
    path: str, service: Service | None = None, *, allow_files: bool = False
) -> str:
    """Resolve a path rooted in 'Shared with me' to a file/folder ID.

    The first segment is matched against sharedWithMe items. Remaining
    segments are walked via the API using corpora=user.

    Args:
        path: Path where first segment is a shared item name.
        service: Drive API service instance.
        allow_files: If True, the final segment can match files or folders.
    """
    if _is_blank_path(path):
        raise DrivePathError("Shared with me path must not be empty")
    service = service or build_drive_service()
    parts = path.strip("/").split("/")
    first_segment = parts[0]
    remaining = parts[1:]

    # Match first segment against shared-with-me items
    matches = list_shared_with_me(service, name=first_segment)
    if not matches:
        raise DrivePathError(f"'{first_segment}' not found in Shared with me")

    if len(matches) > 1:
        raise _ambiguous_error(first_segment, "in Shared with me", matches)

    item = matches[0]

    # If no remaining segments, return the matched item
    if not remaining:
        if not is_folder(item) and not allow_files:
            raise DrivePathError(f"'{first_segment}' is a file, not a folder")
        return item["id"]

    # First segment must be a folder to walk into
    if not is_folder(item):
        raise DrivePathError(f"'{first_segment}' is a file, not a folder")

    return walk_segments(
        service, item["id"], remaining, allow_files=allow_files, corpora="user"
    )


def direct_file_id(source: str) -> str | None:
    """The file ID a Drive URL or a bare ID gives, or None for a Drive path.

    The one place that tells the three forms apart, so a path is the only form
    that needs a Drive service to resolve. Raises DrivePathError for a blank
    ``source`` and ValueError for a URL that holds no ID.
    """
    if not source.strip():
        raise DrivePathError("source must not be empty")
    if source.startswith(("http://", "https://")):
        return extract_drive_id(source)
    if "/" in source:
        return None
    return source


def resolve_file_id(source: str, service: Service | None = None) -> str:
    """Resolve a Drive URL, bare file ID, or Drive path to a file ID.

    The shared front door for commands that target one file (the ``sheets-*``
    and ``docs-*`` sets): a URL or bare ID goes through ``extract_drive_id``; a
    Drive path (contains ``/``) is walked via ``resolve_path`` with files
    allowed. ``service`` is the *Drive* service used for path resolution; when
    omitted, ``resolve_path`` builds a read-only one.
    """
    file_id = direct_file_id(source)
    if file_id is None:
        return resolve_path(source, service, allow_files=True)
    return file_id


# What a type is called to a person who uploaded a file of it.
_TYPE_WORDS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        "an Excel workbook"
    ),
    "application/vnd.ms-excel": "an Excel workbook",
    "text/csv": "a CSV file",
    "application/vnd.google-apps.folder": "a folder",
    "application/vnd.google-apps.document": "a Google Doc",
    "application/vnd.google-apps.presentation": "a Google Slides file",
}


def check_spreadsheet(file: DriveFile) -> None:
    """Refuse a Drive file that is not a native Google spreadsheet.

    The Sheets API answers a workbook uploaded as-is with an error that does not
    say what is wrong with the file. ``file`` needs ``name`` and ``mimeType``; a
    file the caller resolved itself (a path walk's entry) is checked at no cost.
    A type that ``sheets-create --from`` converts
    (:data:`~gdrives.sheets.create.SOURCE_MIMES`) is refused with that way out.
    """
    mime = file.get("mimeType", "")
    if mime == SPREADSHEET_MIME:
        return
    # Imported here, since the sheets package is not needed to resolve a path.
    from gdrives.sheets.create import SOURCE_MIMES

    words = _TYPE_WORDS.get(mime)
    kind = f"{words} ({mime})" if words else f"of type {mime or 'unknown'}"
    message = f"'{printable(file['name'])}' is {kind}, not a Google spreadsheet"
    if mime in SOURCE_MIMES.values():
        message += (
            "; download it and convert the local copy with 'gdrives "
            "sheets-create --from'"
        )
    raise ValueError(message)


def resolve_spreadsheet_id(source: str, service: Service | None = None) -> str:
    """Resolve a URL, bare ID, or Drive path to a spreadsheet's file ID.

    As :func:`resolve_file_id`, and a Drive path is also checked to be a native
    spreadsheet (:func:`check_spreadsheet`). The check reads the ``mimeType``
    of the listing the last path segment was matched in, so it costs no
    request. A URL or a bare ID is not checked, since that would cost one.
    """
    file_id = direct_file_id(source)
    if file_id is not None:
        return file_id
    service, drive_id, parts = _drive_root(source, service)
    file_id, entry = _walk(service, drive_id, parts, True, "allDrives")
    # A path of a drive alone walks nothing, and names the drive.
    check_spreadsheet(
        entry
        or {
            "id": file_id,
            "name": source.strip("/"),
            "mimeType": "application/vnd.google-apps.folder",
        }
    )
    return file_id


def resolve_and_report(
    source: str,
    label: str,
    service: Service | None = None,
    *,
    spreadsheet: bool = False,
) -> str:
    """Resolve ``source`` with :func:`resolve_file_id` and echo the ID to stderr.

    With ``spreadsheet``, :func:`resolve_spreadsheet_id` resolves it instead.

    Every Sheets and Docs command opens the same way, so the resolve-then-
    announce step lives here once. ``label`` names the file kind in the
    message (``"Spreadsheet ID: ..."``, ``"Document ID: ..."``).
    """
    resolve = resolve_spreadsheet_id if spreadsheet else resolve_file_id
    file_id = resolve(source, service)
    print(f"{label} ID: {file_id}", file=sys.stderr)
    return file_id
