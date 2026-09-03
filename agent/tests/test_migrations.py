"""The Phase 2 schema, exercised against a real Postgres.

RED first, and against a REAL database rather than a fake one on purpose: the
things that break a schema - a composite type asyncpg cannot decode, an
append-only trigger that fires on the wrong row, a unique index that rejects a
legitimate second revision - are exactly the things a mock cannot have an
opinion about. `docs/02-` Phase 2 is the contract; this is it round-tripped.

Gated on `DATABASE_URL_TEST`, so the core-only install (ADR-002) and any
checkout without a database skip rather than fail. CI supplies it from the
postgres service container; locally:

    docker run --rm -e POSTGRES_PASSWORD=x -p 5433:5432 -d postgres:16
    export DATABASE_URL_TEST=postgresql://postgres:x@localhost:5433/postgres
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see this module's docstring",
)


@pytest.fixture
async def database() -> str:
    """An EMPTY database per test, so a migration cannot pass on someone
    else's leftovers."""
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_test_{uuid.uuid4().hex[:12]}"
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


async def test_migrations_round_trip_every_phase_2_contract(database: str) -> None:
    """One call and one document, written and read back through the repository.

    Every Phase 2 table in `docs/02-` is touched: a lead with its encrypted
    brief and contact, its turns, its analysis and score, an append-only
    decision, a knowledge document with a chunk, a figure and both review
    kinds, the knowledge-use provenance a turn froze, and an audit event.
    """
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        session_id = "sess_" + uuid.uuid4().hex[:12]
        lead_id = await repo.start_lead(
            session_id=session_id,
            language="en",
            requested_language="en",
            uncertified_fallback=False,
            inventory_version="10-records",
            started_at=datetime.now(timezone.utc),
        )

        await repo.finish_lead(
            lead_id,
            ended_at=datetime.now(timezone.utc),
            call_end_reason="buyer_farewell",
            ended_cleanly=True,
        )
        await repo.put_brief(lead_id, _envelope(b"brief"))
        await repo.put_contact(
            lead_id,
            status="captured",
            asked_turn_index=3,
            source_turn_index=4,
            name=_envelope(b"name"),
            phone=_envelope(b"phone"),
            email=None,
            phone_fingerprint="f" * 64,
            email_fingerprint=None,
            contact_permission=True,
            confirmed=True,
        )
        await repo.add_turn(
            lead_id,
            turn_index=1,
            timestamp=datetime.now(timezone.utc),
            audit_incomplete=False,
            payload=_envelope(b"turn"),
        )
        await repo.put_analysis(
            lead_id,
            status="complete",
            summary=_envelope(b"summary"),
            score_total=55,
            score_version="v1",
            breakdown=[{"signal": "budget_stated", "points_awarded": 15}],
        )

        lead = await repo.get_lead(lead_id)
        assert lead["session_id"] == session_id
        assert lead["call_end_reason"] == "buyer_farewell"
        assert lead["ended_cleanly"] is True
        assert lead["status"] == "unreviewed"
        assert lead["analysis_status"] == "complete"
        assert lead["score_total"] == 55
        assert lead["brief"]["ciphertext"] == b"brief"
        assert lead["contact_status"] == "captured"
        assert lead["contact_phone"]["ciphertext"] == b"phone"

        turns = await repo.get_turns(lead_id)
        assert [t["turn_index"] for t in turns] == [1]
        assert turns[0]["payload"]["ciphertext"] == b"turn"

        # The decision moves the lead and appends, in one transaction.
        await repo.record_decision(
            lead_id,
            new_status="qualified",
            reason_code="ready",
            note=_envelope(b"note"),
            actor_kind="admin",
            actor_id=None,
            expected_lead_revision=lead["revision"],
        )
        moved = await repo.get_lead(lead_id)
        assert moved["status"] == "qualified"
        assert moved["revision"] == lead["revision"] + 1
        decisions = await repo.get_decisions(lead_id)
        assert [d["sequence"] for d in decisions] == [1]
        assert decisions[0]["previous_status"] == "unreviewed"

        # Knowledge, through to what a turn is allowed to have seen.
        document_id = await repo.add_document(
            revision=1,
            title="Skyrise brochure",
            source_type="pdf",
            original_filename="skyrise.pdf",
            mime_type="application/pdf",
            source_bytes=1024,
            source_sha256="a" * 64,
            extracted_text="A studio starts at AED 985,000.",
        )
        chunk_id = await repo.add_chunk(
            document_id,
            document_revision=1,
            ordinal=0,
            heading=None,
            body="A studio starts at AED 985,000.",
            content_sha256="b" * 64,
        )
        figure_id = await repo.add_figure(
            document_id,
            document_revision=1,
            chunk_id=chunk_id,
            value="985000",
            kind="amount",
            currency="AED",
            surface="AED 985,000",
            source_sentence="A studio starts at AED 985,000.",
        )
        chunk = await repo.get_chunk(chunk_id)
        assert chunk["retrieval_scope"] == "admin_only", "a chunk defaults closed"
        assert chunk["prompt_body"] is None

        review_id = await repo.review_chunk(
            chunk_id, action="general_knowledge", project_id=None, actor_kind="admin"
        )
        approval_id = await repo.review_figure(
            figure_id, action="approved", actor_kind="admin"
        )
        assert (await repo.get_chunk(chunk_id))["scope_review_id"] == review_id
        assert (await repo.get_figure(figure_id))["active_approval_id"] == approval_id

        await repo.record_knowledge_use(
            lead_id,
            turn_index=1,
            query_fingerprint="c" * 64,
            chunk_refs=[
                {
                    "chunk_id": str(chunk_id),
                    "document_id": str(document_id),
                    "document_revision": 1,
                    "retrieval_scope": "general_knowledge",
                    "project_id": None,
                }
            ],
            figure_review_ids=[approval_id],
            withheld_figure_match=False,
            elapsed_ms=120,
        )
        use = await repo.get_knowledge_use(lead_id, turn_index=1)
        assert use["chunk_refs"][0]["document_revision"] == 1
        assert use["elapsed_ms"] == 120

        await repo.add_audit_event(lead_id, event="lead_persisted", detail={"turns": 1})
        assert [e["event"] for e in await repo.get_audit_events(lead_id)] == [
            "lead_persisted"
        ]
    finally:
        await repo.close()


