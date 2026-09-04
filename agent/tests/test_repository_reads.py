"""Every read in the repository runs against the real schema, with a row.

A named-column typo is invisible to everything except Postgres. asyncpg
prepares lazily, so `SELECT content_sha` costs nothing until a row exists to
return - and until knowledge ingestion landed, nothing could create a chunk.
The admin-API route tests passed because their repository was a fake that
agreed to any column name, and the container proof passed because the table
was empty. The first real chunk 500s.

So these tests write through the repository's own writers and read back
through its readers, against a real Postgres. A fake cannot hold this
property, because the property IS agreement with the schema.

`test_every_sql_statement_prepares_against_the_real_schema` is the general
form: it asks Postgres to parse every statement in the module, so a query no
test exercises yet still cannot name a column that does not exist. The
round-trip tests below stay because preparing proves a statement is legal,
not that the value written comes back.

Imports are inside each test so a RED run reads N failed = N cases rather
than one collection error.
"""

from __future__ import annotations

import ast
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

REPOSITORY = Path(__file__).resolve().parents[1] / "src" / "adapter" / "repository.py"


def _envelope(marker: bytes) -> dict[str, object]:
    """Shaped like a sealed value, without a sealer.

    These reads never decrypt, so the bytes need only survive the round trip.
    """
    return {
        "algorithm": "AES-256-GCM",
        "key_version": "v1",
        "nonce": b"\x00" * 12,
        "ciphertext": marker,
    }


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_reads_{uuid.uuid4().hex[:10]}"
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
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


@pytest.fixture
async def repository(database: str):
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        yield repo
    finally:
        await repo.close()


@pytest.fixture
async def document(repository):
    """A published-shape document with one chunk and one figure on it."""
    document_id = await repository.add_document(
        revision=1,
        title="Canal Residences brochure",
        source_type="pdf",
        original_filename="canal.pdf",
        mime_type="application/pdf",
        source_bytes=2048,
        source_sha256="a" * 64,
        extracted_text="Handover is in 2027.",
    )
    chunk_id = await repository.add_chunk(
        document_id,
        document_revision=1,
        ordinal=0,
        heading="Handover",
        body="Handover is in 2027.",
        content_sha256="b" * 64,
    )
    figure_id = await repository.add_figure(
        document_id,
        document_revision=1,
        chunk_id=chunk_id,
        value="2027",
        kind="year",
        surface="2027",
        source_sentence="Handover is in 2027.",
        page=3,
    )
    return {"document_id": document_id, "chunk_id": chunk_id, "figure_id": figure_id}


@pytest.fixture
async def lead(repository):
    """A finished lead carrying a turn, an analysis and an audit event."""
    lead_id = await repository.start_lead(
        session_id=f"sess-{uuid.uuid4().hex[:8]}",
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="2026-09-01.1",
        started_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    await repository.add_turn(
        lead_id,
        turn_index=0,
        timestamp=datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc),
        audit_incomplete=False,
        payload=_envelope(b"turn-zero"),
    )
    await repository.put_analysis(
        lead_id,
        status="complete",
        summary=_envelope(b"summary"),
        score_total=55,
        score_version="2026-09-03.1",
        breakdown=[{"signal": "budget_stated", "awarded": 15}],
    )
    await repository.finish_lead(
        lead_id,
        ended_at=datetime(2026, 9, 3, 12, 4, tzinfo=timezone.utc),
        call_end_reason="buyer_left",
        ended_cleanly=True,
    )
    return lead_id


# -- the general form ---------------------------------------------------


async def test_every_sql_statement_prepares_against_the_real_schema(database):
    """Postgres parses every statement in repository.py.

    Preparing resolves table and column names without running anything, so
    this covers the statements no test has a row for yet. It is the check
    that would have caught `content_sha` on the day it was written.
    """
    connection = await asyncpg.connect(database)
    try:
        statements = [
            (node.lineno, node.value.strip())
            for node in ast.walk(ast.parse(REPOSITORY.read_text()))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip().split(" ")[0].upper()
            in {"SELECT", "INSERT", "UPDATE", "DELETE"}
        ]
        assert len(statements) > 20, "SQL is no longer written as module literals"
        rejected = []
        for lineno, sql in statements:
            try:
                await connection.prepare(sql)
            except asyncpg.PostgresSyntaxError:
                raise
            except asyncpg.PostgresError as exc:
                rejected.append(
                    f"{REPOSITORY.name}:{lineno} {type(exc).__name__}: {exc}"
                )
    finally:
        await connection.close()
    assert rejected == []


