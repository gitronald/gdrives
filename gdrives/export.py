"""Export a Google Doc, Sheet, or Slides to Office/text formats via the Drive API."""

import re
import sys
from pathlib import Path

from gdrives.files import Service, extract_drive_id
from gdrives.local import atomic_output, line_ending

_OOXML = "application/vnd.openxmlformats-officedocument"

# Single source of truth for Google-native exports: type label -> (extension, MIME).
# download.py derives its label->extension map from this; EXPORT_MIME_TYPES derives
# the extension->MIME map below. Add a new exportable native type here only.
NATIVE_EXPORTS = {
    "gdoc": (".docx", f"{_OOXML}.wordprocessingml.document"),
    "gsheet": (".xlsx", f"{_OOXML}.spreadsheetml.sheet"),
    "gslides": (".pptx", f"{_OOXML}.presentationml.presentation"),
}

# Extension -> export MIME type. Derived from NATIVE_EXPORTS, plus the type-specific
# text formats: CSV (Sheets only) and plain text / Markdown (Docs only). These are
# reachable via an explicit `-o file.<ext>`, never via folder auto-export.
EXPORT_MIME_TYPES = {ext: mime for ext, mime in NATIVE_EXPORTS.values()}
EXPORT_MIME_TYPES[".csv"] = "text/csv"
EXPORT_MIME_TYPES[".txt"] = "text/plain"
EXPORT_MIME_TYPES[".md"] = "text/markdown"


# The exports that are text, whose line endings --newline can rewrite.
TEXT_EXTENSIONS = frozenset({".csv", ".txt", ".md"})

# A CSV row ending outside a quoted cell. A quote opens a cell only at the start
# of one (the file's start, or after a comma or a line ending), the same rule the
# csv module reads by, and a doubled quote inside a cell is part of it; a cell
# left open at the end of the file runs to it. A line ending is CRLF, LF, or a
# lone CR, as the csv module reads them, so a conversion never merges a CR with
# the LF after it. A byte-order mark is no part of the first cell, so the scan
# starts after one.
_CSV_SCAN = re.compile(
    rb'(?P<cell>(?<![^,\r\n])"[^"]*(?:""[^"]*)*(?:"|\Z))|(?P<end>\r\n|\r|\n)'
)
_TEXT_END = re.compile(rb"\r\n|\r|\n")
_BOM = b"\xef\xbb\xbf"


def mime_for_output(output_path: str) -> str:
    """Return the export MIME type for the given output path's extension."""
    suffix = Path(output_path).suffix.lower()
    try:
        return EXPORT_MIME_TYPES[suffix]
    except KeyError:
        supported = ", ".join(sorted(EXPORT_MIME_TYPES))
        raise ValueError(
            f"Unsupported output extension {suffix!r}. Supported: {supported}"
        )


def set_line_endings(content: bytes, extension: str, newline: str) -> bytes:
    """Rewrite the line endings of a text export to ``newline`` (``lf`` or ``crlf``).

    Works on the bytes Drive sent, so a file that is not valid UTF-8 is not
    corrupted or refused. A line ending is ``\\r\\n``, ``\\n``, or a lone ``\\r``,
    as Python's universal newlines read them (Drive sends no lone ``\\r``).
    In a ``.txt`` or ``.md`` file every line ending is rewritten. In a ``.csv``
    file a line break inside a quoted cell is part of the cell's value and
    stays as the cell holds it: only the row endings change, and which cells
    are quoted, and every other byte, stay as they were. A UTF-8 byte-order
    mark at the start of the file is kept, and the first cell starts after it.
    """
    ending = line_ending(newline).encode()
    if extension.lower() != ".csv":
        return _TEXT_END.sub(ending, content)

    def row_ending(match: re.Match[bytes]) -> bytes:
        return ending if match.group("end") else match.group("cell")

    mark = _BOM if content.startswith(_BOM) else b""
    return mark + _CSV_SCAN.sub(row_ending, content[len(mark) :])


def check_newline(output_path: str, newline: str | None) -> None:
    """Refuse a ``newline`` that is not one, or that goes with a binary export."""
    if newline is None:
        return
    line_ending(newline)
    suffix = Path(output_path).suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        raise ValueError(
            f"newline applies to a text export ({', '.join(sorted(TEXT_EXTENSIONS))}), "
            f"not {suffix!r}"
        )


def export_file(
    service: Service, file_id: str, output_path: str, newline: str | None = None
):
    """Export a Doc, Sheet, or Slides to the format implied by output_path.

    ``newline`` (``"lf"`` or ``"crlf"``) rewrites the line endings of a text
    export (``.csv``, ``.txt``, ``.md``) before the file appears under its name;
    None leaves the bytes as Drive sent them. It is refused, before any request,
    for a binary format and for any other value.
    """
    mime = mime_for_output(output_path)
    check_newline(output_path, newline)
    content = service.files().export(fileId=file_id, mimeType=mime).execute()
    if newline is not None:
        content = set_line_endings(content, Path(output_path).suffix, newline)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(out) as f:
        f.write(content)
    print(f"Exported to {output_path} ({len(content)} bytes)")


def run(source: str, output: str, newline: str | None = None):
    """Export a Doc (.docx/.txt/.md), Sheet (.xlsx/.csv), or Slides (.pptx) file."""
    from gdrives.auth import build_drive_service

    check_newline(output, newline)
    file_id = extract_drive_id(source)
    print(f"File ID: {file_id}", file=sys.stderr)

    service = build_drive_service()
    export_file(service, file_id, output, newline=newline)
