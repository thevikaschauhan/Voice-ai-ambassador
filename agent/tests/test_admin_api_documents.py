"""`figures_pending` on every row of GET /v1/knowledge/documents
(task-api-documents-figures-pending, docs/02- and docs/10-).

The overview's "Needs attention" panel needs a count of figures awaiting
approval per document. Figure state lived only on the detail route, so the
panel would have had to fan out one detail read per document to say it.

Gated on DATABASE_URL_TEST, and it has to be: the whole contract is the SQL.
`active_approval_id` is a projection of the append-only review history, the
count is a property of the query plan, and a fake repository would hand back
whatever number it was constructed with no matter which revision the figures
sat on.

Both sides of the seam are asserted against ONE seeded database - the
repository projection and the route body. Two suites that each seed through
their own side agree with each other and not with production; that is exactly
how the `turns.{i}` payload path shipped broken (memory, #137).

Imports live inside each test and fixture so the file COLLECTS before the
column exists: a module-level import of a missing name is a collection error
rather than N failing cases, and the gate counts cases (docs/06-).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see this module's docstring",
)

TOKEN = "a-shared-admin-token-for-document-list-tests"


@pytest.fixture
async def database() -> str:
    """An EMPTY database per test, migrated by the real runner."""
    import asyncpg

    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_doc_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    dsn = admin_dsn.rsplit("/", 1)[0] + f"/{name}"
    try:
        await apply_migrations(dsn)
        yield dsn
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


async def _document(repository: Any, title: str) -> Any:
    """One draft document at revision 1, through the real writer."""
    return await repository.add_document(
        revision=1,
        title=title,
        source_type="txt",
        original_filename=f"{title}.txt",
        mime_type="text/plain",
        source_bytes=len(title),
        source_sha256=hashlib.sha256(title.encode()).hexdigest(),
        extracted_text=f"{title} body",
    )


async def _chunk(repository: Any, document_id: Any, *, revision: int) -> Any:
    body = f"chunk of {document_id} r{revision}"
    return await repository.add_chunk(
        document_id,
        document_revision=revision,
        ordinal=0,
        heading=None,
        body=body,
        content_sha256=hashlib.sha256(body.encode()).hexdigest(),
    )


async def _figure(
    repository: Any, document_id: Any, chunk_id: Any, *, revision: int, surface: str
) -> Any:
    """An extracted figure. Parsing never approves one, so it arrives
    pending - which is the state this count is about."""
    return await repository.add_figure(
        document_id,
        document_revision=revision,
        chunk_id=chunk_id,
        value="1000000",
        kind="amount",
        currency="AED",
        unit=None,
        surface=surface,
        source_sentence=f"The price is {surface}.",
        page=1,
    )


@pytest.fixture
async def seeded(database):
    """Four documents, one per case in the contract, in one database.

    Case 4 needs a SUPERSEDED revision of the same document, and
    `add_document` mints a fresh id on every call by design, so the extra
    `knowledge_documents` row is the one statement written here directly. Its
    chunk and its figure still go through the real writers.
    """
    import asyncpg

    from adapter.repository import Repository

    repository = await Repository.connect(database)
    ids: dict[str, Any] = {}

    # 1. No figures at all.
    ids["bare"] = await _document(repository, "bare")

    # 2. Two figures, neither reviewed.
    ids["two_pending"] = await _document(repository, "two-pending")
    chunk = await _chunk(repository, ids["two_pending"], revision=1)
    for surface in ("1,000,000", "2,000,000"):
        await _figure(
            repository, ids["two_pending"], chunk, revision=1, surface=surface
        )

    # 3. One approved, one approved-then-revoked, one untouched. The revoked
    #    one counts as pending again: `active_approval_id` is NULL once more.
    ids["mixed"] = await _document(repository, "mixed")
    chunk = await _chunk(repository, ids["mixed"], revision=1)
    approved = await _figure(
        repository, ids["mixed"], chunk, revision=1, surface="3,000,000"
    )
    revoked = await _figure(
        repository, ids["mixed"], chunk, revision=1, surface="4,000,000"
    )
    await _figure(repository, ids["mixed"], chunk, revision=1, surface="5,000,000")
    await repository.review_figure(approved, action="approved", actor_kind="admin")
    await repository.review_figure(revoked, action="approved", actor_kind="admin")
    await repository.review_figure(revoked, action="revoked", actor_kind="admin")

    # 4. Two revisions. The superseded one carries two pending figures the
    #    count must NOT see; the current one carries a single pending figure.
    ids["revised"] = await _document(repository, "revised")
    old_chunk = await _chunk(repository, ids["revised"], revision=1)
    for surface in ("6,000,000", "7,000,000"):
        await _figure(
            repository, ids["revised"], old_chunk, revision=1, surface=surface
        )
    connection = await asyncpg.connect(database)
    try:
        await connection.execute(
            """
            INSERT INTO knowledge_documents (
                id, revision, title, source_type, original_filename, mime_type,
                source_bytes, source_sha256, extracted_text, status)
            VALUES ($1, 2, 'revised', 'txt', 'revised.txt', 'text/plain',
                    7, 'sha-r2', 'revised body', 'draft')
            """,
            ids["revised"],
        )
    finally:
        await connection.close()
    new_chunk = await _chunk(repository, ids["revised"], revision=2)
    await _figure(
        repository, ids["revised"], new_chunk, revision=2, surface="8,000,000"
    )

    try:
        yield repository, ids
    finally:
        await repository.close()


@pytest.fixture
async def client(monkeypatch, seeded):
    from httpx import ASGITransport, AsyncClient

    from adapter import admin_api

    repository, _ = seeded
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    admin_api.app.state.repository = repository
    transport = ASGITransport(app=admin_api.app)
    async with AsyncClient(transport=transport, base_url="http://admin") as http:
        yield http


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _by_title(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["title"]: row for row in rows}


# --- the repository projection --------------------------------------------


async def test_a_document_with_no_figures_counts_zero(seeded):
    """0, not absent and not null. A panel that has to treat a missing key as
    zero cannot tell "none pending" from "not computed"."""
    repository, _ = seeded
    rows = _by_title(await repository.list_documents())
    assert rows["bare"]["figures_pending"] == 0


async def test_two_unreviewed_figures_count_two(seeded):
    repository, _ = seeded
    rows = _by_title(await repository.list_documents())
    assert rows["two-pending"]["figures_pending"] == 2


async def test_a_revoked_approval_is_pending_again(seeded):
    """Three figures, one approved, one approved-then-revoked, one untouched.

    The revoked one is the case worth the fixture: `knowledge_figure_reviews`
    is append-only, so its approval is still on record, and only the
    `active_approval_id` projection says the figure no longer carries one.
    Counting review rows instead would answer 1 here.
    """
    repository, _ = seeded
    rows = _by_title(await repository.list_documents())
    assert rows["mixed"]["figures_pending"] == 2


async def test_only_the_current_revision_is_counted(seeded):
    """The superseded revision holds two pending figures and the current one
    holds a single figure, so a count keyed on `document_id` alone answers 3
    here and passes every other case in this file."""
    repository, _ = seeded
    rows = _by_title(await repository.list_documents())
    assert rows["revised"]["figures_pending"] == 1


async def test_the_field_is_an_integer_on_every_row(seeded):
    """Present on all four, and an int rather than a driver's own numeric
    wrapper - this value crosses a JSON boundary."""
    repository, _ = seeded
    rows = await repository.list_documents()
    assert len(rows) == 4
    for row in rows:
        assert type(row["figures_pending"]) is int, row["title"]


async def test_the_list_still_withholds_the_extracted_text(seeded):
    """The reason this projection names its columns. Re-asserted because this
    change rewrites the select list the exclusion lives in."""
    repository, _ = seeded
    for row in await repository.list_documents():
        assert "extracted_text" not in row


# --- the same four documents through the route ----------------------------


async def test_the_route_carries_the_count_on_every_row(client):
    """The other side of the seam, against the same seeded database. The
    repository tests above prove the SQL; this proves the number survives the
    route and its JSON encoding, which is where a non-int would fail.
    """
    response = await client.get("/v1/knowledge/documents", headers=auth())
    assert response.status_code == 200, response.text
    rows = _by_title(response.json())
    assert {title: row["figures_pending"] for title, row in rows.items()} == {
        "bare": 0,
        "two-pending": 2,
        "mixed": 2,
        "revised": 1,
    }
