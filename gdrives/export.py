"""Export a Google Doc, Sheet, or Slides to Office/CSV via the Drive API."""

import sys
from pathlib import Path

from gdrives.files import Service, extract_drive_id

_OOXML = "application/vnd.openxmlformats-officedocument"

# Single source of truth for Google-native exports: type label -> (extension, MIME).
# download.py derives its label->extension map from this; EXPORT_MIME_TYPES derives
# the extension->MIME map below. Add a new exportable native type here only.
NATIVE_EXPORTS = {
    "gdoc": (".docx", f"{_OOXML}.wordprocessingml.document"),
    "gsheet": (".xlsx", f"{_OOXML}.spreadsheetml.sheet"),
    "gslides": (".pptx", f"{_OOXML}.presentationml.presentation"),
}

# Extension -> export MIME type. Derived from NATIVE_EXPORTS, plus CSV (Sheets only,
# reachable via an explicit `-o file.csv`, never via folder auto-export).
EXPORT_MIME_TYPES = {ext: mime for ext, mime in NATIVE_EXPORTS.values()}
EXPORT_MIME_TYPES[".csv"] = "text/csv"


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


def export_file(service: Service, file_id: str, output_path: str):
    """Export a Doc, Sheet, or Slides to the format implied by output_path."""
    mime = mime_for_output(output_path)
    content = service.files().export(fileId=file_id, mimeType=mime).execute()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    print(f"Exported to {output_path} ({len(content)} bytes)")


def run(source: str, output: str):
    """Export a Google Doc, Sheet, or Slides file to .docx, .xlsx, .pptx, or .csv."""
    from gdrives.auth import build_drive_service

    file_id = extract_drive_id(source)
    print(f"File ID: {file_id}", file=sys.stderr)

    service = build_drive_service()
    export_file(service, file_id, output)
