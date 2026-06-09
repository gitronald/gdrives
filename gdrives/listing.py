"""Drive listing — collection, formatting, and public ls()."""

import csv
import io
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from gdrives.auth import build_drive_service
from gdrives.files import (
    DriveFile,
    Service,
    file_type,
    file_url,
    is_folder,
    list_shared_with_me,
    modified_date,
    owner_email,
    shared_by,
    walk_tree,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DriveEntry:
    url: str
    path: str
    name: str
    file_type: str
    modified: str
    owner: str
    shared_by: str = ""
    depth: int = 0

    @property
    def is_folder(self) -> bool:
        return self.file_type == "folder"


def _entry_from_dict(f: DriveFile, *, prefix: str = "", depth: int = 0) -> DriveEntry:
    """Build a DriveEntry from a Drive API file dict.

    Folders get a trailing ``/`` on their display name; ``prefix`` nests the
    path under a parent folder (empty for top-level / shared-with-me entries).
    ``depth`` is the 0-based nesting level used for markdown indentation, so a
    name that itself contains ``/`` doesn't inflate the rendered depth.
    """
    name = f["name"]
    display = name + "/" if is_folder(f) else name
    return DriveEntry(
        url=file_url(f),
        path=f"{prefix}{display}" if prefix else display,
        name=name,
        file_type=file_type(f),
        modified=modified_date(f),
        owner=owner_email(f),
        shared_by=shared_by(f),
        depth=depth,
    )


def collect(
    folder_id: str,
    *,
    depth: int | None = None,
    _service: Service | None = None,
) -> list[DriveEntry]:
    """Collect Drive folder contents recursively (folders-first, depth-first).

    Maps the shared ``walk_tree`` generator into ``DriveEntry`` rows: each
    entry's ``prefix`` is its ancestor folder names joined raw (so a name
    containing ``/`` doesn't inflate the rendered depth), and its ``depth`` is
    the walk's 0-based nesting level.
    """
    service = _service or build_drive_service()
    items = list(walk_tree(service, folder_id, depth=depth))
    logger.info("Found %d entries", sum(1 for it in items if it.depth == 0))
    return [
        _entry_from_dict(
            it.file,
            prefix="".join(f"{name}/" for name in it.ancestors),
            depth=it.depth,
        )
        for it in items
    ]


# -- Output formats --


def format_table(rows: list[DriveEntry]) -> str:
    """Format as aligned URL + path + type + modified + owner + shared_by columns."""
    if not rows:
        return ""
    max_url = max(len(r.url) for r in rows)
    max_path = max(len(r.path) for r in rows)
    max_type = max(len(r.file_type) for r in rows)
    max_owner = max(len(r.owner) for r in rows)
    lines = [
        f"{r.url:<{max_url}}   {r.path:<{max_path}}"
        f"   {r.file_type:<{max_type}}   {r.modified}"
        f"   {r.owner:<{max_owner}}   {r.shared_by}"
        for r in rows
    ]
    return "\n".join(lines) + "\n"


def format_markdown(rows: list[DriveEntry]) -> str:
    """Format as nested markdown bullets with hyperlinks."""
    lines = []
    for r in rows:
        indent = "  " * r.depth
        if r.url:
            lines.append(f"{indent}- [{r.name}]({r.url})")
        else:
            lines.append(f"{indent}- {r.name}")
    return "\n".join(lines) + "\n"


def format_csv(rows: list[DriveEntry]) -> str:
    """Format as CSV."""
    buf = io.StringIO()
    fieldnames = [
        "path",
        "name",
        "type",
        "modified",
        "owner",
        "shared_by",
        "url",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "path": r.path.rstrip("/"),
                "name": r.name,
                "type": r.file_type,
                "modified": r.modified,
                "owner": r.owner,
                "shared_by": r.shared_by,
                "url": r.url,
            }
        )
    return buf.getvalue()


# -- Public API --


def ls(
    folder_id: str | None = None,
    *,
    depth: int | None = None,
    save_as: list[str] | None = None,
    shared_with_me: bool = False,
):
    """List Drive folder contents.

    The folder is traversed once; each path in ``save_as`` is written from that
    same collection (so ``--save-as map.md --save-as data.csv`` makes one set of
    API calls). Format is chosen per path by extension (.md vs .csv).
    """
    if shared_with_me and folder_id is None:
        service = build_drive_service()
        items = list_shared_with_me(service)
        logger.info("Found %d entries", len(items))
        rows = [_entry_from_dict(f) for f in items]
    else:
        assert folder_id is not None
        rows = collect(folder_id, depth=depth)

    if not rows:
        print("No files found", file=sys.stderr)
        return

    if save_as:
        for path in save_as:
            out = Path(path)
            text = format_markdown(rows) if out.suffix == ".md" else format_csv(rows)
            out.write_text(text, encoding="utf-8")
            print(f"Wrote {out}", file=sys.stderr)
    else:
        print(format_table(rows), end="")
