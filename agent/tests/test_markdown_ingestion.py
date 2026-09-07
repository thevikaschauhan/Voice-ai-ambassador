"""Markdown uploads: `.md` and `.markdown` in, prose out (task-api-upload-markdown).

Imports live inside each test for the reason test_ingestion.py gives: the file
must COLLECT before the code exists, so a missing name is N failing cases rather
than one collection error.

One test per normalisation rule rather than one test with nine assertions. The
rules are independent, and a regression should name the rule it broke in the
failure line instead of stopping at whichever assertion happened to be first.

The fixtures are unmistakably synthetic (NOTAREAL) so nothing here can be read
later as a real Binghatti price.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# The document every normalisation case reads. One fixture rather than nine,
# because the rules have to hold TOGETHER: a table inside a document that also
# has emphasis is the shape that finds an ordering bug between two rules.
NOTAREAL_MARKDOWN = """\
# NOTAREAL Tower

## Payment plans

A **sixty forty** plan on *handover*, with the `deposit_waived` flag set.

See the [payment schedule](https://notareal.example.com/schedule) for detail.

![NOTAREAL floor plan](https://notareal.example.com/plan.png)

- Two bedroom residences
- Three bedroom residences

| Unit type | Price |
| --- | --- |
| Two bedroom | AED 2,000,000 |
| Three bedroom | AED 3,500,000 |

```json
{"price": 999999999}
```

<div class="notareal">Handover is Q4 2029.</div>
"""


def _md(text: str = NOTAREAL_MARKDOWN, filename: str = "notareal.md") -> Any:
    from adapter.ingestion import parse_document

    return parse_document(text.encode("utf-8"), filename)


def test_a_markdown_file_is_stored_as_md_with_its_own_mime() -> None:
    parsed = _md()
    assert parsed.source_type == "md"
    assert parsed.mime_type == "text/markdown"


def test_the_markdown_extension_variant_is_accepted_too() -> None:
    """`.markdown` is the same format under a longer name, and refusing it
    would be an `unsupported_type` an admin cannot act on."""
    parsed = _md(filename="notareal.markdown")
    assert parsed.source_type == "md"


def test_heading_text_survives_and_the_marker_never_reaches_the_prose() -> None:
    """The heading is a line of its own; the `#` is not prose.

    The marker is kept in the extracted text on purpose and stripped one layer
    down: `ambassador.knowledge._is_heading` keys on a leading `#` and
    `_heading_text` removes it, so the chunk BODY - the text that is retrieved
    and read aloud - never contains it. Removing it here instead would leave the
    chunker no heading signal at all; see the chunker test below.
    """
    from ambassador.knowledge import chunk_text, load_limits

    lines = _md().text.splitlines()
    assert "NOTAREAL Tower" in [line.lstrip("#").strip() for line in lines]

    for chunk in chunk_text(_md().text, load_limits()):
        assert "#" not in chunk.body


def test_markdown_keeps_the_headings_the_chunker_needs() -> None:
    """The rule the other heading test protects, stated as its own case.

    A `.md` must not ingest WORSE than the same bytes renamed `.txt`. The
    chunker starts a new chunk at every heading ("two topics in one excerpt is
    how a retrieved chunk answers the wrong question"), so a document whose
    headings have been flattened into prose comes back as one undifferentiated
    chunk with no heading - which is a retrieval regression, not a formatting
    detail.
    """
    from ambassador.knowledge import chunk_text, load_limits

    chunks = chunk_text(_md().text, load_limits())
    assert [chunk.heading for chunk in chunks] == ["Payment plans"] * len(chunks)
    assert all(chunk.heading for chunk in chunks)


def test_emphasis_strong_and_inline_code_markers_are_removed_keeping_the_words() -> (
    None
):
    text = _md().text
    assert "sixty forty" in text
    assert "handover" in text
    assert "deposit_waived" in text
    assert "**" not in text
    assert "*" not in text
    assert "`" not in text


def test_a_link_keeps_its_text_and_loses_its_url() -> None:
    """The URL is not speakable and the words around it are."""
    text = _md().text
    assert "payment schedule" in text
    assert "https://notareal.example.com/schedule" not in text
    assert "](" not in text


def test_an_image_is_dropped_entirely_including_its_alt_text() -> None:
    """An image is not prose. Its alt text describes a picture nobody in a
    voice call can see, so keeping it would put a caption in the middle of a
    sentence."""
    text = _md().text
    assert "floor plan" not in text
    assert "plan.png" not in text


def test_list_markers_are_removed_and_the_item_text_stays() -> None:
    text = _md().text
    assert "Two bedroom residences" in text
    assert not any(line.startswith(("- ", "* ", "+ ")) for line in text.splitlines())


def test_a_table_becomes_one_line_per_row_with_no_separator_row() -> None:
    """Cells joined by ', ' so a row reads as a sentence rather than a grid."""
    text = _md().text
    assert "Two bedroom, AED 2,000,000" in text
    assert "Three bedroom, AED 3,500,000" in text
    assert "Unit type, Price" in text
    assert "|" not in text
    assert "---" not in text


def test_a_fenced_code_block_is_dropped() -> None:
    """A code fence is configuration, not something to say - and the number
    inside it would otherwise become a figure an admin is asked to approve."""
    text = _md().text
    assert "999999999" not in text
    assert "```" not in text
    assert "json" not in text


def test_blank_line_paragraph_structure_is_preserved() -> None:
    """The chunker splits on `\\n\\n`, so paragraph breaks are structure."""
    text = _md().text
    assert "\n\n" in text
    assert "\n\n\n" not in text, "runs of blank lines collapse to one break"


def test_html_tags_are_stripped_and_the_text_inside_them_stays() -> None:
    text = _md().text
    assert "Handover is Q4 2029." in text
    assert "<div" not in text
    assert "</div>" not in text
    assert 'class="notareal"' not in text


def test_a_markdown_table_yields_a_figure_whose_sentence_carries_no_pipe() -> None:
    """The reason the table rule exists.

    A figure's `source_sentence` is what an admin reads when approving a number
    and what anchors it afterwards. A raw table row would put `|` characters
    into it, which is both unreadable in review and unspeakable in a call.
    """
    from adapter.ingestion import figures_in

    figures = figures_in(_md().text)
    assert figures, "AED 2,000,000 in a table row is still a figure"
    for figure in figures:
        assert "|" not in figure.source_sentence
        assert "|" not in figure.surface


def test_the_upload_route_accepts_a_markdown_file_and_stores_it_as_md(
    monkeypatch: Any,
) -> None:
    """201, and `md` reaches the repository.

    The assertion is on what the route WROTE rather than on the response body:
    the upload response is `{id, revision, status, chunks, figures}` and carries
    no source_type, and widening it is a contract change this card does not
    make. What would be wrong if this broke is the stored row.
    """
    from adapter.admin_api import app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ADMIN_API_TOKEN", "test-token")
    written: list[dict[str, Any]] = []

    class Repository:
        async def add_document(self, **kwargs: Any) -> str:
            written.append(kwargs)
            return "doc-md"

        async def add_chunk(self, **kwargs: Any) -> str:
            return "chunk-1"

        async def add_figure(self, **kwargs: Any) -> str:
            return "fig-1"

    app.state.repository = Repository()
    with TestClient(app) as client:
        response = client.post(
            "/v1/knowledge/documents/upload",
            headers={"authorization": "Bearer test-token"},
            files={
                "file": (
                    "notareal.md",
                    NOTAREAL_MARKDOWN.encode("utf-8"),
                    "text/markdown",
                )
            },
            data={"title": "NOTAREAL payment plans"},
        )

    assert response.status_code == 201, response.text
    assert written, "the document must be written through the repository"
    assert written[0]["source_type"] == "md"
    assert written[0]["mime_type"] == "text/markdown"


# --- the schema half ------------------------------------------------------
#
# Marked per test rather than at module level, for the reason
# test_call_end_reason.py gives: a module-level skip hides these on the
# core-only install instead of reporting them absent.

needs_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)


async def _fresh_database() -> tuple[str, str]:
    import asyncpg

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_markdown_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    return admin_dsn.rsplit("/", 1)[0] + f"/{name}", name


async def _drop_database(name: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(os.environ["DATABASE_URL_TEST"])
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await admin.close()


async def _insert_document(connection: Any, source_type: str) -> None:
    await connection.execute(
        "INSERT INTO knowledge_documents (revision, title, source_type, mime_type,"
        " source_bytes, source_sha256) VALUES (1, $1, $2, $3, 1, $4)",
        f"NOTAREAL {source_type}",
        source_type,
        "text/markdown" if source_type == "md" else "text/plain",
        uuid.uuid4().hex,
    )


@pytest.fixture
async def database() -> Any:  # noqa: D401
    """An EMPTY database per test, migrated by the real runner."""
    from adapter.migrations import apply_migrations

    dsn, name = await _fresh_database()
    try:
        await apply_migrations(dsn)
        yield dsn
    finally:
        await _drop_database(name)


@needs_database
async def test_postgres_accepts_md_and_still_refuses_an_unknown_source_type(
    database: str,
) -> None:
    """The CHECK is the other half of `SourceType`.

    A type the parser can produce and the database refuses fails at INSERT,
    after the upload has been parsed and chunked - the admin sees a 500 for a
    file the service told them it supports.
    """
    import asyncpg

    connection = await asyncpg.connect(database)
    try:
        for source_type in ("pdf", "docx", "txt", "paste", "md"):
            await _insert_document(connection, source_type)

        # Still a constraint, not a dropped one: a migration that merely
        # removed the CHECK would pass every line above.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert_document(connection, "xlsx")
    finally:
        await connection.close()


@needs_database
async def test_0005_upgrades_a_database_already_at_0004(tmp_path: Path) -> None:
    """The case a fresh run cannot cover: production already has rows.

    0001..0004 with the real runner, then the real directory, which must apply
    exactly 0005.
    """
    import asyncpg

    from adapter.migrations import apply_migrations

    older = tmp_path / "migrations"
    older.mkdir()
    for version in ("0001", "0002", "0003", "0004"):
        source = next(MIGRATIONS.glob(f"{version}_*.sql"))
        shutil.copy(source, older / source.name)

    dsn, name = await _fresh_database()
    try:
        assert await apply_migrations(dsn, older) == ["0001", "0002", "0003", "0004"]

        connection = await asyncpg.connect(dsn)
        try:
            # A document stored under the old CHECK, so the upgrade has to
            # survive existing rows rather than an empty table.
            await _insert_document(connection, "pdf")
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await _insert_document(connection, "md")
        finally:
            await connection.close()

        assert await apply_migrations(dsn) == ["0005"]

        connection = await asyncpg.connect(dsn)
        try:
            await _insert_document(connection, "md")
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await _insert_document(connection, "xlsx")
            assert (
                await connection.fetchval("SELECT count(*) FROM knowledge_documents")
                == 2
            )
        finally:
            await connection.close()
    finally:
        await _drop_database(name)
