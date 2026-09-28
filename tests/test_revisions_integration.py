"""Live integration tests for gdrives.revisions against the real Drive API v3.

These list the revisions of the shared test spreadsheet and download its
newest revision as ``.xlsx``, and do the same for a binary file when the test
setup has one — checking what the fake-service unit tests in
test_revisions.py can't: the actual shape of `revisions.list`/`.get`, whether
an old native revision really is exportable, and what an export link's GET
returns.

The spreadsheet test runs **only** when a service account is available and
``GDRIVES_TEST_SPREADSHEET_ID`` points at a spreadsheet shared with it (see
test_sheets_integration.py); otherwise it skips, so CI without credentials
stays green. The binary-file test additionally needs ``GDRIVES_TEST_FILE_ID``,
naming a small binary file shared with the service account, and skips without
it (the shared test setup does not have one by default):

    export GDRIVES_TEST_FILE_ID=<id of a small binary file shared with the SA>

Nothing here writes, restores, or deletes anything — every request is one of
the five the module's hard rule allows. Select or skip the suite with
``-m integration`` / ``-m "not integration"``.
"""

import os

import pytest

import gdrives.auth  # import loads .env (python-dotenv), so a .env-set id is visible
from gdrives.revisions import download_revision, list_revisions

pytestmark = pytest.mark.integration

SPREADSHEET_ID_ENV = "GDRIVES_TEST_SPREADSHEET_ID"
FILE_ID_ENV = "GDRIVES_TEST_FILE_ID"


def _live_drive_service():
    """A read-only Drive service built straight from the service account, or None."""
    creds = gdrives.auth.authenticate_service_account(gdrives.auth.SCOPES)
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds)


@pytest.fixture(scope="session")
def spreadsheet_id():
    """The shared test spreadsheet's file ID, or skip."""
    sid = os.environ.get(SPREADSHEET_ID_ENV)
    if not sid:
        pytest.skip(
            f"set {SPREADSHEET_ID_ENV} to a sheet shared with the service account"
        )
    return sid


@pytest.fixture(scope="session")
def live_service():
    """The Drive service for the spreadsheet tests, or skip if unreachable."""
    service = _live_drive_service()
    if service is None:
        pytest.skip("no service account configured")
    return service


class TestSpreadsheetRevisions:
    def test_lists_and_downloads_the_newest_as_xlsx(
        self, live_service, spreadsheet_id, tmp_path
    ):
        revisions = list_revisions(live_service, spreadsheet_id)
        assert revisions  # a spreadsheet always has at least one revision
        newest = revisions[-1]
        assert newest.mime_type == "application/vnd.google-apps.spreadsheet"

        target = download_revision(
            live_service, spreadsheet_id, newest.id, str(tmp_path), mime_type="xlsx"
        )
        assert target.exists()
        assert target.suffix == ".xlsx"
        assert target.stat().st_size > 0


class TestBinaryFileRevisions:
    @pytest.fixture(scope="session")
    def file_id(self):
        fid = os.environ.get(FILE_ID_ENV)
        if not fid:
            pytest.skip(f"set {FILE_ID_ENV} to a binary file shared with the SA")
        return fid

    @pytest.fixture(scope="session")
    def service(self):
        service = _live_drive_service()
        if service is None:
            pytest.skip("no service account configured")
        return service

    def test_lists_and_downloads_the_newest(self, service, file_id, tmp_path):
        revisions = list_revisions(service, file_id)
        assert revisions
        newest = revisions[-1]
        assert newest.size is not None

        target = download_revision(service, file_id, newest.id, str(tmp_path))
        assert target.exists()
        assert target.stat().st_size == newest.size
