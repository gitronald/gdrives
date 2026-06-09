"""CLI for Google Drive operations."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

app = typer.Typer(help="Google Drive file management tools.")


@contextmanager
def _cli_errors() -> Iterator[None]:
    """Translate domain, filesystem, and Drive API errors into clean CLI output.

    One seam so every command surfaces 'Error: ...' + exit 1 instead of a raw
    traceback, and a new command can't forget to handle HttpError or OSError.
    """
    from googleapiclient.errors import HttpError

    from gdrives.resolve import DrivePathError

    try:
        yield
    except (DrivePathError, ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except HttpError as e:
        print(f"Error: Drive API request failed: {e}", file=sys.stderr)
        raise SystemExit(1)


@app.command()
def export(
    source: Annotated[str, typer.Argument(help="Google Drive URL or file ID")],
    output: Annotated[
        str,
        typer.Option(
            "-o",
            "--output",
            help="Output: .docx (Docs), .xlsx/.csv (Sheets), .pptx (Slides)",
        ),
    ],
):
    """Export a Google Doc to .docx, a Sheet to .xlsx/.csv, or Slides to .pptx."""
    from gdrives.export import run

    with _cli_errors():
        run(source, output)


@app.command()
def download(
    source: Annotated[
        str,
        typer.Argument(help="Drive file or folder URL or path (e.g. 'My Drive/refs')"),
    ],
    output_dir: Annotated[
        str,
        typer.Option(
            "-o", "--output-dir", help="Local destination directory (default: cwd)"
        ),
    ] = ".",
    depth: Annotated[
        int | None,
        typer.Option(
            "--depth",
            help="Max recursion depth (1=flat, 2=one level, ...; default: unlimited)",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Skip the confirmation prompt"),
    ] = False,
):
    """Download a Drive file or folder to a local directory.

    A single file downloads straight into the directory under its Drive name.
    A folder is scanned first, showing a summary, then prompts before
    downloading (recurses by default; --depth only affects folders).

    Google Docs/Sheets/Slides auto-export to .docx/.xlsx/.pptx; other
    Google-native types are skipped. Filename collisions get a ' (N)' suffix
    matching Drive's UI convention.
    """
    from gdrives.download import run

    with _cli_errors():
        run(source, output_dir, depth=depth, yes=yes)


@app.command()
def ls(
    path: Annotated[
        str | None,
        typer.Argument(help="Drive path (e.g. 'My Drive/projects')"),
    ] = None,
    drive_id: Annotated[
        str | None,
        typer.Option("--drive-id", help="Folder ID (skip path resolution)"),
    ] = None,
    depth: Annotated[
        int, typer.Option("--depth", help="Max directory depth to list")
    ] = 1,
    save_as: Annotated[
        list[str] | None,
        typer.Option(
            "--save-as",
            help="Save to file (.md/.csv); repeat to write both in one traversal",
        ),
    ] = None,
    shared_with_me: Annotated[
        bool,
        typer.Option(
            "--shared-with-me",
            help="Resolve path from 'Shared with me' items",
        ),
    ] = False,
):
    """List contents of a Drive folder by path or ID."""
    if shared_with_me and drive_id:
        print(
            "Error: --shared-with-me and --drive-id are mutually exclusive",
            file=sys.stderr,
        )
        raise SystemExit(1)

    bad_save_as = [p for p in (save_as or []) if not p.endswith((".md", ".csv"))]
    if bad_save_as:
        print(
            f"Error: --save-as must end in .md or .csv: {', '.join(bad_save_as)}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from gdrives.listing import ls as remote_ls
    from gdrives.resolve import resolve_path, resolve_shared_path

    with _cli_errors():
        if shared_with_me:
            if path is None:
                if depth != 1:
                    print(
                        "Error: --depth is not supported when listing all shared items",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                remote_ls(shared_with_me=True, save_as=save_as)
            else:
                folder_id = resolve_shared_path(path)
                remote_ls(folder_id, depth=depth, save_as=save_as)
        else:
            folder_id = drive_id or resolve_path(path or "My Drive")
            remote_ls(folder_id, depth=depth, save_as=save_as)


@app.command(name="show-drives")
def show_drives():
    """Fetch and cache available drives."""
    from gdrives.auth import build_drive_service
    from gdrives.drives import CACHE_PATH, fetch, save

    with _cli_errors():
        service = build_drive_service()
        drives = fetch(service)
        save(drives)

        max_url = max(len(d["url"]) for d in drives)
        max_kind = max(len(d["type"]) for d in drives)
        for d in drives:
            print(
                f"{d['url']:<{max_url}}   {d['type']:<{max_kind}}   "
                f"{d['name']} ({d['id']})"
            )
        print(f"\nSaved to {CACHE_PATH}", file=sys.stderr)
