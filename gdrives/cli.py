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


# A spreadsheet target accepted by every sheets command: a Sheet URL, a bare
# file ID, or a Drive path (e.g. 'My Drive/budget'). Shared help string.
_SOURCE_HELP = "Sheet URL, file ID, or Drive path (e.g. 'My Drive/budget')"
_RANGE_HELP = "A1 range, e.g. 'Sheet1!A1:C10' (bare 'A1:C10' targets the first tab)"


@app.command(name="sheets-get")
def sheets_get(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    range_: Annotated[
        str | None,
        typer.Argument(metavar="[RANGE]", help=f"{_RANGE_HELP} (default: first tab)"),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("-o", "--output", help="Write delimited rows to this file"),
    ] = None,
    csv_out: Annotated[
        bool,
        typer.Option("--csv", help="Print comma-delimited rows instead of columns"),
    ] = False,
    tsv_out: Annotated[
        bool,
        typer.Option("--tsv", help="Tab-delimited (for --csv-style stdout or -o)"),
    ] = False,
):
    """Read a range of cells from a Google Sheet.

    Prints aligned columns to stdout by default; --csv/--tsv print delimited
    rows, and -o writes a delimited file (CSV, or TSV with --tsv).
    """
    if csv_out and tsv_out:
        print("Error: --csv and --tsv are mutually exclusive", file=sys.stderr)
        raise SystemExit(1)

    from gdrives.sheets import run_get

    delimiter = "\t" if tsv_out else ","
    with _cli_errors():
        run_get(
            source,
            range_,
            output=output,
            delimiter=delimiter,
            aligned=not (csv_out or tsv_out),
        )


@app.command(name="sheets-update")
def sheets_update(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    range_: Annotated[str, typer.Argument(metavar="RANGE", help=_RANGE_HELP)],
    values_file: Annotated[
        str,
        typer.Option("--values-file", help="Local CSV of the rows to write"),
    ],
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Store literal strings (skip USER_ENTERED parsing)"),
    ] = False,
):
    """Overwrite a range with rows from a local CSV file (needs write access)."""
    from gdrives.sheets import run_update

    with _cli_errors():
        run_update(source, range_, values_file, raw=raw)


@app.command(name="sheets-append")
def sheets_append(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    range_: Annotated[str, typer.Argument(metavar="RANGE", help=_RANGE_HELP)],
    values_file: Annotated[
        str,
        typer.Option("--values-file", help="Local CSV of the rows to append"),
    ],
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Store literal strings (skip USER_ENTERED parsing)"),
    ] = False,
):
    """Append rows from a local CSV file after the table in a range (write access)."""
    from gdrives.sheets import run_append

    with _cli_errors():
        run_append(source, range_, values_file, raw=raw)


@app.command(name="sheets-clear")
def sheets_clear(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    range_: Annotated[str, typer.Argument(metavar="RANGE", help=_RANGE_HELP)],
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Skip the confirmation prompt"),
    ] = False,
):
    """Clear the values in a range, keeping formatting (needs write access)."""
    from gdrives.sheets import run_clear

    with _cli_errors():
        run_clear(source, range_, yes=yes)
