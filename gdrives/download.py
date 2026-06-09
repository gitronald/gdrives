"""Download a Drive file or folder to a local directory.

A source that resolves to a single file downloads straight to output_dir
(no scan or prompt). A folder is scanned, summarized, and confirmed first.
Both honor the same per-entry rules below.

Recurses by default to unlimited depth; pass depth=N (CLI: --depth N) to cap.
Depth semantics match `gdrives ls`: depth=1 means flat (current folder only),
depth=2 includes one level of subfolders, and so on. Binary files download
directly. Google-native files (Docs, Sheets, Slides) auto-export to
.docx/.xlsx/.pptx; other native types (Forms, Drawings, etc.) are skipped
with a warning.

Filename collisions
-------------------
Drive allows two files with identical names in the same folder (e.g. Form
response uploads from different submitters). The Drive Web UI dedups these
for display by appending ' (1)', ' (2)', etc., but the API returns the raw
stored name — so we see two identical strings when listing. On a real local
collision, `unique_path` adds a ' (N)' suffix to mirror Drive's display
convention. A '(N)' you see in an *API-returned* name was baked into the
filename by the uploader (or by Google Forms), not added by Drive's UI or
by us.
"""

import re
import sys
from pathlib import Path

import typer
from googleapiclient.http import MediaIoBaseDownload

from gdrives.export import NATIVE_EXPORTS, export_file
from gdrives.files import (
    DriveFile,
    Service,
    WalkItem,
    extract_drive_id,
    file_type,
    is_folder,
    is_native,
    walk_tree,
)

# Map Google-native type label -> local extension, derived from the canonical
# export table (export.NATIVE_EXPORTS) so download and `gdrives export` never drift.
NATIVE_EXPORT_EXT = {label: ext for label, (ext, _mime) in NATIVE_EXPORTS.items()}


def safe_filename(name: str) -> str:
    """Sanitize a Drive file name for use on the local filesystem.

    Replaces both path separators (``/`` and ``\\``) and NUL, then neutralizes
    the ``.``/``..`` dot segments so a Drive entry named ``..`` can't escape the
    target directory (``out / ".."`` would otherwise resolve to its parent).
    Backslash is replaced too so a name like ``..\\..\\evil`` can't traverse on
    Windows, where ``\\`` is a separator. Legitimate dotfiles like ``.env`` are
    preserved.
    """
    cleaned = re.sub(r"[/\\\x00]", "_", name).strip()
    if cleaned in {".", ".."}:
        cleaned = cleaned.replace(".", "_")  # "." -> "_", ".." -> "__"
    return cleaned or "file"


def _ensure_dir(out: Path) -> None:
    """Create directory ``out``, with a clear error if a file occupies its path."""
    if out.exists() and not out.is_dir():
        raise NotADirectoryError(
            f"cannot create folder '{out}': a file with that name exists"
        )
    out.mkdir(parents=True, exist_ok=True)


