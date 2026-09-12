"""Rename and move Drive files and folders via ``files.update``.

Mirrors Unix ``mv``: the destination decides the operation.

- A destination with **no path separator** is a new name — the item is renamed
  in place and keeps its parent folder.
- A destination path that resolves to an **existing folder** is a move — the
  item keeps its name and changes parent.
- A destination path whose **parent folder exists but whose final segment does
  not** is a move *and* a rename, both in one ``files.update`` call.

``--dry-run`` resolves everything and prints the intended change without
calling ``files.update``, so it stays on the read-only default scope and never
triggers a write-scope consent. A real move needs the full ``drive`` scope
(``drive.readonly`` cannot call ``files.update``), cached in its own token file.
"""

import sys
from typing import Any

from gdrives.files import DriveFile, Service, get_file_metadata, is_folder

# files.update needs the current parents (to detach on a move) and the drive the
# item lives in (to refuse a cross-drive move); neither is in the default
# metadata fields.
MV_FIELDS = "id, name, mimeType, parents, driveId"


def get_metadata(service: Service, file_id: str) -> DriveFile:
    """Fetch the metadata ``mv`` needs for one file or folder."""
    return get_file_metadata(service, file_id, fields=MV_FIELDS)


def check_arguments(
    source: str | None,
    dest: str | None,
    source_id: str | None,
    dest_id: str | None,
    name: str | None,
) -> None:
    """Validate the source/destination argument combination.

    The by-ID flags exist to skip path resolution, so they are alternatives to
    the positional arguments rather than modifiers of them; mixing the two ways
    of naming the same thing is rejected instead of silently preferring one.
    """
    if (source is None) == (source_id is None):
        raise ValueError("pass exactly one of SOURCE or --source-id")
    if dest is not None and (dest_id or name):
        raise ValueError("DEST cannot be combined with --dest-id or --name")
    if dest is None and not (dest_id or name):
        raise ValueError("pass a DEST path, or --dest-id and/or --name")


def sole_parent(meta: DriveFile) -> str:
    """Return the item's one parent folder ID.

    Drive lets a file have several parents. A move has to name the parent it
    detaches from, and guessing could silently unfile the item from the wrong
    folder, so several parents is an error listing them all.
    """
    parents = meta.get("parents") or []
    if not parents:
        raise ValueError(
            f"'{meta['name']}' has no parent folder to move it out of "
            "(a drive root, or an item only shared with you)"
        )
    if len(parents) > 1:
        raise ValueError(
            f"'{meta['name']}' has {len(parents)} parents; mv will not guess "
            f"which to remove: {', '.join(parents)}"
        )
    return parents[0]


def check_destination(service: Service, parent_id: str, source: DriveFile) -> DriveFile:
    """Fetch the destination folder, refusing a non-folder or a cross-drive move.

    ``files.update`` cannot move an item between drives — a My Drive item and a
    shared-drive item carry different ``driveId`` values — so the mismatch is
    caught here with a clear message instead of a 403 from the API.
    """
    folder = get_metadata(service, parent_id)
    if not is_folder(folder):
        raise ValueError(f"destination '{folder['name']}' is not a folder")
    if (source.get("driveId") or "") != (folder.get("driveId") or ""):
        raise ValueError(
            f"cannot move '{source['name']}' into '{folder['name']}': "
            "files.update cannot move items between drives"
        )
    return folder


def resolve_destination(service: Service, dest: str) -> tuple[str | None, str | None]:
    """Resolve a destination path to ``(parent_id, new_name)``.

    ``parent_id`` is None when the item keeps its current folder (a pure
    rename); ``new_name`` is None when it keeps its current name (a pure move).
    A path is first tried whole as an existing folder; only if that fails is the
    final segment treated as a new name under an existing parent.
    """
    from gdrives.resolve import DrivePathError, resolve_path

    if "/" not in dest:
        return None, dest
    try:
        return resolve_path(dest, service), None
    except DrivePathError:
        parent_path, _, final = dest.rstrip("/").rpartition("/")
        if not parent_path:
            raise
        return resolve_path(parent_path, service), final


def describe(meta: DriveFile, folder: DriveFile | None, new_name: str | None) -> str:
    """Phrase the pending change for the dry-run and the result message."""
    parts = []
    if new_name:
        parts.append(f"rename '{meta['name']}' -> '{new_name}'")
    if folder:
        parts.append(f"move it into '{folder['name']}' ({folder['id']})")
    return " and ".join(parts)


def apply_move(
    service: Service,
    file_id: str,
    *,
    name: str | None = None,
    add_parent: str | None = None,
    remove_parent: str | None = None,
) -> DriveFile:
    """Send the single ``files.update`` performing the rename and/or reparent."""
    kwargs: dict[str, Any] = {
        "fileId": file_id,
        "fields": MV_FIELDS,
        "supportsAllDrives": True,
    }
    if name:
        kwargs["body"] = {"name": name}
    if add_parent:
        kwargs["addParents"] = add_parent
        kwargs["removeParents"] = remove_parent
    return service.files().update(**kwargs).execute()


def run(
    source: str | None = None,
    dest: str | None = None,
    *,
    source_id: str | None = None,
    dest_id: str | None = None,
    name: str | None = None,
    dry_run: bool = False,
) -> None:
    """Rename and/or move one Drive file or folder."""
    from gdrives.auth import DRIVE_WRITE_SCOPES, build_drive_service
    from gdrives.resolve import resolve_file_id

    check_arguments(source, dest, source_id, dest_id, name)

    # A dry run only reads, so it keeps the read-only default scope.
    service = build_drive_service(None if dry_run else DRIVE_WRITE_SCOPES)

    file_id = source_id or resolve_file_id(str(source), service)
    print(f"File ID: {file_id}", file=sys.stderr)
    meta = get_metadata(service, file_id)

    if dest is not None:
        parent_id, new_name = resolve_destination(service, dest)
    else:
        parent_id, new_name = dest_id, name

    folder: DriveFile | None = None
    remove_parent: str | None = None
    if parent_id:
        folder = check_destination(service, parent_id, meta)
        # Compare the ID the API echoed back, not the one passed in: --dest-id
        # takes aliases like "root", which never equal the item's real parent ID
        # and would send a redundant add/remove of the very same folder.
        parent_id = folder["id"]
        remove_parent = sole_parent(meta)
        if parent_id == remove_parent:
            # Already in the destination folder: a rename may still be pending.
            folder, parent_id, remove_parent = None, None, None
    if new_name == meta["name"]:
        new_name = None

    if new_name is None and parent_id is None:
        print(f"Nothing to do: '{meta['name']}' is already there, under that name")
        return

    action = describe(meta, folder, new_name)
    if dry_run:
        print(f"Would {action}")
        return

    apply_move(
        service,
        file_id,
        name=new_name,
        add_parent=parent_id,
        remove_parent=remove_parent,
    )
    print(f"Done: {action}")
