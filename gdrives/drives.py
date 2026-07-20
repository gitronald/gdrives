"""Drive cache — fetch, save, and look up drive IDs by name."""

import json
from pathlib import Path
from typing import TypedDict

from gdrives.files import Service


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
    """Save drives list to JSON cache."""
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(drives, indent=2) + "\n", encoding="utf-8")


def load(path: Path = CACHE_PATH) -> list[DriveInfo]:
    """Load drives from JSON cache."""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def find_drive(drives: list[DriveInfo], name: str) -> DriveInfo | None:
    """Find a drive by name (case-insensitive) in an already-loaded list.

    Returns the full drive dict (id, type, name, url) or None. Lets callers that
    match several names (e.g. path-prefix resolution) load the cache once.
    """
    for d in drives:
        if d["name"].lower() == name.lower():
            return d
    return None


def resolve_name(name: str, path: Path = CACHE_PATH) -> DriveInfo | None:
    """Look up a drive by name from the cache. Case-insensitive.

    Returns the full drive dict (id, type, name, url) or None.
    """
    return find_drive(load(path), name)
