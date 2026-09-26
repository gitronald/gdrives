"""Drive cache — fetch, save, and look up drive IDs by name."""

import json
from pathlib import Path
from typing import TypedDict

from gdrives.files import Service
from gdrives.local import write_text


class DriveInfo(TypedDict):
    id: str
    type: str
    name: str
    url: str


CACHE_DIR = Path(".gdrives")
CACHE_PATH = CACHE_DIR / "cache.json"


def fetch(service: Service) -> list[DriveInfo]:
    """Fetch all accessible drives from the API."""
    drives: list[DriveInfo] = []

    # My Drive
    root = service.files().get(fileId="root", fields="id").execute()
    drives.append(
        {
            "id": root["id"],
            "type": "personal",
            "name": "My Drive",
            "url": "https://drive.google.com/drive/my-drive",
        }
    )

    # Shared drives (paginated — a single page caps at the API default, ~100)
    page_token = None
    while True:
        kwargs: dict[str, object] = {"pageSize": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.drives().list(**kwargs).execute()
        for d in results.get("drives", []):
            drives.append(
                {
                    "id": d["id"],
                    "type": "shared",
                    "name": d["name"],
                    "url": f"https://drive.google.com/drive/folders/{d['id']}",
                }
            )
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return drives


def save(drives: list[DriveInfo], path: Path = CACHE_PATH) -> None:
    """Save drives list to JSON cache.

    Written atomically: an interrupted ``show-drives`` leaves the previous cache
    in place rather than a truncated file every Drive-path command then chokes on.
    """
    write_text(path, json.dumps(drives, indent=2) + "\n")


def _is_drive(entry: object) -> bool:
    """True for a cache entry carrying every DriveInfo field as a string."""
    return isinstance(entry, dict) and all(
        isinstance(entry.get(key), str) for key in DriveInfo.__annotations__
    )


def load(path: Path = CACHE_PATH) -> list[DriveInfo]:
    """Load drives from JSON cache, refusing one that is not a list of drives.

    A hand-edited or otherwise damaged cache raises ValueError naming the file
    and the fix, instead of a KeyError or TypeError deep inside a lookup.
    """
    if not path.exists():
        return []
    rerun = "rerun 'gdrives show-drives' to rebuild it"
    try:
        drives = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"drive cache {path} is not valid JSON ({e}); {rerun}")
    if not isinstance(drives, list) or not all(_is_drive(d) for d in drives):
        raise ValueError(f"drive cache {path} is malformed; {rerun}")
    return drives


def find_drive(drives: list[DriveInfo], name: str) -> DriveInfo | None:
    """Find a drive by name (case-insensitive) in an already-loaded list.

    Returns the full drive dict (id, type, name, url) or None. Lets callers that
    match several names (e.g. path-prefix resolution) load the cache once.
    """
    matches = [d for d in drives if d["name"].lower() == name.lower()]
    if len(matches) > 1:
        ids = ", ".join(d["id"] for d in matches)
        raise ValueError(f"multiple drives named {name!r}; use a drive ID: {ids}")
    return matches[0] if matches else None
