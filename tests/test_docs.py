"""Tests for gdrives.docs — reading, index-safe writes, tabs, and entry points.

The core helpers run against ``FakeDocsService`` (tests/helpers.py), a stateful
fake that renders its plain-text tabs as real Docs structural elements with
correct indices and applies batchUpdate requests to them, so index arithmetic,
request ordering, and the revision guard are exercised end to end. Hand-built
element trees cover the parts of the document model the fake does not render
(lists, tables, tables of contents, non-text elements). The ``run_*`` entry
points patch ``build_docs_service`` at its source (``gdrives.auth``) since they
import it lazily.
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeDocsService, raw_text

from gdrives.docs import (
    append_text,
    batch_update,
    body_range,
    body_text,
    clear_text,
    count_occurrences,
    create_document,
    document_text,
    first_tab_id,
    iter_tabs,
    pull_document,
    read_text_file,
    replace_text,
    resolve_tab_id,
    run_append,
    run_clear,
    run_create,
    run_get,
    run_replace,
    run_update,
    set_text,
    tab_body,
    url_tab_id,
)

# -- element builders (the parts of the model the fake does not render) --


def paragraph(
    text: str,
    *,
    bullet: dict[str, Any] | None = None,
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A paragraph holding one text run, plus optional non-text elements."""
    para: dict[str, Any] = {"elements": [{"textRun": {"content": text}}]}
    if extra:
        para["elements"].extend(extra)
    if bullet is not None:
        para["bullet"] = bullet
    return {"paragraph": para}


def table(rows: list[list[str]]) -> dict[str, Any]:
    """A table whose cells each hold one paragraph of the given text."""
    return {
        "table": {
            "tableRows": [
                {"tableCells": [{"content": [paragraph(c + "\n")]} for c in row]}
                for row in rows
            ]
        }
    }


def doc_with(content: list[dict[str, Any]], *, tab_id: str = "t.0") -> dict[str, Any]:
    """A tabs-populated document whose only tab holds ``content``."""
    return {
        "documentId": "DOC",
        "revisionId": "rev-1",
        "tabs": [
            {
                "tabProperties": {"tabId": tab_id, "title": "Tab 1", "index": 0},
                "documentTab": {"body": {"content": content}},
            }
        ],
    }


def plain(tab_id: str | None = None) -> list[dict[str, Any]]:
    """The requests resetting the body's leftover paragraph at [1, 2) to plain."""

    def span() -> dict[str, Any]:
        rng: dict[str, Any] = {"startIndex": 1, "endIndex": 2}
        if tab_id is not None:
            rng["tabId"] = tab_id
        return rng

    return [
        {"deleteParagraphBullets": {"range": span()}},
        {
            "updateParagraphStyle": {
                "range": span(),
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "*",
            }
        },
        {"updateTextStyle": {"range": span(), "textStyle": {}, "fields": "*"}},
    ]


def patch_service(monkeypatch, svc):
    """Patch build_docs_service to return ``svc``; record the scopes it got."""
    rec = {}
    monkeypatch.setattr(
        "gdrives.auth.build_docs_service",
        lambda scopes=None: rec.update(scopes=scopes) or svc,
    )
    return rec


# -- pull_document / create_document --


class TestPullDocument:
    def test_requests_every_tab_and_returns_document(self):
        svc = FakeDocsService("Hello")
        doc = pull_document(svc, "DOC")
        assert svc.calls == [
            ("documents.get", {"documentId": "DOC", "includeTabsContent": True})
        ]
        assert doc["revisionId"] == "rev-1"
        assert document_text(doc) == "Hello\n"


class TestCreateDocument:
    def test_creates_titled_document_and_returns_id(self):
        svc = FakeDocsService()
        assert create_document(svc, "Notes") == "NEW_DOC"
        assert svc.calls == [("documents.create", {"body": {"title": "Notes"}})]
        assert svc.title == "Notes"


# -- tabs --

NESTED = {
    "tabs": [
        {
            "tabProperties": {"tabId": "a", "title": "Intro"},
            "childTabs": [{"tabProperties": {"tabId": "b", "title": "Detail"}}],
        },
        {"tabProperties": {"tabId": "c", "title": "Intro"}},
    ]
}


