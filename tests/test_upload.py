"""Tests for gdrives.upload — what decides the operation, the write, the read-back.

A fake Drive ``files`` resource holds files with their content, so a create
or a replace is asserted by what Drive holds afterwards as well as by the
request sent.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from helpers import FOLDER_MIME, SHEET_MIME, FakeDriveFiles, patch_drive_service

from gdrives import upload
from gdrives.resolve import DrivePathError

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


def folder(id: str = "D", name: str = "reports") -> dict[str, Any]:
    return {"id": id, "name": name, "mimeType": FOLDER_MIME, "parents": ["root"]}


def held(
    name: str,
    *,
    id: str = "F",
    parent: str = "D",
    content: bytes | None = b"old",
    mime: str = "application/pdf",
) -> dict[str, Any]:
    """A file Drive already holds."""
    item: dict[str, Any] = {
        "id": id,
        "name": name,
        "mimeType": mime,
        "parents": [parent],
        "webViewLink": f"https://drive.google.com/file/d/{id}/view?usp=drivesdk",
    }
    if content is not None:
        item["content"] = content
    return item


@pytest.fixture
def local(tmp_path: Path) -> Path:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF new content")
    return path


def patch_folders(monkeypatch: pytest.MonkeyPatch, paths: dict[str, Any]) -> None:
    """Make ``resolve_folder`` answer from ``paths``, a path to its result."""

    def resolve_folder(service: Any, dest: str) -> tuple[str, str | None]:
        if dest not in paths:
            raise DrivePathError(f"folder '{dest}' not found in Drive")
        return paths[dest]

    monkeypatch.setattr("gdrives.mv.resolve_folder", resolve_folder)


class TestCheckArguments:
    @pytest.mark.parametrize(
        "dest, dest_id, file_id",
        [(None, None, None), ("My Drive/a", "D", None), ("My Drive/a", None, "F")],
    )
    def test_exactly_one_target(self, dest, dest_id, file_id):
        with pytest.raises(ValueError, match="exactly one of DEST"):
            upload.check_arguments(dest, dest_id, file_id, None)

    @pytest.mark.parametrize(
        "dest, dest_id, file_id, name, label",
        [
            (" ", None, None, None, "DEST"),
            (None, "", None, None, "--dest-id"),
            (None, None, " ", None, "--file-id"),
            (None, "D", None, "", "--name"),
        ],
    )
    def test_blank_values_are_rejected(self, dest, dest_id, file_id, name, label):
        with pytest.raises(ValueError, match=f"{label} must not be empty"):
            upload.check_arguments(dest, dest_id, file_id, name)

    @pytest.mark.parametrize("dest, file_id", [("My Drive/a", None), (None, "F")])
    def test_name_goes_with_dest_id_only(self, dest, file_id):
        with pytest.raises(ValueError, match="--name goes with --dest-id"):
            upload.check_arguments(dest, None, file_id, "new.pdf")

    def test_name_with_dest_id_is_accepted(self):
        upload.check_arguments(None, "D", None, "new.pdf")


class TestLocal:
    def test_mime_type_is_guessed_from_the_extension(self):
        assert upload.guess_mime_type(Path("a/report.pdf")) == "application/pdf"

    def test_unknown_extension_is_a_byte_stream(self):
        assert upload.guess_mime_type(Path("data.zzzz")) == upload.DEFAULT_MIME_TYPE

    def test_digest_is_size_and_md5(self, local):
        content = local.read_bytes()
        assert upload.local_digest(local) == (
            len(content),
            hashlib.md5(content).hexdigest(),
        )

    def test_digest_of_an_empty_file(self, tmp_path):
        path = tmp_path / "empty"
        path.write_bytes(b"")
        assert upload.local_digest(path) == (0, hashlib.md5(b"").hexdigest())

    def test_missing_local_file_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            upload.check_local(str(tmp_path / "nope.pdf"))

    def test_a_directory_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="one file per run"):
            upload.check_local(str(tmp_path))


class TestFindNamed:
    def test_matches_without_regard_to_case_and_skips_folders(self):
        svc = FakeDriveFiles(
            [
                folder(),
                held("Report.pdf", id="B"),
                held("report.pdf", id="A"),
                held("other.pdf", id="C"),
                {**folder("E", "report.pdf"), "parents": ["D"]},
                held("report.pdf", id="Z", parent="elsewhere"),
            ]
        )
        found = upload.find_named(svc, "D", "report.pdf")
        assert [f["id"] for f in found] == ["A", "B"]

    def test_query_escapes_the_name_and_reaches_shared_drives(self):
        svc = FakeDriveFiles([folder(), held("it's.pdf")])
        (found,) = upload.find_named(svc, "D", "it's.pdf")
        assert found["id"] == "F"
        (call,) = svc.named("list")
        assert call["q"] == (
            "'D' in parents and name = 'it\\'s.pdf' and trashed = false"
        )
        assert call["supportsAllDrives"] is True
        assert call["includeItemsFromAllDrives"] is True

    def test_every_page_is_read(self):
        files = [held("report.pdf", id=f"F{i}") for i in range(3)]
        svc = FakeDriveFiles([folder(), *files], pages=2)
        assert len(upload.find_named(svc, "D", "report.pdf")) == 3
        assert len(svc.named("list")) == 2


class TestPlanUpload:
    def test_no_file_of_that_name_is_a_create(self, local):
        svc = FakeDriveFiles([folder()])
        plan = upload.plan_upload(svc, local, folder_id="D")
        content = local.read_bytes()
        assert plan == upload.UploadPlan(
            operation="create",
            name="report.pdf",
            folder={"id": "D", "name": "reports", "mimeType": FOLDER_MIME}
            | {"parents": ["root"]},
            file_id=None,
            mime_type="application/pdf",
            size=len(content),
            md5=hashlib.md5(content).hexdigest(),
        )

    def test_one_file_of_that_name_is_a_replace(self, local):
        svc = FakeDriveFiles([folder(), held("Report.pdf")])
        plan = upload.plan_upload(svc, local, folder_id="D")
        assert (plan.operation, plan.file_id) == ("replace", "F")
        # The file keeps the name it has.
        assert plan.name == "Report.pdf"

    def test_name_overrides_the_local_name(self, local):
        svc = FakeDriveFiles([folder(), held("report.pdf"), held("q3.pdf", id="Q")])
        plan = upload.plan_upload(svc, local, folder_id="D", name="q3.pdf")
        assert (plan.operation, plan.file_id) == ("replace", "Q")

    def test_several_files_of_that_name_are_refused_with_their_ids(self, local):
        svc = FakeDriveFiles(
            [folder(), held("report.pdf", id="B"), held("report.pdf", id="A")]
        )
        with pytest.raises(ValueError) as exc:
            upload.plan_upload(svc, local, folder_id="D")
        assert str(exc.value).split("\n") == [
            "2 files named 'report.pdf' in 'reports'; upload will not guess "
            "which to replace. Pass --file-id with one of:",
            "  report.pdf  A",
            "  report.pdf  B",
        ]

    def test_a_native_file_of_that_name_is_refused(self, local):
        svc = FakeDriveFiles(
            [folder(), held("report.pdf", mime=SHEET_MIME, content=None)]
        )
        with pytest.raises(ValueError, match="Google-native file"):
            upload.plan_upload(svc, local, folder_id="D")

    def test_file_id_names_the_file_to_replace(self, local):
        svc = FakeDriveFiles(
            [folder(), held("report.pdf", id="B"), held("report.pdf", id="A")]
        )
        plan = upload.plan_upload(svc, local, file_id="B")
        assert (plan.operation, plan.file_id, plan.folder) == ("replace", "B", None)
        assert svc.named("list") == []

    def test_file_id_of_a_native_file_is_refused(self, local):
        svc = FakeDriveFiles([held("Budget", mime=SHEET_MIME, content=None)])
        with pytest.raises(ValueError, match="cannot replace its content"):
            upload.plan_upload(svc, local, file_id="F")

    def test_file_id_of_a_folder_is_refused(self, local):
        svc = FakeDriveFiles([folder()])
        with pytest.raises(ValueError, match="is a folder"):
            upload.plan_upload(svc, local, file_id="D")

    def test_a_destination_that_is_not_a_folder_is_refused(self, local):
        svc = FakeDriveFiles([held("report.pdf")])
        with pytest.raises(ValueError, match="is not a folder"):
            upload.plan_upload(svc, local, folder_id="F")

    @pytest.mark.parametrize(
        "kwargs", [{}, {"folder_id": "D", "file_id": "F"}], ids=["neither", "both"]
    )
    def test_takes_a_folder_or_a_file(self, local, kwargs):
        with pytest.raises(ValueError, match="exactly one of folder_id or file_id"):
            upload.plan_upload(FakeDriveFiles([]), local, **kwargs)

    def test_mime_type_can_be_set(self, local):
        svc = FakeDriveFiles([folder()])
        plan = upload.plan_upload(svc, local, folder_id="D", mime_type="text/plain")
        assert plan.mime_type == "text/plain"

    def test_planning_writes_nothing(self, local):
        svc = FakeDriveFiles([folder(), held("report.pdf")])
        upload.plan_upload(svc, local, folder_id="D")
        assert {name for name, _ in svc.calls} == {"get", "list"}


class TestDescribe:
    def test_create(self, local):
        svc = FakeDriveFiles([folder()])
        plan = upload.plan_upload(svc, local, folder_id="D")
        assert upload.describe(plan) == (
            "create 'report.pdf' in 'reports' (D): 16 bytes, application/pdf"
        )

    def test_replace_by_id_names_no_folder(self, local):
        svc = FakeDriveFiles([held("report.pdf")])
        plan = upload.plan_upload(svc, local, file_id="F")
        assert upload.describe(plan) == (
            "replace the content of 'report.pdf' (F): 16 bytes, application/pdf"
        )

    def test_names_are_escaped_for_the_terminal(self, local):
        svc = FakeDriveFiles([folder(name="re\x1b[2Jports"), held("a\x07.pdf")])
        plan = upload.plan_upload(svc, local, folder_id="D", name="a\x07.pdf")
        text = upload.describe(plan)
        assert "\x1b" not in text and "\x07" not in text


class TestUploadFile:
    def test_create_puts_the_file_in_the_folder(self, local):
        svc = FakeDriveFiles([folder()])
        meta = upload.upload_file(svc, local, folder_id="D")

        (create,) = svc.named("create")
        assert create["body"] == {"name": "report.pdf", "parents": ["D"]}
        assert create["supportsAllDrives"] is True
        assert create["media_body"].resumable() is True
        assert create["media_body"].mimetype() == "application/pdf"
        assert svc.items[meta["id"]]["content"] == local.read_bytes()
        assert svc.named("update") == []

    def test_replace_keeps_the_id_the_name_and_the_parents(self, local):
        svc = FakeDriveFiles([folder(), held("Report.pdf")])
        meta = upload.upload_file(svc, local, folder_id="D")

        (update,) = svc.named("update")
        assert update["fileId"] == "F"
        assert "body" not in update
        assert "addParents" not in update and "removeParents" not in update
        assert update["supportsAllDrives"] is True
        assert update["media_body"].resumable() is True
        assert meta["id"] == "F"
        assert svc.items["F"]["name"] == "Report.pdf"
        assert svc.items["F"]["parents"] == ["D"]
        assert svc.items["F"]["content"] == local.read_bytes()
        assert svc.named("create") == []

    def test_a_create_with_no_folder_names_no_parent(self, local):
        # Drive puts a file with no parent in the root of My Drive.
        svc = FakeDriveFiles([])
        size, md5 = upload.local_digest(local)
        plan = upload.UploadPlan(
            "create", "report.pdf", None, None, "application/pdf", size, md5
        )
        upload.apply_upload(svc, local, plan)
        (create,) = svc.named("create")
        assert create["body"] == {"name": "report.pdf"}

    def test_the_file_is_read_back(self, local):
        svc = FakeDriveFiles([folder()])
        meta = upload.upload_file(svc, local, folder_id="D")
        last = svc.calls[-1]
        assert last == (
            "get",
            {
                "fileId": meta["id"],
                "fields": upload.UPLOAD_FIELDS,
                "supportsAllDrives": True,
            },
        )
        assert meta["md5Checksum"] == hashlib.md5(local.read_bytes()).hexdigest()

    def test_a_file_that_reads_back_different_is_an_error(self, local):
        svc = FakeDriveFiles([folder(), held("report.pdf")])
        svc.corrupt = True
        with pytest.raises(upload.UploadError) as exc:
            upload.upload_file(svc, local, folder_id="D")
        message = str(exc.value)
        assert message.startswith(
            "'report.pdf' (F) does not read back as the local file: "
            "size 15 (local 16), md5 "
        )

    def test_an_empty_file_reads_back_as_itself(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_bytes(b"")
        svc = FakeDriveFiles([folder()])
        meta = upload.upload_file(svc, path, folder_id="D")
        assert meta["size"] == "0"

    def test_a_file_drive_reports_no_size_for_is_an_error(self, local):
        svc = FakeDriveFiles([folder(), held("report.pdf")])
        plan = upload.plan_upload(svc, local, folder_id="D")
        del svc.items["F"]["content"]
        with pytest.raises(upload.UploadError, match=r"size None \(local 16\)"):
            upload.verify(svc, "F", plan)

    def test_missing_local_file_is_refused_before_any_request(self, tmp_path):
        svc = FakeDriveFiles([folder()])
        with pytest.raises(ValueError, match="does not exist"):
            upload.upload_file(svc, tmp_path / "nope.pdf", folder_id="D")
        assert svc.calls == []


class TestRun:
    def test_upload_into_a_folder_path(self, monkeypatch, capsys, local):
        svc = FakeDriveFiles([folder()])
        rec = patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {"My Drive/reports": ("D", None)})

        upload.run(str(local), "My Drive/reports")

        assert rec["scopes"] == DRIVE_SCOPE
        (create,) = svc.named("create")
        assert create["body"] == {"name": "report.pdf", "parents": ["D"]}
        out = capsys.readouterr()
        assert out.out == "https://drive.google.com/file/d/new1\n"
        assert out.err.split("\n")[:2] == [
            "File ID: new1",
            "Done: create 'report.pdf' in 'reports' (D): 16 bytes, application/pdf",
        ]

    def test_final_segment_is_the_name(self, monkeypatch, local):
        svc = FakeDriveFiles([folder()])
        patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {"My Drive/reports/q3.pdf": ("D", "q3.pdf")})

        upload.run(str(local), "My Drive/reports/q3.pdf")

        (create,) = svc.named("create")
        assert create["body"] == {"name": "q3.pdf", "parents": ["D"]}

    def test_replace_prints_the_same_url(self, monkeypatch, capsys, local):
        svc = FakeDriveFiles([folder(), held("report.pdf")])
        patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {"My Drive/reports": ("D", None)})

        upload.run(str(local), "My Drive/reports")

        assert svc.items["F"]["content"] == local.read_bytes()
        out = capsys.readouterr()
        assert out.out == "https://drive.google.com/file/d/F\n"
        assert "File ID: F\n" in out.err

    def test_dest_id_and_name_skip_resolution(self, monkeypatch, local):
        svc = FakeDriveFiles([folder()])
        patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {})

        upload.run(str(local), dest_id="D", name="q3.pdf", mime_type="text/plain")

        (create,) = svc.named("create")
        assert create["body"] == {"name": "q3.pdf", "parents": ["D"]}
        assert create["media_body"].mimetype() == "text/plain"

    def test_file_id_replaces_that_file(self, monkeypatch, local):
        svc = FakeDriveFiles(
            [folder(), held("report.pdf", id="A"), held("report.pdf", id="B")]
        )
        patch_drive_service(monkeypatch, svc)

        upload.run(str(local), file_id="B")

        assert svc.items["B"]["content"] == local.read_bytes()
        assert svc.items["A"]["content"] == b"old"

    def test_dry_run_writes_nothing_and_stays_read_only(
        self, monkeypatch, capsys, local
    ):
        svc = FakeDriveFiles([folder(), held("report.pdf")])
        rec = patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {"My Drive/reports": ("D", None)})

        upload.run(str(local), "My Drive/reports", dry_run=True)

        assert rec["scopes"] is None
        assert svc.named("create") == svc.named("update") == []
        assert svc.items["F"]["content"] == b"old"
        assert capsys.readouterr().out == (
            "Would replace the content of 'report.pdf' (F) in 'reports' (D): "
            "16 bytes, application/pdf\n"
        )

    def test_checksum_mismatch_names_the_file_first(self, monkeypatch, capsys, local):
        svc = FakeDriveFiles([folder()])
        svc.corrupt = True
        patch_drive_service(monkeypatch, svc)

        with pytest.raises(upload.UploadError, match="does not read back"):
            upload.run(str(local), dest_id="D")

        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == "File ID: new1\n"

    def test_missing_local_file_is_refused_before_authenticating(
        self, monkeypatch, tmp_path
    ):
        def build(scopes=None):
            raise AssertionError("authenticated")

        monkeypatch.setattr("gdrives.auth.build_drive_service", build)
        with pytest.raises(ValueError, match="does not exist"):
            upload.run(str(tmp_path / "nope.pdf"), "My Drive/reports")

    def test_destination_whose_parent_does_not_exist(self, monkeypatch, local):
        svc = FakeDriveFiles([folder()])
        patch_drive_service(monkeypatch, svc)
        patch_folders(monkeypatch, {})

        with pytest.raises(DrivePathError, match="not found"):
            upload.run(str(local), "My Drive/nope/report.pdf")
        assert svc.calls == []

    def test_blank_mime_type_is_rejected(self, local):
        with pytest.raises(ValueError, match="--mime-type must not be empty"):
            upload.run(str(local), "My Drive/reports", mime_type=" ")


class TestResolveFolder:
    """``mv.resolve_folder`` as ``upload`` uses it: a bare name is a drive."""

    def test_bare_drive_name_is_its_root(self, monkeypatch):
        from gdrives import mv

        monkeypatch.setattr(
            "gdrives.resolve.resolve_path",
            lambda path, service: {"My Drive": "R"}[path],
        )
        assert mv.resolve_folder(None, "My Drive") == ("R", None)

    def test_bare_name_that_is_no_drive_is_refused(self, monkeypatch):
        from gdrives import mv

        monkeypatch.setattr("gdrives.drives.load", lambda: [])
        with pytest.raises(DrivePathError, match="No drive matching 'report.pdf'"):
            mv.resolve_folder(None, "report.pdf")
