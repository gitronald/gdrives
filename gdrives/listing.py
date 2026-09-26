"""Drive listing — collection, formatting, and public ls()."""

import csv
import io
import logging
import re
import sys
from dataclasses import dataclass
from html import escape
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
from gdrives.local import escape_formula, printable, write_text

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
    """Format as aligned URL + path + type + modified + owner + shared_by columns.

    Every cell goes through :func:`printable` before it is measured, so a shared
    item whose name embeds an escape sequence is shown in the terminal rather
    than obeyed, and the columns still line up.
    """
    if not rows:
        return ""
    table = [
        [
            printable(cell)
            for cell in (r.url, r.path, r.file_type, r.modified, r.owner, r.shared_by)
        ]
        for r in rows
    ]
    # Pad every column but the last to its widest cell.
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]) - 1)]
    lines = [
        "   ".join([cell.ljust(w) for cell, w in zip(row, widths)] + [row[-1]])
        for row in table
    ]
    return "\n".join(lines) + "\n"


def format_markdown(rows: list[DriveEntry]) -> str:
    """Format as nested markdown bullets with hyperlinks."""
    lines = []
    for r in rows:
        indent = "  " * r.depth
        name = re.sub(r"([\\`*_\[\]#!])", r"\\\1", escape(r.name, quote=False))
        name = name.replace("\r", " ").replace("\n", " ")
        if r.url:
            url = r.url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            lines.append(f"{indent}- [{name}]({url})")
        else:
            lines.append(f"{indent}- {name}")
    return "\n".join(lines) + "\n"


def format_csv(rows: list[DriveEntry]) -> str:
    """Format as CSV, with each cell kept from running as a spreadsheet formula.

    Names come from whoever owns a file, so a shared item named
    ``=HYPERLINK(...)`` would otherwise run when the CSV is opened in Excel or
    LibreOffice; :func:`escape_formula` prefixes such cells with ``'``.
    """
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
        row = {
            "path": r.path.removesuffix("/") if r.is_folder else r.path,
            "name": r.name,
            "type": r.file_type,
            "modified": r.modified,
            "owner": r.owner,
            "shared_by": r.shared_by,
            "url": r.url,
        }
        writer.writerow({key: escape_formula(value) for key, value in row.items()})
    return buf.getvalue()


def _render(rows: list[DriveEntry], suffix: str) -> str:
    """Render rows for a ``--save-as`` path, choosing the format by extension.

    ``.md`` renders nested markdown and ``.csv`` renders CSV, in any letter
    case; any other suffix is rejected rather than silently written as CSV, so
    every caller (not just the CLI, which pre-validates) gets the same guarantee.
    """
    if suffix.lower() == ".md":
        return format_markdown(rows)
    if suffix.lower() == ".csv":
        return format_csv(rows)
    raise ValueError(f"unsupported --save-as extension {suffix!r}: use .md or .csv")


# -- Public API --


def ls(
    folder_id: str | None = None,
    *,
    depth: int | None = None,
    save_as: list[str] | None = None,
    shared_with_me: bool = False,
    service: Service | None = None,
):
    """List Drive folder contents.

    The folder is traversed once; each path in ``save_as`` is written from that
    same collection (so ``--save-as map.md --save-as data.csv`` makes one set of
    API calls). Format is chosen per path by extension (.md vs .csv). Pass the
    ``service`` that resolved ``folder_id`` to reuse it rather than
    authenticating again.
    """
    if shared_with_me and folder_id is None:
        items = list_shared_with_me(service or build_drive_service())
        logger.info("Found %d entries", len(items))
        rows = [_entry_from_dict(f) for f in items]
    else:
        assert folder_id is not None
        rows = collect(folder_id, depth=depth, _service=service)

    if not rows:
        print("No files found", file=sys.stderr)
        return

    if save_as:
        for path in save_as:
            out = Path(path)
            write_text(out, _render(rows, out.suffix))
            print(f"Wrote {out}", file=sys.stderr)
    else:
        print(format_table(rows), end="")
