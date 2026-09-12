"""Tests for gdrives.mv — destination resolution, guard rails, and files.update.

A fake Drive service records every ``files.get``/``files.update`` call and
serves metadata from a dict, so both the request shape and the decision logic
are asserted without the API.
"""

from typing import Any

import pytest

from gdrives import mv
from gdrives.resolve import AmbiguousPathError, DrivePathError

FOLDER_MIME = "application/vnd.google-apps.folder"


def item(
    name: str,
    *,
    id: str = "F",
    parents: list[str] | None = None,
    drive_id: str | None = None,
    folder: bool = False,
) -> dict[str, Any]:
    """Build the metadata shape mv requests (MV_FIELDS)."""
    meta: dict[str, Any] = {
        "id": id,
        "name": name,
        "mimeType": FOLDER_MIME if folder else "text/plain",
    }
    if parents is not None:
        meta["parents"] = parents
    if drive_id is not None:
        meta["driveId"] = drive_id
    return meta


class _Executable:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class FakeDriveService:
    """A minimal fake of the Drive v3 service for files.get / files.update."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        self.items = items
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def files(self) -> "FakeDriveService":
        return self

    def get(self, **kwargs: Any) -> _Executable:
        self.calls.append(("get", kwargs))
        return _Executable(self.items[kwargs["fileId"]])

    def update(self, **kwargs: Any) -> _Executable:
        self.calls.append(("update", kwargs))
        return _Executable(self.items.get(kwargs["fileId"], {}))

    @property
    def updates(self) -> list[dict[str, Any]]:
        return [kwargs for method, kwargs in self.calls if method == "update"]


def patch_service(monkeypatch: pytest.MonkeyPatch, svc: FakeDriveService) -> dict:
    """Make build_drive_service return ``svc``; the dict records its ``scopes``."""
    rec: dict[str, Any] = {}
    monkeypatch.setattr(
        "gdrives.auth.build_drive_service",
        lambda scopes=None: rec.update(scopes=scopes) or svc,
    )
    return rec


def patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    paths: dict[str, str],
    folders: dict[tuple[str, str], str],
) -> dict[str, list]:
    """Patch path resolution: ``paths`` maps a parent path to its folder ID and
    ``folders`` maps (parent_id, segment) to the child folder's ID.

    Anything absent raises DrivePathError, mirroring a name that is not a folder
    in that parent. The returned dict records each call so a test can assert how
    many times the tree was walked.
    """
    seen: dict[str, list] = {"resolve_path": [], "walk_segments": []}

    def resolve_path(path, service):
        seen["resolve_path"].append(path)
        if path in paths:
            return paths[path]
        raise DrivePathError(f"folder '{path}' not found in Drive")

    def walk_segments(service, folder_id, segments):
        seen["walk_segments"].append((folder_id, segments[0]))
        key = (folder_id, segments[0])
        if key in folders:
            return folders[key]
        raise DrivePathError(f"folder '{segments[0]}' not found in Drive")

    monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)
    monkeypatch.setattr("gdrives.resolve.walk_segments", walk_segments)
    return seen


class TestCheckArguments:
    def test_requires_a_source(self):
        with pytest.raises(ValueError, match="exactly one of SOURCE"):
            mv.check_arguments(None, "new.txt", None, None, None)

    def test_rejects_both_source_forms(self):
        with pytest.raises(ValueError, match="exactly one of SOURCE"):
            mv.check_arguments("a", "new.txt", "ID", None, None)

    def test_rejects_dest_with_dest_flags(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            mv.check_arguments("a", "b", None, None, "new.txt")

    def test_requires_a_destination(self):
        with pytest.raises(ValueError, match="pass a DEST path"):
            mv.check_arguments("a", None, None, None, None)

    def test_rejects_empty_dest(self):
        # Empty is not None, so without this guard it reaches files.update as a
        # request with nothing to change.
        with pytest.raises(ValueError, match="DEST must not be empty"):
            mv.check_arguments("a", "   ", None, None, None)

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="--name must not be empty"):
            mv.check_arguments("a", None, None, None, "")


class TestSoleParent:
    def test_returns_the_single_parent(self):
        assert mv.sole_parent(item("a.txt", parents=["P"])) == "P"

    def test_no_parent_errors(self):
        with pytest.raises(ValueError, match="no parent folder"):
            mv.sole_parent(item("a.txt"))

    def test_multiple_parents_lists_them(self):
        with pytest.raises(ValueError, match="P1, P2"):
            mv.sole_parent(item("a.txt", parents=["P1", "P2"]))


class TestCheckDestination:
    def test_returns_the_folder(self):
        svc = FakeDriveService({"A": item("archive", id="A", folder=True)})
        folder = mv.check_destination(svc, "A", item("a.txt", parents=["P"]))
        assert folder["id"] == "A"

    def test_non_folder_destination_errors(self):
        svc = FakeDriveService({"A": item("notes.txt", id="A")})
        with pytest.raises(ValueError, match="is not a folder"):
            mv.check_destination(svc, "A", item("a.txt", parents=["P"]))

    def test_cross_drive_move_errors(self):
        svc = FakeDriveService(
            {"A": item("archive", id="A", folder=True, drive_id="D2")}
        )
        source = item("a.txt", parents=["P"], drive_id="D1")
        with pytest.raises(ValueError, match="between drives"):
            mv.check_destination(svc, "A", source)

    def test_folder_into_itself_errors(self):
        folder = item("A", id="A", parents=["P"], folder=True)
        svc = FakeDriveService({"A": folder})
        with pytest.raises(ValueError, match="into itself"):
            mv.check_destination(svc, "A", folder)

    def test_folder_into_own_descendant_errors(self):
        # B lives inside A, so moving A into B would make the tree cyclic.
        svc = FakeDriveService(
            {
                "A": item("A", id="A", parents=["P"], folder=True),
                "B": item("B", id="B", parents=["A"], folder=True),
            }
        )
        source = item("A", id="A", parents=["P"], folder=True)
        with pytest.raises(ValueError, match="is inside it"):
            mv.check_destination(svc, "B", source)

    def test_deeper_descendant_is_detected(self):
        svc = FakeDriveService(
            {
                "A": item("A", id="A", parents=["P"], folder=True),
                "B": item("B", id="B", parents=["A"], folder=True),
                "C": item("C", id="C", parents=["B"], folder=True),
            }
        )
        source = item("A", id="A", parents=["P"], folder=True)
        with pytest.raises(ValueError, match="is inside it"):
            mv.check_destination(svc, "C", source)

    def test_unrelated_folder_is_allowed(self):
        svc = FakeDriveService(
            {
                "B": item("B", id="B", parents=["ROOT"], folder=True),
                "ROOT": item("My Drive", id="ROOT", folder=True),
            }
        )
        source = item("A", id="A", parents=["P"], folder=True)
        assert mv.check_destination(svc, "B", source)["id"] == "B"

    def test_moving_a_file_skips_the_ancestry_walk(self):
        # A file can't contain anything, so no parent walk is needed.
        svc = FakeDriveService({"B": item("B", id="B", parents=["A"], folder=True)})
        source = item("notes.txt", id="F", parents=["P"])
        assert mv.check_destination(svc, "B", source)["id"] == "B"
        assert [c for c in svc.calls if c[1].get("fileId") == "A"] == []

    def test_existing_cycle_does_not_loop_forever(self):
        # Defensive: if the Drive already contains a cycle, the walk terminates.
        svc = FakeDriveService(
            {
                "B": item("B", id="B", parents=["C"], folder=True),
                "C": item("C", id="C", parents=["B"], folder=True),
            }
        )
        source = item("A", id="A", parents=["P"], folder=True)
        assert mv.check_destination(svc, "B", source)["id"] == "B"


class TestResolveDestination:
    def test_bare_name_is_a_rename(self):
        assert mv.resolve_destination(None, "renamed.txt") == (None, "renamed.txt")

    def test_drive_root_with_trailing_slash_is_a_move(self, monkeypatch):
        patch_paths(monkeypatch, {"My Drive": "R"}, {})
        assert mv.resolve_destination(None, "My Drive/") == ("R", None)

    def test_existing_folder_is_a_move(self, monkeypatch):
        patch_paths(monkeypatch, {"My Drive": "R"}, {("R", "archive"): "A"})
        assert mv.resolve_destination(None, "My Drive/archive") == ("A", None)

    def test_missing_final_segment_is_move_and_rename(self, monkeypatch):
        patch_paths(monkeypatch, {"My Drive/archive": "A"}, {})
        assert mv.resolve_destination(None, "My Drive/archive/new.txt") == (
            "A",
            "new.txt",
        )

    def test_parent_is_walked_only_once(self, monkeypatch):
        # Resolving the whole path first and falling back re-walked every
        # ancestor a second time; the parent-first order must not.
        seen = patch_paths(monkeypatch, {"My Drive/a/b": "B"}, {})
        mv.resolve_destination(None, "My Drive/a/b/new.txt")
        assert seen["resolve_path"] == ["My Drive/a/b"]
        assert seen["walk_segments"] == [("B", "new.txt")]

    def test_ambiguous_final_segment_propagates(self, monkeypatch):
        # Two folders share the name: treating that as "not found, so it must be
        # a new name" would move the item somewhere never asked for.
        def walk_segments(service, folder_id, segments):
            raise AmbiguousPathError("multiple items named 'archive' in path:")

        monkeypatch.setattr("gdrives.resolve.resolve_path", lambda path, service: "R")
        monkeypatch.setattr("gdrives.resolve.walk_segments", walk_segments)
        with pytest.raises(AmbiguousPathError, match="multiple items named"):
            mv.resolve_destination(None, "My Drive/archive")

    def test_rootless_path_errors(self, monkeypatch):
        patch_paths(monkeypatch, {}, {})
        with pytest.raises(DrivePathError, match="has no drive name"):
            mv.resolve_destination(None, "/new.txt")

    def test_missing_parent_propagates(self, monkeypatch):
        patch_paths(monkeypatch, {}, {})
        with pytest.raises(DrivePathError, match="not found"):
            mv.resolve_destination(None, "My Drive/nope/new.txt")


class TestApplyMove:
    def test_omits_remove_parents_when_absent(self):
        # Guards the helper for a caller that adds a parent without naming one
        # to detach; sending removeParents=None would be a malformed request.
        svc = FakeDriveService({"F": item("notes.txt")})
        mv.apply_move(svc, "F", add_parent="A")
        (update,) = svc.updates
        assert update["addParents"] == "A"
        assert "removeParents" not in update


class TestRun:
    def test_rename_in_place(self, monkeypatch, capsys):
        svc = FakeDriveService({"F": item("notes.txt", parents=["P"])})
        patch_service(monkeypatch, svc)

        mv.run("F", "renamed.txt")

        assert svc.updates == [
            {
                "fileId": "F",
                "fields": mv.MV_FIELDS,
                "supportsAllDrives": True,
                "body": {"name": "renamed.txt"},
            }
        ]
        assert "renamed.txt" in capsys.readouterr().out

    def test_move_into_folder(self, monkeypatch):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["P"]),
                "A": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)
        patch_paths(monkeypatch, {"My Drive": "R"}, {("R", "archive"): "A"})

        mv.run("F", "My Drive/archive")

        # Full-dict equality: a key corrupted only on the move branch would
        # escape assertions that check addParents/removeParents alone.
        assert svc.updates == [
            {
                "fileId": "F",
                "fields": mv.MV_FIELDS,
                "supportsAllDrives": True,
                "addParents": "A",
                "removeParents": "P",
            }
        ]

    def test_move_and_rename_in_one_call(self, monkeypatch, capsys):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["P"]),
                "A": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)
        patch_paths(monkeypatch, {"My Drive/archive": "A"}, {})

        mv.run("F", "My Drive/archive/new.txt")

        assert svc.updates == [
            {
                "fileId": "F",
                "fields": mv.MV_FIELDS,
                "supportsAllDrives": True,
                "body": {"name": "new.txt"},
                "addParents": "A",
                "removeParents": "P",
            }
        ]
        # Both halves of the action must reach the user, not just the first.
        out = capsys.readouterr().out
        assert "rename 'notes.txt' -> 'new.txt'" in out
        assert "and move it into 'archive'" in out

    def test_by_id_flags_skip_resolution(self, monkeypatch):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["P"]),
                "A": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)

        def boom(path, service):  # resolution must not be reached
            raise AssertionError("resolve_path should not be called")

        monkeypatch.setattr("gdrives.resolve.resolve_path", boom)

        mv.run(None, None, source_id="F", dest_id="A", name="new.txt")

        (update,) = svc.updates
        assert update["addParents"] == "A"
        assert update["body"] == {"name": "new.txt"}

    def test_name_alone_renames_by_id(self, monkeypatch):
        # --name with no DEST and no --dest-id is a valid rename-by-ID.
        svc = FakeDriveService({"F": item("notes.txt", parents=["P"])})
        patch_service(monkeypatch, svc)

        mv.run(None, None, source_id="F", name="new.txt")

        (update,) = svc.updates
        assert update["body"] == {"name": "new.txt"}
        assert "addParents" not in update

    def test_dest_id_alias_to_current_parent_is_not_a_move(self, monkeypatch):
        # --dest-id takes aliases like "root", which files.get answers with the
        # folder's real ID. Comparing the alias against the item's parent would
        # never match, sending a redundant add/remove of the very same folder.
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["R"]),
                "root": item("My Drive", id="R", folder=True),
            }
        )
        patch_service(monkeypatch, svc)

        mv.run(None, None, source_id="F", dest_id="root", name="new.txt")

        (update,) = svc.updates
        assert update["body"] == {"name": "new.txt"}
        assert "addParents" not in update  # already in that folder
        assert "removeParents" not in update

    def test_dest_id_alias_sends_the_canonical_id(self, monkeypatch):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["P"]),
                "alias": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)

        mv.run(None, None, source_id="F", dest_id="alias")

        (update,) = svc.updates
        assert update["addParents"] == "A"  # the real ID, not "alias"
        assert update["removeParents"] == "P"

    def test_dry_run_makes_no_update_and_stays_read_only(self, monkeypatch, capsys):
        svc = FakeDriveService({"F": item("notes.txt", parents=["P"])})
        rec = patch_service(monkeypatch, svc)

        mv.run("F", "renamed.txt", dry_run=True)

        assert svc.updates == []
        assert rec["scopes"] is None  # the read-only default
        assert capsys.readouterr().out.startswith("Would rename")

    def test_write_requests_the_drive_scope(self, monkeypatch):
        from gdrives.auth import DRIVE_WRITE_SCOPES

        svc = FakeDriveService({"F": item("notes.txt", parents=["P"])})
        rec = patch_service(monkeypatch, svc)

        mv.run("F", "renamed.txt")

        assert rec["scopes"] == DRIVE_WRITE_SCOPES

    def test_same_name_same_folder_is_a_no_op(self, monkeypatch, capsys):
        svc = FakeDriveService({"F": item("notes.txt", parents=["P"])})
        patch_service(monkeypatch, svc)

        mv.run("F", "notes.txt")

        assert svc.updates == []
        assert "Nothing to do" in capsys.readouterr().out

    def test_move_into_current_folder_still_renames(self, monkeypatch):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["A"]),
                "A": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)
        patch_paths(monkeypatch, {"My Drive/archive": "A"}, {})

        mv.run("F", "My Drive/archive/new.txt")

        (update,) = svc.updates
        assert update["body"] == {"name": "new.txt"}
        assert "addParents" not in update  # already in that folder


@pytest.mark.parametrize("dest", ["Team/2026", "Team/2026/"])
def test_destination_can_be_a_drive_name_containing_slashes(monkeypatch, dest):
    monkeypatch.setattr(
        "gdrives.drives.load",
        lambda: [{"id": "D", "name": "Team/2026", "type": "shared", "url": ""}],
    )
    patch_paths(monkeypatch, {}, {})
    assert mv.resolve_destination(None, dest) == ("D", None)


def test_trailing_slash_requires_existing_destination_folder(monkeypatch):
    monkeypatch.setattr("gdrives.drives.load", lambda: [])
    patch_paths(monkeypatch, {"My Drive": "R"}, {})
    with pytest.raises(DrivePathError, match="not found"):
        mv.resolve_destination(None, "My Drive/missing/")


def test_blank_final_destination_name_is_rejected(monkeypatch):
    monkeypatch.setattr("gdrives.drives.load", lambda: [])
    with pytest.raises(ValueError, match="destination name must not be empty"):
        mv.resolve_destination(None, "My Drive/   ")


@pytest.mark.parametrize(
    "source, source_id, dest_id, label",
    [
        ("", None, None, "SOURCE"),
        (None, " ", None, "--source-id"),
        ("F", None, "", "--dest-id"),
    ],
)
def test_blank_source_and_id_flags_are_rejected(source, source_id, dest_id, label):
    with pytest.raises(ValueError, match=f"{label} must not be empty"):
        mv.check_arguments(source, None, source_id, dest_id, "new")
