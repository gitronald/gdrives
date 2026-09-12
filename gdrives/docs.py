"""Read and edit Google Docs content via the Docs API v1.

Distinct from ``gdrives export``, which downloads a *whole* document to a local
``.docx``/``.txt``/``.md`` file through the Drive API. Here we operate on the
live document with ``documents.get`` / ``documents.batchUpdate`` /
``documents.create``, so callers can read a document's text and write changes
back in place without a round-trip through an Office file.

A document is a tree of structural elements (paragraphs, tables, section
breaks, tables of contents) whose text runs carry ``startIndex`` / ``endIndex``
in UTF-16 code units, and every edit is index-addressed: an insertion or
deletion shifts every index after it. The helpers here keep that bookkeeping
away from callers:

- Reads flatten the tree to plain text (:func:`document_text`). The flattening
  skips non-text elements that still occupy an index each, so it is never
  reused for index math.
- Writes prefer index-free requests: ``insertText`` at the end of the segment
  (:func:`append_text`) and ``replaceAllText`` (:func:`replace_text`). The two
  index-addressed operations (:func:`set_text`, :func:`clear_text`) compute
  the body span from a fetched document and send that document's
  ``revisionId`` as ``writeControl.requiredRevisionId``, so the write fails
  cleanly (HTTP 400) if the document changed in between.
- Docs can have several tabs, each with its own index space. Every operation
  takes an optional ``tab_id``; ``None`` targets the first tab, the API's own
  default for a request without one.
"""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from gdrives.files import Service

# A Docs API document, tab, body, or structural element (JSON as a dict).
Document = dict[str, Any]


# -- fetch and create --


def pull_document(service: Service, document_id: str) -> Document:
    """Fetch a document with every tab's content populated (``documents.get``).

    Content lives under ``tabs[i].documentTab.body`` (``includeTabsContent`` is
    always requested so multi-tab documents are fully readable); the top-level
    ``revisionId`` guards later writes.
    """
    return (
        service.documents()
        .get(documentId=document_id, includeTabsContent=True)
        .execute()
    )


def create_document(service: Service, title: str) -> str:
    """Create an empty document titled ``title`` and return its ID.

    ``documents.create`` places the new file in the root of the caller's My
    Drive; ``gdrives mv`` moves it into a folder afterwards.
    """
    doc = service.documents().create(body={"title": title}).execute()
    return doc["documentId"]


# -- tabs --


def iter_tabs(doc: Document) -> Iterator[Document]:
    """Yield every tab in document order, descending into child tabs."""

    def walk(tabs: list[Document]) -> Iterator[Document]:
        for tab in tabs:
            yield tab
            yield from walk(tab.get("childTabs", []))

    yield from walk(doc.get("tabs", []))


def first_tab_id(doc: Document) -> str | None:
    """Return the first tab's ID, or None for a document with no tabs content."""
    for tab in iter_tabs(doc):
        return tab["tabProperties"]["tabId"]
    return None


def resolve_tab_id(doc: Document, tab: str) -> str:
    """Return the tab ID for ``tab``, which may be a tab ID or a tab title.

    IDs are checked first, then titles, so a title that happens to look like an
    ID still resolves. Raises ValueError listing the available tabs when nothing
    matches, or when more than one tab carries that title.
    """
    tabs = list(iter_tabs(doc))
    if any(t["tabProperties"]["tabId"] == tab for t in tabs):
        return tab
    by_title = [t for t in tabs if t["tabProperties"].get("title") == tab]
    if len(by_title) == 1:
        return by_title[0]["tabProperties"]["tabId"]
    listing = ", ".join(
        f"{t['tabProperties'].get('title', '')!r} ({t['tabProperties']['tabId']})"
        for t in tabs
    )
    if by_title:
        raise ValueError(f"tab title {tab!r} is ambiguous; use its ID: {listing}")
    raise ValueError(f"tab {tab!r} not found; tabs: {listing or 'none'}")


