"""Upload a local file to Drive, replacing a file of the same name in place.

The destination decides the operation, as it does for ``mv``:

- A path that resolves to an **existing folder** uploads into it under the
  local file's name.
- A path whose **parent folder exists but whose final segment does not**
  uploads into the parent under that name.
- A path with no separator is a drive, and uploads into its root.

What the folder already holds decides the rest. One file of that name has its
content replaced (``files.update`` with a media body), so its ID and every
link to it stay the same, and its name and parents are not touched. No such
file creates one (``files.create``). Several are refused, each listed with its
ID, and ``--file-id`` names the one to replace. A Google-native file (a Doc,
Sheet, or Slides file) is refused: an upload cannot replace its content.

After the write the file's metadata is read back, and its size and MD5
checksum are compared with the local file's.

``--dry-run`` resolves everything and prints the operation without writing,
so it stays on the read-only default scope and never triggers a write-scope
consent. A real upload needs the full ``drive`` scope: ``drive.file`` reaches
only the files the app created, so it cannot replace one something else made.
"""

import hashlib
import mimetypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gdrives.files import (
    DriveFile,
    Service,
    file_url,
    find_named,
    get_file_metadata,
    is_folder,
    is_native,
)
from gdrives.local import printable

# What a replace needs to know of its target, and what the read-back compares.
UPLOAD_FIELDS = "id, name, mimeType, size, md5Checksum, webViewLink"

# The type sent for a file whose extension names none.
DEFAULT_MIME_TYPE = "application/octet-stream"

CREATE = "create"
REPLACE = "replace"

# Bytes hashed per read, so a large file is never held in memory whole.
_CHUNK = 1024 * 1024


class UploadError(Exception):
    """Raised when an uploaded file does not read back as the local file."""


@dataclass(frozen=True)
class UploadPlan:
    """One upload, decided: what is written, where, and from which bytes.

    ``operation`` is :data:`CREATE` or :data:`REPLACE`. A create has the
    ``folder`` the file goes in (None is the root of My Drive) and no
    ``file_id``; a replace has the ``file_id`` whose content is replaced, and
    its ``folder`` is None when the target was named by ID. ``name`` is the
    file's name in Drive: the one a create gives it, or the one a replaced
    file already has. ``size`` and ``md5`` are the local file's.
    """

    operation: str
    name: str
    folder: DriveFile | None
    file_id: str | None
    mime_type: str
    size: int
    md5: str


def check_arguments(
    dest: str | None,
    dest_id: str | None,
    file_id: str | None,
    name: str | None,
) -> None:
    """Validate the destination argument combination.

    DEST, ``--dest-id``, and ``--file-id`` each name the target by themselves,
    so exactly one is given. ``--name`` is the name a file takes in the folder
    ``--dest-id`` names: a DEST path carries its own, and a replace by
    ``--file-id`` never renames.
    """
    for label, value in (
        ("DEST", dest),
        ("--dest-id", dest_id),
        ("--file-id", file_id),
        ("--name", name),
    ):
        if value is not None and not value.strip():
            raise ValueError(f"{label} must not be empty")
    given = [value for value in (dest, dest_id, file_id) if value is not None]
    if len(given) != 1:
        raise ValueError("pass exactly one of DEST, --dest-id, or --file-id")
    if name is not None and dest_id is None:
        raise ValueError("--name goes with --dest-id")


def guess_mime_type(path: Path) -> str:
    """The MIME type of ``path``'s extension, or :data:`DEFAULT_MIME_TYPE`."""
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or DEFAULT_MIME_TYPE


def local_digest(path: Path) -> tuple[int, str]:
    """Return ``path``'s size in bytes and the MD5 checksum Drive reports."""
    # Drive's md5Checksum identifies content; it guards nothing.
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def check_local(local: str) -> Path:
    """Return ``local`` as a path, refusing one that is not a file."""
    path = Path(local)
    if not path.exists():
        raise ValueError(f"local file '{local}' does not exist")
    if not path.is_file():
        raise ValueError(f"'{local}' is not a file; upload takes one file per run")
    return path


def check_folder(service: Service, folder_id: str) -> DriveFile:
    """Fetch the destination folder, refusing anything that is not one."""
    folder = get_file_metadata(service, folder_id)
    if not is_folder(folder):
        raise ValueError(f"destination '{folder['name']}' is not a folder")
    return folder


def check_replaceable(target: DriveFile) -> None:
    """Refuse a target whose content an upload cannot replace."""
    if is_folder(target):
        raise ValueError(f"'{target['name']}' is a folder; upload replaces a file")
    if is_native(target):
        raise ValueError(
            f"'{target['name']}' ({target['id']}) is a Google-native file "
            f"({target['mimeType']}); an upload cannot replace its content"
        )


def _several(name: str, folder: DriveFile, matches: list[DriveFile]) -> ValueError:
    """The refusal for a name that several files in the folder share."""
    lines = [
        f"{len(matches)} files named '{name}' in '{folder['name']}'; upload "
        "will not guess which to replace. Pass --file-id with one of:"
    ]
    lines.extend(f"  {m['name']}  {m['id']}" for m in matches)
    return ValueError("\n".join(lines))


