"""Every call becomes a lead, and no buyer word leaves in the clear.

docs/10- "Lead finalisation": `shutdown_session` owns the sequence for every
ending, including `buyer_left` and the duration cap. The redacted event stream
is not the source - the in-process turns and the last accepted brief are.

Against a real Postgres, because idempotency on `session_id` and the
authenticated envelope are both things a fake would agree to regardless.

Imports are inside each test so a RED run reads N failed = N cases rather than
one collection error.
"""

from __future__ import annotations

import os
import uuid
from io import StringIO

import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

KEY = "k" * 64  # 32 bytes, hex
HASH_KEY = "h" * 64


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_persist_{uuid.uuid4().hex[:10]}"
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


def _snapshot(session_id: str, **overrides):
    from ambassador.schemas import (
        ContactCapture,
        LeadBrief,
        LeadSnapshot,
        SpokenChunk,
        Timings,
        TurnRecord,
    )

    turn = TurnRecord(
        session_id=session_id,
        turn_index=1,
        timestamp="2026-09-03T12:00:00+00:00",
        buyer_utterance="What does a studio at Skyrise cost?",
        generated_sentences=["A studio is AED 985,000."],
        spoken_chunks=[SpokenChunk(text="A studio is AED 985,000.", completed=True)],
        guardrail_decisions=[],
        actions=[],
        timings_ms=Timings(total=4200.0),
        inventory_version="10-records",
        model="qwen/qwen3.7-flash",
        prompt_mode="ambassador",
        guardrail_mode="enforce",
    )
    base = dict(
        session_id=session_id,
        started_at="2026-09-03T11:59:00+00:00",
        ended_at="2026-09-03T12:00:10+00:00",
        call_end_reason="buyer_farewell",
        ended_cleanly=True,
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="10-records",
        ambassador_name="Jane",
        turns=[turn],
        brief=LeadBrief(intent="invest", language="en"),
        contact=ContactCapture(
            status="captured",
            asked_turn_index=1,
            source_turn_index=1,
            phone="+971500000000",
            contact_permission=True,
            confirmed=True,
        ),
    )
    base.update(overrides)
    return LeadSnapshot(**base)


async def test_a_call_becomes_a_lead(database: str) -> None:
    from adapter.persist import LeadWriter

    session_id = "sess_" + uuid.uuid4().hex[:10]
    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        lead_id = await writer.persist(_snapshot(session_id))
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["session_id"] == session_id
    assert lead["call_end_reason"] == "buyer_farewell"
    assert lead["ended_cleanly"] is True
    assert lead["analysis_status"] == "pending"
    assert lead["status"] == "unreviewed"


