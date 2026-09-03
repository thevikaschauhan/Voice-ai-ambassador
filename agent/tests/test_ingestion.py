"""Document ingestion: the four parsers and the route that writes them (P2-S05
follow-on, task-p2-ingestion, docs/10- 'Knowledge ingestion').

Imports live inside each test so the file COLLECTS before the module exists -
a module-level import of a missing module is a collection error rather than N
failing cases, and the gate counts cases (docs/06-).

The parsers are tested on bytes built here rather than on fixture files: a
committed PDF is a binary nobody reviews, and the one property that matters -
a scan reports no text rather than an empty document - is easier to construct
than to find.
"""

from __future__ import annotations

import zipfile
from typing import Any


def _docx_bytes(paragraphs: list[str]) -> bytes:
    """The smallest real .docx: a zip with document.xml in it."""
    import io

    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf_bytes(page_text: str | None) -> bytes:
    """A one-page PDF. `None` produces a page with no text operators at all,
    which is what a scan looks like to a text extractor."""
    content = f"BT /F1 12 Tf 72 720 Td ({page_text}) Tj ET" if page_text else ""
    stream = content.encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(start).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def test_pdf_docx_txt_and_paste_parse_while_a_scanned_pdf_reports_no_text() -> None:
    """The card's named test: all four sources, and the one that must fail loudly.

    A scanned PDF parses cleanly and yields nothing, which is the case that
    would otherwise become an empty published document nobody notices - so it
    is `failed/no_extractable_text` and says OCR is deferred (docs/10- step 2).
    """
    from adapter.ingestion import ParseFailed, parse_document

    pdf = parse_document(
        _pdf_bytes("Two bedroom residences start at AED 2,000,000."), "brochure.pdf"
    )
    assert "AED 2,000,000" in pdf.text
    assert pdf.source_type == "pdf"

    docx = parse_document(
        _docx_bytes(["Payment plans", "Sixty forty on handover."]), "plans.docx"
    )
    assert "Sixty forty on handover." in docx.text
    assert docx.source_type == "docx"

    txt = parse_document("A plain note about the tower.".encode(), "note.txt")
    assert txt.text == "A plain note about the tower."
    assert txt.source_type == "txt"

    pasted = parse_document(None, None, pasted="Pasted paragraph about the plan.")
    assert pasted.text == "Pasted paragraph about the plan."
    assert pasted.source_type == "paste"

    try:
        parse_document(_pdf_bytes(None), "scan.pdf")
    except ParseFailed as failure:
        assert failure.code == "no_extractable_text"
    else:  # pragma: no cover - the assertion below is the failure message
        raise AssertionError("a scan must report no text, not an empty document")


def test_an_unsupported_extension_is_refused_by_code() -> None:
    from adapter.ingestion import ParseFailed, parse_document

    try:
        parse_document(b"whatever", "sheet.xlsx")
    except ParseFailed as failure:
        assert failure.code == "unsupported_type"
    else:
        raise AssertionError("xlsx is deferred (docs/06-), so it must be refused")


def test_text_that_is_not_utf8_reports_invalid_encoding() -> None:
    from adapter.ingestion import ParseFailed, parse_document

    try:
        parse_document(b"\xff\xfe\x00not utf-8 at all", "note.txt")
    except ParseFailed as failure:
        assert failure.code == "invalid_encoding"
    else:
        raise AssertionError("a TXT that is not UTF-8 must say so")


def test_the_byte_cap_comes_from_data_and_is_the_web_route_s_cap() -> None:
    """One number, two tiers. The web route caps at the same figure (#113)."""
    from adapter.ingestion import max_source_bytes

    assert max_source_bytes() == 8 * 1024 * 1024


def test_figures_are_extracted_as_occurrences_and_never_approved() -> None:
    from adapter.ingestion import figures_in

    found = figures_in(
        "Two bedroom residences start at AED 2,000,000. A sister tower is also "
        "AED 2,000,000. There are 3 layouts."
    )
    surfaces = [figure.surface for figure in found]
    # The SAME value twice is two occurrences, because approval is per
    # occurrence and a de-duplicating extractor would approve a sentence
    # nobody read.
    assert surfaces.count("AED 2,000,000") == 2
    assert any(figure.kind == "count" for figure in found)
    # Nothing here may approve anything (the card's boundary).
    assert all(getattr(figure, "active_approval_id", None) is None for figure in found)


def test_every_figure_carries_the_sentence_it_came_from() -> None:
    from adapter.ingestion import figures_in

    found = figures_in("Prices start at AED 985,000. Handover is 2027.")
    for figure in found:
        assert figure.source_sentence.endswith(".")
        assert figure.surface in figure.source_sentence


def test_the_parser_libraries_are_main_dependencies_not_dev() -> None:
    """The uvicorn lesson: a runtime import in a dev-only dependency is an
    ImportError in the deployed image and nowhere else."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    main = " ".join(pyproject["project"]["dependencies"])
    for package in ("pypdf", "python-docx"):
        assert package in main, f"{package} must be a main dependency"


def test_post_documents_accepts_pasted_text_and_writes_through_the_repository() -> None:
    from adapter.admin_api import app
    from fastapi.testclient import TestClient

    written: dict[str, list[Any]] = {"documents": [], "chunks": [], "figures": []}

    class Repository:
        async def add_document(self, **kwargs: Any) -> str:
            written["documents"].append(kwargs)
            return "doc-1"

        async def add_chunk(self, **kwargs: Any) -> str:
            written["chunks"].append(kwargs)
            return f"chunk-{len(written['chunks'])}"

        async def add_figure(self, **kwargs: Any) -> str:
            written["figures"].append(kwargs)
            return f"fig-{len(written['figures'])}"

    app.state.repository = Repository()
    with TestClient(app) as client:
        response = client.post(
            "/v1/knowledge/documents",
            headers={"authorization": "Bearer test-token"},
            json={
                "source_type": "paste",
                "title": "Payment plan note",
                "text": "Payment plans\n\nTwo bedrooms start at AED 2,000,000.",
            },
        )
    assert response.status_code == 201, response.text
    assert written["documents"], "the document must be written through the repository"
    assert written["chunks"], "chunks come from ambassador.knowledge.chunk_text"
    assert written["figures"], "every occurrence is recorded, unapproved"


def test_post_documents_refuses_an_over_cap_upload_with_413() -> None:
    from adapter.admin_api import app
    from adapter.ingestion import max_source_bytes
    from fastapi.testclient import TestClient

    app.state.repository = object()
    with TestClient(app) as client:
        response = client.post(
            "/v1/knowledge/documents",
            headers={"authorization": "Bearer test-token"},
            files={
                "file": ("big.pdf", b"x" * (max_source_bytes() + 1), "application/pdf")
            },
            data={"title": "Too big"},
        )
    # The same status my web route returns, so the two tiers agree (#113).
    assert response.status_code == 413


def test_post_documents_requires_the_bearer() -> None:
    from adapter.admin_api import app
    from fastapi.testclient import TestClient

    app.state.repository = object()
    with TestClient(app) as client:
        response = client.post(
            "/v1/knowledge/documents",
            json={"source_type": "paste", "title": "x", "text": "y"},
        )
    assert response.status_code in (401, 403)