def tab_content(doc: Document, tab_id: str | None = None) -> Document:
    """Return one tab's ``documentTab`` (the first when ``tab_id`` is None).

    That dict holds the tab's ``body`` plus its ``headers``, ``footers``, and
    ``footnotes`` maps. Also accepts a document fetched without tabs content,
    whose first-tab fields sit at the top level of the document itself.
    """
    if "tabs" not in doc and tab_id is None:
        return doc
    if tab_id is None:
        tab_id = first_tab_id(doc)
    for tab in iter_tabs(doc):
        if tab["tabProperties"]["tabId"] == tab_id:
            return tab.get("documentTab", {})
    raise ValueError(f"tab {tab_id!r} not found")


def tab_body(doc: Document, tab_id: str | None = None) -> Document:
    """Return the ``body`` of one tab (the first when ``tab_id`` is None)."""
    return tab_content(doc, tab_id).get("body", {})


def tab_segments(doc: Document, tab_id: str | None = None) -> Iterator[Document]:
    """Yield every text segment of a tab: its body, then headers, footers, and
    footnotes, each as a ``{"content": [...]}`` dict.

    These are exactly the segments ``replaceAllText`` edits, so anything that
    counts or previews a replacement must walk all of them, not just the body.
    """
    tab = tab_content(doc, tab_id)
    yield tab.get("body", {})
    for kind in ("headers", "footers", "footnotes"):
        yield from tab.get(kind, {}).values()


# -- reading --


def _paragraph_text(paragraph: Document) -> str:
    """Concatenate a paragraph's text runs (its trailing newline included)."""
    return "".join(
        pe["textRun"].get("content", "")
        for pe in paragraph.get("elements", [])
        if "textRun" in pe
    )


def _text_runs(content: list[Document]) -> Iterator[str]:
    """Yield the raw text of every paragraph under ``content``, in order.

    Tables are walked row by row and cell by cell; tables of contents recurse.
    Non-text paragraph elements (inline images, footnote references, page
    breaks, ...) contribute nothing, though each still occupies one index.
    """
    for element in content:
        if "paragraph" in element:
            yield _paragraph_text(element["paragraph"])
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _text_runs(cell.get("content", []))
        elif "tableOfContents" in element:
            yield from _text_runs(element["tableOfContents"].get("content", []))


def raw_text(doc: Document, tab_id: str | None = None) -> str:
    """Return a tab's text runs concatenated verbatim, with no decoration.

    Covers every segment ``replaceAllText`` matches against (body, headers,
    footers, and footnotes, in that order), so it is what
    :func:`count_occurrences` searches; :func:`document_text` is the readable
    body-only view with list and table decoration.
    """
    return "".join(
        "".join(_text_runs(segment.get("content", [])))
        for segment in tab_segments(doc, tab_id)
    )


def body_text(content: list[Document]) -> str:
    """Flatten structural elements to readable plain text.

    Paragraphs keep their trailing newline; a list item is prefixed with ``- ``
    (indented two spaces per nesting level); a table becomes one line per row
    with cells joined by tabs (a multi-paragraph cell is joined with spaces); a
    table of contents is flattened like a body. Non-text elements are skipped.
    """
    out: list[str] = []
    for element in content:
        if "paragraph" in element:
            paragraph = element["paragraph"]
            text = _paragraph_text(paragraph)
            bullet = paragraph.get("bullet")
            if bullet is not None:
                text = "  " * bullet.get("nestingLevel", 0) + "- " + text
            out.append(text)
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                cells = [
                    body_text(cell.get("content", [])).rstrip("\n").replace("\n", " ")
                    for cell in row.get("tableCells", [])
                ]
                out.append("\t".join(cells) + "\n")
        elif "tableOfContents" in element:
            out.append(body_text(element["tableOfContents"].get("content", [])))
    return "".join(out)


def document_text(doc: Document, tab_id: str | None = None) -> str:
    """Return a tab's content as readable plain text (see :func:`body_text`)."""
    return body_text(tab_body(doc, tab_id).get("content", []))