class TestTabs:
    def test_iter_tabs_descends_into_children_in_order(self):
        assert [t["tabProperties"]["tabId"] for t in iter_tabs(NESTED)] == [
            "a",
            "b",
            "c",
        ]

    def test_first_tab_id(self):
        assert first_tab_id(NESTED) == "a"
        assert first_tab_id({"body": {}}) is None
        assert first_tab_id({"tabs": []}) is None

    def test_resolve_by_id(self):
        assert resolve_tab_id(NESTED, "b") == "b"

    def test_resolve_by_unique_title(self):
        assert resolve_tab_id(NESTED, "Detail") == "b"

    def test_ambiguous_title_refuses_and_lists_ids(self):
        with pytest.raises(ValueError, match="ambiguous") as exc:
            resolve_tab_id(NESTED, "Intro")
        assert "'Intro' (a)" in str(exc.value) and "'Intro' (c)" in str(exc.value)

    def test_unknown_tab_lists_available(self):
        with pytest.raises(ValueError, match="not found; tabs: 'Intro' \\(a\\)"):
            resolve_tab_id(NESTED, "nope")

    def test_unknown_tab_with_no_tabs(self):
        with pytest.raises(ValueError, match="tabs: none"):
            resolve_tab_id({"tabs": []}, "nope")

    def test_tab_body_defaults_to_first_tab(self):
        doc = FakeDocsService(tabs={"t.0": "first", "t.1": "second"}).document()
        assert body_text(tab_body(doc)["content"]) == "first\n"
        assert body_text(tab_body(doc, "t.1")["content"]) == "second\n"

    def test_tab_body_unknown_tab_raises(self):
        with pytest.raises(ValueError, match="tab 'zz' not found"):
            tab_body(FakeDocsService("x").document(), "zz")

    def test_tab_body_no_tabs_raises(self):
        with pytest.raises(ValueError, match="not found"):
            tab_body({"tabs": []})

    @pytest.mark.parametrize(
        "source, tab",
        [
            ("https://docs.google.com/document/d/DOC/edit?tab=t.abc", "t.abc"),
            ("https://docs.google.com/document/d/DOC/edit?usp=x&tab=t.1#h=h.2", "t.1"),
            ("https://docs.google.com/document/d/DOC/edit", None),
            ("https://docs.google.com/document/d/DOC/edit?tab=", ""),
            ("https://drive.google.com/open?id=DOC&tab=t.abc", None),
            ("DOC", None),
            ("My Drive/notes", None),
        ],
    )
    def test_url_tab_id(self, source, tab):
        assert url_tab_id(source) == tab


# -- body_text / document_text --


class TestBodyText:
    def test_paragraphs_keep_newlines(self):
        assert body_text([paragraph("a\n"), paragraph("b\n")]) == "a\nb\n"

    def test_list_items_get_dash_prefix_indented_by_nesting(self):
        content = [
            paragraph("top\n", bullet={"listId": "l"}),
            paragraph("nested\n", bullet={"listId": "l", "nestingLevel": 1}),
        ]
        assert body_text(content) == "- top\n  - nested\n"

    def test_table_rows_join_cells_with_tabs(self):
        assert body_text([table([["a", "b"], ["c", "d"]])]) == "a\tb\nc\td\n"

    def test_multi_paragraph_cell_joins_with_spaces(self):
        cell = {"content": [paragraph("x\n"), paragraph("y\n")]}
        content = [{"table": {"tableRows": [{"tableCells": [cell]}]}}]
        assert body_text(content) == "x y\n"

    def test_table_of_contents_flattens(self):
        content = [{"tableOfContents": {"content": [paragraph("Heading\n")]}}]
        assert body_text(content) == "Heading\n"

    def test_non_text_elements_are_skipped(self):
        content = [
            {"sectionBreak": {}},
            paragraph(
                "See ",
                extra=[
                    {"inlineObjectElement": {"inlineObjectId": "img"}},
                    {"footnoteReference": {"footnoteId": "fn"}},
                    {"pageBreak": {}},
                    {"textRun": {"content": "\n"}},
                ],
            ),
        ]
        assert body_text(content) == "See \n"

    def test_document_text_targets_tab(self):
        doc = FakeDocsService(tabs={"t.0": "one", "t.1": "two"}).document()
        assert document_text(doc) == "one\n"
        assert document_text(doc, "t.1") == "two\n"


# -- raw_text / count_occurrences --


class TestRawText:
    def test_has_no_decoration(self):
        doc = doc_with([paragraph("Item\n", bullet={"listId": "l"}), table([["c"]])])
        assert document_text(doc) == "- Item\nc\n"
        # the table, its row, and its cell each hold an index: one placeholder each
        assert raw_text(doc) == "Item\n\ufffc\ufffc\ufffcc\n"

    def test_count_is_case_sensitive_by_default(self):
        doc = doc_with([paragraph("foo Foo foo\n")])
        assert count_occurrences(doc, "foo") == 2
        assert count_occurrences(doc, "foo", match_case=False) == 3

    def test_count_does_not_see_list_prefix(self):
        doc = doc_with([paragraph("x\n", bullet={"listId": "l"})])
        assert count_occurrences(doc, "- x") == 0

    def test_walks_table_of_contents(self):
        toc = {"tableOfContents": {"content": [paragraph("Heading\n")]}}
        text = raw_text(doc_with([toc, paragraph("body\n")]))
        assert text == "\ufffcHeading\nbody\n"

    def test_empty_search_text_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            count_occurrences(doc_with([]), "")

    def test_counts_headers_footers_and_footnotes(self):
        # replaceAllText edits every segment, so the count must see them all;
        # the readable text stays body-only.
        svc = FakeDocsService(
            "draft body",
            headers={"h.0": "draft header"},
            footers={"f.0": "draft footer"},
            footnotes={"n.0": "draft note"},
        )
        doc = svc.document()
        assert count_occurrences(doc, "draft") == 4
        # each segment the fake renders opens with a section break
        assert raw_text(doc).replace("\ufffc", "") == (
            "draft body\ndraft header\ndraft footer\ndraft note\n"
        )
        assert document_text(doc) == "draft body\n"

    def test_no_match_across_an_inline_image(self):
        # "See B" + image + "C now": the API's text has the image between B and
        # C, so replaceAllText finds no "BC", and neither must the count.
        doc = doc_with(
            [
                paragraph(
                    "See B",
                    extra=[
                        {"inlineObjectElement": {"inlineObjectId": "img"}},
                        {"textRun": {"content": "C now\n"}},
                    ],
                )
            ]
        )
        assert count_occurrences(doc, "BC") == 0
        assert count_occurrences(doc, "See B") == 1
        assert document_text(doc) == "See BC now\n"  # the readable view drops it

    def test_no_match_across_table_cells(self):
        doc = doc_with([table([["left", "right"]])])
        assert count_occurrences(doc, "left\nright") == 0
        assert count_occurrences(doc, "left\n") == 1


