"""Live integration tests for gdrives.docs against the real Docs API v1.

These exercise the read, append, and find-and-replace paths — and the revision
guard — against a real document, so they catch what the fake-service unit tests
in test_docs.py can't: request-shape mismatches, scope problems, and how the
API actually renders and indexes content.

They run **only** when a service account is available and
``GDRIVES_TEST_DOCUMENT_ID`` points at a document shared with it as Editor;
otherwise every test skips, so CI without credentials stays green. The document
ID is read from the environment (or a gitignored ``.env``, which
``gdrives.auth`` loads on import) rather than hard-coded, since this is a public
repo. Set it to a throwaway document:

    export GDRIVES_TEST_DOCUMENT_ID=<id of a doc shared with the SA>

Each test appends its own uniquely tagged paragraph and removes it afterward,
so runs never collide with each other or leave state behind — the document's
existing content is never modified. The whole-body operations (``set_text``,
``clear_text``) would wipe a shared tab, so they run only on a temporary tab
the test adds and then deletes. Select or skip the suite with
``-m integration`` / ``-m "not integration"``.
"""

import os
import uuid

import pytest
from googleapiclient.errors import HttpError
from helpers import raw_text

import gdrives.auth  # import loads .env (python-dotenv), so a .env-set id is visible
from gdrives import docs

pytestmark = pytest.mark.integration

DOCUMENT_ID_ENV = "GDRIVES_TEST_DOCUMENT_ID"


@pytest.fixture(scope="session")
def live_service():
    """A (service, document_id) pair for the shared test document, or skip.

    Skips — never fails — when the document id is unset, no service account is
    configured, or the document can't be reached (offline, not shared, Docs API
    disabled). The service is built straight from the service account so the
    test path is deterministic, bypassing the OAuth-first precedence in
    authenticate().
    """
    did = os.environ.get(DOCUMENT_ID_ENV)
    if not did:
        pytest.skip(f"set {DOCUMENT_ID_ENV} to a doc shared with the service account")
    creds = gdrives.auth.authenticate_service_account(gdrives.auth.DOCS_WRITE_SCOPES)
    if creds is None:
        pytest.skip("no service account configured")
    from googleapiclient.discovery import build

    service = build("docs", "v1", credentials=creds)
    try:
        docs.pull_document(service, did)  # sanity: the SA can actually reach it
    except Exception as exc:  # network down, not shared, API disabled, bad id, ...
        pytest.skip(f"test document not reachable via service account: {exc}")
    return service, did


def _remove_tagged_paragraph(service, document_id: str, tag: str) -> None:
    """Delete the paragraph containing ``tag`` from the first tab, if present."""
    doc = docs.pull_document(service, document_id)
    for element in docs.tab_body(doc).get("content", []):
        if "paragraph" in element and tag in docs.body_text([element]):
            # The tagged paragraph was appended, so it is the body's last one and
            # its own newline is the undeletable final newline: delete the text
            # together with the newline that *precedes* it instead.
            span = {
                "startIndex": element["startIndex"] - 1,
                "endIndex": element["endIndex"] - 1,
            }
            docs.batch_update(
                service,
                document_id,
                [{"deleteContentRange": {"range": span}}],
                required_revision=doc["revisionId"],
            )
            return


@pytest.fixture
def tagged(live_service):
    """Yield (service, document_id, tag) after appending a unique tagged paragraph.

    The paragraph is removed afterward, even if the test fails or changes its
    text — as long as the tag itself survives — so the document is left as found.
    """
    service, did = live_service
    tag = "itest_" + uuid.uuid4().hex[:8]
    docs.append_text(service, did, "\n" + tag)
    try:
        yield service, did, tag
    finally:
        _remove_tagged_paragraph(service, did, tag)


def test_appended_paragraph_is_readable(tagged):
    service, did, tag = tagged
    doc = docs.pull_document(service, did)
    assert doc["revisionId"]
    assert docs.document_text(doc).endswith(tag + "\n")
    assert docs.count_occurrences(doc, tag) == 1
    assert docs.count_occurrences(doc, tag.upper(), match_case=False) == 1


def test_append_with_explicit_tab_continues_last_line(tagged):
    service, did, tag = tagged
    tab_id = docs.first_tab_id(docs.pull_document(service, did))
    docs.append_text(service, did, " (tab)", tab_id=tab_id)
    assert docs.document_text(docs.pull_document(service, did)).endswith(
        f"{tag} (tab)\n"
    )


def test_replace_text_changes_only_the_tagged_phrase(tagged):
    service, did, tag = tagged
    before = raw_text(docs.pull_document(service, did))
    assert docs.replace_text(service, did, tag, f"{tag}-final") == 1
    after = raw_text(docs.pull_document(service, did))
    assert after == before.replace(tag, f"{tag}-final")


