"""Is the lead path reachable from the production entry?

The audit that produced this card found `persist` and `analysis` merged, tested
and unreachable: `shutdown_session` was called without `lead_writer=`, no
`LeadWriter` was ever constructed, and `finalise_analysis` had no caller outside
its own test file. Two green cards, no lead on any call.

So one of these cases is a WIRING case rather than a behaviour case, and it
reads the production entry's own source. That is deliberate: the defect was not
a wrong behaviour, it was an argument nobody passed, and no behavioural test of
`shutdown_session` could have failed for it. The repository already uses an AST
test to enforce a discipline types cannot (`test_events.py`).

Imports inside each test, so RED reads N failed = N cases.
"""

from __future__ import annotations

import ast
import os
import uuid
from io import StringIO
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")
pytest.importorskip("cryptography")

AGENT_PY = Path(__file__).resolve().parents[1] / "src" / "adapter" / "agent.py"
KEY = "wJ8Qx3nB2vK7pL9mR4tY6uI1oP5aS0dF8gH2jK4lZ6c"


def test_the_production_entry_passes_a_lead_writer_to_shutdown() -> None:
    """The exact omission this card exists to fix.

    `shutdown_session` takes `lead_writer` as an optional keyword, so a caller
    that forgets it type-checks, passes review and stores nothing. This asserts
    the production call site supplies it.
    """
    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "shutdown_session"
    ]
    assert calls, "shutdown_session is never called from agent.py"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "lead_writer" in keywords, (
            "a shutdown_session call omits lead_writer, which is how two merged "
            "cards shipped unreachable"
        )


async def test_no_database_url_says_so_once_and_stays_quiet() -> None:
    """Absence has to be READABLE: a log must distinguish "not configured"
    from "not wired", which is the state the audit found and could only
    diagnose by reading source."""
    from adapter.events import EventLog
    from adapter.persist import build_lead_writer

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    settings = _settings(database_url="")

    writer = await build_lead_writer(settings, log)
    await log.aclose()

    assert writer is None
    events = [line for line in buf.getvalue().splitlines() if line.strip()]
    names = [__import__("json").loads(line)["event"] for line in events]
    assert names.count("lead_store_disabled") == 1
    assert not [
        name
        for name in names
        if name != "lead_store_disabled" and ("lead" in name or "analysis" in name)
    ], names


async def test_a_dsn_without_its_keys_refuses_and_names_the_variable() -> None:
    """Configured halfway is the dangerous state: a DSN with no key would
    either write plaintext or fail per call. It fails at startup instead, and
    the message names the VARIABLE and never a value."""
    from adapter.events import EventLog
    from adapter.persist import build_lead_writer

    log = EventLog("sess_test", stream=StringIO(), verbose=False)
    for missing, other in (
        ("pii_encryption_key", "pii_hash_key"),
        ("pii_hash_key", "pii_encryption_key"),
    ):
        settings = _settings(
            database_url="postgresql://u:p@localhost:5435/x",
            **{missing: "", other: KEY},
        )
        with pytest.raises(ValueError) as raised:
            await build_lead_writer(settings, log)
        message = str(raised.value)
        assert missing.upper() in message, message
        assert KEY not in message


async def test_a_finished_call_persists_and_scores_without_being_asked(
    database: str,
) -> None:
    """The behaviour half. `shutdown_session` must run BOTH halves of docs/10-
    finalisation - the audit found it running neither."""
    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    agent, log, buf, _ = _agent()
    try:
        await shutdown_session(
            agent=agent,
            log=log,
            llm=_llm(),
            stt_node=None,
            lead_writer=writer,
            ask=_ask(),
        )
        leads = await _all_leads(database)
    finally:
        await writer.close()

    assert len(leads) == 1
    assert leads[0]["analysis_status"] == "complete"
    assert leads[0]["score_total"] is not None


async def test_an_analysis_that_hangs_becomes_a_failure_not_a_hung_shutdown(
    database: str,
) -> None:
    """The worker gathers shutdown callbacks with NO per-callback timeout and
    force-closes the process at `shutdown_process_timeout` (10.0s in
    livekit-agents 1.7.0), so an unbounded model call here does not slow the
    shutdown - it loses the whole shutdown, including the audit seal."""
    import asyncio

    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter

    async def hangs(prompt: str, *, repair: bool = False) -> str:
        await asyncio.sleep(30)
        return "{}"

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    agent, log, buf, _ = _agent()
    try:
        await asyncio.wait_for(
            shutdown_session(
                agent=agent,
                log=log,
                llm=_llm(),
                stt_node=None,
                lead_writer=writer,
                ask=hangs,
            ),
            timeout=9.0,
        )
        leads = await _all_leads(database)
    finally:
        await writer.close()

    assert leads[0]["analysis_status"] == "failed"
    await log.aclose()
    assert "analysis_failed" in buf.getvalue()
    assert '"code": "timeout"' in buf.getvalue()


