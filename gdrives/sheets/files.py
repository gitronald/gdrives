"""Local delimited files (CSV/TSV) read and written as rows of string cells."""

import csv
import io
from pathlib import Path

from gdrives.local import write_text


def read_values_csv(path: str, *, delimiter: str = ",") -> list[list[str]]:
    """Read a local delimited file into rows of string cells.

    A UTF-8 byte-order mark (Excel's "CSV UTF-8" format starts with one) is
    dropped instead of becoming part of the first cell, where it would break a
    later header lookup. A file the csv module rejects (an oversized field, a
    stray NUL) raises ValueError naming the line.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            return list(reader)
        except csv.Error as e:
            raise ValueError(f"{path}, line {reader.line_num}: {e}")


def write_values_csv(
    path: str, values: list[list[str]], *, delimiter: str = ","
) -> None:
    """Write rows of cells to a local delimited file, creating parent dirs.

    Written atomically, so a failed run never leaves a partial file behind.
    """
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter).writerows(values)
    write_text(Path(path), buf.getvalue())