def _envelope(plaintext: bytes) -> dict[str, object]:
    """A persistence envelope. The repository stores what it is handed and
    never encrypts - key handling belongs to the card that owns the keys."""
    return {
        "algorithm": "aes-256-gcm",
        "key_version": "v1",
        "nonce": b"0" * 12,
        "ciphertext": plaintext,
    }


async def test_applying_twice_changes_nothing(database: str) -> None:
    """The migration runner is the admin-api's preDeployCommand, so it runs on
    every deploy whether or not anything changed."""
    from adapter.migrations import apply_migrations, latest_version

    assert await apply_migrations(database) == [], "already applied by the fixture"
    connection = await asyncpg.connect(database)
    try:
        from adapter.migrations import assert_schema_current

        assert await assert_schema_current(connection) == latest_version()
    finally:
        await connection.close()


async def test_a_worker_on_an_old_schema_refuses_rather_than_migrating(
    database: str,
) -> None:
    """Both processes CHECK the version; neither applies it. A worker that
    migrated on startup would race the admin-api's pre-deploy run."""
    from adapter.migrations import SchemaOutOfDate, assert_schema_current

    connection = await asyncpg.connect(database)
    try:
        await connection.execute("DELETE FROM schema_migrations")
        with pytest.raises(SchemaOutOfDate, match="preDeployCommand"):
            await assert_schema_current(connection)
    finally:
        await connection.close()


async def test_one_call_cannot_become_two_leads(database: str) -> None:
    """`session_id` is the idempotency key: a persist retried after a
    half-finished shutdown must not produce a second lead."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        first = await repo.start_lead(**_lead_kwargs("sess_same"))
        again = await repo.start_lead(**_lead_kwargs("sess_same"))
        assert first == again
    finally:
        await repo.close()


async def test_a_decision_on_a_stale_revision_is_refused(database: str) -> None:
    """Two admins deciding at once. The second must fail rather than quietly
    overwrite the first, and the failure must leave nothing appended."""
    from adapter.repository import ConcurrentDecision, Repository

    repo = await Repository.connect(database)
    try:
        lead_id = await repo.start_lead(**_lead_kwargs("sess_race"))
        await repo.record_decision(
            lead_id,
            new_status="qualified",
            reason_code="ready",
            note=None,
            actor_kind="admin",
            actor_id=None,
            expected_lead_revision=0,
        )
        with pytest.raises(ConcurrentDecision):
            await repo.record_decision(
                lead_id,
                new_status="rejected",
                reason_code="duplicate",
                note=None,
                actor_kind="admin",
                actor_id=None,
                expected_lead_revision=0,
            )
        assert len(await repo.get_decisions(lead_id)) == 1
        assert (await repo.get_lead(lead_id))["status"] == "qualified"
    finally:
        await repo.close()


async def test_a_decision_cannot_be_edited_or_deleted(database: str) -> None:
    """Append-only is a property of the TABLE, not of the code that happens to
    write it: a future route with a stray UPDATE has to fail loudly."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        lead_id = await repo.start_lead(**_lead_kwargs("sess_append"))
        await repo.record_decision(
            lead_id,
            new_status="qualified",
            reason_code="ready",
            note=None,
            actor_kind="admin",
            actor_id=None,
            expected_lead_revision=0,
        )
        connection = await asyncpg.connect(database)
        try:
            for statement in (
                "UPDATE admin_decisions SET reason_code = 'other'",
                "DELETE FROM admin_decisions",
            ):
                with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                    await connection.execute(statement)
        finally:
            await connection.close()
    finally:
        await repo.close()


