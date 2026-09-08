"""Document approval is one transaction, never a loop of browser writes."""

import os
import secrets
from uuid import uuid4

import pytest


def test_publication_requires_the_exact_reviewed_set_and_preserves_closures():
    from ambassador.document_review import publication_scopes
    from ambassador.schemas import KnowledgeReviewChunk, KnowledgeScopeSelection

    ids = [uuid4() for _ in range(3)]
    chunks = [
        KnowledgeReviewChunk(
            id=i,
            ordinal=n,
            body="Reviewed prose.",
            retrieval_scope="inventory_governed" if n == 2 else "admin_only",
        )
        for n, i in enumerate(ids)
    ]
    selection = [
        KnowledgeScopeSelection(chunk_id=i, action="general_knowledge") for i in ids
    ]
    with pytest.raises(ValueError, match="inventory"):
        publication_scopes(chunks, selection, {"known"})
    selection[2] = KnowledgeScopeSelection(chunk_id=ids[2], action="inventory_governed")
    assert len(publication_scopes(chunks, selection, {"known"})) == 3
    with pytest.raises(ValueError, match="every"):
        publication_scopes(chunks, selection[:2], {"known"})
    selection[0] = KnowledgeScopeSelection(
        chunk_id=ids[0], action="project_knowledge", project_id="missing"
    )
    with pytest.raises(ValueError, match="project"):
        publication_scopes(chunks, selection, {"known"})


@pytest.fixture
async def repository():
    if not os.environ.get("DATABASE_URL_TEST"):
        pytest.skip("DATABASE_URL_TEST required")
    import asyncpg
    from adapter.migrations import apply_migrations
    from adapter.repository import Repository

    base = os.environ["DATABASE_URL_TEST"]
    name = "publication_" + uuid4().hex[:12]
    admin = await asyncpg.connect(base)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = base.rsplit("/", 1)[0] + "/" + name
    await apply_migrations(dsn)
    repo = await Repository.connect(dsn)
    try:
        yield repo
    finally:
        await repo.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def seed(repo):
    from adapter.ingestion import parse_document, store_document

    text = "# Design\n\nOpen living spaces with shaded terraces.\n\n# Guidance\n\nAllow 37 minutes for the visit."
    result = await store_document(
        repo, title="Review fixture", parsed=parse_document(None, None, pasted=text)
    )
    return result["id"]


async def request_for(repo, document_id):
    from ambassador.schemas import KnowledgePublicationRequest

    document = await repo.get_document_review(document_id)
    return KnowledgePublicationRequest(
        request_id=uuid4(),
        expected_revision=document["revision"],
        expected_review_token=document["review_token"],
        confirmed=True,
        selections=[
            {"chunk_id": str(c["id"]), "action": "general_knowledge"}
            for c in document["chunks"]
        ],
    )


async def test_atomic_publish_keeps_numbers_withheld_and_retries_once(repository):
    from ambassador.schemas import KnowledgePublicationRequest

    repo = repository
    doc_id = await seed(repo)
    request = await request_for(repo, doc_id)
    result = await repo.publish_document(doc_id, request)
    assert result["status"] == "published"
    assert await repo.publish_document(doc_id, request) == result
    doc = await repo.get_document_review(doc_id)
    assert doc["status"] == "published"
    assert all(f["active_approval_id"] is None for f in doc["figures"])
    assert "37" not in doc["chunks"][1]["review_body"]
    rows = await repo.search_chunks(["terraces"], project_ids=[])
    assert len(rows) == 1
    async with repo._pool.acquire() as c:
        assert (
            await c.fetchval("SELECT count(*) FROM knowledge_document_publications")
            == 1
        )
        assert await c.fetchval("SELECT count(*) FROM knowledge_chunk_reviews") == 2
    changed = KnowledgePublicationRequest.model_validate(
        {**request.model_dump(), "confirmed": False}
    )
    with pytest.raises(ValueError):
        await repo.publish_document(doc_id, changed)


async def test_stale_figure_review_cannot_publish_and_exclusions_stay_closed(
    repository,
):
    from adapter.repository import ConcurrentPublication
    from ambassador.schemas import KnowledgeScopeSelection

    repo = repository
    doc_id = await seed(repo)
    old = await request_for(repo, doc_id)
    figures = await repo.get_figures(doc_id, revision=1)
    await repo.review_figure(figures[0]["id"], action="approved", actor_kind="admin")
    with pytest.raises(ConcurrentPublication):
        await repo.publish_document(doc_id, old)
    assert (await repo.get_document(doc_id))["status"] == "draft"
    fresh = await request_for(repo, doc_id)
    fresh.selections[1] = KnowledgeScopeSelection(
        chunk_id=fresh.selections[1].chunk_id, action="admin_only"
    )
    await repo.publish_document(doc_id, fresh)
    assert await repo.search_chunks(["minutes"], project_ids=[]) == []


