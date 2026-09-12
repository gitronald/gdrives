"""CLI for Google Drive operations."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

app = typer.Typer(help="Google Drive file management tools.")

# The "-y/--yes" flag shared by every command that confirms before writing.
YesFlag = Annotated[
    bool, typer.Option("-y", "--yes", help="Skip the confirmation prompt")
]


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
            help="Output: .docx/.txt/.md (Docs), .xlsx/.csv (Sheets), .pptx (Slides)",
        ),
    ],
):
    """Export a Doc to .docx/.txt/.md, a Sheet to .xlsx/.csv, or Slides to .pptx."""
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
    yes: YesFlag = False,
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

    if shared_with_me and path is None and depth != 1:
        print(
            "Error: --depth is not supported when listing all shared items",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from gdrives.listing import ls as remote_ls
    from gdrives.resolve import resolve_path, resolve_shared_path

    with _cli_errors():
        if shared_with_me and path is None:
            remote_ls(shared_with_me=True, save_as=save_as)
        else:
            if shared_with_me:
                assert path is not None  # the path-less shared case returned above
                folder_id = resolve_shared_path(path)
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
    yes: YesFlag = False,
):
    """Clear the values in a range, keeping formatting (needs write access)."""
    from gdrives.sheets import run_clear

    with _cli_errors():
        run_clear(source, range_, yes=yes)


@app.command(name="sheets-set")
def sheets_set(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    match: Annotated[
        list[str],
        typer.Option(
            "-m",
            "--match",
            help="COLUMN=VALUE row filter; repeat for a composite AND key",
        ),
    ],
    set_: Annotated[
        list[str],
        typer.Option(
            "-s",
            "--set",
            help="COLUMN=VALUE to write; repeat to set multiple columns",
        ),
    ],
    tab: Annotated[
        str | None,
        typer.Option("--tab", help="Tab name (default: first tab)"),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", help="Update every matching row (default: exactly one)"),
    ] = False,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Store literal strings (skip USER_ENTERED parsing)"),
    ] = False,
):
    """Set column(s) on the row(s) matching COLUMN=VALUE condition(s) (write access).

    Locates rows by header-named columns and writes the target cells in one call.
    Refuses unless exactly one row matches, unless --all is given. Example:
    gdrives sheets-set <sheet> -m year=2026 -m id=C300 -s status=paid -s amount=250
    """
    from gdrives.sheets import parse_pairs, run_set

    with _cli_errors():
        match_map = parse_pairs(match, "--match")
        updates = parse_pairs(set_, "--set")
        run_set(source, match_map, updates, tab=tab, raw=raw, allow_multiple=all_)


@app.command(name="sheets-rules")
def sheets_rules(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the raw rules as JSON (for --rule-json)"),
    ] = False,
):
    """List a Sheet's conditional format rules, grouped by tab.

    Each rule is prefixed with its index on its tab: the first matching rule
    wins, and sheets-delete-rule takes that index.
    """
    from gdrives.sheets import run_rules

    with _cli_errors():
        run_rules(source, as_json=as_json)


def _flag(name: str, what: str) -> typer.models.OptionInfo:
    return typer.Option(f"--{name}", help=f"Format matching cells {what}")


@app.command(name="sheets-add-rule")
def sheets_add_rule(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    range_: Annotated[
        list[str] | None,
        typer.Option("--range", help=f"{_RANGE_HELP}; repeat for several"),
    ] = None,
    formula: Annotated[
        str | None,
        typer.Option("--formula", help="Custom formula, e.g. '=$F2=\"Rejected\"'"),
    ] = None,
    bold: Annotated[bool, _flag("bold", "bold")] = False,
    italic: Annotated[bool, _flag("italic", "italic")] = False,
    strikethrough: Annotated[bool, _flag("strikethrough", "struck through")] = False,
    underline: Annotated[bool, _flag("underline", "underlined")] = False,
    text_color: Annotated[
        str | None,
        typer.Option("--text-color", help="Text color as hex, e.g. '#999999'"),
    ] = None,
    background: Annotated[
        str | None,
        typer.Option("--background", help="Fill color as hex, e.g. '#fce8e6'"),
    ] = None,
    rule_json: Annotated[
        str | None,
        typer.Option(
            "--rule-json",
            help="JSON file holding one rule (or one sheets-rules --json entry)",
        ),
    ] = None,
    index: Annotated[
        int,
        typer.Option("--index", min=0, help="Position in the tab's rules (0 = first)"),
    ] = 0,
):
    """Add a conditional format rule (needs write access).

    Builds a custom-formula rule from --range/--formula and the format options,
    or replays one captured with sheets-rules --json. Example:
    gdrives sheets-add-rule <sheet> --range 'Sheet1!A2:AA'
    --formula '=$F2="Rejected"' --strikethrough --text-color '#999999'
    """
    from gdrives.sheets import run_add_rule

    with _cli_errors():
        run_add_rule(
            source,
            ranges=range_,
            formula=formula,
            bold=bold,
            italic=italic,
            strikethrough=strikethrough,
            underline=underline,
            text_color=text_color,
            background=background,
            rule_json=rule_json,
            index=index,
        )


@app.command(name="sheets-delete-rule")
def sheets_delete_rule(
    source: Annotated[str, typer.Argument(help=_SOURCE_HELP)],
    index: Annotated[
        int, typer.Option("--index", help="Rule index on the tab (see sheets-rules)")
    ],
    tab: Annotated[
        str | None,
        typer.Option("--tab", help="Tab name (default: first tab)"),
    ] = None,
    yes: YesFlag = False,
):
    """Delete one conditional format rule by tab and index (needs write access).

    Later rules on the tab shift up by one, so re-run sheets-rules before
    deleting another.
    """
    from gdrives.sheets import run_delete_rule

    with _cli_errors():
        run_delete_rule(source, index, tab=tab, yes=yes)


# A document target accepted by every docs command: a Doc URL, a bare file ID,
# or a Drive path (e.g. 'My Drive/notes'). Shared help strings.
_DOC_SOURCE_HELP = "Doc URL, file ID, or Drive path (e.g. 'My Drive/notes')"
_DOC_TAB_HELP = "Tab title or ID (default: first tab)"
_TEXT_FILE_HELP = "Local UTF-8 text file (one trailing newline is dropped)"


@app.command(name="docs-get")
def docs_get(
    source: Annotated[str, typer.Argument(help=_DOC_SOURCE_HELP)],
    tab: Annotated[str | None, typer.Option("--tab", help=_DOC_TAB_HELP)] = None,
    output: Annotated[
        str | None,
        typer.Option("-o", "--output", help="Write the text (or JSON) to this file"),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the raw document JSON instead of text"),
    ] = False,
):
    """Print a Google Doc's text: paragraphs, list items, and table rows.

    Lists are prefixed with '- ' and table rows are tab-separated. --json prints
    the full documents.get response (every tab) for inspection or scripting.
    """
    from gdrives.docs import run_get

    with _cli_errors():
        run_get(source, tab=tab, output=output, as_json=as_json)


@app.command(name="docs-update")
def docs_update(
    source: Annotated[str, typer.Argument(help=_DOC_SOURCE_HELP)],
    text_file: Annotated[str, typer.Option("--text-file", help=_TEXT_FILE_HELP)],
    tab: Annotated[str | None, typer.Option("--tab", help=_DOC_TAB_HELP)] = None,
    yes: YesFlag = False,
):
    """Overwrite a Doc's whole body with a local text file (needs write access).

    Existing content, including formatting, is replaced by the file's plain
    text; the write is refused if the document changed since it was read.
    """
    from gdrives.docs import run_update

    with _cli_errors():
        run_update(source, text_file, tab=tab, yes=yes)


@app.command(name="docs-append")
def docs_append(
    source: Annotated[str, typer.Argument(help=_DOC_SOURCE_HELP)],
    text: Annotated[
        str | None,
        typer.Option("--text", help="Text to append (verbatim)"),
    ] = None,
    text_file: Annotated[
        str | None,
        typer.Option("--text-file", help=_TEXT_FILE_HELP),
    ] = None,
    tab: Annotated[str | None, typer.Option("--tab", help=_DOC_TAB_HELP)] = None,
):
    """Append text as new paragraph(s) at the end of a Doc (needs write access).

    Pass exactly one of --text or --text-file.
    """
    from gdrives.docs import run_append

    with _cli_errors():
        run_append(source, text=text, text_file=text_file, tab=tab)


@app.command(name="docs-replace")
def docs_replace(
    source: Annotated[str, typer.Argument(help=_DOC_SOURCE_HELP)],
    find: Annotated[str, typer.Option("--find", help="Text to search for")],
    replace: Annotated[
        str,
        typer.Option("--replace", help="Replacement text (may be empty)"),
    ],
    ignore_case: Annotated[
        bool,
        typer.Option(
            "--ignore-case",
            help=(
                "Match regardless of letter case (the pre-check counts with "
                "Unicode casefolding, which can differ from the API on rare "
                "characters such as ligatures)"
            ),
        ),
    ] = False,
    all_: Annotated[
        bool,
        typer.Option("--all", help="Replace every occurrence (default: exactly one)"),
    ] = False,
    tab: Annotated[str | None, typer.Option("--tab", help=_DOC_TAB_HELP)] = None,
):
    """Find and replace text in a Doc (needs write access).

    Refuses when the phrase occurs more than once unless --all is given, so a
    targeted edit never rewrites the wrong sentence; errors when it occurs
    nowhere. Example: gdrives docs-replace <doc> --find "draft" --replace "final"
    """
    from gdrives.docs import run_replace

    with _cli_errors():
        run_replace(
            source,
            find,
            replace,
            match_case=not ignore_case,
            tab=tab,
            allow_multiple=all_,
        )


@app.command(name="docs-clear")
def docs_clear(
    source: Annotated[str, typer.Argument(help=_DOC_SOURCE_HELP)],
    tab: Annotated[str | None, typer.Option("--tab", help=_DOC_TAB_HELP)] = None,
    yes: YesFlag = False,
):
    """Empty a Doc's body (needs write access)."""
    from gdrives.docs import run_clear

    with _cli_errors():
        run_clear(source, tab=tab, yes=yes)


