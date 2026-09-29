"""Local output: atomic file writes, CSV cells, and names shown in a terminal.

Everything gdrives writes on this machine goes through here. Files (downloads,
exports, saved listings, the drive cache, the OAuth token) are replaced
atomically, cells bound for a CSV can be kept from running as formulas, Drive
names bound for a terminal have their control characters escaped, and names
that become local file names are made safe to join onto a directory.
"""

import os
import re
import stat
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO

#: Mode a cached credential file must land with: readable by its owner only.
PRIVATE = 0o600


def umask_mode() -> int:
    """The mode a plain ``open()`` would give a new file under this umask.

    ``NamedTemporaryFile`` creates at 0600 so the scratch file is never briefly
    world-readable, but ordinary output (a download, an export) has to end up
    with the permissions the write it replaced would have produced. Reading the
    umask means querying it by setting it, so it is restored immediately.
    """
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _existing_mode(target: Path) -> int | None:
    """The permission bits of the file at ``target``, or None when there is none."""
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return None


@contextmanager
def atomic_output(
    target: Path, *, mode: int | None = None
) -> Generator[IO[bytes], None, None]:
    """Replace target only after a successful write to a private sibling file.

    Each writer owns a unique temporary file, created with mode 0600. Existing
    files and symlinks beside the target are never used as scratch space.

    ``mode`` is applied before the rename. By default the result gets the
    permissions a direct write would have left: those of the file it replaces,
    or :func:`umask_mode` for a new one. Pass :data:`PRIVATE` for a file that
    must stay owner-only whatever was there before. The rename replaces the
    target path itself, so a symlink or hard link there is swapped for the new
    file rather than written through.
    """
    temporary = NamedTemporaryFile(dir=target.parent, prefix=".gdrives-", delete=False)
    path = Path(temporary.name)
    try:
        with temporary:
            yield temporary.file
        if mode is None:
            mode = _existing_mode(target)
        path.chmod(umask_mode() if mode is None else mode)
        path.replace(target)
    finally:
        path.unlink(missing_ok=True)


def write_text(target: Path, text: str) -> None:
    """Atomically write ``text`` to ``target`` as UTF-8, creating parent folders.

    The bytes are written untranslated, so line endings are the ones ``text``
    holds on every platform. A text-mode write would turn each ``\\n`` into
    ``\\r\\n`` on Windows, and a CSV row's ``\\r\\n`` into ``\\r\\r\\n``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(target) as f:
        f.write(text.encode("utf-8"))


# Characters that make a spreadsheet app read a CSV cell as a formula (OWASP's
# CSV injection list: a leading tab or carriage return can precede one).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_formula(cell: str) -> str:
    """Prefix ``'`` to a CSV cell that a spreadsheet app would run as a formula.

    Excel and LibreOffice evaluate a cell starting with ``=``, ``+``, ``-``, or
    ``@`` when they open a CSV, so a shared file named
    ``=HYPERLINK("http://...")`` would run on open; a leading tab or carriage
    return can hide one of those, so it is escaped too. The apostrophe makes
    the cell text.
    """
    return "'" + cell if cell.startswith(_FORMULA_PREFIXES) else cell


#: C0 and C1 control characters and DEL: ESC starts ANSI and OSC sequences, and
#: C1 holds single-character forms of the same introducers (CSI, OSC). Escaped
#: for the terminal by :func:`printable`; :func:`safe_filename` replaces them
#: in local file names.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _escape_control(match: re.Match[str]) -> str:
    return f"\\x{ord(match.group()):02x}"


#: The names of the line endings a text file can be written with.
NEWLINES = frozenset({"lf", "crlf"})

_LINE_ENDINGS = {"lf": "\n", "crlf": "\r\n"}


def line_ending(newline: str) -> str:
    """The line ending called ``newline``, refusing a name that is not one."""
    if newline not in NEWLINES:
        raise ValueError(f"newline must be one of {sorted(NEWLINES)}, not {newline!r}")
    return _LINE_ENDINGS[newline]


def printable(text: str) -> str:
    """Escape control characters so a Drive name can't drive the terminal.

    Someone else picks a shared item's name. Printed raw, an embedded escape
    sequence could rewrite or hide lines of output, retitle the terminal, or set
    the clipboard (OSC 52). Each control character is shown as ``\\xNN``.
    """
    return CONTROL_CHARACTERS.sub(_escape_control, text)


def slug(title: str) -> str:
    """A title as a lower-case file stem of letters, digits, and hyphens.

    Each run of characters other than letters and digits becomes one hyphen,
    and hyphens are trimmed from the ends: ``Form responses 1`` is
    ``form-responses-1``. Letters and digits of any script are kept, and a
    combining mark stays with the letter it follows, so a title is one stem
    whether its accents are composed or not. Raises ValueError for a title
    that leaves nothing.
    """
    kept: list[str] = []
    # Composed after lower-casing, which can decompose a letter.
    for ch in unicodedata.normalize("NFC", title.lower()):
        marks = bool(kept) and kept[-1] != "-" and unicodedata.category(ch)[0] == "M"
        kept.append(ch if ch.isalnum() or marks else "-")
    stem = re.sub(r"-+", "-", "".join(kept)).strip("-")
    if not stem:
        raise ValueError(f"{title!r} has no letter or digit to make a slug of")
    return stem


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
    cleaned = re.sub(r"[/\\]", "_", CONTROL_CHARACTERS.sub("_", name)).strip()
    if cleaned in {".", ".."}:
        cleaned = cleaned.replace(".", "_")  # "." -> "_", ".." -> "__"
    return cleaned or "file"