def count_occurrences(
    doc: Document, find: str, *, match_case: bool = True, tab_id: str | None = None
) -> int:
    """Count non-overlapping occurrences of ``find`` in a tab's raw text.

    Case-insensitive counting uses ``str.casefold()``, the closest match to
    the API's ``matchCase: false`` comparison: a live probe showed the API
    folds ``ß`` to ``ss`` and dotted ``İ`` to ``i`` like ``casefold()`` does
    (``lower()`` misses ``ß``), but does not fold ligatures such as ``ﬁ``. For
    text containing such characters the count can differ from the number of
    occurrences the API changes.
    """
    if not find:
        raise ValueError("search text must not be empty")
    text = raw_text(doc, tab_id)
    if not match_case:
        text, find = text.casefold(), find.casefold()
    return text.count(find)


def body_range(doc: Document, tab_id: str | None = None) -> tuple[int, int]:
    """Return the editable ``(start, end)`` index span of a tab's body.

    The body starts at index 1 (index 0 is its leading section break) and its
    final newline cannot be deleted, so the span stops one short of the last
    element's ``endIndex``. An empty body yields ``(1, 1)``.
    """
    content = tab_body(doc, tab_id).get("content", [])
    if not content:
        return (1, 1)
    return (1, max(content[-1].get("endIndex", 2) - 1, 1))


# -- writing --


def batch_update(
    service: Service,
    document_id: str,
    requests: list[Document],
    *,
    required_revision: str | None = None,
) -> Document:
    """Send ``requests`` in one ``documents.batchUpdate`` call.

    With ``required_revision``, the API rejects the batch (HTTP 400) unless that
    is still the document's latest revision — the guard every read-locate-write
    path relies on. An empty ``requests`` list is a no-op returning ``{}``
    rather than an invalid empty batch.
    """
    if not requests:
        return {}
    body: Document = {"requests": requests}
    if required_revision is not None:
        body["writeControl"] = {"requiredRevisionId": required_revision}
    return service.documents().batchUpdate(documentId=document_id, body=body).execute()


def _with_tab(target: Document, tab_id: str | None) -> Document:
    """Add ``tabId`` to a location/range dict when a tab is targeted explicitly."""
    if tab_id is not None:
        target["tabId"] = tab_id
    return target


def append_text(
    service: Service, document_id: str, text: str, *, tab_id: str | None = None
) -> Document:
    """Insert ``text`` at the end of a tab's body (before its final newline).

    Index-free (``endOfSegmentLocation``), so no prior read is needed. The text
    continues the body's last line; start it with a newline to begin a new
    paragraph — :func:`run_append` does exactly that when the body has content.
    """
    if not text:
        raise ValueError("text to append must not be empty")
    request = {
        "insertText": {"text": text, "endOfSegmentLocation": _with_tab({}, tab_id)}
    }
    return batch_update(service, document_id, [request])


def replace_text(
    service: Service,
    document_id: str,
    find: str,
    replace: str,
    *,
    match_case: bool = True,
    tab_id: str | None = None,
    required_revision: str | None = None,
) -> int:
    """Replace every occurrence of ``find`` with ``replace``; return the count.

    Index-free (``replaceAllText``). ``tab_id`` narrows the replacement to one
    tab; None applies it to every tab, the API's default for this request.
    ``required_revision`` ties the write to the read that counted occurrences.
    """
    if not find:
        raise ValueError("search text must not be empty")
    request: Document = {
        "replaceAllText": {
            "containsText": {"text": find, "matchCase": match_case},
            "replaceText": replace,
        }
    }
    if tab_id is not None:
        request["replaceAllText"]["tabsCriteria"] = {"tabIds": [tab_id]}
    result = batch_update(
        service, document_id, [request], required_revision=required_revision
    )
    replies = result.get("replies", [])
    if not replies:
        return 0
    return replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)


def _delete_body(doc: Document, tab_id: str | None) -> list[Document]:
    """Build the request deleting a tab's whole body span, or none if empty."""
    start, end = body_range(doc, tab_id)
    if end <= start:
        return []
    span = _with_tab({"startIndex": start, "endIndex": end}, tab_id)
    return [{"deleteContentRange": {"range": span}}]


