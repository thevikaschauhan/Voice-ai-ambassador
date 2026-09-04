"""Steps 5-6 of docs/10- Lead finalisation.

The model returns a `LeadAnalysisDraft` and never a score. Every evidence index
is checked against the turns that were SAVED, the score is computed in
`ambassador/leads.py`, and a failed analysis leaves `analysis_status=failed`
rather than a missing lead - the whole reason the snapshot is persisted first.

Against a real Postgres, and with the model injected as a callable: what is
worth testing here is what happens to the LEAD, and an httpx mock would only
prove I can mock httpx.

Imports inside each test so RED reads N failed = N cases.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from io import StringIO

import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("cryptography")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

KEY = "wJ8Qx3nB2vK7pL9mR4tY6uI1oP5aS0dF8gH2jK4lZ6c"


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_fin_{uuid.uuid4().hex[:10]}"
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


def _snapshot(session_id: str):
    from ambassador.schemas import (
        ContactCapture,
        LeadSnapshot,
        SpokenChunk,
        Timings,
        TurnRecord,
    )

    turns = [
        TurnRecord(
            session_id=session_id,
            turn_index=index,
            timestamp="2026-09-04T12:00:0%d+00:00" % index,
            buyer_utterance="My budget is about two million dirhams.",
            generated_sentences=["A studio is AED 985,000."],
            spoken_chunks=[
                SpokenChunk(text="A studio is AED 985,000.", completed=True)
            ],
            guardrail_decisions=[],
            actions=[],
            timings_ms=Timings(total=4000.0),
            inventory_version="10-records",
            model="qwen/qwen3.7-flash",
            prompt_mode="ambassador",
            guardrail_mode="enforce",
        )
        for index in (1, 2)
    ]
    return LeadSnapshot(
        session_id=session_id,
        started_at="2026-09-04T11:58:00+00:00",
        ended_at="2026-09-04T12:00:30+00:00",
        call_end_reason="buyer_farewell",
        ended_cleanly=True,
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="10-records",
        ambassador_name="Jane",
        turns=turns,
        contact=ContactCapture(status="not_asked"),
    )


def _draft(**overrides):
    from ambassador.schemas import LeadAnalysisDraft, SignalEvidence

    base = dict(
        summary="The buyer asked about a studio and stated a two million dirham budget.",
        budget_stated=SignalEvidence(observed=True, turn_indexes=[1]),
        project_named=SignalEvidence(observed=False, turn_indexes=[]),
        project_ids=[],
        timeline_stated=SignalEvidence(observed=False, turn_indexes=[]),
        viewing_or_human_requested=SignalEvidence(observed=False, turn_indexes=[]),
        question_turn_indexes=[1, 2],
    )
    base.update(overrides)
    return LeadAnalysisDraft(**base)


def _log():
    from adapter.events import EventLog

    buf = StringIO()
    return EventLog("sess_test", stream=buf, verbose=False), buf


async def _persisted(database: str, session_id: str):
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    lead_id = await writer.persist(_snapshot(session_id))
    return writer, lead_id


async def test_a_completed_analysis_scores_the_lead(database: str) -> None:
    from adapter.analysis import finalise_analysis

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(_draft()),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["analysis_status"] == "complete"
    assert lead["score_total"] > 0
    assert lead["score_version"]
    assert lead["summary"]["ciphertext"]


async def test_the_score_is_computed_here_and_never_taken_from_the_model(
    database: str,
) -> None:
    """The model returns a draft. If a total ever arrives from it, it is
    ignored - the rubric is the only thing allowed to produce points."""
    from adapter.analysis import finalise_analysis
    from ambassador.leads import ScoringInputs, load_rubric, score_interest

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    snapshot = _snapshot("ignored")
    draft = _draft()
    log = _log()
    try:
        await finalise_analysis(
            snapshot=snapshot,
            lead_id=lead_id,
            writer=writer,
            # A model trying to hand us a score, in the only way it could.
            ask=_answers(draft, extra={"total": 100, "score": 100}),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    expected = score_interest(
        ScoringInputs(
            draft=draft,
            contact=snapshot.contact,
            started_at=datetime.fromisoformat(snapshot.started_at),
            ended_at=datetime.fromisoformat(snapshot.ended_at),
            buyer_turn_indexes=snapshot.buyer_turn_indexes,
            project_ids_in_inventory=[],
        ),
        load_rubric(),
    )
    assert lead["score_total"] == expected.total
    assert lead["score_total"] != 100


async def test_evidence_pointing_outside_the_saved_turns_fails_the_analysis(
    database: str,
) -> None:
    """A signal quietly scoring on half its evidence is worse than one that
    does not score, so an unknown index fails the analysis - and the LEAD
    survives with analysis_status=failed."""
    from adapter.analysis import finalise_analysis
    from ambassador.schemas import SignalEvidence

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(budget_stated=SignalEvidence(observed=True, turn_indexes=[9]))
            ),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["analysis_status"] == "failed"
    assert lead["score_total"] is None
    assert lead["session_id"], "the lead is still there"


async def test_one_invalid_response_is_retried_once(database: str) -> None:
    """docs/10-: retry one invalid response. Not two - a model that cannot
    produce the shape twice is not going to on the third attempt, and this
    runs while a Railway deploy waits to drain."""
    from adapter.analysis import finalise_analysis

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    asked: list[str] = []
    log = _log()

    async def ask(prompt: str, *, repair: bool = False) -> str:
        asked.append("repair" if repair else "first")
        if not repair:
            return "not json at all"
        return _draft().model_dump_json()

    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=ask,
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert asked == ["first", "repair"]
    assert lead["analysis_status"] == "complete"


async def test_two_invalid_responses_fail_and_keep_the_lead(database: str) -> None:
    from adapter.analysis import finalise_analysis

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    attempts: list[str] = []
    log = _log()

    async def ask(prompt: str, *, repair: bool = False) -> str:
        attempts.append("repair" if repair else "first")
        return "{}"

    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=ask,
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert attempts == ["first", "repair"]
    assert lead["analysis_status"] == "failed"


async def test_the_failure_event_names_a_stage_and_no_buyer_words(
    database: str,
) -> None:
    from adapter.analysis import finalise_analysis

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()

    async def ask(prompt: str, *, repair: bool = False) -> str:
        raise RuntimeError("upstream said no: two million dirhams")

    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=ask,
            log=log[0],
        )
    finally:
        await writer.close()
    await log[0].aclose()

    written = log[1].getvalue()
    assert "two million dirhams" not in written
    assert "upstream said no" not in written
    failure = [line for line in written.splitlines() if "analysis_failed" in line]
    assert failure, written


async def test_the_project_ids_reach_a_column_the_list_can_read(
    database: str,
) -> None:
    """toby's finding: docs/10:315 specifies a Projects column on the lead
    list, and the only surviving copy of the ids was inside the encrypted
    brief - so the list could only render it by decrypting buyer-derived data
    on every page view. Inventory ids are OUR ids, so they belong in a column."""
    from adapter.analysis import finalise_analysis
    from ambassador.inventory import load_inventory
    from ambassador.schemas import SignalEvidence

    known = load_inventory()[0].id
    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(
                    project_named=SignalEvidence(observed=True, turn_indexes=[1]),
                    project_ids=[known],
                )
            ),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["project_ids"] == [known]


async def test_two_named_projects_both_reach_the_column(database: str) -> None:
    """The list has to show both, not the first."""
    from adapter.analysis import finalise_analysis
    from ambassador.inventory import load_inventory
    from ambassador.schemas import SignalEvidence

    projects = [project.id for project in load_inventory()[:2]]
    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(
                    project_named=SignalEvidence(observed=True, turn_indexes=[1, 2]),
                    project_ids=projects,
                )
            ),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["project_ids"] == projects
    assert len(projects) == 2


async def test_a_project_id_that_is_not_in_inventory_fails_the_analysis(
    database: str,
) -> None:
    """Same discipline as the turn-index check: a model-supplied id that does
    not resolve is a validation failure, not a row value. The list can then
    assume every id in that column is a real project."""
    from adapter.analysis import finalise_analysis
    from ambassador.schemas import SignalEvidence

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(
                    project_named=SignalEvidence(observed=True, turn_indexes=[1]),
                    project_ids=["not-a-real-project"],
                )
            ),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert lead["analysis_status"] == "failed"
    assert lead["project_ids"] == []


async def test_the_names_the_model_returns_reach_the_column_as_ids(
    database: str,
) -> None:
    """The human's 08:32Z call, end to end.

    The model named the three projects the way the conversation did. Before
    this, `_score` rejected them and the lead was filed failed with no summary,
    no score and an empty Projects column - a whole call lost to a prompt that
    never said what an id was.
    """
    from adapter.analysis import finalise_analysis
    from ambassador.schemas import SignalEvidence

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        completed = await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(
                    project_named=SignalEvidence(observed=True, turn_indexes=[1]),
                    project_ids=[
                        "Binghatti Skyrise",
                        "Binghatti Aquarise",
                        "Binghatti Circle",
                    ],
                )
            ),
            log=log[0],
        )
        lead = await writer.repository.get_lead(lead_id)
    finally:
        await writer.close()

    assert completed is True
    assert lead["analysis_status"] == "complete"
    assert lead["project_ids"] == [
        "binghatti-skyrise",
        "binghatti-aquarise",
        "binghatti-circle",
    ]


async def test_an_unresolvable_project_is_not_filed_as_evidence(
    database: str,
) -> None:
    """Two different failures with two different operator actions.

    `evidence` means the model cited a turn that never happened: look at the
    transcript. An unresolvable project means the model named something we do
    not sell, or the prompt did not tell it what we do: look at the inventory
    and the instruction. One code for both sent ryan to the wrong place.
    """
    from adapter.analysis import finalise_analysis
    from ambassador.schemas import SignalEvidence

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(
                    project_named=SignalEvidence(observed=True, turn_indexes=[1]),
                    project_ids=["Binghatti Moonrise"],
                )
            ),
            log=log[0],
        )
    finally:
        await writer.close()
    await log[0].aclose()

    written = log[1].getvalue()
    assert '"code": "unknown_project"' in written, written
    assert '"code": "evidence"' not in written
    assert "Moonrise" not in written


async def test_an_invented_turn_index_is_still_evidence(
    database: str,
) -> None:
    """A GUARD, passing before and after: the new code must take failures FROM
    the old one, not replace it."""
    from adapter.analysis import finalise_analysis
    from ambassador.schemas import SignalEvidence

    writer, lead_id = await _persisted(database, "sess_" + uuid.uuid4().hex[:8])
    log = _log()
    try:
        await finalise_analysis(
            snapshot=_snapshot("ignored"),
            lead_id=lead_id,
            writer=writer,
            ask=_answers(
                _draft(budget_stated=SignalEvidence(observed=True, turn_indexes=[99]))
            ),
            log=log[0],
        )
    finally:
        await writer.close()
    await log[0].aclose()

    assert '"code": "evidence"' in log[1].getvalue()


def _answers(draft, extra: dict | None = None):
    """A model that returns this draft, optionally with fields it is not
    allowed to send."""
    import json

    payload = json.loads(draft.model_dump_json())
    if extra:
        payload.update(extra)

    async def ask(prompt: str, *, repair: bool = False) -> str:
        return json.dumps(payload)

    return ask