# -- knowledge reads ----------------------------------------------------


async def test_get_chunks_returns_the_chunk_that_was_written(repository, document):
    chunks = await repository.get_chunks(document["document_id"], revision=1)
    assert [chunk["id"] for chunk in chunks] == [document["chunk_id"]]


async def test_get_chunks_returns_the_content_hash_under_its_real_name(
    repository, document
):
    """The column is `content_sha256`. `content_sha` is a different column
    that does not exist, and naming it takes the whole read down."""
    (chunk,) = await repository.get_chunks(document["document_id"], revision=1)
    assert chunk["content_sha256"] == "b" * 64


async def test_get_chunks_omits_the_search_vector(repository, document):
    """Named columns exist to keep the tsvector out - it is an index
    artefact with no JSON form. Fixing the hash column must not become
    `SELECT *`."""
    (chunk,) = await repository.get_chunks(document["document_id"], revision=1)
    assert "search_vector" not in chunk


async def test_get_document_returns_the_document_that_was_written(repository, document):
    found = await repository.get_document(document["document_id"], revision=1)
    assert found["title"] == "Canal Residences brochure"
    assert found["source_sha256"] == "a" * 64


async def test_list_documents_returns_the_document_that_was_written(
    repository, document
):
    (found,) = await repository.list_documents()
    assert found["id"] == document["document_id"]
    assert found["status"] == "draft"


async def test_list_documents_omits_the_extracted_text(repository, document):
    """The list names its columns so the transcript never reaches a list
    response."""
    (found,) = await repository.list_documents()
    assert "extracted_text" not in found


async def test_get_figures_returns_the_figure_that_was_written(repository, document):
    (figure,) = await repository.get_figures(document["document_id"], revision=1)
    assert figure["id"] == document["figure_id"]
    assert figure["surface"] == "2027"


async def test_get_chunk_returns_the_single_chunk(repository, document):
    chunk = await repository.get_chunk(document["chunk_id"])
    assert chunk["body"] == "Handover is in 2027."


async def test_get_figure_returns_the_single_figure(repository, document):
    figure = await repository.get_figure(document["figure_id"])
    assert figure["kind"] == "year"


# -- lead reads ---------------------------------------------------------


async def test_list_leads_returns_the_lead_that_was_started(repository, lead):
    (found,) = await repository.list_leads(
        status=None, language=None, limit=50, offset=0
    )
    assert found["id"] == lead
    assert found["score_total"] == 55


async def test_list_leads_omits_every_buyer_column(repository, lead):
    """The projection is the control: a column absent from the SELECT
    cannot reach an admin list response."""
    (found,) = await repository.list_leads(
        status=None, language=None, limit=50, offset=0
    )
    for column in (
        "summary",
        "brief",
        "contact_name",
        "contact_phone",
        "contact_email",
    ):
        assert column not in found


async def test_get_lead_returns_the_lead_that_was_started(repository, lead):
    found = await repository.get_lead(lead)
    assert found["call_end_reason"] == "buyer_left"
    assert found["analysis_status"] == "complete"


async def test_get_turns_returns_the_turn_that_was_added(repository, lead):
    (turn,) = await repository.get_turns(lead)
    assert turn["turn_index"] == 0
    assert turn["payload"]["ciphertext"] == b"turn-zero"


async def test_get_decisions_returns_the_decision_that_was_recorded(repository, lead):
    await repository.record_decision(
        lead,
        new_status="qualified",
        reason_code="ready",
        note=_envelope(b"note"),
        actor_kind="admin",
        actor_id=uuid.uuid4(),
        expected_lead_revision=(await repository.get_lead(lead))["revision"],
    )
    (decision,) = await repository.get_decisions(lead)
    assert decision["new_status"] == "qualified"
    assert decision["sequence"] == 1


