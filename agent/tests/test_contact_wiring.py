"""Is the one contact ask reachable from the production turn path? (P2-S05)

`ambassador/contact.py` shipped in #118 core-complete and referenced nowhere in
`adapter/`, so every persisted lead reads `contact_status=not_asked`: the
policy was correct and unreachable, the same defect shape #126 found for the
lead path. So one case here is a WIRING case that reads the production entry's
own source - the defect is an argument nobody passed, and no behavioural test
of the turn path could fail for it.

The rest are the three boundaries the card holds, and each of them is a
restraint rather than a feature:

**One ask, declinable.** The first goodbye is intercepted for the ask; a second
goodbye is honoured immediately (docs/10- 'Contact capture'). A policy that can
ask twice will ask twice, and a buyer who already said no is the last person to
ask again.

**No copy in a language nobody reviewed.** `data/contact.yaml` carries an
English ask and deliberately empty Arabic and Hindi ones (AGENTS.md:52). The
reachable behaviour for those calls is `not_asked` with a readable event, NOT
an English sentence in an Arabic call - this is the one moment the ambassador
asks the buyer to hand something over.

**No contact value on the emitted stream.** docs/10-:392 names this RED
exactly. The digits belong in the sealed record the ambassador view reads, and
nowhere else.

Imports are inside each test so a RED run reads N failed = N cases rather than
one collection error.
"""

from __future__ import annotations

import ast
import os
import uuid
from io import StringIO
from pathlib import Path

import pytest

AGENT_PY = Path(__file__).resolve().parents[1] / "src" / "adapter" / "agent.py"
CONTACT_PY = Path(__file__).resolve().parents[1] / "src" / "ambassador" / "contact.py"
EVENTS_PY = Path(__file__).resolve().parents[1] / "src" / "adapter" / "events.py"

# From data/contact.yaml, which is the only place the ask exists. Matched
# loosely so reviewed rewording does not fail the test - the claim is that the
# authored line was spoken, not that it says a particular sentence.
ASK_MARKER = "may I take your name"
NUMBER = "0501234567"
NAME = "Sara"

needs_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)


def test_the_production_entry_passes_a_contact_policy_to_the_agent() -> None:
    """The exact omission this card exists to fix.

    `AmbassadorAgent` takes the policy as an optional keyword, so an entrypoint
    that forgets it type-checks, passes review, and asks nobody for anything -
    which is the state main is in today.
    """
    tree = ast.parse(AGENT_PY.read_text(encoding="utf-8"))
    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AmbassadorAgent"
    ]
    assert constructions, "AmbassadorAgent is never constructed in agent.py"
    for call in constructions:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "contact" in keywords, (
            "the production AmbassadorAgent construction omits contact=, which "
            "is how a correct policy ships unreachable"
        )


def test_every_event_the_contact_policy_can_emit_is_classified() -> None:
    """Wiring the policy in puts its events on the emitted stream.

    `docs/03-` requires every emitted event to appear in exactly one of
    `_REDACTED_FIELDS` or `CLEAR_EVENTS`, and `test_events.py` enforces it by
    scanning `src/adapter/`. This policy is core, so its `emit` calls are
    outside that scan and its events would reach the stream unclassified - the
    same hole one directory over.
    """
    from adapter.events import CLEAR_EVENTS, _REDACTED_FIELDS

    tree = ast.parse(CONTACT_PY.read_text(encoding="utf-8"))
    funnel = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_emit"
    )
    emitted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else None
        if name == "_emit" and node.args:
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"event names must be literals: contact.py:{first.lineno}"
            )
            emitted.add(first.value)
        elif name == "emit":
            # One funnel, so this discovery is exhaustive: the only `emit` call
            # in the module is the forwarding one inside `_emit`, which is the
            # single place a variable event name is legitimate.
            assert funnel.lineno <= node.lineno <= (funnel.end_lineno or node.lineno), (
                f"an emit call outside _emit at contact.py:{node.lineno} would "
                "bypass this check"
            )

    assert emitted, "the emit discovery found nothing, so it proves nothing"
    unclassified = sorted(
        event
        for event in emitted
        if event not in CLEAR_EVENTS and event not in _REDACTED_FIELDS
    )
    assert not unclassified, f"unclassified contact events: {unclassified}"


def _agent(language: str = "en", replies: int = 4):
    """An agent with the contact policy wired, and its event buffer."""
    from test_agent import HealthyStream, SpyLLM, make_settings

    from adapter.agent import AmbassadorAgent, build_contact_policy
    from adapter.events import EventLog

    settings = make_settings(language=language)
    buffer = StringIO()
    log = EventLog(f"sess_contact_{uuid.uuid4().hex[:6]}", stream=buffer, verbose=False)
    agent = AmbassadorAgent(
        settings=settings,
        log=log,
        contact=build_contact_policy(settings, log),
    )
    agent._llm = SpyLLM([HealthyStream(["A studio is AED 985,000. "])] * replies)
    return agent, log, buffer