# -- body_range --


class TestBodyRange:
    def test_empty_body(self):
        assert body_range(FakeDocsService("").document()) == (1, 1)

    def test_excludes_final_newline(self):
        # "Hello\n" spans indices 1..7; the newline at 6 must stay.
        assert body_range(FakeDocsService("Hello").document()) == (1, 6)

    def test_no_content(self):
        assert body_range(doc_with([])) == (1, 1)

    def test_explicit_tab(self):
        doc = FakeDocsService(tabs={"t.0": "a", "t.1": "longer"}).document()
        assert body_range(doc, "t.1") == (1, 7)


# -- batch_update --


class TestBatchUpdate:
    def test_empty_requests_is_a_no_op(self):
        svc = FakeDocsService("x")
        assert batch_update(svc, "DOC", []) == {}
        assert svc.calls == []

    def test_sends_write_control_when_revision_given(self):
        svc = FakeDocsService("x")
        request = {"insertText": {"text": "y", "endOfSegmentLocation": {}}}
        result = batch_update(svc, "DOC", [request], required_revision="rev-1")
        assert svc.calls == [
            (
                "documents.batchUpdate",
                {
                    "documentId": "DOC",
                    "body": {
                        "requests": [request],
                        "writeControl": {"requiredRevisionId": "rev-1"},
                    },
                },
            )
        ]
        assert result["writeControl"] == {"requiredRevisionId": "rev-2"}
        assert svc.text == "xy\n"

    def test_omits_write_control_without_revision(self):
        svc = FakeDocsService("x")
        batch_update(
            svc, "DOC", [{"insertText": {"text": "y", "location": {"index": 1}}}]
        )
        assert "writeControl" not in svc.calls[0][1]["body"]
        assert svc.text == "yx\n"

    def test_stale_revision_is_refused(self):
        svc = FakeDocsService("x")
        request = {"insertText": {"text": "y", "endOfSegmentLocation": {}}}
        with pytest.raises(HttpError):
            batch_update(svc, "DOC", [request], required_revision="rev-0")
        assert svc.text == "x\n"


# -- append_text --


class TestAppendText:
    def test_inserts_before_final_newline(self):
        svc = FakeDocsService("Hello")
        append_text(svc, "DOC", " world")
        assert svc.text == "Hello world\n"
        assert svc.calls[0][1]["body"]["requests"] == [
            {"insertText": {"text": " world", "endOfSegmentLocation": {}}}
        ]

    def test_targets_tab(self):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        append_text(svc, "DOC", "\nc", tab_id="t.1")
        assert svc.tabs == {"t.0": "a\n", "t.1": "b\nc\n"}
        request = svc.calls[0][1]["body"]["requests"][0]
        assert request["insertText"]["endOfSegmentLocation"] == {"tabId": "t.1"}

    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            append_text(FakeDocsService("x"), "DOC", "")

    def test_required_revision_guards_the_insert(self):
        svc = FakeDocsService("Hello")
        append_text(svc, "DOC", "!", required_revision="rev-1")
        assert svc.calls[0][1]["body"]["writeControl"] == {
            "requiredRevisionId": "rev-1"
        }
        with pytest.raises(HttpError):
            append_text(svc, "DOC", "?", required_revision="rev-1")  # now rev-2
        assert svc.text == "Hello!\n"


# -- replace_text --