async def test_a_lead_store_that_cannot_connect_does_not_fail_the_call() -> None:
    """A connect failure degrades to a shutdown-time report. The call is over
    by then; the buyer heard everything they were going to hear."""
    from adapter.events import EventLog
    from adapter.persist import build_lead_writer

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    settings = _settings(
        database_url="postgresql://u:p@127.0.0.1:1/nothing",
        pii_encryption_key=KEY,
        pii_hash_key=KEY,
    )

    writer = await build_lead_writer(settings, log)
    await log.aclose()

    assert writer is None
    assert "lead_persist_failed" in buf.getvalue()
    assert '"stage": "connect"' in buf.getvalue()
    assert '"code": "unavailable"' in buf.getvalue()


# --- fixtures and doubles -------------------------------------------------


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    dsn_root = os.environ.get("DATABASE_URL_TEST")
    if not dsn_root:
        pytest.skip("DATABASE_URL_TEST is not set; see tests/test_migrations.py")
    name = f"amb_wire_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(dsn_root)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    dsn = dsn_root.rsplit("/", 1)[0] + f"/{name}"
    try:
        await apply_migrations(dsn)
        yield dsn
    finally:
        admin = await asyncpg.connect(dsn_root)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


def _settings(**overrides):
    from test_agent import make_settings

    base = dict(database_url="", pii_encryption_key="", pii_hash_key="")
    base.update(overrides)
    return make_settings(**base)


def _agent():
    from test_agent import make_agent, HealthyStream

    return make_agent([HealthyStream(["A studio is AED 985,000. "])])


def _llm():
    class _Built:
        async def aclose(self) -> None:
            return None

    return _Built()


def _ask():
    from ambassador.schemas import LeadAnalysisDraft, SignalEvidence

    draft = LeadAnalysisDraft(
        summary="The buyer asked about a studio.",
        budget_stated=SignalEvidence(observed=False, turn_indexes=[]),
        project_named=SignalEvidence(observed=False, turn_indexes=[]),
        timeline_stated=SignalEvidence(observed=False, turn_indexes=[]),
        viewing_or_human_requested=SignalEvidence(observed=False, turn_indexes=[]),
        question_turn_indexes=[],
    )

    async def ask(prompt: str, *, repair: bool = False) -> str:
        return draft.model_dump_json()

    return ask


async def _all_leads(dsn: str) -> list[dict]:
    connection = await asyncpg.connect(dsn)
    try:
        return [dict(row) for row in await connection.fetch("SELECT * FROM leads")]
    finally:
        await connection.close()


async def test_a_hang_up_persists_too_even_though_no_room_was_deleted(
    database: str,
) -> None:
    """The path ryan actually observed, and a GUARD rather than a RED case: it
    passes on this implementation because persist lives in `shutdown_session`
    and never consults `end_call`.

    It is worth pinning anyway. `room_deleted` is emitted only inside
    `end_call`, so a buyer who closes the tab (CLIENT_INITIATED, reason
    `buyer_left`) never produces that event - and a persist that had been
    hung off the farewell close would have stored nothing for exactly the
    calls docs/10- says must still become leads.
    """
    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    agent, log, buf, _ = _agent()
    agent.note_buyer_left()  # the hang-up, without end_call ever running
    try:
        await shutdown_session(
            agent=agent,
            log=log,
            llm=_llm(),
            stt_node=None,
            lead_writer=writer,
            ask=_ask(),
        )
        leads = await _all_leads(database)
    finally:
        await writer.close()

    await log.aclose()
    assert len(leads) == 1
    assert leads[0]["call_end_reason"] == "buyer_left"
    assert "room_deleted" not in buf.getvalue()


async def test_the_analysis_model_defaults_to_the_brief_model() -> None:
    """One shared upstream quota took brief extraction down on 2026-09-03, so
    the two are separable - but they are the same job, so the default is the
    same model rather than a second thing to configure."""
    from test_agent import make_settings

    assert make_settings(analysis_model="x").analysis_model == "x"


async def test_a_snapshot_that_will_not_validate_still_seals_the_session(
    database: str, monkeypatch
) -> None:
    """The snapshot build was OUTSIDE the never-raises boundary.

    `agent.lead_snapshot()` ran before `persist_or_report` and outside anything
    that catches, so a snapshot that fails validation raised straight out of the
    shutdown callback - losing the provider closes, `session_end` and the audit
    seal. That is precisely the loss the 4s analysis bound exists to prevent,
    arriving through the door next to it.

    The trigger ryan reproduced is real - a call ending
    `buyer_farewell_repeated`, which #98 emits and `CallEndReason` does not
    list - but this case monkeypatches instead, because the enum fix is jim's
    card and a guard that depended on the bug would pass for the wrong reason
    the moment he lands it.
    """
    from pydantic import ValidationError

    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    agent, log, buf, _ = _agent()

    def explode(**_kwargs):
        raise ValidationError.from_exception_data("LeadSnapshot", [])

    monkeypatch.setattr(agent, "lead_snapshot", explode)
    try:
        await shutdown_session(
            agent=agent, log=log, llm=_llm(), stt_node=None,
            lead_writer=writer, ask=_ask(),
        )
        leads = await _all_leads(database)
    finally:
        await writer.close()
    await log.aclose()

    written = buf.getvalue()
    assert "session_end" in written, "the seal must survive an unusable snapshot"
    assert '"stage": "snapshot"' in written
    assert '"event": "lead_persist_failed"' in written
    assert leads == [], "nothing half-written"