def plan_upload(
    service: Service,
    path: Path,
    *,
    folder_id: str | None = None,
    name: str | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
) -> UploadPlan:
    """Decide what uploading ``path`` does, reading Drive and writing nothing.

    ``file_id`` names the file to replace. Otherwise ``folder_id`` is the
    folder and ``name`` the file's name in it (the local file's by default),
    and what the folder holds under that name decides: one file is replaced,
    none is created, and several raise ValueError listing each.
    """
    if (folder_id is None) == (file_id is None):
        raise ValueError("pass exactly one of folder_id or file_id")
    size, md5 = local_digest(path)
    mime = mime_type or guess_mime_type(path)

    if file_id is not None:
        target = get_file_metadata(service, file_id, fields=UPLOAD_FIELDS)
        check_replaceable(target)
        return UploadPlan(REPLACE, target["name"], None, target["id"], mime, size, md5)

    folder = check_folder(service, str(folder_id))
    name = name or path.name
    matches = find_named(service, folder["id"], name, fields=UPLOAD_FIELDS)
    if len(matches) > 1:
        raise _several(name, folder, matches)
    if matches:
        (target,) = matches
        check_replaceable(target)
        return UploadPlan(
            REPLACE, target["name"], folder, target["id"], mime, size, md5
        )
    return UploadPlan(CREATE, name, folder, None, mime, size, md5)


def describe(plan: UploadPlan) -> str:
    """Phrase the upload for the dry run and the result message.

    Names pass through :func:`printable`, so a file named with an escape
    sequence can't rewrite the terminal the message is printed to.
    """
    name = printable(plan.name)
    where = ""
    if plan.folder is not None:
        where = f" in '{printable(plan.folder['name'])}' ({plan.folder['id']})"
    size = f"{plan.size} bytes, {plan.mime_type}"
    if plan.operation == REPLACE:
        return f"replace the content of '{name}' ({plan.file_id}){where}: {size}"
    return f"create '{name}'{where}: {size}"


def _send(request: Any) -> DriveFile:
    """Run a resumable upload request to its end, chunk by chunk."""
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def apply_upload(service: Service, path: Path, plan: UploadPlan) -> DriveFile:
    """Send the upload ``plan`` describes and return the file's metadata.

    The upload is resumable, so a large file survives a dropped connection.
    A replace sends no metadata: the file keeps its name and its parents.
    """
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype=plan.mime_type, resumable=True)
    kwargs: dict[str, Any] = {
        "media_body": media,
        "fields": UPLOAD_FIELDS,
        "supportsAllDrives": True,
    }
    if plan.operation == REPLACE:
        request = service.files().update(fileId=plan.file_id, **kwargs)
    else:
        body: dict[str, Any] = {"name": plan.name}
        if plan.folder is not None:
            body["parents"] = [plan.folder["id"]]
        request = service.files().create(body=body, **kwargs)
    return _send(request)


def verify(service: Service, file_id: str, plan: UploadPlan) -> DriveFile:
    """Read the file back and compare its size and checksum with the local file's.

    Raises UploadError naming each that differs. The file is in Drive either
    way, so the error says which file to look at.
    """
    meta = get_file_metadata(service, file_id, fields=UPLOAD_FIELDS)
    problems = []
    # The API sends size as a string, and omits both for a file with no content.
    if int(meta.get("size") or 0) != plan.size:
        problems.append(f"size {meta.get('size')} (local {plan.size})")
    if (meta.get("md5Checksum") or "") != plan.md5:
        problems.append(f"md5 {meta.get('md5Checksum')} (local {plan.md5})")
    if problems:
        raise UploadError(
            f"'{meta['name']}' ({file_id}) does not read back as the local "
            f"file: {', '.join(problems)}"
        )
    return meta


def upload_file(
    service: Service,
    local: str | Path,
    *,
    folder_id: str | None = None,
    name: str | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
) -> DriveFile:
    """Upload ``local``, replacing the file of its name in place or creating it.

    The library's one call: plan, write, and read back. Returns the file's
    metadata (:data:`UPLOAD_FIELDS`). See :func:`plan_upload` for what decides
    the operation, and :func:`verify` for the read-back.
    """
    path = check_local(str(local))
    plan = plan_upload(
        service,
        path,
        folder_id=folder_id,
        name=name,
        file_id=file_id,
        mime_type=mime_type,
    )
    written = apply_upload(service, path, plan)
    return verify(service, written["id"], plan)


def run(
    local: str,
    dest: str | None = None,
    *,
    dest_id: str | None = None,
    file_id: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    dry_run: bool = False,
) -> None:
    """Upload one local file to Drive, printing its URL."""
    from gdrives.auth import DRIVE_WRITE_SCOPES, build_drive_service
    from gdrives.mv import resolve_folder

    check_arguments(dest, dest_id, file_id, name)
    if mime_type is not None and not mime_type.strip():
        raise ValueError("--mime-type must not be empty")
    path = check_local(local)

    # A dry run only reads, so it keeps the read-only default scope.
    service = build_drive_service(None if dry_run else DRIVE_WRITE_SCOPES)

    folder_id = dest_id
    if dest is not None:
        folder_id, name = resolve_folder(service, dest)
    plan = plan_upload(
        service,
        path,
        folder_id=folder_id,
        name=name,
        file_id=file_id,
        mime_type=mime_type,
    )

    action = describe(plan)
    if dry_run:
        print(f"Would {action}")
        return

    written = apply_upload(service, path, plan)
    print(f"File ID: {written['id']}", file=sys.stderr)
    meta = verify(service, written["id"], plan)
    print(f"Done: {action}", file=sys.stderr)
    print(file_url(meta))