def test_replace_with_stale_revision_is_refused(tagged):
    service, did, tag = tagged
    stale = docs.pull_document(service, did)
    docs.append_text(service, did, " edited")  # a collaborator moves the doc on
    with pytest.raises(HttpError):
        docs.replace_text(
            service, did, tag, f"{tag}-x", required_revision=stale["revisionId"]
        )
    assert f"{tag}-x" not in raw_text(docs.pull_document(service, did))


def test_body_range_excludes_the_final_newline(tagged):
    service, did, _tag = tagged
    doc = docs.pull_document(service, did)
    content = docs.tab_body(doc)["content"]
    start, end = docs.body_range(doc)
    assert start == 1
    assert end == content[-1]["endIndex"] - 1


def test_tab_resolution(live_service):
    service, did = live_service
    doc = docs.pull_document(service, did)
    tab_id = docs.first_tab_id(doc)
    assert tab_id is not None
    assert docs.resolve_tab_id(doc, tab_id) == tab_id
    title = next(iter(docs.iter_tabs(doc)))["tabProperties"]["title"]
    assert docs.resolve_tab_id(doc, title) == tab_id


@pytest.fixture
def scratch_tab(live_service):
    """Yield (service, document_id, tab_id) for a temporary tab, deleted after.

    The whole-body writes replace everything in a tab, so they get a tab of
    their own instead of one the shared document keeps.
    """
    service, did = live_service
    title = "itest_" + uuid.uuid4().hex[:8]
    added = docs.batch_update(
        service, did, [{"addDocumentTab": {"tabProperties": {"title": title}}}]
    )
    tab_id = added["replies"][0]["addDocumentTab"]["tabProperties"]["tabId"]
    try:
        yield service, did, tab_id
    finally:
        docs.batch_update(service, did, [{"deleteTab": {"tabId": tab_id}}])


def _seed_formatted_body(service, did: str, tab_id: str) -> None:
    """Fill the tab with a centered heading over a bulleted list ending in bold."""

    def span(start: int, end: int) -> dict:
        return {"startIndex": start, "endIndex": end, "tabId": tab_id}

    # "Title\n" is [1, 7), "item one\n" [7, 16), "item two\n" [16, 25).
    docs.batch_update(
        service,
        did,
        [
            {
                "insertText": {
                    "text": "Title\nitem one\nitem two",
                    "location": {"index": 1, "tabId": tab_id},
                }
            },
            {
                "updateParagraphStyle": {
                    "range": span(1, 7),
                    "paragraphStyle": {
                        "namedStyleType": "HEADING_1",
                        "alignment": "CENTER",
                    },
                    "fields": "namedStyleType,alignment",
                }
            },
            {
                "createParagraphBullets": {
                    "range": span(7, 25),
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            },
            {
                "updateTextStyle": {
                    "range": span(16, 25),
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            },
        ],
    )


def _assert_plain(paragraph: dict) -> None:
    """No list, heading, alignment, indent, or text formatting survives."""
    assert "bullet" not in paragraph
    style = paragraph.get("paragraphStyle", {})
    assert style.get("namedStyleType") == "NORMAL_TEXT"
    assert style.get("alignment", "START") == "START"
    assert not style.get("indentStart", {}).get("magnitude")
    assert not style.get("indentFirstLine", {}).get("magnitude")
    for element in paragraph["elements"]:
        assert element["textRun"].get("textStyle", {}) == {}


def _paragraphs(service, did: str, tab_id: str) -> list[dict]:
    doc = docs.pull_document(service, did)
    content = docs.tab_body(doc, tab_id)["content"]
    return [element["paragraph"] for element in content if "paragraph" in element]


def test_set_text_replaces_formatting_with_plain_paragraphs(scratch_tab):
    service, did, tab_id = scratch_tab
    _seed_formatted_body(service, did, tab_id)
    (last,) = _paragraphs(service, did, tab_id)[-1:]
    assert "bullet" in last  # the leftover paragraph would carry this over

    doc = docs.pull_document(service, did)
    docs.set_text(service, did, "plain one\nplain two", doc=doc, tab_id=tab_id)

    paragraphs = _paragraphs(service, did, tab_id)
    assert docs.document_text(docs.pull_document(service, did), tab_id) == (
        "plain one\nplain two\n"
    )
    assert len(paragraphs) == 2
    for paragraph in paragraphs:
        _assert_plain(paragraph)


def test_clear_text_leaves_one_plain_paragraph(scratch_tab):
    service, did, tab_id = scratch_tab
    _seed_formatted_body(service, did, tab_id)

    docs.clear_text(service, did, doc=docs.pull_document(service, did), tab_id=tab_id)

    (paragraph,) = _paragraphs(service, did, tab_id)
    assert docs.document_text(docs.pull_document(service, did), tab_id) == "\n"
    _assert_plain(paragraph)
