"""What the writer sealed, the reader opens - across the module boundary.

The defect this exists for reached a real call. `LeadWriter.persist` sealed
each turn under `turns.{i}`; the admin detail route opened
`turns.{i}.payload`. The field path is the AAD, so every payload on the
human's lead came back `EnvelopeError` and all nine turns of a real
conversation were unreadable in the admin surface.

Both suites were green the whole time, and that is the part worth fixing.
`test_admin_api_pii.py` seeds turns through the READER's path, so it agreed
with the reader; `test_persist_call.py` never reads through the route, so it
agreed with the writer. Each side tested itself. Nothing crossed the seam,
so nothing could see that the two sides disagreed.

So this module only ever writes with the real writer and reads with the real
route. It is deliberately the slow shape - a real Postgres, the real ASGI app
- because a fake on either side would reintroduce exactly the agreement that
hid the bug.

`brief`, `summary` and `contact.*` are here as GUARDS rather than as the
subject: they pass today, and the point is that a shared path helper must not
break them while fixing turns.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

# 32 bytes each, as hex. Test constants, never a real key.
KEY = "0a" * 32
HASH_KEY = "1b" * 32
TOKEN = "a-shared-admin-token-for-round-trip-tests"

TURN_COUNT = 3
BUYER_WORDS = "my budget is about two million dirhams"
BUYER_NAME = "A Buyer"
BUYER_PHONE = "+971500000077"
BUYER_EMAIL = "buyer@example.test"
SUMMARY_TEXT = "Investor, two million, wants a Skyrise studio."


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_rt_{uuid.uuid4().hex[:10]}"
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
        LeadBrief,
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
            buyer_utterance=f"{BUYER_WORDS} ({index})",
            generated_sentences=["A studio is AED 985,000."],
            spoken_chunks=[
                SpokenChunk(text="A studio is AED 985,000.", completed=True)
            ],
            guardrail_decisions=[],
            actions=[],
            timings_ms=Timings(total=4200.0),
            inventory_version="4-records",
            model="qwen/qwen3.7-flash",
            prompt_mode="ambassador",
            guardrail_mode="enforce",
        )
        for index in range(TURN_COUNT)
    ]
    return LeadSnapshot(
        session_id=session_id,
        started_at="2026-09-04T11:59:00+00:00",
        ended_at="2026-09-04T12:00:10+00:00",
        call_end_reason="buyer_farewell",
        ended_cleanly=True,
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="4-records",
        ambassador_name="Jane",
        turns=turns,
        brief=LeadBrief(intent="invest", language="en"),
        contact=ContactCapture(
            status="captured",
            asked_turn_index=1,
            source_turn_index=1,
            name=BUYER_NAME,
            phone=BUYER_PHONE,
            email=BUYER_EMAIL,
            contact_permission=True,
            confirmed=True,
        ),
    )


@pytest.fixture
async def persisted(database):
    """A call written by the REAL writer, then read by the REAL route.

    The summary goes through `writer.seal`, the way the analysis finaliser
    writes it, so the summary path is crossed here too.
    """
    from adapter.persist import LeadWriter

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=HASH_KEY)
    lead_id = await writer.persist(_snapshot("sess_" + uuid.uuid4().hex[:10]))
    await writer.repository.put_analysis(
        lead_id,
        status="complete",
        summary=writer.seal(lead_id, "summary", SUMMARY_TEXT.encode("utf-8")),
        score_total=55,
        score_version="2026-09-03.1",
        breakdown=None,
    )
    try:
        yield writer, lead_id
    finally:
        await writer.close()


@pytest.fixture
async def client(monkeypatch, persisted):
    """The real ASGI app over the real repository and the real sealer."""
    from httpx import ASGITransport, AsyncClient

    from adapter import admin_api
    from adapter.events import EventLog

    writer, lead_id = persisted
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setenv("LEAD_ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("LEAD_HASH_KEY", HASH_KEY)
    admin_api.app.state.repository = writer.repository
    admin_api.app.state.sealer = writer._sealer
    admin_api.app.state.log = EventLog(session_id="round-trip")
    transport = ASGITransport(app=admin_api.app)
    async with AsyncClient(transport=transport, base_url="http://admin") as http:
        yield http, writer, lead_id


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


async def read_detail(client) -> dict[str, Any]:
    http, _, lead_id = client
    response = await http.get(f"/v1/leads/{lead_id}", headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


async def decide(client, *, note: str | None, new_status: str = "qualified"):
    http, _, lead_id = client
    current = await read_detail(client)
    return await http.post(
        f"/v1/leads/{lead_id}/decisions",
        headers=auth(),
        json={
            "new_status": new_status,
            "reason_code": "ready",
            "note": note,
            "expected_lead_revision": current["revision"],
        },
    )


@pytest.fixture
async def detail(client) -> dict[str, Any]:
    return await read_detail(client)


# -- the defect ---------------------------------------------------------


@pytest.mark.parametrize("index", range(TURN_COUNT))
def test_a_turn_sealed_by_the_writer_opens_through_the_detail_route(detail, index):
    """One case per turn, so the count says how much of a real call was lost.

    On the human's lead this was nine.
    """
    turn = next(t for t in detail["turns"] if t["turn_index"] == index)
    assert turn["payload_error"] is None, turn["payload_error"]
    assert turn["payload"] is not None
    assert f"({index})" in str(turn["payload"])


# -- the guards, which pass today and must keep passing -----------------


def test_the_brief_sealed_by_the_writer_opens_through_the_detail_route(detail):
    assert detail["brief_error"] is None
    assert detail["brief"] is not None


def test_the_summary_sealed_by_the_finaliser_opens_through_the_detail_route(detail):
    assert detail["summary_error"] is None
    assert SUMMARY_TEXT in str(detail["summary"])


@pytest.mark.parametrize(
    "field,expected",
    [("name", BUYER_NAME), ("phone", BUYER_PHONE), ("email", BUYER_EMAIL)],
)
def test_a_contact_field_sealed_by_the_writer_opens_through_the_detail_route(
    detail, field, expected
):
    contact = detail["contact"]
    assert contact.get(f"{field}_error") is None
    assert contact[field] == expected


# -- the generalisation -------------------------------------------------


def test_no_module_outside_field_paths_spells_a_field_path():
    """The paths are one vocabulary shared by two services, so they belong in
    one place.

    Two f-strings in two files that have to agree, with nothing that fails
    when they stop agreeing, is what put nine unreadable turns on a real
    lead. This is the same shape as the events registry guard: the AAD
    vocabulary has one definition, and a literal anywhere else is a second
    one.

    It matches the VOCABULARY rather than the call shape. An earlier version
    flagged any `.open(...)` with a string argument and caught `Path.open`
    and `wave.open`; the version after that matched bare `brief` and
    `summary` and flagged thirty dict keys. A guard with false positives is
    one people learn to skim - the lesson from the column-diff regex that
    excluded digits - so this one is scoped to the compound paths, where it
    is exact, and it fires on a stray path built far from any call site.
    """
    import ast
    import re
    from pathlib import Path

    # Only the STRUCTURED paths. `brief` and `summary` are one bare word
    # each and are also ordinary dict keys, event names and column names -
    # matching them flagged thirty innocent lines. They route through
    # `field_paths` for consistency, but this guard cannot enforce them
    # without becoming the kind of checker people learn to skim, and a
    # single word shared by both sides is not where drift happens anyway.
    # The compound paths are, and those are unambiguous.
    vocabulary = re.compile(r"^(contact|turns|decisions)\.")
    source_dir = Path(__file__).resolve().parents[1] / "src" / "adapter"
    offenders = []
    for path in sorted(source_dir.glob("*.py")):
        if path.name == "field_paths.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if node in docstrings:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(
                    part.value
                    for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            else:
                continue
            if vocabulary.match(text):
                offenders.append(f"{path.name}:{node.lineno} {text!r}")
    assert offenders == [], (
        "field paths must come from adapter.field_paths, which owns the AAD "
        f"vocabulary: {offenders}"
    )


# -- a decision note is sealed under its own sequence --------------------

NOTE_TEXT = "Spoke to the buyer, wants a viewing on Saturday."


async def test_a_decision_carrying_a_note_is_accepted(client):
    """The route passed `{"note": ...}` - a plain dict - where an envelope was
    expected, so `_envelope_tuple` raised `KeyError: 'algorithm'`.

    It did not surface as a 500. `KeyError` IS a `LookupError`, and the route
    catches `LookupError` to mean "no such lead" - so an admin who added a
    note was told **404, the lead does not exist**, while the lead sat there
    and the decision was never recorded. A wrong answer that reads as a
    legitimate one, which is why nothing looked broken."""
    response = await decide(client, note=NOTE_TEXT)
    assert response.status_code < 300, response.text


async def test_the_note_reads_back_opened_through_the_detail_route(client):
    assert (await decide(client, note=NOTE_TEXT)).status_code < 300
    (decision,) = (await read_detail(client))["decisions"]
    assert decision["note_error"] is None
    assert NOTE_TEXT in str(decision["note"])


async def test_each_note_opens_under_the_sequence_it_was_actually_given(client):
    """The AAD carries the sequence, and the sequence is allocated INSIDE the
    transaction. So the note cannot be sealed before the call - sealing it
    against a guessed sequence would open for the first decision and fail for
    every one after it, which is the shape a single-decision test misses."""
    assert (await decide(client, note="first note")).status_code < 300
    assert (
        await decide(client, note="second note", new_status="rejected")
    ).status_code < 300

    decisions = sorted(
        (await read_detail(client))["decisions"], key=lambda d: d["sequence"]
    )
    assert [d["sequence"] for d in decisions] == [1, 2]
    assert [d["note_error"] for d in decisions] == [None, None]
    assert "first note" in str(decisions[0]["note"])
    assert "second note" in str(decisions[1]["note"])


async def test_the_note_is_not_stored_in_the_clear(client):
    """The whole reason it is sealed. Read the column, not the response."""
    assert (await decide(client, note=NOTE_TEXT)).status_code < 300
    _, writer, lead_id = client
    stored = await writer.repository._pool.fetchval(
        "SELECT note::text FROM admin_decisions WHERE lead_id = $1", lead_id
    )
    assert NOTE_TEXT not in (stored or "")


async def test_a_decision_without_a_note_still_records(client):
    """The guard: notes are optional and the common case must not regress."""
    assert (await decide(client, note=None)).status_code < 300
    (decision,) = (await read_detail(client))["decisions"]
    assert decision["note"] is None
    assert decision["note_error"] is None


async def test_an_internal_failure_is_never_reported_as_a_missing_lead(client):
    """What made the note bug invisible, fixed separately from it.

    `except LookupError` around a body that can raise `KeyError` turns any
    internal mapping error into "no such lead". The repository now raises
    `NoSuchLead`, so the route says 404 only when the lead really is absent.
    """
    import uuid as _uuid

    http, _, _ = client
    absent = await http.post(
        f"/v1/leads/{_uuid.uuid4()}/decisions",
        headers=auth(),
        json={
            "new_status": "qualified",
            "reason_code": "ready",
            "note": None,
            "expected_lead_revision": 0,
        },
    )
    assert absent.status_code == 404, absent.text
    assert "no such lead" in absent.text