async def test_get_knowledge_use_returns_the_row_that_was_recorded(
    repository, lead, document
):
    await repository.record_knowledge_use(
        lead,
        turn_index=0,
        query_fingerprint="handover",
        chunk_refs=[{"chunk_id": str(document["chunk_id"]), "revision": 1}],
        figure_review_ids=[],
        withheld_figure_match=False,
        elapsed_ms=42,
    )
    use = await repository.get_knowledge_use(lead, turn_index=0)
    assert use["elapsed_ms"] == 42
    assert use["chunk_refs"][0]["chunk_id"] == str(document["chunk_id"])


async def test_get_audit_events_returns_the_event_that_was_added(repository, lead):
    await repository.add_audit_event(
        lead, event="lead_detail_read", detail={"by": "admin"}
    )
    (event,) = await repository.get_audit_events(lead)
    assert event["event"] == "lead_detail_read"
    assert event["detail"] == '{"by": "admin"}'


# -- full-text retrieval (ADR-019) --------------------------------------


@pytest.fixture
async def published(repository):
    """A published document with three chunks: general, bound project, and
    one still closed. Only Postgres can settle what the search returns."""
    document_id = await repository.add_document(
        revision=1,
        title="Handbook",
        source_type="txt",
        original_filename=None,
        mime_type="text/plain",
        source_bytes=512,
        source_sha256="c" * 64,
        extracted_text="",
    )
    ids = {}
    for ordinal, (key, body, scope, project) in enumerate(
        [
            (
                "general",
                "The rooftop pool is open to residents.",
                "general_knowledge",
                None,
            ),
            (
                "project",
                "Canal Residences faces the water.",
                "project_knowledge",
                "binghatti-canal",
            ),
            ("closed", "Internal margin guidance for the pool.", "admin_only", None),
        ]
    ):
        chunk_id = await repository.add_chunk(
            document_id,
            document_revision=1,
            ordinal=ordinal,
            heading=None,
            body=body,
            content_sha256=f"{ordinal:064d}",
        )
        ids[key] = chunk_id
        if scope != "admin_only":
            await repository._pool.execute(
                "UPDATE knowledge_chunks SET retrieval_scope = $2, project_id = $3,"
                " prompt_body = body WHERE id = $1",
                chunk_id,
                scope,
                project,
            )
    await repository._pool.execute(
        "UPDATE knowledge_documents SET status = 'published' WHERE id = $1",
        document_id,
    )
    return {"document_id": document_id, **ids}


async def test_search_returns_reviewed_prose_and_never_a_closed_chunk(
    repository, published
):
    rows = await repository.search_chunks("pool", project_ids=[], limit=4)
    assert [row["id"] for row in rows] == [published["general"]]


async def test_search_skips_a_document_that_is_not_published(repository, published):
    await repository._pool.execute(
        "UPDATE knowledge_documents SET status = 'draft' WHERE id = $1",
        published["document_id"],
    )
    assert await repository.search_chunks("pool", project_ids=[], limit=4) == []


async def test_a_bound_project_chunk_ranks_ahead_of_general_knowledge(
    repository, published
):
    """docs/10-: when the turn's project is known its prose sorts first, and
    general knowledge stays eligible on every turn."""
    await repository._pool.execute(
        "UPDATE knowledge_chunks SET prompt_body = 'The rooftop pool faces the water.'"
        " WHERE id = $1",
        published["general"],
    )
    rows = await repository.search_chunks(
        "water", project_ids=["binghatti-canal"], limit=4
    )
    assert rows[0]["id"] == published["project"]


async def test_figures_for_chunks_reports_approval_from_the_active_review(
    repository, published
):
    """`approved` is derived from `active_approval_id`, so a revocation reads
    as False here without a second query."""
    unapproved = await repository.add_figure(
        published["document_id"],
        document_revision=1,
        chunk_id=published["general"],
        value="1250000",
        kind="amount",
        currency="AED",
        surface="1,250,000",
        source_sentence="Prices start at 1,250,000.",
    )
    (figure,) = await repository.figures_for_chunks([published["general"]])
    assert figure["id"] == unapproved
    assert figure["approved"] is False
