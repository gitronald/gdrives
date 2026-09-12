"""Tests for gdrives.mv — destination resolution, guard rails, and files.update.

A fake Drive service records every ``files.get``/``files.update`` call and
serves metadata from a dict, so both the request shape and the decision logic
are asserted without the API.
"""

from typing import Any

import pytest

from gdrives import mv
from gdrives.resolve import DrivePathError

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


class TestResolveDestination:
    def test_bare_name_is_a_rename(self):
        assert mv.resolve_destination(None, "renamed.txt") == (None, "renamed.txt")

    def test_existing_folder_is_a_move(self, monkeypatch):
        monkeypatch.setattr("gdrives.resolve.resolve_path", lambda path, service: "A")
        assert mv.resolve_destination(None, "My Drive/archive") == ("A", None)

    def test_missing_final_segment_is_move_and_rename(self, monkeypatch):
        def resolve_path(path, service):
            if path == "My Drive/archive":
                return "A"
            raise DrivePathError(f"folder 'new.txt' not found in Drive: {path}")

        monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)
        assert mv.resolve_destination(None, "My Drive/archive/new.txt") == (
            "A",
            "new.txt",
        )

    def test_rootless_path_propagates(self, monkeypatch):
        # "/new.txt" leaves no parent path to fall back to, so the original
        # not-found error stands rather than becoming a confusing second one.
        def resolve_path(path, service):
            raise DrivePathError("folder 'new.txt' not found in Drive")

        monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)
        with pytest.raises(DrivePathError, match="not found"):
            mv.resolve_destination(None, "/new.txt")

    def test_missing_parent_propagates(self, monkeypatch):
        def resolve_path(path, service):
            raise DrivePathError("folder 'nope' not found in Drive")

        monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)
        with pytest.raises(DrivePathError):
            mv.resolve_destination(None, "My Drive/nope/new.txt")


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
        monkeypatch.setattr("gdrives.resolve.resolve_path", lambda path, service: "A")

        mv.run("F", "My Drive/archive")

        (update,) = svc.updates
        assert update["addParents"] == "A"
        assert update["removeParents"] == "P"
        assert "body" not in update  # a pure move sends no name

    def test_move_and_rename_in_one_call(self, monkeypatch):
        svc = FakeDriveService(
            {
                "F": item("notes.txt", parents=["P"]),
                "A": item("archive", id="A", folder=True),
            }
        )
        patch_service(monkeypatch, svc)

        def resolve_path(path, service):
            if path == "My Drive/archive":
                return "A"
            raise DrivePathError("not found")

        monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)

        mv.run("F", "My Drive/archive/new.txt")

        (update,) = svc.updates
        assert update["body"] == {"name": "new.txt"}
        assert update["addParents"] == "A"
        assert update["removeParents"] == "P"

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

        def resolve_path(path, service):
            if path == "My Drive/archive":
                return "A"
            raise DrivePathError("not found")

        monkeypatch.setattr("gdrives.resolve.resolve_path", resolve_path)

        mv.run("F", "My Drive/archive/new.txt")

        (update,) = svc.updates
        assert update["body"] == {"name": "new.txt"}
        assert "addParents" not in update  # already in that folder