class TestReplaceText:
    def test_replaces_all_and_returns_count(self):
        svc = FakeDocsService("foo bar foo")
        assert replace_text(svc, "DOC", "foo", "baz") == 2
        assert svc.text == "baz bar baz\n"
        assert svc.calls[0][1]["body"]["requests"] == [
            {
                "replaceAllText": {
                    "containsText": {"text": "foo", "matchCase": True},
                    "replaceText": "baz",
                }
            }
        ]

    def test_ignore_case(self):
        svc = FakeDocsService("Foo foo")
        assert replace_text(svc, "DOC", "FOO", "x", match_case=False) == 2
        assert svc.text == "x x\n"

    def test_tab_restricts_replacement(self):
        svc = FakeDocsService(tabs={"t.0": "foo", "t.1": "foo"})
        assert replace_text(svc, "DOC", "foo", "bar", tab_id="t.1") == 1
        assert svc.tabs == {"t.0": "foo\n", "t.1": "bar\n"}
        request = svc.calls[0][1]["body"]["requests"][0]
        assert request["replaceAllText"]["tabsCriteria"] == {"tabIds": ["t.1"]}

    def test_no_tab_replaces_in_every_tab(self):
        # The API default for replaceAllText without tabsCriteria.
        svc = FakeDocsService(tabs={"t.0": "foo", "t.1": "foo"})
        assert replace_text(svc, "DOC", "foo", "bar") == 2

    def test_stale_revision_is_refused(self):
        svc = FakeDocsService("foo")
        with pytest.raises(HttpError):
            replace_text(svc, "DOC", "foo", "bar", required_revision="rev-0")
        assert svc.text == "foo\n"

    def test_missing_reply_counts_zero(self):
        svc = MagicMock()
        svc.documents().batchUpdate().execute.return_value = {}
        assert replace_text(svc, "DOC", "foo", "bar") == 0

    def test_empty_search_text_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            replace_text(FakeDocsService("x"), "DOC", "", "y")


# -- set_text --


class TestSetText:
    def test_overwrites_body_with_delete_reset_then_insert(self):
        svc = FakeDocsService("old text\nline 2")
        set_text(svc, "DOC", "new", doc=pull_document(svc, "DOC"))
        assert svc.text == "new\n"
        method, kwargs = svc.calls[1]
        assert method == "documents.batchUpdate"
        assert kwargs["body"] == {
            "requests": [
                {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 16}}},
                *plain(),
                {"insertText": {"text": "new", "location": {"index": 1}}},
            ],
            "writeControl": {"requiredRevisionId": "rev-1"},
        }
        assert svc.revision == "rev-2"

    def test_multi_line_text_becomes_paragraphs(self):
        svc = FakeDocsService("x")
        set_text(svc, "DOC", "a\nb", doc=pull_document(svc, "DOC"))
        assert document_text(pull_document(svc, "DOC")) == "a\nb\n"

    def test_empty_body_resets_style_then_inserts(self):
        svc = FakeDocsService("")
        set_text(svc, "DOC", "hi", doc=pull_document(svc, "DOC"))
        assert svc.text == "hi\n"
        assert svc.calls[1][1]["body"]["requests"] == [
            *plain(),
            {"insertText": {"text": "hi", "location": {"index": 1}}},
        ]

    def test_empty_text_deletes_and_resets_style(self):
        svc = FakeDocsService("abc")
        set_text(svc, "DOC", "", doc=pull_document(svc, "DOC"))
        assert svc.text == "\n"
        assert [list(r) for r in svc.calls[1][1]["body"]["requests"]] == [
            ["deleteContentRange"],
            ["deleteParagraphBullets"],
            ["updateParagraphStyle"],
            ["updateTextStyle"],
        ]

    def test_empty_body_and_text_still_resets_the_leftover_style(self):
        # An empty body can still be a heading or a list item.
        svc = FakeDocsService("")
        set_text(svc, "DOC", "", doc=pull_document(svc, "DOC"))
        assert svc.calls[1][1]["body"]["requests"] == plain()

    def test_stale_snapshot_is_refused_and_leaves_body_intact(self):
        svc = FakeDocsService("old")
        doc = pull_document(svc, "DOC")
        svc.edit_externally("changed by a collaborator")
        with pytest.raises(HttpError):
            set_text(svc, "DOC", "new", doc=doc)
        assert svc.text == "changed by a collaborator\n"

    def test_writes_from_the_given_snapshot_without_fetching(self):
        svc = FakeDocsService("old")
        doc = pull_document(svc, "DOC")
        svc.calls.clear()
        set_text(svc, "DOC", "new", doc=doc)
        assert [c[0] for c in svc.calls] == ["documents.batchUpdate"]

    def test_targets_tab(self):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        set_text(svc, "DOC", "z", doc=pull_document(svc, "DOC"), tab_id="t.1")
        assert svc.tabs == {"t.0": "a\n", "t.1": "z\n"}
        requests = svc.calls[1][1]["body"]["requests"]
        assert requests[0]["deleteContentRange"]["range"]["tabId"] == "t.1"
        assert requests[1:4] == plain("t.1")
        assert requests[4]["insertText"]["location"]["tabId"] == "t.1"


# -- clear_text --


