"""Turning an uploaded or pasted document into text, chunks and figures.

ADAPTER, not core: it imports the PDF and DOCX libraries, so it cannot live
beside the pure policy under ADR-002. What it does NOT do is decide anything -
chunking is `ambassador.knowledge.chunk_text`, figure extraction is
`ambassador.figures.extract_figures`, and both are imported rather than
reimplemented. This module is the bytes-to-text seam and nothing else.

Two rules from docs/10- step 2 shape it. A scanned PDF ends as
`failed/no_extractable_text` rather than an empty document, because an empty
published document is a silence nobody notices while a failure says OCR is
deferred. And original bytes are discarded at the end of the request: the
extracted text is the source of record, and re-parsing means re-uploading.

Nothing here approves a figure. Every occurrence is recorded unapproved and
every chunk is written without a scope, which the repository enforces by taking
no scope argument at all - approval and scope are an admin's, through toby's
review routes.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from ambassador.figures import extract_figures
from ambassador.knowledge import chunk_text, load_limits

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

SourceType = Literal["pdf", "docx", "txt", "md", "paste"]

ParseErrorCode = Literal[
    "unsupported_type",
    "invalid_encoding",
    "limit_exceeded",
    "no_extractable_text",
    "malformed",
]

_EXTENSIONS: Final[dict[str, SourceType]] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
}

# A sentence, for the figure's context. Deliberately simple: the figure's own
# surface anchors it, so this only has to find the boundaries around it.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

_PARAGRAPH_BREAK: Final = "\n\n"

# How a table row's cells are joined, for every format that has tables. One
# constant rather than a literal in each parser: DOCX joined with " | " for
# months while Markdown was written to join with ", ", and a figure's
# source_sentence carried the separator into review and into speech. The two
# produce text for the same chunker, so "the same way" should be a fact rather
# than a coincidence two files have to keep agreeing on.
_TABLE_CELL_JOIN: Final = ", "


class ParseFailed(Exception):
    """A parse that failed for a reason worth showing an admin.

    Carries the enum from docs/02-'s `parse_error_code` rather than a message,
    so the route answers with a code the web tier already renders advice for
    (#113) instead of a string it would have to pattern-match.
    """

    def __init__(self, code: ParseErrorCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedDocument:
    source_type: SourceType
    text: str
    original_filename: str | None
    mime_type: str
    source_bytes: int
    source_sha256: str


@dataclass(frozen=True)
class ExtractedFigure:
    """One occurrence, with the sentence that gives it meaning.

    `active_approval_id` is always None here and is not a parameter: a parser
    that could approve a figure is a parser that eventually does.
    """

    value: str
    kind: str
    currency: str | None
    unit: str | None
    surface: str
    source_sentence: str
    page: int | None = None
    active_approval_id: None = None


def max_source_bytes() -> int:
    """The upload cap, from `data/knowledge.yaml`.

    One number for two tiers: the web route refuses above it twice (#113) and
    this refuses it again, which is the refusal that counts.
    """
    loaded = yaml.safe_load((_DATA_DIR / "knowledge.yaml").read_text(encoding="utf-8"))
    return int(loaded["max_source_bytes"])


def _check_cap(size: int) -> None:
    """The one cap, for whichever way the bytes arrived.

    Counted in BYTES, which is the distinction a paste turns into a bug: an
    upload arrives as bytes already, while pasted text arrives as characters,
    and Arabic or a curly quote is two or three bytes each - so a cap measured
    in characters admits several times the configured number.

    It lives here rather than in the routes because it was written down twice
    before: the upload handler compared lengths itself, which made the paste
    branch look guarded while it had no check at all. 8388609 bytes of pasted
    text returned 201 and were chunked into Postgres.
    """
    if size > max_source_bytes():
        raise ParseFailed("limit_exceeded")


def parse_document(
    raw: bytes | None,
    filename: str | None,
    *,
    pasted: str | None = None,
) -> ParsedDocument:
    """Bytes or pasted text in, text out - or `ParseFailed` with a code."""
    if pasted is not None:
        text = pasted.strip()
        encoded = text.encode("utf-8")
        _check_cap(len(encoded))
        if not text:
            raise ParseFailed("no_extractable_text")
        return ParsedDocument(
            source_type="paste",
            text=text,
            original_filename=None,
            mime_type="text/plain",
            source_bytes=len(encoded),
            source_sha256=hashlib.sha256(encoded).hexdigest(),
        )

    if raw is None or filename is None:
        raise ParseFailed("unsupported_type")
    _check_cap(len(raw))

    suffix = Path(filename).suffix.lower()
    source_type = _EXTENSIONS.get(suffix)
    if source_type is None:
        # xlsx, doc, images and URLs are deferred (docs/06-), and a parser that
        # guessed from the bytes would ingest one of them by accident.
        raise ParseFailed("unsupported_type")

    text = _extract(source_type, raw).strip()
    if not text:
        # The scan case. It parsed; there is simply nothing in it.
        raise ParseFailed("no_extractable_text")

    return ParsedDocument(
        source_type=source_type,
        text=text,
        original_filename=filename,
        mime_type=_MIME[source_type],
        source_bytes=len(raw),
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )


_MIME: Final[dict[SourceType, str]] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "paste": "text/plain",
}


def _extract(source_type: SourceType, raw: bytes) -> str:
    if source_type == "txt":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ParseFailed("invalid_encoding") from None
    if source_type == "md":
        return _extract_markdown(raw)
    if source_type == "pdf":
        return _extract_pdf(raw)
    return _extract_docx(raw)


def _extract_pdf(raw: bytes) -> str:
    """Page order preserved, pages separated as paragraphs.

    Page numbers are kept by the separation rather than by an annotation: the
    chunker works on paragraphs, so a page break that is also a paragraph break
    keeps a chunk from spanning two pages silently.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, OSError):
        raise ParseFailed("malformed") from None
    return _PARAGRAPH_BREAK.join(page for page in pages if page)


def _extract_docx(raw: bytes) -> str:
    """Paragraphs and table cells in document order (docs/10- step 2).

    A row is one paragraph with its cells joined by `_TABLE_CELL_JOIN`, so it
    reads as a sentence rather than as a drawing: the separator ends up inside
    a figure's `source_sentence`, which an admin reads in review and the
    chunker hands on to be spoken.
    """
    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(io.BytesIO(raw))
    except (PackageNotFoundError, KeyError, ValueError, OSError):
        raise ParseFailed("malformed") from None

    parts = [paragraph.text.strip() for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(_TABLE_CELL_JOIN.join(cells))
    return _PARAGRAPH_BREAK.join(part for part in parts if part)


# --- Markdown ------------------------------------------------------------
#
# Regex rather than a Markdown library, and the reason is the direction of
# travel: every library here renders Markdown to HTML, which would leave this
# module stripping HTML back to prose - two conversions to arrive one step
# behind where it started. What is wanted is not a renderer but the inverse of
# one, and the syntax that survives an admin's copy-paste is small enough to
# name in full.
#
# The rules exist because chunk text is spoken. A `**` inside a figure's
# `source_sentence` breaks the numeric surface the guardrail matches on, and a
# table pipe read aloud is noise in a call.

_MD_FENCE: Final = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_MD_TABLE_SEPARATOR_CELL: Final = re.compile(r"^:?-+:?$")
_MD_THEMATIC_BREAK: Final = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
_MD_SETEXT_UNDERLINE: Final = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_MD_HEADING: Final = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_LIST_MARKER: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_MD_QUOTE_MARKER: Final = re.compile(r"^\s*>+\s?")
_MD_LINK_DEFINITION: Final = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+")
_MD_TABLE_CELL_SPLIT: Final = re.compile(r"(?<!\\)\|")
_MD_IMAGE: Final = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK: Final = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_REFERENCE_LINK: Final = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_MD_INLINE_CODE: Final = re.compile(r"(`+)(.*?)\1")
_MD_STRONG: Final = re.compile(r"(\*\*|__)(.+?)\1")
_MD_EMPHASIS_STAR: Final = re.compile(r"\*([^*\n]+)\*")
# `_` only at a word boundary, or `deposit_waived` loses its middle.
_MD_EMPHASIS_UNDERSCORE: Final = re.compile(
    r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])"
)
_MD_STRIKETHROUGH: Final = re.compile(r"~~(.+?)~~")
_MD_HTML_TAG: Final = re.compile(r"<[^>]+>")
_MD_ESCAPE: Final = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_MD_CODE_SLOT: Final = "\x00{}\x00"