@app.command(name="docs-create")
def docs_create(
    title: Annotated[str, typer.Option("--title", help="Title of the new document")],
    text_file: Annotated[
        str | None,
        typer.Option("--text-file", help=f"{_TEXT_FILE_HELP} for the initial body"),
    ] = None,
):
    """Create a new Google Doc in the root of My Drive (needs write access).

    Prints the new document's URL. It lands in My Drive root; move it into a
    folder afterwards with `gdrives mv`.
    """
    from gdrives.docs import run_create

    with _cli_errors():
        run_create(title, text_file=text_file)


@app.command()
def mv(
    source: Annotated[
        str | None,
        typer.Argument(help="Drive path, URL, or file ID to move (e.g. 'My Drive/a')"),
    ] = None,
    dest: Annotated[
        str | None,
        typer.Argument(
            help="New name, an existing folder path, or a folder path + new name"
        ),
    ] = None,
    source_id: Annotated[
        str | None,
        typer.Option("--source-id", help="Source file/folder ID (skip resolution)"),
    ] = None,
    dest_id: Annotated[
        str | None,
        typer.Option("--dest-id", help="Destination folder ID (skip resolution)"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="New name, used with or instead of --dest-id"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the intended change without making it"),
    ] = False,
):
    """Rename and/or move a Drive file or folder (write access, except --dry-run).

    Like Unix mv, DEST decides the operation: a bare name renames in place, an
    existing folder path moves the item into it, and a folder path plus a new
    final segment does both in one call. Examples:
    gdrives mv "My Drive/notes.txt" "renamed.txt";
    gdrives mv "My Drive/notes.txt" "My Drive/archive" --dry-run
    """
    from gdrives.mv import run

    with _cli_errors():
        run(
            source,
            dest,
            source_id=source_id,
            dest_id=dest_id,
            name=name,
            dry_run=dry_run,
        )
