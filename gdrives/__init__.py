"""Google Drive, Sheets, and Docs from the command line and from Python."""

from importlib.metadata import PackageNotFoundError, version

#: What ``__version__`` holds when the package has no installed metadata.
UNKNOWN_VERSION = "0+unknown"


def _installed_version() -> str:
    """The installed package's version, or :data:`UNKNOWN_VERSION` without one.

    A source tree on ``sys.path`` that was never installed has no metadata.
    """
    try:
        return version("gdrives")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = _installed_version()

__all__ = ["UNKNOWN_VERSION", "__version__"]