def format_bytes(n: int) -> str:
    """Convert byte count to a human-readable string."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def classify_entry(f: DriveFile) -> str:
    """Return the download disposition of a non-folder Drive entry.

    ``"binary"`` downloads via get_media; ``"export"`` is a Google-native file
    with an export format (Doc/Sheet/Slides); ``"skip"`` is a Google-native file
    with no export format (Forms, Drawings, etc.). Shared by the summary counter
    and ``download_entry`` so the two never disagree on what an entry is.
    """
    if is_native(f):
        return "export" if file_type(f) in NATIVE_EXPORT_EXT else "skip"
    return "binary"


def summarize(items: list[WalkItem]) -> dict[str, int]:
    """Tally a materialized ``walk_tree`` into the counts ``print_summary`` shows.

    Counts each entry once from the same list the download pass consumes, so the
    summary can't drift from what is actually fetched.
    """
    summary = {
        "binary_files": 0,
        "binary_bytes": 0,
        "auto_export": 0,
        "skipped_natives": 0,
        "subfolders": 0,
        "skipped_subfolders": 0,
    }
    for item in items:
        f = item.file
        if is_folder(f):
            if item.descended:
                summary["subfolders"] += 1
            else:
                summary["skipped_subfolders"] += 1
            continue

        disposition = classify_entry(f)
        if disposition == "export":
            summary["auto_export"] += 1
        elif disposition == "skip":
            summary["skipped_natives"] += 1
        else:
            summary["binary_files"] += 1
            try:
                summary["binary_bytes"] += int(f.get("size") or 0)
            except (ValueError, TypeError):
                pass
    return summary


def print_summary(summary: dict[str, int], output_dir: str) -> None:
    """Print a download plan summary to stderr."""
    print(f"\nDownload plan for {output_dir}/:", file=sys.stderr)
    print(
        f"  {summary['binary_files']:>4} binary file(s) "
        f"({format_bytes(summary['binary_bytes'])})",
        file=sys.stderr,
    )
    if summary["auto_export"]:
        print(
            f"  {summary['auto_export']:>4} Google Doc/Sheet/Slides to auto-export",
            file=sys.stderr,
        )
    if summary["skipped_natives"]:
        print(
            f"  {summary['skipped_natives']:>4} native file(s) skipped "
            "(Forms, Drawings, etc.)",
            file=sys.stderr,
        )
    if summary["subfolders"]:
        print(
            f"  {summary['subfolders']:>4} subfolder(s) to create",
            file=sys.stderr,
        )
    if summary["skipped_subfolders"]:
        print(
            f"  {summary['skipped_subfolders']:>4} subfolder(s) skipped (depth limit)",
            file=sys.stderr,
        )


def unique_path(target: Path) -> Path:
    """If target exists, append ' (1)', ' (2)', ... before the extension until unique.

    Mirrors Google Drive Web UI's display-time dedup convention so that locally
    disambiguated names look the way Drive would have rendered them.
    """
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def get_file_metadata(service: Service, file_id: str) -> DriveFile:
    """Fetch id, name, and mimeType for a Drive file or folder.

    Includes supportsAllDrives so IDs in shared drives resolve.
    """
    return (
        service.files()
        .get(fileId=file_id, fields="id, name, mimeType", supportsAllDrives=True)
        .execute()
    )


def download_file(service: Service, file_id: str, output_path: str) -> int:
    """Download a binary Drive file to output_path. Returns bytes written.

    Streams chunks straight to a temporary ``.part`` file and renames it into
    place, so memory stays bounded regardless of file size. A failed download
    removes the partial ``.part`` file and leaves nothing at the final path.
    """
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    target = Path(output_path)
    tmp = target.with_name(target.name + ".part")
    try:
        with tmp.open("wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        tmp.replace(target)
    finally:
        # On success tmp was renamed away (no-op); on failure drop the partial.
        tmp.unlink(missing_ok=True)
    return target.stat().st_size


def download_entry(service: Service, f: DriveFile, out: Path) -> None:
    """Download one non-folder Drive entry into the existing directory `out`.

    Google-native Docs/Sheets/Slides auto-export to .docx/.xlsx/.pptx; other
    native types (Forms, Drawings, etc.) are skipped with a warning. Binary
    files download via get_media. Names that collide locally get a ' (N)' suffix.
    """
    name = safe_filename(f["name"])
    disposition = classify_entry(f)

    if disposition == "skip":
        print(f"  skip {file_type(f)} (no export format): {name}", file=sys.stderr)
        return

    if disposition == "export":
        ext = NATIVE_EXPORT_EXT[file_type(f)]
        target = unique_path(out / f"{name}{ext}")
        export_file(service, f["id"], str(target))
        return

    target = unique_path(out / name)
    size = download_file(service, f["id"], str(target))
    print(f"  {target} ({size} bytes)")


def download_walk(service: Service, items: list[WalkItem], output_dir: str) -> None:
    """Download a materialized ``walk_tree`` into output_dir, depth-first.

    Consumes the same list ``summarize`` counted, so the download can't diverge
    from the summary. Each item's local parent directory is ``output_dir`` joined
    with its sanitized ancestor names; a within-depth folder creates its
    subdirectory (even when empty) and prints ``-> subdir/``, while a
    depth-limited folder prints a skip notice. Messages fire here, at download
    time, in the walk's depth-first order.
    """
    out = Path(output_dir)
    _ensure_dir(out)

    for item in items:
        parent = out.joinpath(*(safe_filename(a) for a in item.ancestors))
        f = item.file
        if is_folder(f):
            name = safe_filename(f["name"])
            if item.descended:
                subdir = parent / name
                print(f"  -> {subdir}/", file=sys.stderr)
                _ensure_dir(subdir)
            else:
                print(f"  skip subfolder (depth limit): {name}/", file=sys.stderr)
            continue

        download_entry(service, f, parent)


def download_single(service: Service, meta: DriveFile, output_dir: str) -> None:
    """Download a single resolved Drive file into output_dir.

    Creates output_dir if needed, then writes the file using its Drive name.
    Google-native Docs/Sheets auto-export; other native types are skipped.
    """
    print(f"File ID: {meta['id']}", file=sys.stderr)
    out = Path(output_dir)
    _ensure_dir(out)
    download_entry(service, meta, out)


def run(
    source: str,
    output_dir: str = ".",
    depth: int | None = None,
    yes: bool = False,
) -> None:
    """Download a Drive file or folder to output_dir.

    A single file downloads immediately. A folder is scanned first, with a
    summary and a confirmation prompt before its contents download. `depth`
    only affects folders.
    """
    from gdrives.auth import build_drive_service
    from gdrives.resolve import resolve_path

    service = build_drive_service()

    if source.startswith(("http://", "https://")):
        entry_id = extract_drive_id(source)
    else:
        entry_id = resolve_path(source, service, allow_files=True)

    meta = get_file_metadata(service, entry_id)

    if not is_folder(meta):
        download_single(service, meta, output_dir)
        return

    folder_id = meta["id"]
    print(f"Folder ID: {folder_id}", file=sys.stderr)
    print("Scanning folder...", file=sys.stderr)
    # Walk the tree exactly once: the summary and the download both come from
    # this single list, so they can't disagree and the proceed path makes one
    # set of list_children calls instead of two.
    items = list(walk_tree(service, folder_id, depth=depth))
    summary = summarize(items)
    print_summary(summary, output_dir)

    if summary["binary_files"] == 0 and summary["auto_export"] == 0:
        print("\nNothing to download.", file=sys.stderr)
        return

    if not yes and not typer.confirm("\nProceed?", default=False):
        print("Aborted.", file=sys.stderr)
        return

    download_walk(service, items, output_dir)