class TestClearText:
    def test_clears_body_under_revision_guard(self):
        svc = FakeDocsService("abc\ndef")
        clear_text(svc, "DOC", doc=pull_document(svc, "DOC"))
        assert svc.text == "\n"
        body = svc.calls[1][1]["body"]
        assert body["requests"] == [
            {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 8}}},
            *plain(),
        ]
        assert body["writeControl"] == {"requiredRevisionId": "rev-1"}

    def test_empty_body_only_resets_the_style(self):
        svc = FakeDocsService("")
        clear_text(svc, "DOC", doc=pull_document(svc, "DOC"))
        assert svc.calls[1][1]["body"]["requests"] == plain()
        assert svc.text == "\n"

    def test_stale_snapshot_is_refused(self):
        svc = FakeDocsService("abc")
        doc = pull_document(svc, "DOC")
        svc.edit_externally("abcd")
        with pytest.raises(HttpError):
            clear_text(svc, "DOC", doc=doc)
        assert svc.text == "abcd\n"


# -- read_text_file --


class TestReadTextFile:
    def test_drops_one_trailing_newline(self, tmp_path):
        path = tmp_path / "body.txt"
        path.write_text("a\nb\n", encoding="utf-8")
        assert read_text_file(str(path)) == "a\nb"

    def test_keeps_content_without_newline(self, tmp_path):
        path = tmp_path / "body.txt"
        path.write_text("a\nb", encoding="utf-8")
        assert read_text_file(str(path)) == "a\nb"

    def test_drops_only_one_newline(self, tmp_path):
        path = tmp_path / "body.txt"
        path.write_text("a\n\n", encoding="utf-8")
        assert read_text_file(str(path)) == "a\n"

    def test_drops_a_utf8_bom(self, tmp_path):
        path = tmp_path / "body.txt"
        path.write_bytes(b"\xef\xbb\xbfHello\n")
        assert read_text_file(str(path)) == "Hello"


# -- run_get --


class TestRunGet:
    def test_prints_text_with_read_scope(self, monkeypatch, capsys):
        svc = FakeDocsService("Hello\nWorld")
        rec = patch_service(monkeypatch, svc)
        run_get("DOC")
        assert rec["scopes"] is None  # read-only default
        out = capsys.readouterr()
        assert out.out == "Hello\nWorld\n"
        assert "Document ID: DOC" in out.err

    def test_json_prints_raw_document(self, monkeypatch, capsys):
        svc = FakeDocsService("Hello")
        patch_service(monkeypatch, svc)
        run_get("DOC", as_json=True)
        out = capsys.readouterr().out
        assert out.endswith("\n")
        assert json.loads(out)["documentId"] == "DOC"

    def test_output_writes_file(self, monkeypatch, tmp_path, capsys):
        svc = FakeDocsService("Hello")
        patch_service(monkeypatch, svc)
        out = tmp_path / "sub" / "doc.txt"
        run_get("DOC", output=str(out))
        assert out.read_text(encoding="utf-8") == "Hello\n"
        assert "Wrote 6 character(s) to" in capsys.readouterr().err

    def test_tab_by_title(self, monkeypatch, capsys):
        svc = FakeDocsService(tabs={"t.0": "one", "t.1": "two"})
        patch_service(monkeypatch, svc)
        run_get("DOC", tab="Tab 2")
        assert capsys.readouterr().out == "two\n"

    def test_unknown_tab_raises(self, monkeypatch):
        patch_service(monkeypatch, FakeDocsService("x"))
        with pytest.raises(ValueError, match="tab 'nope' not found"):
            run_get("DOC", tab="nope")

    def test_url_tab_is_the_default(self, monkeypatch, capsys):
        svc = FakeDocsService(tabs={"t.0": "one", "t.1": "two"})
        patch_service(monkeypatch, svc)
        run_get("https://docs.google.com/document/d/DOC/edit?tab=t.1")
        assert capsys.readouterr().out == "two\n"

    def test_explicit_tab_wins_over_url_tab(self, monkeypatch, capsys):
        svc = FakeDocsService(tabs={"t.0": "one", "t.1": "two"})
        patch_service(monkeypatch, svc)
        run_get("https://docs.google.com/document/d/DOC/edit?tab=t.1", tab="Tab 1")
        assert capsys.readouterr().out == "one\n"

    def test_empty_tab_is_an_error_not_the_first_tab(self, monkeypatch):
        patch_service(monkeypatch, FakeDocsService(tabs={"t.0": "a", "t.1": "b"}))
        with pytest.raises(ValueError, match="tab '' not found"):
            run_get("DOC", tab="")


# -- run_update --