def _extract_markdown(raw: bytes) -> str:
    """Markdown in, prose out.

    `invalid_encoding` for bytes that are not UTF-8, exactly as TXT does: the
    two arrive the same way and an admin should not have to learn which of the
    two text formats reports a decode failure differently.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ParseFailed("invalid_encoding") from None
    return _markdown_to_prose(text)


def _markdown_to_prose(text: str) -> str:
    """Blocks first, then inline, because a block rule can delete a whole line.

    Headings KEEP their `#`. It is the one marker that is already handled
    downstream: `ambassador.knowledge._is_heading` starts a new chunk at a
    paragraph beginning with `#` and `_heading_text` strips it, so the marker
    never reaches a chunk body while the heading still divides the document.
    Removing it here would leave the chunker no heading signal at all - a
    sectioned document collapses into one chunk with no heading and the heading
    text glued into the prose, which would make a `.md` ingest worse than the
    same bytes renamed `.txt`.
    """
    blocks: list[str] = []
    paragraph: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if paragraph:
            blocks.append("\n".join(paragraph))
            paragraph.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        opening = _MD_FENCE.match(line)
        if fence is not None:
            # Only a fence of the same character and at least the same length
            # closes one, so ``` inside a ~~~ block stays code.
            if (
                opening
                and opening.group(1)[0] == fence[0]
                and len(opening.group(1)) >= len(fence)
            ):
                fence = None
            continue
        if opening:
            flush()
            fence = opening.group(1)
            continue

        if not line.strip():
            flush()
            continue
        if _MD_LINK_DEFINITION.match(line):
            continue
        if line.lstrip().startswith("|"):
            row = _markdown_table_row(line)
            if row:
                paragraph.append(row)
            continue
        underline = _MD_SETEXT_UNDERLINE.match(line)
        if underline and paragraph:
            # CommonMark: an underline under a paragraph is a setext heading,
            # and it wins over the thematic break the same characters would be
            # on their own. Dropping it instead would read "=====" aloud.
            promoted = paragraph.pop()
            flush()
            blocks.append(
                ("#" if underline.group(1)[0] == "=" else "##") + " " + promoted
            )
            continue
        if _MD_THEMATIC_BREAK.match(line):
            flush()
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            rendered = _markdown_inline(heading.group(2))
            if rendered:
                # Its own block, so the chunker sees the heading alone rather
                # than a heading with the first sentence stuck to it.
                blocks.append(f"{heading.group(1)} {rendered}")
            continue

        stripped = _MD_QUOTE_MARKER.sub("", line)
        stripped = _MD_LIST_MARKER.sub("", stripped)
        rendered = _markdown_inline(stripped)
        if rendered:
            # An image on a line of its own renders to nothing, and an empty
            # line inside a paragraph would become a stray break.
            paragraph.append(rendered)
    flush()

    return _PARAGRAPH_BREAK.join(blocks)


def _markdown_table_row(line: str) -> str:
    """One row, one line, cells joined by ', ' - or '' for the separator.

    Joined rather than kept as a grid because the row is what gets read: a
    figure's sentence is the row it sits in, and "Two bedroom, AED 2,000,000"
    is a sentence while "| Two bedroom | AED 2,000,000 |" is a drawing.
    """
    cells = [cell.strip() for cell in _MD_TABLE_CELL_SPLIT.split(line.strip())]
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    if cells and all(_MD_TABLE_SEPARATOR_CELL.match(cell) for cell in cells):
        return ""
    rendered = [_markdown_inline(cell) for cell in cells]
    return _TABLE_CELL_JOIN.join(cell for cell in rendered if cell)


def _markdown_inline(text: str) -> str:
    """Inline markers off, words kept.

    Code spans are lifted out first and put back last: their contents are not
    Markdown, so `*` or `_` inside one must survive the emphasis rules.
    """
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(match.group(2))
        return _MD_CODE_SLOT.format(len(codes) - 1)

    out = _MD_INLINE_CODE.sub(stash, text)
    # Images before links: an image IS a link with a bang, so the link rule
    # would otherwise keep its alt text and drop only the bang.
    out = _MD_IMAGE.sub("", out)
    out = _MD_LINK.sub(r"\1", out)
    out = _MD_REFERENCE_LINK.sub(r"\1", out)
    out = _MD_STRONG.sub(r"\2", out)
    out = _MD_EMPHASIS_STAR.sub(r"\1", out)
    out = _MD_EMPHASIS_UNDERSCORE.sub(r"\1", out)
    out = _MD_STRIKETHROUGH.sub(r"\1", out)
    out = _MD_HTML_TAG.sub("", out)
    out = html.unescape(out)
    out = _MD_ESCAPE.sub(r"\1", out)
    for index, code in enumerate(codes):
        out = out.replace(_MD_CODE_SLOT.format(index), code)
    # A removal leaves two spaces where one word used to be.
    return " ".join(out.split())


def figures_in(text: str) -> list[ExtractedFigure]:
    """Every currency amount and count in the text, as occurrences.

    NOT de-duplicated. The same value written twice is two rows, because
    approval is per occurrence and one approval standing for both would approve
    a sentence nobody read. The extractor is the existing deterministic one, so
    the figures an admin reviews are the same figures the guardrail knows.
    """
    found: list[ExtractedFigure] = []
    for match in extract_figures(text):
        figure = match.figure
        found.append(
            ExtractedFigure(
                value=str(figure.value),
                kind=figure.kind,
                currency=getattr(figure, "currency", None),
                unit=getattr(figure, "unit", None),
                surface=figure.surface,
                source_sentence=_sentence_around(text, match.start),
            )
        )
    return found


def _sentence_around(text: str, position: int) -> str:
    """The sentence a figure sits in, for review.

    docs/10-: approving a value without its sentence and page is not review. So
    this is not decoration - it is the thing being reviewed.
    """
    start = 0
    for boundary in _SENTENCE_END.finditer(text):
        if boundary.end() > position:
            break
        start = boundary.end()
    end = len(text)
    for boundary in _SENTENCE_END.finditer(text, position):
        end = boundary.start()
        break
    return text[start:end].strip()


async def store_document(
    repository: Any,
    *,
    title: str,
    parsed: ParsedDocument,
) -> dict[str, Any]:
    """Write the document, its chunks and every figure, through the repository.

    No SQL here (ADR-021, toby's convention): the repository owns the
    statements, and `add_chunk` deliberately takes no scope argument, so this
    cannot write a chunk that is anything but the default `admin_only`.
    """
    document_id = await repository.add_document(
        revision=1,
        title=title,
        source_type=parsed.source_type,
        original_filename=parsed.original_filename,
        mime_type=parsed.mime_type,
        source_bytes=parsed.source_bytes,
        source_sha256=parsed.source_sha256,
        extracted_text=parsed.text,
    )

    chunks = chunk_text(parsed.text, load_limits())
    written_chunks = 0
    written_figures = 0
    for chunk in chunks:
        chunk_id = await repository.add_chunk(
            document_id=document_id,
            document_revision=1,
            ordinal=chunk.ordinal,
            heading=chunk.heading,
            body=chunk.body,
            content_sha256=hashlib.sha256(chunk.body.encode("utf-8")).hexdigest(),
        )
        written_chunks += 1
        # Figures are found per CHUNK so each one belongs to the chunk whose
        # scope decides whether it could ever be spoken.
        for figure in figures_in(chunk.body):
            await repository.add_figure(
                document_id=document_id,
                document_revision=1,
                chunk_id=chunk_id,
                value=figure.value,
                kind=figure.kind,
                currency=figure.currency,
                unit=figure.unit,
                surface=figure.surface,
                source_sentence=figure.source_sentence,
                page=figure.page,
            )
            written_figures += 1

    return {
        "id": str(document_id),
        "revision": 1,
        "status": "draft",
        "chunks": written_chunks,
        "figures": written_figures,
    }