async def _say(agent, text: str) -> str:
    """One buyer turn through the real `llm_node`, returning what was spoken.

    `_tracker = None` first, which is how the existing multi-turn tests start a
    new turn: nothing seals here, and without it every utterance would land on
    turn 0 - where the contact decision is deliberately cached and replayed.
    """
    from test_agent import run_llm_node, user_ctx

    agent._tracker = None
    chunks = await run_llm_node(agent, user_ctx(text))
    return "".join(str(chunk) for chunk in chunks)


async def test_a_first_goodbye_asks_once_and_a_second_goodbye_closes() -> None:
    """docs/10-:392's named case, and the boundary god set.

    The first goodbye gets the ask INSTEAD of the closing line, and the close
    is NOT armed - a call that closed on the ask would ask and hang up. The
    reply settles it, and the authored farewell takes that same turn, so a
    buyer who has just declined is not left waiting to be dismissed.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    agent, log, buffer = _agent()

    first = await _say(agent, "Thanks, goodbye.")
    assert ASK_MARKER in first, first
    # Armed, not fired, is `_closing_turn`. Nothing is armed here: the call
    # has to stay open for the answer.
    assert agent._closing_turn is None, "the ask must not close the call"

    second = await _say(agent, "No thanks, goodbye.")
    assert ASK_MARKER not in second, "one ask means one ask"
    assert agent._contact.state.status == "declined"
    assert agent._closing_turn is not None, (
        "a second goodbye is honoured immediately, on the turn that settles it"
    )
    assert second.strip().endswith(agent._farewell_line.strip()), second

    await log.aclose()
    stream = buffer.getvalue()
    assert '"event": "contact_asked"' in stream
    assert '"status": "declined"' in stream
    assert '"event": "farewell_spoken"' in stream
    assert stream.count('"event": "contact_asked"') == 1


async def test_a_phone_is_read_back_and_captured_only_after_the_buyer_agrees() -> None:
    """One misheard digit is worse than no number, so the echo is not optional.

    The read-back is rendered from the reviewed digit forms, never by a model,
    and the number is not captured until the buyer agrees to what they heard.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    agent, log, buffer = _agent()

    await _say(agent, "That's all, bye.")
    read_back = await _say(agent, f"It's {NAME}, my number is {NUMBER}.")
    assert NUMBER in read_back.replace(" ", ""), read_back
    assert agent._contact.state.status == "unconfirmed", (
        "a number is not captured until the buyer has agreed to the read-back"
    )
    assert agent._closing_turn is None, "the read-back does not close the call"

    closing = await _say(agent, "Yes, that's right.")
    state = agent._contact.state
    assert state.status == "captured"
    assert state.phone is not None and NUMBER in state.phone.replace(" ", "")
    assert state.name == NAME
    assert state.confirmed and state.contact_permission
    assert agent._closing_turn is not None, (
        "once contact is settled the authored farewell takes the turn"
    )
    assert agent._farewell_line.strip() in closing

    await log.aclose()
    assert '"event": "contact_read_back"' in buffer.getvalue()
    assert '"status": "captured"' in buffer.getvalue()


def test_no_arabic_or_hindi_contact_copy_is_authored_by_the_build_team() -> None:
    """AGENTS.md:52, and it holds on the CORE-ONLY install.

    Imports nothing from `adapter` on purpose: the leanest install is where
    this should be provable, and it is the one place a translation slipped in
    by a well-meaning agent would show up with no framework in the way. The
    ask is native-review data; an empty line is the correct state until a
    reviewer authors one, and the policy stays silent rather than saying an
    English sentence in a call that is not in English.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    copy = load_contact_copy()
    assert copy.ask("en").strip(), "the English ask is authored"
    assert copy.enabled("en") is True

    for language in ("ar", "hi"):
        assert not copy.ask(language).strip(), (
            f"{language} contact copy must stay native-review data, not a "
            "translation by the build team"
        )
        policy = ContactPolicy(copy, language)
        assert policy.owes_request() is False
        assert policy.on_farewell(1) is None, "no ask, so the farewell happens"
        assert policy.state.status == "not_asked"


async def test_session_start_says_whether_this_call_can_ask_for_contact() -> None:
    """Silence has to be READABLE.

    An operator seeing `contact_status=not_asked` on every Arabic lead needs to
    know the ask was off rather than broken, and `session_start` is the line
    they already read for what this call is. Asserted through the adapter
    rather than by running an Arabic call, because the voice path refuses to
    construct one at all without a native-authored disclosure
    (`UncertifiedLanguageError`) - the same gate one door up.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from adapter.agent import _session_start_fields, build_contact_policy
    from adapter.events import EventLog
    from test_agent import make_settings

    assert _session_start_fields(make_settings(language="en"))["contact_ask"] is True
    for language in ("ar", "hi"):
        assert (
            _session_start_fields(make_settings(language=language))["contact_ask"]
            is False
        )
        # And the wired policy agrees with the field, so the two cannot drift.
        policy = build_contact_policy(
            make_settings(language=language), EventLog("sess_off", verbose=False)
        )
        assert policy.owes_request() is False