class TestRunUpdate:
    def test_yes_overwrites_with_write_scope(self, monkeypatch, tmp_path, capsys):
        from gdrives.auth import DOCS_WRITE_SCOPES

        svc = FakeDocsService("old")
        rec = patch_service(monkeypatch, svc)
        monkeypatch.setattr(
            "typer.confirm", lambda *a, **k: pytest.fail("must not prompt with yes")
        )
        body = tmp_path / "body.txt"
        body.write_text("new body\n", encoding="utf-8")
        run_update("DOC", str(body), yes=True)
        assert rec["scopes"] == DOCS_WRITE_SCOPES
        assert svc.text == "new body\n"
        assert "Replaced body with 8 character(s)" in capsys.readouterr().out

    def test_declined_confirm_aborts_before_any_write(
        self, monkeypatch, tmp_path, capsys
    ):
        svc = FakeDocsService("old", title="Notes")
        patch_service(monkeypatch, svc)
        prompts = []
        monkeypatch.setattr(
            "typer.confirm", lambda text, **k: prompts.append(text) or False
        )
        body = tmp_path / "body.txt"
        body.write_text("new", encoding="utf-8")
        run_update("DOC", str(body))
        assert prompts == ["Replace the entire body of 'Notes', tab 'Tab 1'?"]
        assert [c[0] for c in svc.calls] == ["documents.get"]
        assert "Aborted." in capsys.readouterr().err

    def test_accepted_confirm_writes_tab(self, monkeypatch, tmp_path):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        prompts = []
        monkeypatch.setattr(
            "typer.confirm", lambda text, **k: prompts.append(text) or True
        )
        body = tmp_path / "body.txt"
        body.write_text("z", encoding="utf-8")
        run_update("DOC", str(body), tab="Tab 2")
        assert prompts == ["Replace the entire body of 'Untitled', tab 'Tab 2'?"]
        assert svc.tabs == {"t.0": "a\n", "t.1": "z\n"}

    def test_url_tab_is_the_target(self, monkeypatch, tmp_path):
        # A link copied while viewing Tab 2 must not overwrite Tab 1.
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        body = tmp_path / "body.txt"
        body.write_text("z", encoding="utf-8")
        run_update(
            "https://docs.google.com/document/d/DOC/edit?tab=t.1", str(body), yes=True
        )
        assert svc.tabs == {"t.0": "a\n", "t.1": "z\n"}

    def test_empty_url_tab_refuses_instead_of_writing_the_first_tab(
        self, monkeypatch, tmp_path
    ):
        # "...edit?tab=$TAB" with TAB unset: same refusal as --tab "".
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        body = tmp_path / "body.txt"
        body.write_text("z", encoding="utf-8")
        url = "https://docs.google.com/document/d/DOC/edit?tab="
        with pytest.raises(ValueError, match="tab '' not found"):
            run_update(url, str(body), yes=True)
        assert svc.tabs == {"t.0": "a\n", "t.1": "b\n"}

    def test_empty_tab_refuses_instead_of_writing_the_first_tab(
        self, monkeypatch, tmp_path
    ):
        # `--tab "$TAB"` with TAB unset must not fall back to Tab 1.
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        body = tmp_path / "body.txt"
        body.write_text("z", encoding="utf-8")
        with pytest.raises(ValueError, match="tab '' not found"):
            run_update("DOC", str(body), tab="", yes=True)
        assert svc.tabs == {"t.0": "a\n", "t.1": "b\n"}

    def test_edit_during_the_prompt_refuses_the_write(self, monkeypatch, tmp_path):
        svc = FakeDocsService("old")
        patch_service(monkeypatch, svc)

        def collaborator_edits(*a, **k):
            svc.edit_externally("typed while the prompt waited")
            return True

        monkeypatch.setattr("typer.confirm", collaborator_edits)
        body = tmp_path / "body.txt"
        body.write_text("new", encoding="utf-8")
        with pytest.raises(HttpError):
            run_update("DOC", str(body))
        assert svc.text == "typed while the prompt waited\n"


# -- run_append --


