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

Failures and reruns
-------------------
One entry that fails (a Doc too large to export, a download-restricted file,
a name the local filesystem rejects) does not stop a folder download: the rest
still download, and the failures are listed at the end with a non-zero exit.
Rerunning with ``skip_existing`` (CLI: --skip-existing) maps every entry to
the path the first run gave it and skips the ones already there, so only what
is missing is fetched again instead of being saved a second time as ' (1)'.
"""

import re
import sys
from collections.abc import Callable
from os.path import lexists
from pathlib import Path

import typer
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from gdrives.export import NATIVE_EXPORTS, export_file
from gdrives.files import (
    DriveFile,
    Service,
    WalkItem,
    file_type,
    get_file_metadata,
    is_folder,
    is_native,
    walk_tree,
)
from gdrives.local import atomic_output, printable

# Map Google-native type label -> local extension, derived from the canonical
# export table (export.NATIVE_EXPORTS) so download and `gdrives export` never drift.
NATIVE_EXPORT_EXT = {label: ext for label, (ext, _mime) in NATIVE_EXPORTS.items()}


class DownloadError(Exception):
    """Raised after a folder download in which some entries failed."""


def safe_filename(name: str) -> str:
    """Sanitize a Drive file name for use on the local filesystem.

    Replaces both path separators (``/`` and ``\\``) and control characters
    (NUL, newlines, and the ESC that starts a terminal escape sequence), then
    neutralizes the ``.``/``..`` dot segments so a Drive entry named ``..`` can't
    escape the target directory (``out / ".."`` would otherwise resolve to its
    parent). Backslash is replaced too so a name like ``..\\..\\evil`` can't
    traverse on Windows, where ``\\`` is a separator. Legitimate dotfiles like
    ``.env`` are preserved.
    """
    cleaned = re.sub(r"[/\\\x00-\x1f\x7f-\x9f]", "_", name).strip()
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


def unique_path(target: Path, taken: Callable[[Path], bool] = lexists) -> Path:
    """If target is taken, append ' (1)', ' (2)', ... before the extension until not.

    Mirrors Google Drive Web UI's display-time dedup convention so that locally
    disambiguated names look the way Drive would have rendered them. ``taken``
    defaults to "exists on disk" (a dangling symlink counts).
    """
    if not taken(target):
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not taken(candidate):
            return candidate
        n += 1


class LocalNames:
    """Choose the local path for each Drive entry in one download run.

    By default a name already on disk gets a ' (N)' suffix, so nothing local is
    overwritten. With ``skip_existing`` the suffix is chosen against the paths
    this run has already handed out instead: a rerun then maps every entry to
    the path the first run gave it (a second ``a.pdf`` is ``a (1).pdf`` both
    times, since ``list_children`` orders duplicates the same way on every
    call), and an entry whose path is already there is skipped rather than
    saved again as a copy.
    """

    def __init__(self, *, skip_existing: bool = False) -> None:
        self.skip_existing = skip_existing
        self._used: set[Path] = set()

    def claim(self, target: Path) -> Path:
        """Return the path for the entry that would land at ``target``."""
        taken = self._used.__contains__ if self.skip_existing else lexists
        path = unique_path(target, taken)
        self._used.add(path)
        return path


def download_file(service: Service, file_id: str, output_path: str) -> int:
    """Download a binary Drive file to output_path. Returns bytes written.

    Streams chunks straight to a private temporary file and renames it into
    place, so memory stays bounded regardless of file size. A failed download
    removes the temporary file and preserves any existing final file.
    """
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    target = Path(output_path)
    with atomic_output(target) as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target.stat().st_size


def download_entry(
    service: Service, f: DriveFile, out: Path, names: LocalNames | None = None
) -> None:
    """Download one non-folder Drive entry into the existing directory `out`.

    Google-native Docs/Sheets/Slides auto-export to .docx/.xlsx/.pptx; other
    native types (Forms, Drawings, etc.) are skipped with a warning. Binary
    files download via get_media. ``names`` picks the local path (see
    :class:`LocalNames`): by default a name that collides locally gets a ' (N)'
    suffix.
    """
    names = names or LocalNames()
    name = safe_filename(f["name"])
    disposition = classify_entry(f)

    if disposition == "skip":
        print(f"  skip {file_type(f)} (no export format): {name}", file=sys.stderr)
        return

    ext = NATIVE_EXPORT_EXT[file_type(f)] if disposition == "export" else ""
    target = names.claim(out / f"{name}{ext}")
    if names.skip_existing and lexists(target):
        print(f"  skip (already there): {target}", file=sys.stderr)
        return

    if disposition == "export":
        export_file(service, f["id"], str(target))
        return

    size = download_file(service, f["id"], str(target))
    print(f"  {target} ({size} bytes)")


def download_walk(
    service: Service,
    items: list[WalkItem],
    output_dir: str,
    *,
    skip_existing: bool = False,
) -> list[str]:
    """Download a materialized ``walk_tree`` into output_dir, depth-first.

    Consumes the same list ``summarize`` counted, so the download can't diverge
    from the summary. A stack tracks each folder's allocated local directory,
    keeping duplicate or sanitized names distinct. A within-depth folder creates its
    subdirectory (even when empty) and prints ``-> subdir/``, while a
    depth-limited folder prints a skip notice. Messages fire here, at download
    time, in the walk's depth-first order.

    An entry that fails with an API or filesystem error is reported and the
    walk moves on; a folder that can't be created takes its contents with it.
    Returns one line per failure, empty when everything downloaded.
    ``skip_existing`` is :class:`LocalNames`' rerun mode.
    """
    out = Path(output_dir)
    _ensure_dir(out)
    names = LocalNames(skip_existing=skip_existing)
    # None marks a folder that could not be created: its contents are skipped.
    parents: list[Path | None] = [out]
    failures: list[str] = []

    def fail(item: WalkItem, error: Exception, note: str = "") -> None:
        label = printable("/".join((*item.ancestors, item.file["name"])))
        failures.append(f"{label}: {error}{note}")
        print(f"  failed: {label}: {error}", file=sys.stderr)

    for item in items:
        del parents[item.depth + 1 :]
        parent = parents[item.depth]
        f = item.file
        if parent is None:
            if is_folder(f) and item.descended:
                parents.append(None)
            continue
        if is_folder(f):
            name = safe_filename(f["name"])
            if not item.descended:
                print(f"  skip subfolder (depth limit): {name}/", file=sys.stderr)
                continue
            subdir = names.claim(parent / name)
            print(f"  -> {subdir}/", file=sys.stderr)
            try:
                _ensure_dir(subdir)
            except OSError as e:
                fail(item, e, " (its contents were not downloaded)")
                parents.append(None)
            else:
                parents.append(subdir)
            continue

        try:
            download_entry(service, f, parent, names)
        except (HttpError, OSError) as e:
            fail(item, e)
    return failures


def download_single(
    service: Service, meta: DriveFile, output_dir: str, *, skip_existing: bool = False
) -> None:
    """Download a single resolved Drive file into output_dir.

    Creates output_dir if needed, then writes the file using its Drive name.
    Google-native Docs/Sheets auto-export; other native types are skipped.
    With ``skip_existing``, a file already at that path is left alone.
    """
    print(f"File ID: {meta['id']}", file=sys.stderr)
    out = Path(output_dir)
    _ensure_dir(out)
    download_entry(service, meta, out, LocalNames(skip_existing=skip_existing))


def run(
    source: str,
    output_dir: str = ".",
    depth: int | None = None,
    yes: bool = False,
    skip_existing: bool = False,
) -> None:
    """Download a Drive file or folder to output_dir.

    ``source`` is a Drive URL, a bare file or folder ID, a Drive path, or the
    name of a cached drive. A single file downloads immediately. A folder is
    scanned first, with a summary and a confirmation prompt before its contents
    download; `depth` only affects folders. Entries that fail are listed in a
    DownloadError raised once the rest have downloaded.
    """
    from gdrives.auth import build_drive_service
    from gdrives.drives import find_drive, load
    from gdrives.resolve import resolve_file_id

    service = build_drive_service()

    # A bare name can be a whole drive ("Team Drive"); resolve_file_id takes
    # anything without a slash for a file ID, which is right otherwise.
    drive = None if "/" in source else find_drive(load(), source)
    entry_id = drive["id"] if drive else resolve_file_id(source, service)

    meta = get_file_metadata(service, entry_id)

    if not is_folder(meta):
        download_single(service, meta, output_dir, skip_existing=skip_existing)
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

    if not (summary["binary_files"] or summary["auto_export"] or summary["subfolders"]):
        print("\nNothing to download.", file=sys.stderr)
        return

    if not yes and not typer.confirm("\nProceed?", default=False):
        print("Aborted.", file=sys.stderr)
        return

    failures = download_walk(service, items, output_dir, skip_existing=skip_existing)
    if failures:
        raise DownloadError(
            f"{len(failures)} item(s) failed to download:\n"
            + "\n".join(f"  {line}" for line in failures)
            + "\nRerun with --skip-existing to fetch only what is missing."
        )