async def test_failed_publication_rolls_back_all_reviews(repository):
    repo = repository
    doc_id = await seed(repo)
    request = await request_for(repo, doc_id)
    async with repo._pool.acquire() as c:
        await c.execute("""CREATE FUNCTION reject_publication() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test publication failure'; END $$;
        CREATE TRIGGER reject_publication BEFORE INSERT ON knowledge_document_publications
        FOR EACH ROW EXECUTE FUNCTION reject_publication();""")
    with pytest.raises(Exception, match="test publication failure"):
        await repo.publish_document(doc_id, request)
    assert (await repo.get_document(doc_id))["status"] == "draft"
    async with repo._pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM knowledge_chunk_reviews") == 0
    assert all(
        c["prompt_body"] is None for c in await repo.get_chunks(doc_id, revision=1)
    )


async def test_publication_route_is_bearer_protected_and_publishes(
    repository, monkeypatch
):
    from httpx import ASGITransport, AsyncClient
    from adapter import admin_api

    doc_id = await seed(repository)
    request = await request_for(repository, doc_id)
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("ADMIN_API_TOKEN", token)
    monkeypatch.setattr(admin_api.app.state, "repository", repository, raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=admin_api.app), base_url="http://admin"
    ) as client:
        url = f"/v1/knowledge/documents/{doc_id}/publish"
        assert (
            await client.post(url, json=request.model_dump(mode="json"))
        ).status_code == 401
        response = await client.post(
            url,
            json=request.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "published"


@pytest.mark.parametrize(
    "case", ["duplicate", "empty", "conflict", "general_binding", "missing_project"]
)
def test_publication_refuses_incomplete_or_unsafe_choices(case):
    from ambassador.document_review import publication_scopes
    from ambassador.schemas import KnowledgeReviewChunk, KnowledgeScopeSelection

    chunk = KnowledgeReviewChunk(id=uuid4(), ordinal=0, body="A description.")
    choice = KnowledgeScopeSelection(chunk_id=chunk.id, action="general_knowledge")
    selections = [choice]
    if case == "duplicate":
        selections.append(choice)
    elif case == "empty":
        choice.action = "admin_only"
    elif case == "conflict":
        chunk.conflict_code = "unknown_project"
    elif case == "general_binding":
        choice.project_id = "known"
    else:
        choice.action = "project_knowledge"
    with pytest.raises(ValueError):
        publication_scopes([chunk], selections, {"known"})


def test_excluding_inventory_never_erases_its_authority_boundary():
    from ambassador.document_review import publication_scopes
    from ambassador.schemas import KnowledgeReviewChunk, KnowledgeScopeSelection

    chunks = [
        KnowledgeReviewChunk(
            id=uuid4(),
            ordinal=i,
            body="Source prose.",
            retrieval_scope="inventory_governed" if i else "admin_only",
        )
        for i in range(2)
    ]
    choices = [
        KnowledgeScopeSelection(
            chunk_id=c.id,
            action="admin_only" if i else "project_knowledge",
            project_id=None if i else "known",
        )
        for i, c in enumerate(chunks)
    ]
    result = publication_scopes(chunks, choices, {"known"})
    assert result[0].project_id == "known"
    assert result[1].retrieval_scope == "inventory_governed"


async def test_missing_occurrences_cannot_be_published(repository):
    from adapter.ingestion import parse_document, store_document

    repo = repository
    doc_id = (
        await store_document(
            repo,
            title="Extraction interrupted",
            parsed=parse_document(None, None, pasted="Allow 37 minutes."),
        )
    )["id"]
    async with repo._pool.acquire() as c:
        await c.execute("DELETE FROM knowledge_figures WHERE document_id=$1", doc_id)
    request = await request_for(repo, doc_id)
    with pytest.raises(ValueError, match="extraction is incomplete"):
        await repo.publish_document(doc_id, request)


async def test_concurrent_publications_have_one_winner(repository):
    import asyncio
    from adapter.repository import ConcurrentPublication

    doc_id = await seed(repository)
    first = await request_for(repository, doc_id)
    second = first.model_copy(update={"request_id": uuid4()})
    results = await asyncio.gather(
        repository.publish_document(doc_id, first),
        repository.publish_document(doc_id, second),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConcurrentPublication) for r in results) == 1
    async with repository._pool.acquire() as c:
        assert (
            await c.fetchval("SELECT count(*) FROM knowledge_document_publications")
            == 1
        )