async def test_a_truncated_call_does_not_read_as_a_complete_one(
    database: str,
) -> None:
    """`ended_cleanly=false` and the fixed reason are what keep a disconnect
    out of the same bucket as a finished conversation (docs/10-)."""
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        lead_id = await writer.persist(
            _snapshot(
                "sess_" + uuid.uuid4().hex[:10],
                call_end_reason="buyer_left",
                ended_cleanly=False,
            )
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["ended_cleanly"] is False
    assert lead["call_end_reason"] == "buyer_left"


async def test_per_turn_audit_incomplete_survives_to_the_row(database: str) -> None:
    """A stranded final turn has to stay visible per turn, not only per call."""
    from adapter.persist import LeadWriter

    snapshot = _snapshot("sess_" + uuid.uuid4().hex[:10])
    snapshot.turns[0].audit_incomplete = True

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        lead_id = await writer.persist(snapshot)
        turns = await writer.repository.get_turns(lead_id)
    finally:
        await writer.close()

    assert turns[0]["audit_incomplete"] is True


async def test_persisting_twice_leaves_one_lead(database: str) -> None:
    """The session id is the idempotency key. A retried finalisation after a
    half-finished shutdown must not double the lead (ADR-020)."""
    from adapter.persist import LeadWriter

    session_id = "sess_" + uuid.uuid4().hex[:10]
    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        first = await writer.persist(_snapshot(session_id))
        second = await writer.persist(_snapshot(session_id))
        turns = await writer.repository.get_turns(first)
    finally:
        await writer.close()

    assert first == second
    assert len(turns) == 1


async def test_the_brief_reaches_postgres_encrypted(database: str) -> None:
    """Buyer-derived payloads are encrypted before they reach Postgres
    (docs/10-). The row must not contain the plaintext."""
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        lead_id = await writer.persist(_snapshot("sess_" + uuid.uuid4().hex[:10]))
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    envelope = lead["brief"]
    assert envelope["key_version"]
    assert b"invest" not in envelope["ciphertext"]
    assert envelope["algorithm"] == "aes-256-gcm"


async def test_the_envelope_is_bound_to_its_lead_and_field(database: str) -> None:
    """docs/02-: the envelope binds lead id plus field path as associated data,
    so a ciphertext moved to another row or another column fails to open
    instead of decrypting into the wrong place."""
    from adapter.crypto import EnvelopeError, Sealer

    sealer = Sealer(encryption_key=KEY, hash_key=HASH_KEY)
    lead_id = uuid.uuid4()
    sealed = sealer.seal(lead_id, "brief", b"secret")

    assert sealer.open(lead_id, "brief", sealed) == b"secret"
    with pytest.raises(EnvelopeError):
        sealer.open(lead_id, "summary", sealed)
    with pytest.raises(EnvelopeError):
        sealer.open(uuid.uuid4(), "brief", sealed)


async def test_a_phone_is_fingerprinted_not_indexed_in_the_clear(
    database: str,
) -> None:
    """Equality and duplicate detection without indexing the clear value. A
    fingerprint is a keyed HMAC and is not presented as encryption."""
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    try:
        lead_id = await writer.persist(_snapshot("sess_" + uuid.uuid4().hex[:10]))
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["contact_phone_fingerprint"]
    assert "+971500000000" not in lead["contact_phone_fingerprint"]
    assert lead["contact_phone"]["ciphertext"] != b"+971500000000"


async def test_the_same_number_fingerprints_the_same_way(database: str) -> None:
    """Which is the whole point: two calls from one buyer are findable."""
    from adapter.crypto import Sealer

    sealer = Sealer(encryption_key=KEY, hash_key=HASH_KEY)
    assert sealer.fingerprint("+971500000000") == sealer.fingerprint("+971500000000")
    assert sealer.fingerprint("+971500000000") != sealer.fingerprint("+971500000001")


async def test_a_database_failure_never_blocks_the_shutdown(database: str) -> None:
    """ADR-018 and ADR-020: persistence fails closed and visibly, and never
    stops the job ending. A farewell must not wait on Postgres."""
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    await writer.close()  # the pool is gone; every query will now fail

    log = _log()
    # Must NOT raise.
    lead_id = await writer.persist_or_report(_snapshot("sess_boom"), log=log[0])
    assert lead_id is None
    events = _events(log)
    assert any(e["event"] == "lead_persist_failed" for e in events)


async def test_the_failure_event_carries_no_buyer_words_and_no_exception_text(
    database: str,
) -> None:
    """docs/10-: an enum stage and error code, never an exception string or
    buyer text."""
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    await writer.close()

    log = _log()
    await writer.persist_or_report(_snapshot("sess_boom"), log=log[0])
    written = log[1].getvalue()

    assert "studio at Skyrise" not in written
    assert "InterfaceError" not in written
    failure = [e for e in _events(log) if e["event"] == "lead_persist_failed"][0]
    assert failure["stage"] in ("connect", "write")
    assert failure["code"]


async def test_a_missing_key_refuses_rather_than_storing_plaintext() -> None:
    """The one failure that must not be graceful. A writer with no key has to
    refuse at construction, not fall back to writing readable buyer text."""
    from adapter.crypto import Sealer

    with pytest.raises(ValueError, match="PII_ENCRYPTION_KEY"):
        Sealer(encryption_key="", hash_key=HASH_KEY)
    with pytest.raises(ValueError, match="PII_HASH_KEY"):
        Sealer(encryption_key=KEY, hash_key="")
    with pytest.raises(ValueError):
        Sealer(encryption_key="tooshort", hash_key=HASH_KEY)


def _log():
    from adapter.events import EventLog

    buf = StringIO()
    return EventLog("sess_test", stream=buf, verbose=False), buf


def _events(log) -> list[dict]:
    import json

    return [
        json.loads(line)
        for line in log[1].getvalue().splitlines()
        if line.strip()
    ]