class TestRunAppend:
    def test_starts_new_paragraph_after_content(self, monkeypatch, capsys):
        from gdrives.auth import DOCS_WRITE_SCOPES

        svc = FakeDocsService("Hello")
        rec = patch_service(monkeypatch, svc)
        run_append("DOC", text="World")
        assert rec["scopes"] == DOCS_WRITE_SCOPES
        assert svc.text == "Hello\nWorld\n"
        assert "Appended 5 character(s)" in capsys.readouterr().out

    def test_empty_body_gets_no_leading_newline(self, monkeypatch):
        svc = FakeDocsService("")
        patch_service(monkeypatch, svc)
        run_append("DOC", text="Hi")
        assert svc.text == "Hi\n"

    def test_text_file(self, monkeypatch, tmp_path):
        svc = FakeDocsService("a")
        patch_service(monkeypatch, svc)
        more = tmp_path / "more.txt"
        more.write_text("b\nc\n", encoding="utf-8")
        run_append("DOC", text_file=str(more))
        assert svc.text == "a\nb\nc\n"

    def test_targets_tab(self, monkeypatch):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        run_append("DOC", text="c", tab="t.1")
        assert svc.tabs == {"t.0": "a\n", "t.1": "b\nc\n"}

    def test_url_tab_is_the_target(self, monkeypatch):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        run_append("https://docs.google.com/document/d/DOC/edit?tab=t.1", text="c")
        assert svc.tabs == {"t.0": "a\n", "t.1": "b\nc\n"}

    def test_write_is_tied_to_the_read(self, monkeypatch):
        # The leading-newline choice comes from the read; an edit in between
        # (here, into the empty body) must refuse the write, not glue onto it.
        svc = FakeDocsService("")
        patch_service(monkeypatch, svc)
        real_pull = pull_document

        def pull_then_edit(service, document_id):
            doc = real_pull(service, document_id)
            svc.edit_externally("someone's line")
            return doc

        monkeypatch.setattr("gdrives.docs.pull_document", pull_then_edit)
        with pytest.raises(HttpError):
            run_append("DOC", text="mine")
        assert svc.text == "someone's line\n"
        body = svc.calls[-1][1]["body"]
        assert body["writeControl"] == {"requiredRevisionId": "rev-1"}

    @pytest.mark.parametrize("kwargs", [{}, {"text": "x", "text_file": "f"}])
    def test_requires_exactly_one_source(self, monkeypatch, kwargs):
        svc = FakeDocsService("a")
        patch_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="exactly one of --text or --text-file"):
            run_append("DOC", **kwargs)
        assert svc.calls == []

    def test_empty_text_refused(self, monkeypatch):
        svc = FakeDocsService("a")
        patch_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="nothing to append"):
            run_append("DOC", text="")
        assert svc.calls == []


# -- run_replace --


class TestRunReplace:
    def test_single_occurrence_is_replaced_under_guard(self, monkeypatch, capsys):
        from gdrives.auth import DOCS_WRITE_SCOPES

        svc = FakeDocsService("status: draft")
        rec = patch_service(monkeypatch, svc)
        run_replace("DOC", "draft", "final")
        assert rec["scopes"] == DOCS_WRITE_SCOPES
        assert svc.text == "status: final\n"
        body = svc.calls[1][1]["body"]
        assert body["requests"][0]["replaceAllText"]["tabsCriteria"] == {
            "tabIds": ["t.0"]
        }
        assert body["writeControl"] == {"requiredRevisionId": "rev-1"}
        assert "Replaced 1 occurrence(s) of 'draft'" in capsys.readouterr().out

    def test_empty_search_text_refused_before_any_network_work(self, monkeypatch):
        svc = FakeDocsService("hello")
        patch_service(monkeypatch, svc)
        monkeypatch.setattr(
            "gdrives.resolve.resolve_file_id",
            lambda *a, **k: pytest.fail("must not resolve with an empty --find"),
        )
        with pytest.raises(ValueError, match="must not be empty"):
            run_replace("DOC", "", "x")
        assert svc.calls == []

    def test_occurrence_in_footer_counts_toward_the_guard(self, monkeypatch):
        # Without segment-aware counting the body's single match would pass
        # the exactly-one guard and the API would still change two places.
        svc = FakeDocsService("status: draft", footers={"f.0": "draft v2"})
        patch_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="occurs 2 times; pass --all"):
            run_replace("DOC", "draft", "final")
        assert svc.text == "status: draft\n"
        run_replace("DOC", "draft", "final", allow_multiple=True)
        assert svc.text == "status: final\n"
        assert svc.segments["footers"] == {"f.0": "final v2\n"}

    def test_no_occurrence_is_an_error(self, monkeypatch):
        svc = FakeDocsService("hello")
        patch_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="no occurrence of 'nope'"):
            run_replace("DOC", "nope", "x")
        assert [c[0] for c in svc.calls] == ["documents.get"]

    def test_multiple_occurrences_refused_without_all(self, monkeypatch):
        svc = FakeDocsService("a a")
        patch_service(monkeypatch, svc)
        with pytest.raises(ValueError, match="occurs 2 times; pass --all"):
            run_replace("DOC", "a", "b")
        assert svc.text == "a a\n"

    def test_all_replaces_every_occurrence(self, monkeypatch, capsys):
        svc = FakeDocsService("a a")
        patch_service(monkeypatch, svc)
        run_replace("DOC", "a", "b", allow_multiple=True)
        assert svc.text == "b b\n"
        assert "Replaced 2 occurrence(s)" in capsys.readouterr().out

    def test_ignore_case(self, monkeypatch):
        svc = FakeDocsService("Foo foo")
        patch_service(monkeypatch, svc)
        run_replace("DOC", "FOO", "x", match_case=False, allow_multiple=True)
        assert svc.text == "x x\n"

    def test_tab_title_scopes_count_and_replacement(self, monkeypatch):
        svc = FakeDocsService(tabs={"t.0": "same", "t.1": "same"})
        patch_service(monkeypatch, svc)
        run_replace("DOC", "same", "other", tab="Tab 2")
        assert svc.tabs == {"t.0": "same\n", "t.1": "other\n"}

    def test_url_tab_scopes_count_and_replacement(self, monkeypatch):
        svc = FakeDocsService(tabs={"t.0": "same", "t.1": "same"})
        patch_service(monkeypatch, svc)
        run_replace(
            "https://docs.google.com/document/d/DOC/edit?tab=t.1", "same", "other"
        )
        assert svc.tabs == {"t.0": "same\n", "t.1": "other\n"}

    @pytest.mark.parametrize("changed", [0, 2])
    def test_count_the_api_disagrees_with_is_an_error(
        self, monkeypatch, capsys, changed
    ):
        svc = FakeDocsService("status: draft")
        patch_service(monkeypatch, svc)
        monkeypatch.setattr("gdrives.docs.replace_text", lambda *a, **k: changed)
        with pytest.raises(
            ValueError,
            match=f"counted 1 occurrence\\(s\\) of 'draft', but the API replaced "
            f"{changed}",
        ):
            run_replace("DOC", "draft", "final")
        assert "Replaced" not in capsys.readouterr().out


