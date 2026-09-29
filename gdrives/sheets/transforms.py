"""Stock ``transform`` hooks: ready-made cleaning of the rows a tab is read as.

A config names one in a tab's (or a target's) ``hooks``, as
``"transform": "gdrives.sheets.transforms:trim_cells"``, or code passes it as
``transform=``. Each takes the rows and returns them cleaned, as
:data:`~gdrives.sheets.sync.Transform` requires.
"""

from collections.abc import Mapping, Sequence

from gdrives.sheets.cells import normalize_key


def _trimmed(text: str) -> str:
    """``text`` stripped, each line normalized as a key is, line breaks kept."""
    return "\n".join(normalize_key(line) for line in text.strip().split("\n"))


def trim_cells(
    rows: Sequence[Mapping[str, str]], title: str | None = None
) -> list[dict[str, str]]:
    """Strip every cell and collapse the whitespace inside each of its lines.

    Each line of a cell gets what :func:`~gdrives.sheets.cells.normalize_key`
    does to a key: stripped, and every run of whitespace made one space. Line
    breaks (``\\n``) are kept, since a cell may hold several lines, and a key
    is never one; a break at either end of the cell is stripped with the rest
    of its whitespace, and a blank line inside it stays. A carriage return
    counts as whitespace, so ``\\r\\n`` becomes ``\\n``. Applied to its own
    result it changes nothing, as a transform must, and a key cell comes out
    with the same :func:`~gdrives.sheets.cells.row_key` it went in with.

    ``title`` is the tab's title, which :func:`~gdrives.sheets.sync.pull_all_tabs`
    gives a transform as a second argument so one function serves every tab; it
    is not used, and a call with the rows alone works.
    """
    return [{column: _trimmed(text) for column, text in row.items()} for row in rows]
