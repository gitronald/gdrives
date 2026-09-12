"""Atomic local output shared by downloads, exports, and credential caching."""

import os
from collections.abc import Iterator
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


@contextmanager
def atomic_output(target: Path, *, mode: int | None = None) -> Iterator[IO[bytes]]:
    """Replace target only after a successful write to a private sibling file.

    Each writer owns a unique temporary file, created with mode 0600. Existing
    files and symlinks beside the target are never used as scratch space.

    ``mode`` is applied before the rename. It defaults to :func:`umask_mode`, so
    a file the user is meant to open keeps the permissions the direct write it
    replaced gave it; pass :data:`PRIVATE` for one that must stay owner-only.
    """
    temporary = NamedTemporaryFile(dir=target.parent, prefix=".gdrives-", delete=False)
    path = Path(temporary.name)
    try:
        with temporary:
            yield temporary.file
        path.chmod(umask_mode() if mode is None else mode)
        path.replace(target)
    finally:
        path.unlink(missing_ok=True)