# -- run_clear --


class TestRunClear:
    def test_yes_clears(self, monkeypatch, capsys):
        from gdrives.auth import DOCS_WRITE_SCOPES

        svc = FakeDocsService("abc")
        rec = patch_service(monkeypatch, svc)
        monkeypatch.setattr(
            "typer.confirm", lambda *a, **k: pytest.fail("must not prompt with yes")
        )
        run_clear("DOC", yes=True)
        assert rec["scopes"] == DOCS_WRITE_SCOPES
        assert svc.text == "\n"
        assert "Cleared document body" in capsys.readouterr().out

    def test_declined_confirm_aborts(self, monkeypatch, capsys):
        svc = FakeDocsService("abc", title="Notes")
        patch_service(monkeypatch, svc)
        prompts = []
        monkeypatch.setattr(
            "typer.confirm", lambda text, **k: prompts.append(text) or False
        )
        run_clear("DOC")
        assert prompts == ["Clear the entire body of 'Notes', tab 'Tab 1'?"]
        assert [c[0] for c in svc.calls] == ["documents.get"]
        assert svc.text == "abc\n"
        assert "Aborted." in capsys.readouterr().err

    def test_accepted_confirm_clears_tab(self, monkeypatch):
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        prompts = []
        monkeypatch.setattr(
            "typer.confirm", lambda text, **k: prompts.append(text) or True
        )
        run_clear("DOC", tab="t.1")
        assert prompts == ["Clear the entire body of 'Untitled', tab 'Tab 2'?"]
        assert svc.tabs == {"t.0": "a\n", "t.1": "\n"}

    def test_url_tab_is_the_target(self, monkeypatch):
        # The scenario from the review: a URL copied while viewing Tab 2.
        svc = FakeDocsService(tabs={"t.0": "a", "t.1": "b"})
        patch_service(monkeypatch, svc)
        run_clear("https://docs.google.com/document/d/DOC/edit?tab=t.1", yes=True)
        assert svc.tabs == {"t.0": "a\n", "t.1": "\n"}


# -- run_create --


class TestRunCreate:
    def test_creates_and_prints_url(self, monkeypatch, capsys):
        from gdrives.auth import DOCS_WRITE_SCOPES

        svc = FakeDocsService()
        rec = patch_service(monkeypatch, svc)
        run_create("Notes")
        assert rec["scopes"] == DOCS_WRITE_SCOPES
        assert [c[0] for c in svc.calls] == ["documents.create"]
        out = capsys.readouterr()
        assert out.out == "https://docs.google.com/document/d/NEW_DOC/edit\n"
        assert "Document ID: NEW_DOC" in out.err

    def test_text_file_fills_body(self, monkeypatch, tmp_path):
        svc = FakeDocsService()
        patch_service(monkeypatch, svc)
        body = tmp_path / "body.txt"
        body.write_text("first\nsecond\n", encoding="utf-8")
        run_create("Notes", text_file=str(body))
        assert svc.text == "first\nsecond\n"
        assert [c[0] for c in svc.calls] == [
            "documents.create",
            "documents.batchUpdate",
        ]


@pytest.mark.parametrize("match_case", [True, False])
def test_count_does_not_join_independent_segments(match_case):
    svc = FakeDocsService("end", headers={"h": "start"}, footers={"f": "end\nstart"})
    find = "end\nstart" if match_case else "END\nSTART"
    assert count_occurrences(svc.document(), find, match_case=match_case) == 1


def test_replace_rejects_phrase_created_only_by_joining_segments(monkeypatch):
    svc = FakeDocsService("end", headers={"h": "start"})
    patch_service(monkeypatch, svc)
    with pytest.raises(ValueError, match="no occurrence"):
        run_replace("DOC", "end\nstart", "replacement")
    assert [call[0] for call in svc.calls] == ["documents.get"]


def test_case_insensitive_replacement_keeps_literal_backslashes(monkeypatch):
    svc = FakeDocsService("Draft")
    patch_service(monkeypatch, svc)
    run_replace("DOC", "draft", r"\1\notes", match_case=False)
    assert svc.text == "\\1\\notes\n"
