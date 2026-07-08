"""Drive path resolution — convert paths to folder/file IDs."""

from gdrives.auth import build_drive_service
from gdrives.drives import find_drive, load
from gdrives.files import (
    DriveFile,
    Service,
    is_folder,
    list_children,
    list_shared_with_me,
    owner_email,
)


class DrivePathError(Exception):
    """Raised when a Drive path cannot be resolved."""


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
    return DrivePathError("\n".join(lines))


def walk_segments(
    service: Service,
    folder_id: str,
    segments: list[str],
    *,
    allow_files: bool = False,
    corpora: str = "allDrives",
) -> str:
    """Walk path segments from a starting folder, returning the final ID."""
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
        folder_id = matches[0]["id"]
    return folder_id


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
    service = service or build_drive_service()

    # Load the drive cache once, then try progressively longer prefixes against it.
    drives = load()
    parts = path.strip("/").split("/")
    drive_entry = None
    split_idx = 0
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i])
        entry = find_drive(drives, candidate)
        if entry:
            drive_entry = entry
            split_idx = i
            break

    if not drive_entry:
        raise DrivePathError(
            f"No drive matching '{parts[0]}' in cache. Run 'gdrives show-drives' first."
        )

    return walk_segments(
        service, drive_entry["id"], parts[split_idx:], allow_files=allow_files
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