def clear_text(
    service: Service,
    document_id: str,
    *,
    tab_id: str | None = None,
    doc: Document | None = None,
) -> Document:
    """Delete a tab's whole body, leaving the one empty paragraph Docs requires.

    Reads the document (or uses ``doc``, an earlier :func:`pull_document`
    result) to find the body span; that snapshot's ``revisionId`` guards the
    delete, so a body that changed since is refused rather than mis-trimmed.
    """
    doc = doc or pull_document(service, document_id)
    return batch_update(
        service,
        document_id,
        _delete_body(doc, tab_id),
        required_revision=doc.get("revisionId"),
    )


def set_text(
    service: Service,
    document_id: str,
    text: str,
    *,
    tab_id: str | None = None,
    doc: Document | None = None,
) -> Document:
    """Overwrite a tab's body with ``text`` in one batch (delete, then insert).

    Reads the document (or uses ``doc``) for the body span and revision, like
    :func:`clear_text`. The delete comes first so the insert's index (1, the
    start of the now-empty body) never needs adjusting for the shifted content.
    """
    doc = doc or pull_document(service, document_id)
    requests = _delete_body(doc, tab_id)
    if text:
        requests.append(
            {"insertText": {"text": text, "location": _with_tab({"index": 1}, tab_id)}}
        )
    return batch_update(
        service, document_id, requests, required_revision=doc.get("revisionId")
    )


# -- source resolution --


def resolve_document_id(source: str, service: Service | None = None) -> str:
    """Resolve a Doc URL, bare file ID, or Drive path to a document ID.

    The Docs-facing name for :func:`gdrives.resolve.resolve_file_id`.
    """
    from gdrives.resolve import resolve_file_id

    return resolve_file_id(source, service)


# -- local text interchange --


def read_text_file(path: str) -> str:
    """Read a local UTF-8 text file, dropping one trailing newline.

    Editors end files with a newline and Docs ends every body with one of its
    own, so writing the file verbatim would leave an empty last paragraph.
    """
    text = Path(path).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


# -- CLI entry points --


def _resolve_and_report(source: str) -> str:
    """Resolve ``source`` to a document ID and echo it to stderr."""
    from gdrives.resolve import resolve_and_report

    return resolve_and_report(source, "Document")


def _target_tab(doc: Document, tab: str | None) -> str | None:
    """Resolve a ``--tab`` (title or ID) to a tab ID; None keeps the first tab."""
    return resolve_tab_id(doc, tab) if tab else None


def run_get(
    source: str,
    *,
    tab: str | None = None,
    output: str | None = None,
    as_json: bool = False,
) -> None:
    """Print a document's text (or its raw JSON), or write it to ``output``."""
    from gdrives.auth import build_docs_service

    document_id = _resolve_and_report(source)
    service = build_docs_service()
    doc = pull_document(service, document_id)
    if as_json:
        content = json.dumps(doc, indent=2)
    else:
        content = document_text(doc, _target_tab(doc, tab))
    if content and not content.endswith("\n"):
        content += "\n"
    if output:
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        print(f"Wrote {len(content)} character(s) to {output}", file=sys.stderr)
    else:
        sys.stdout.write(content)


def run_update(
    source: str, text_file: str, *, tab: str | None = None, yes: bool = False
) -> None:
    """Overwrite the body with a local text file, confirming first unless ``yes``."""
    import typer

    from gdrives.auth import DOCS_WRITE_SCOPES, build_docs_service

    document_id = _resolve_and_report(source)
    text = read_text_file(text_file)
    if not yes and not typer.confirm(
        "Replace the entire document body?", default=False
    ):
        print("Aborted.", file=sys.stderr)
        return
    service = build_docs_service(DOCS_WRITE_SCOPES)
    doc = pull_document(service, document_id)
    set_text(service, document_id, text, tab_id=_target_tab(doc, tab), doc=doc)
    print(f"Replaced body with {len(text)} character(s)")


