"""Drive path resolution — convert paths to folder/file IDs."""

from gdrives.auth import build_drive_service
from gdrives.drives import resolve_name
from gdrives.files import (
    Service,
    is_folder,
    list_children,
    list_shared_with_me,
    owner_email,
)


class DrivePathError(Exception):
    """Raised when a Drive path cannot be resolved."""


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
            # Drive permits duplicate names in one folder; refuse to silently
            # pick one (mirrors the ambiguity error in resolve_shared_path).
            lines = [f"multiple items named '{segment}' in path:"]
            for m in matches:
                kind = "folder" if is_folder(m) else "file"
                owner = owner_email(m) or "unknown"
                lines.append(f"  {kind}  {m['name']}  {m['id']}  (owner: {owner})")
            raise DrivePathError("\n".join(lines))
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

    # Try progressively longer prefixes against the cache
    parts = path.strip("/").split("/")
    drive_entry = None
    split_idx = 0
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i])
        entry = resolve_name(candidate)
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
        lines = [f"multiple items named '{first_segment}' in Shared with me:"]
        for m in matches:
            kind = "folder" if is_folder(m) else "file"
            owner = owner_email(m) or "unknown"
            lines.append(f"  {kind}  {m['name']}  {m['id']}  (owner: {owner})")
        raise DrivePathError("\n".join(lines))

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