async def test_no_contact_value_reaches_the_emitted_stream() -> None:
    """The other half of docs/10-:392, and the one that would be a data leak.

    The number and the name are spoken aloud and sealed into the lead. Neither
    may appear on the event stream, which is the surface an operator tails and
    a log service keeps.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    agent, log, buffer = _agent()
    await _say(agent, "Right, goodbye.")
    await _say(agent, f"It's {NAME}, call me on {NUMBER}.")
    await _say(agent, "Yes.")
    assert agent._contact.state.status == "captured", "the leak needs a capture"
    await log.aclose()

    stream = buffer.getvalue()
    assert '"event": "contact_settled"' in stream, "the stream still says what happened"
    for secret in (NUMBER, "050 123 4567", NAME):
        assert secret not in stream, f"{secret!r} reached the emitted stream"


@pytest.fixture
async def database() -> str:
    """An EMPTY database per test, migrated by the real runner."""
    import asyncpg

    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_contact_{uuid.uuid4().hex[:10]}"
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


async def _persisted(agent, log, database: str) -> dict:
    """Run the real shutdown path and return the one lead row."""
    import asyncpg

    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter
    from test_lead_path_wiring import KEY, _ask, _llm

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    try:
        await shutdown_session(
            agent=agent,
            log=log,
            llm=_llm(),
            stt_node=None,
            lead_writer=writer,
            ask=_ask(),
        )
    finally:
        await writer.close()
    await log.aclose()

    connection = await asyncpg.connect(database)
    try:
        rows = await connection.fetch("SELECT * FROM leads")
    finally:
        await connection.close()
    assert len(rows) == 1, f"expected one lead, got {len(rows)}"
    return dict(rows[0])


@needs_database
async def test_a_captured_contact_is_sealed_into_the_lead_and_opens_to_the_spoken_values(
    database: str,
) -> None:
    """The card's own RED: the spoken values, sealed, read back from Postgres.

    Sealed and not merely stored: `contact_phone` is an envelope bound to this
    lead id and field path, so opening it with the wrong path fails. A test
    that only checked the status would pass with an empty envelope in the row.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")
    pytest.importorskip("cryptography")

    from adapter.crypto import Sealer
    from test_lead_path_wiring import KEY

    agent, log, _buffer = _agent()
    await _say(agent, "Thanks, goodbye.")
    await _say(agent, f"It's {NAME}, my number is {NUMBER}.")
    await _say(agent, "Yes, that's right.")

    lead = await _persisted(agent, log, database)
    assert lead["contact_status"] == "captured"
    assert lead["contact_permission"] is True
    assert lead["contact_confirmed"] is True
    assert lead["contact_phone_fingerprint"], "a fingerprint the admin can match on"
    assert lead["contact_asked_turn_index"] is not None
    assert lead["contact_source_turn_index"] is not None

    sealer = Sealer(encryption_key=KEY, hash_key=KEY)
    lead_id = lead["id"]
    phone = sealer.open(lead_id, "contact.phone", dict(lead["contact_phone"]))
    name = sealer.open(lead_id, "contact.name", dict(lead["contact_name"]))
    assert NUMBER in phone.decode("utf-8").replace(" ", "")
    assert name.decode("utf-8") == NAME
    assert lead["contact_email"] is None


@needs_database
async def test_a_declined_contact_persists_as_declined_with_nothing_sealed(
    database: str,
) -> None:
    """A decline is an answer, and it must be storable as one.

    Nothing sealed is the assertion that matters: a declined lead with an
    envelope in it would look like a contact somebody could open.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")
    pytest.importorskip("cryptography")

    agent, log, _buffer = _agent()
    await _say(agent, "Thanks, goodbye.")
    await _say(agent, "No, I would rather not.")

    lead = await _persisted(agent, log, database)
    assert lead["contact_status"] == "declined"
    assert lead["contact_name"] is None
    assert lead["contact_phone"] is None
    assert lead["contact_email"] is None
    assert lead["contact_phone_fingerprint"] is None
    assert lead["contact_permission"] is False
    assert lead["contact_confirmed"] is False