def run_append(
    source: str,
    *,
    text: str | None = None,
    text_file: str | None = None,
    tab: str | None = None,
) -> None:
    """Append text as new paragraph(s) at the end of the body.

    Exactly one of ``text`` (verbatim) or ``text_file`` (a local file) supplies
    the content. A non-empty body gets a newline first so the appended text
    starts its own paragraph instead of continuing the last line.
    """
    from gdrives.auth import DOCS_WRITE_SCOPES, build_docs_service

    if (text is None) == (text_file is None):
        raise ValueError("pass exactly one of --text or --text-file")
    if text is None:
        assert text_file is not None  # the exclusivity check above guarantees it
        text = read_text_file(text_file)
    if not text:
        raise ValueError("nothing to append")

    document_id = _resolve_and_report(source)
    service = build_docs_service(DOCS_WRITE_SCOPES)
    doc = pull_document(service, document_id)
    tab_id = _target_tab(doc, tab)
    start, end = body_range(doc, tab_id)
    append_text(
        service, document_id, ("\n" if end > start else "") + text, tab_id=tab_id
    )
    print(f"Appended {len(text)} character(s)")


def run_replace(
    source: str,
    find: str,
    replace: str,
    *,
    match_case: bool = True,
    tab: str | None = None,
    allow_multiple: bool = False,
) -> None:
    """Replace ``find`` with ``replace``, refusing ambiguous or absent matches.

    Counts occurrences from a fresh read first: zero is an error, and more than
    one is refused unless ``allow_multiple`` — so a targeted edit never rewrites
    the wrong sentence. The write is tied to that read's revision.
    """
    from gdrives.auth import DOCS_WRITE_SCOPES, build_docs_service

    if not find:
        raise ValueError("search text must not be empty")
    document_id = _resolve_and_report(source)
    service = build_docs_service(DOCS_WRITE_SCOPES)
    doc = pull_document(service, document_id)
    tab_id = _target_tab(doc, tab) or first_tab_id(doc)
    count = count_occurrences(doc, find, match_case=match_case, tab_id=tab_id)
    if count == 0:
        raise ValueError(f"no occurrence of {find!r}")
    if count > 1 and not allow_multiple:
        raise ValueError(
            f"{find!r} occurs {count} times; pass --all to replace every occurrence"
        )
    changed = replace_text(
        service,
        document_id,
        find,
        replace,
        match_case=match_case,
        tab_id=tab_id,
        required_revision=doc.get("revisionId"),
    )
    print(f"Replaced {changed} occurrence(s) of {find!r}")


def run_clear(source: str, *, tab: str | None = None, yes: bool = False) -> None:
    """Empty the body, confirming first unless ``yes``."""
    import typer

    from gdrives.auth import DOCS_WRITE_SCOPES, build_docs_service

    document_id = _resolve_and_report(source)
    if not yes and not typer.confirm("Clear the entire document body?", default=False):
        print("Aborted.", file=sys.stderr)
        return
    service = build_docs_service(DOCS_WRITE_SCOPES)
    doc = pull_document(service, document_id)
    clear_text(service, document_id, tab_id=_target_tab(doc, tab), doc=doc)
    print("Cleared document body")


def run_create(title: str, *, text_file: str | None = None) -> None:
    """Create a document in My Drive root, optionally filled from a text file.

    Prints the new document's URL to stdout (and its ID to stderr).
    """
    from gdrives.auth import DOCS_WRITE_SCOPES, build_docs_service

    text = read_text_file(text_file) if text_file else ""
    service = build_docs_service(DOCS_WRITE_SCOPES)
    document_id = create_document(service, title)
    print(f"Document ID: {document_id}", file=sys.stderr)
    if text:
        # A fresh document's body is empty, so an end-of-segment insert is the
        # whole content — no read, span, or revision needed.
        append_text(service, document_id, text)
    print(f"https://docs.google.com/document/d/{document_id}/edit")
