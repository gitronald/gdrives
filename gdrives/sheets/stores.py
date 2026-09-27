"""Where a tab's local side and its base are kept: a file, memory, or a caller's own.

The orchestration in :mod:`gdrives.sheets.sync` reads and writes the local
side of a tab and its base snapshot through a :class:`Store`, and never
through a path. :class:`FileStore` is what a config file's ``local`` path and
a target's base directory become, and :class:`MemoryStore` holds records in
memory. A caller whose local side is not one flat file per tab writes a small
class of its own: one tab of a file that holds several, typed rows converted
with :func:`~gdrives.sheets.cells.encode_rows` and
:func:`~gdrives.sheets.cells.decode_rows`, or a local side that is computed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gdrives.sheets.cells import ColumnType
from gdrives.sheets.files import Records, read_records, write_records


class Store(Protocol):
    """One side of a tab as records: the local side, or the base.

    A store has a ``label``, which reports and errors show where a path was
    shown, and three methods. What the orchestration needs of them:

    - ``exists()`` says whether there is anything to read. False on the local
      side is refused for a sync or a push, and a pull then creates it. False
      on the base means a first sync.
    - ``read()`` returns the columns, in order, and the rows as canonical cell
      strings. It **returns the same records each time within a run**: after
      a run changes the tab's structure the local side is read again, and the
      run stops unless the second read equals the first. A store that
      computes its rows has to be deterministic between the two.
    - ``write(columns, rows)`` replaces what the store holds. Every row holds
      every column of ``columns``. It may raise ValueError or OSError, which a
      run reports for the tab. The local store is written after the sheet
      and before the base, so a write that fails leaves the base as it was,
      and the next run sees the sheet as already in sync.

    A store that parses cells when it writes (typed rows) should have its
    types declared in the tab's schema. The merged rows are then checked
    before anything is written, and a cell that does not parse stops the run
    there, not in ``write`` after the sheet has changed.
    """

    @property
    def label(self) -> str:
        """What reports and errors call the store."""
        raise NotImplementedError

    def exists(self) -> bool:
        """True when the store holds something to read."""
        raise NotImplementedError

    def read(self) -> Records:
        """The store's columns and rows."""
        raise NotImplementedError

    def write(self, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
        """Replace what the store holds with ``rows``, in ``columns`` order."""
        raise NotImplementedError


@dataclass(frozen=True)
class FileStore:
    """A ``.csv``, ``.tsv``, or ``.json`` record file.

    It reads with :func:`~gdrives.sheets.files.read_records` and writes with
    :func:`~gdrives.sheets.files.write_records`, passing ``types``, ``bom``,
    and ``newline``. Its label is its path.
    """

    path: Path
    types: Mapping[str, ColumnType] | None = None
    bom: bool = False
    newline: str = "lf"

    @property
    def label(self) -> str:
        return str(self.path)

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> Records:
        return read_records(self.path)

    def write(self, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
        write_records(
            self.path,
            columns,
            rows,
            types=self.types,
            bom=self.bom,
            newline=self.newline,
        )


class MemoryStore:
    """Records held in memory, for tests and for a caller that saves them itself.

    A store made with no ``columns`` does not exist until it is written, as a
    file does not. ``columns`` and ``rows`` hold what was given or last
    written, and ``writes`` counts the writes.
    """

    def __init__(
        self,
        columns: Sequence[str] | None = None,
        rows: Sequence[Mapping[str, str]] = (),
        *,
        label: str = "memory",
    ) -> None:
        self.columns: list[str] | None = list(columns) if columns is not None else None
        self.rows: list[dict[str, str]] = [dict(row) for row in rows]
        self.label = label
        self.writes = 0

    def exists(self) -> bool:
        return self.columns is not None

    def read(self) -> Records:
        return Records(list(self.columns or []), [dict(row) for row in self.rows])

    def write(self, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
        self.columns = list(columns)
        self.rows = [dict(row) for row in rows]
        self.writes += 1