async def test_a_half_captured_contact_is_rejected_by_the_schema(
    database: str,
) -> None:
    """A contact marked captured with no phone and no email looks like a lead
    somebody can call. docs/02- forbids it, so the table does."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        lead_id = await repo.start_lead(**_lead_kwargs("sess_contact"))
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await repo.put_contact(
                lead_id,
                status="captured",
                asked_turn_index=2,
                source_turn_index=3,
                name=_envelope(b"name"),
                phone=None,
                email=None,
                phone_fingerprint=None,
                email_fingerprint=None,
                contact_permission=True,
                confirmed=True,
            )
    finally:
        await repo.close()


async def test_an_unreviewed_chunk_can_never_reach_a_prompt(database: str) -> None:
    """The gate the whole knowledge design rests on. Scope is a review, and an
    admin-only chunk carrying prompt text would bypass it."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        document_id = await repo.add_document(**_document_kwargs())
        chunk_id = await repo.add_chunk(
            document_id,
            document_revision=1,
            ordinal=0,
            heading=None,
            body="Something unreviewed.",
            content_sha256="d" * 64,
        )
        connection = await asyncpg.connect(database)
        try:
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await connection.execute(
                    "UPDATE knowledge_chunks SET prompt_body = body WHERE id = $1",
                    chunk_id,
                )
        finally:
            await connection.close()
    finally:
        await repo.close()


async def test_revoking_a_figure_clears_the_projection_and_keeps_the_history(
    database: str,
) -> None:
    """Archiving or revoking must affect new turns without erasing what a
    historic turn was allowed to see (docs/02-)."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        document_id = await repo.add_document(**_document_kwargs())
        chunk_id = await repo.add_chunk(
            document_id,
            document_revision=1,
            ordinal=0,
            heading=None,
            body="A studio starts at AED 985,000.",
            content_sha256="e" * 64,
        )
        figure_id = await repo.add_figure(
            document_id,
            document_revision=1,
            chunk_id=chunk_id,
            value="985000",
            kind="amount",
            currency="AED",
            surface="AED 985,000",
            source_sentence="A studio starts at AED 985,000.",
        )
        await repo.review_figure(figure_id, action="approved", actor_kind="admin")
        await repo.review_figure(figure_id, action="revoked", actor_kind="admin")

        assert (await repo.get_figure(figure_id))["active_approval_id"] is None
        connection = await asyncpg.connect(database)
        try:
            history = await connection.fetch(
                "SELECT action FROM knowledge_figure_reviews WHERE figure_id = $1"
                " ORDER BY created_at, id",
                figure_id,
            )
        finally:
            await connection.close()
        assert [row["action"] for row in history] == ["approved", "revoked"]
    finally:
        await repo.close()


async def test_the_search_vector_uses_the_simple_configuration(database: str) -> None:
    """A stemmer would have to be chosen per document, and this corpus is Gulf
    real estate in three languages where the load-bearing terms - project
    names, unit types, AED - are the ones a stemmer damages."""
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        document_id = await repo.add_document(**_document_kwargs())
        await repo.add_chunk(
            document_id,
            document_revision=1,
            ordinal=0,
            heading="Binghatti Skyrise",
            body="Studios face the Burj Khalifa.",
            content_sha256="f" * 64,
        )
        connection = await asyncpg.connect(database)
        try:
            hits = await connection.fetchval(
                "SELECT count(*) FROM knowledge_chunks"
                " WHERE search_vector @@ to_tsquery('simple', 'skyrise')"
            )
        finally:
            await connection.close()
        assert hits == 1
    finally:
        await repo.close()


def _lead_kwargs(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "language": "en",
        "requested_language": "en",
        "uncertified_fallback": False,
        "inventory_version": "10-records",
        "started_at": datetime.now(timezone.utc),
    }


def _document_kwargs() -> dict[str, object]:
    return {
        "revision": 1,
        "title": "Brochure",
        "source_type": "pdf",
        "original_filename": "b.pdf",
        "mime_type": "application/pdf",
        "source_bytes": 10,
        "source_sha256": "0" * 64,
        "extracted_text": "text",
    }
