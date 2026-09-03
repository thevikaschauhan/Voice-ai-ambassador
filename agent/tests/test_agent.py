"""The adapter's lifecycle, exercised without a live room.

`entrypoint` needs LiveKit transport, a worker process and real credentials, so
none of it is testable directly. Everything it wires up is, and these are the
pieces where a defect is invisible until a demo:

  never silence      when the LLM's retries are exhausted, `LLMStream`
                     re-raises through `__anext__`. Nothing reaches TTS after
                     that point unless `llm_node` catches it, and AGENTS.md is
                     absolute about a turn never ending in silence.
  bounded retries    two retry layers stacked (SDK and framework) turn one
                     congested upstream into minutes of dead air.
  nothing leaked     the plugin does not own a client it was handed, so the
                     httpx client under it is closed by the shutdown path or
                     not at all.
  honest audit       barge-in has to mark the interrupted chunk incomplete, or
                     the Ships table's "what the buyer actually heard" claim is
                     false - and the framework only settles that question when
                     the SpeechHandle resolves, well after the agent has gone
                     back to "listening". See the barge-in section below.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, get_args

import httpx
import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit import rtc  # noqa: E402
from livekit.agents import Agent, APIConnectOptions  # noqa: E402
from livekit.agents import llm as lk_llm  # noqa: E402
from livekit.agents.metrics import EOUMetrics, TTSMetrics  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN  # noqa: E402
from livekit.agents.utils import ConnectionPool  # noqa: E402
from livekit.agents.voice import SpeechHandle  # noqa: E402

from adapter import agent as adapter_agent  # noqa: E402
from adapter.agent import AmbassadorAgent, shutdown_session  # noqa: E402
from adapter.config import (  # noqa: E402
    PROVISIONAL_VOICE_ID_EN,
    PROVISIONAL_VOICE_ID_HI,
    Settings,
)
from adapter.confirmations import ConfirmationCopy  # noqa: E402
from adapter.disclosure import UncertifiedLanguageError  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from ambassador.schemas import Language  # noqa: E402
from adapter.levels import gain_for  # noqa: E402
from adapter.interception import (  # noqa: E402
    BRIDGE_COPY,
    FALLBACK_COPY,
    SentenceGuard,
)
from adapter.llm_openrouter import build_llm, clamp_retry_after  # noqa: E402

FAKE_KEY = "test-key-not-a-real-credential"
GROUNDED = "985,000"
ALLOWED_YEAR = "2026"


def make_settings(**overrides: Any) -> Settings:
    """A Settings with no real credentials in it. `load_settings()` would read
    agent/.env, which holds live keys."""
    base: dict[str, Any] = dict(
        livekit_url="",
        livekit_api_key="",
        livekit_api_secret="",
        openrouter_api_key=FAKE_KEY,
        llm_model="qwen/qwen3.7-flash",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_thinking="off",
        brief_model="qwen/qwen3.7-flash",
        stt_provider="openrouter",
        stt_model_default="qwen/qwen3-asr-1.7b",
        stt_model_ar="",
        stt_enabled=False,
        stt_enabled_explicit=True,
        deepgram_api_key="",
        deepgram_model="nova-3",
        fish_api_key=FAKE_KEY,
        fish_tts_model="s2.1-pro",
        tts_voice_id_en="",
        tts_voice_id_ar="",
        tts_voice_id_hi="",
        guardrail_mode="enforce",
        prompt_mode="ambassador",
        demo_mode=False,
        language="en",
        allow_uncertified_language=False,
        database_url="",
        analysis_model="qwen/qwen3.7-flash",
        pii_encryption_key="",
        pii_hash_key="",
        demo_max_call_seconds=0,
    )
    base.update(overrides)
    return Settings(**base)


def test_the_voice_session_start_matches_the_text_mode_contract():
    settings = make_settings(
        llm_model="test/model-slug",
        language="hi",
        prompt_mode="naive",
        guardrail_mode="warn",
    )

    assert adapter_agent._session_start_fields(settings) == {
        "config": settings.redacted(),
        "model": "test/model-slug",
        "language": "hi",
        "prompt_mode": "naive",
        "guardrail_mode": "warn",
        "inventory_version": adapter_agent.Harness.load().prompt_fingerprint("hi"),
        # The settings above are `hi`, and Hindi's ambassador is Maya. This
        # pinned "" while ar and hi were unnamed; the client has since named
        # all three. The unnamed case still matters - it must stay a plain
        # empty string rather than becoming "None" on the way to a surface -
        # and is covered by fixtures in tests/test_ambassadors.py now that no
        # shipped language exercises it.
        "ambassador_name": "Maya",
    }


def test_the_session_contract_carries_the_ambassador_name_where_there_is_one():
    """The other half, so the assertion above cannot pass by the field being
    absent from both sides."""
    settings = make_settings(language="en")
    assert adapter_agent._session_start_fields(settings)["ambassador_name"] == "Jane"


# --- fakes ----------------------------------------------------------------


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[Any] = field(default_factory=list)


@dataclass
class FakeChatChunk:
    delta: FakeDelta | None = None
    usage: Any = None


class Boom(RuntimeError):
    """Stands in for the APIConnectionError LLMStream raises once its retry
    budget is spent."""


class FailingStream:
    """Yields `before` chunks, then raises the way an exhausted LLMStream does."""

    def __init__(self, before: list[str]) -> None:
        self._before = before
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[FakeChatChunk]:
        for piece in self._before:
            yield FakeChatChunk(delta=FakeDelta(content=piece))
        raise Boom("failed to generate LLM completion after 2 attempts")

    async def aclose(self) -> None:
        self.closed = True


class HealthyStream:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[FakeChatChunk]:
        for piece in self._pieces:
            yield FakeChatChunk(delta=FakeDelta(content=piece))

    async def aclose(self) -> None:
        self.closed = True


class SpyLLM(lk_llm.LLM):
    """A real `livekit.agents.llm.LLM` so `llm_node`'s isinstance gate passes,
    recording the conn_options every call was given."""

    def __init__(self, streams: list[Any]) -> None:
        super().__init__()
        self._streams = list(streams)
        self.conn_options: list[APIConnectOptions] = []
        self.chat_ctxs: list[lk_llm.ChatContext] = []

    def chat(  # type: ignore[override]
        self,
        *,
        chat_ctx: lk_llm.ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: Any = NOT_GIVEN,
        tool_choice: Any = NOT_GIVEN,
        extra_kwargs: Any = NOT_GIVEN,
    ) -> Any:
        self.conn_options.append(conn_options)
        self.chat_ctxs.append(chat_ctx)
        return self._streams.pop(0)


def make_agent(
    streams: list[Any],
) -> tuple[AmbassadorAgent, EventLog, StringIO, SpyLLM]:
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(), log=log)
    spy = SpyLLM(streams)
    # The session normally supplies the LLM; there is no session here.
    agent._llm = spy
    return agent, log, buf, spy


async def run_llm_node(agent: AmbassadorAgent, ctx: lk_llm.ChatContext) -> list[Any]:
    out: list[Any] = []
    async for chunk in agent.llm_node(ctx, [], None):  # type: ignore[arg-type]
        out.append(chunk)
    return out


def user_ctx(text: str = "What does a studio cost?") -> lk_llm.ChatContext:
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="user", content=text)
    return ctx


def spoken(chunks: list[Any]) -> str:
    return " ".join(c for c in chunks if isinstance(c, str))


def json_lines(buf: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


# --- the tool's triggers match the prompt's constraints -------------------
#
# The prompt asks for the tool and the docstring tells the model when to reach
# for it; the model reads both. They drifted: the prompt named an unlisted
# computation as an escalation (AGENTS.md invariant 2) while the tool's trigger
# list did not mention computation at all, so the one constraint with no tool
# name in the prompt also had no matching trigger here.


def test_the_escalation_tool_lists_every_trigger_the_prompt_escalates_on():
    doc = AmbassadorAgent.escalate_to_human.__doc__ or ""
    triggers = doc.lower()
    for topic in (
        "not in the inventory",  # constraint 3
        "price on enquiry",  # constraint 4
        "computation",  # constraint 2
        "negotiate",  # constraint 6
        "complains or is distressed",  # constraint 7
        "asks for a person",  # constraint 7
    ):
        assert topic in triggers, f"no trigger covers {topic!r}"


def test_the_escalation_tool_says_a_spoken_offer_is_not_an_escalation():
    doc = AmbassadorAgent.escalate_to_human.__doc__ or ""
    assert "do not merely mention a colleague" in doc


# --- finding 1: a terminal LLM failure still speaks -----------------------


async def test_a_failure_before_any_content_speaks_the_fallback():
    agent, log, _, _ = make_agent([FailingStream([])])

    chunks = await run_llm_node(agent, user_ctx())

    assert FALLBACK_COPY["en"] in spoken(chunks)
    assert agent.tracker is not None
    assert [c.text for c in agent.tracker.spoken_chunks] == [FALLBACK_COPY["en"]]


async def test_a_failure_after_two_chunks_still_speaks_the_fallback():
    agent, log, _, _ = make_agent(
        [FailingStream(["A studio at Skyrise ", f"is AED {GROUNDED}. "])]
    )

    chunks = await run_llm_node(agent, user_ctx())

    text = spoken(chunks)
    # What the model managed to produce was approved and spoken, and the turn
    # then ends on composed speech rather than mid-sentence silence.
    assert "nine hundred and eighty-five thousand" in text.lower()
    assert FALLBACK_COPY["en"] in text


async def test_the_failure_is_recorded_as_an_llm_failure_event():
    agent, log, buf, _ = make_agent([FailingStream([])])

    await run_llm_node(agent, user_ctx())
    await log.aclose()

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    failures = [e for e in events if e["event"] == "llm_failure"]
    assert len(failures) == 1
    assert failures[0]["error"] == "Boom"
    assert failures[0]["spoken_before"] is False
    # The composed reply is logged as a fallback, not as a bridge.
    assert [e["event"] for e in events if e["event"] in ("bridge", "fallback")] == [
        "fallback"
    ]
    assert [e for e in events if e["event"] == "fallback"][0]["reason"] == "llm_failure"


async def test_no_exception_escapes_llm_node():
    agent, _, _, _ = make_agent([FailingStream(["Half a sen"])])
    # The assertion is that this does not raise.
    await run_llm_node(agent, user_ctx())


async def test_the_turn_still_seals_cleanly_after_a_failure():
    agent, log, _, _ = make_agent([FailingStream([])])
    ctx = user_ctx()

    await run_llm_node(agent, ctx)
    agent.finish_turn(ctx)

    assert len(log.turns) == 1
    record = log.turns[0]
    assert record.spoken_chunks[-1].text == FALLBACK_COPY["en"]
    assert record.spoken_chunks[-1].completed is True
    assert agent.tracker is None


async def test_the_stream_is_closed_even_when_it_raised():
    stream = FailingStream([])
    agent, _, _, _ = make_agent([stream])

    await run_llm_node(agent, user_ctx())

    assert stream.closed is True


# --- finding 2: exactly one bounded retry layer ---------------------------


async def test_chat_is_called_with_explicit_bounded_conn_options():
    agent, _, _, spy = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await run_llm_node(agent, user_ctx())

    assert len(spy.conn_options) == 1
    options = spy.conn_options[0]
    assert options is not DEFAULT_API_CONNECT_OPTIONS
    assert options.max_retry == 1
    assert options.retry_interval == 0.3


async def test_the_regeneration_call_is_bounded_too():
    """A blocked first sentence opens a second stream. That one gets the same
    budget, or the retry policy is only half applied."""
    agent, _, _, spy = make_agent(
        [
            HealthyStream(["Marina Heights is AED 800,000. "]),
            HealthyStream([f"A studio at Skyrise is AED {GROUNDED}. "]),
        ]
    )

    await run_llm_node(agent, user_ctx())

    assert len(spy.conn_options) == 2
    assert all(o.max_retry == 1 and o.retry_interval == 0.3 for o in spy.conn_options)


async def test_the_sdk_client_is_capped_at_one_retry():
    built = build_llm(make_settings(), lambda usage: None)
    try:
        # `_client` is where the plugin keeps the openai AsyncClient it was
        # handed; there is no public accessor for it.
        assert built.llm._client.max_retries == 1
    finally:
        await built.aclose()


def test_an_oversized_retry_after_is_clamped_before_the_sdk_reads_it():
    """openai sleeps for whatever Retry-After says, up to 120s. On a live call
    that is indistinguishable from a dropped line."""
    headers = httpx.Headers({"retry-after": "90"})
    assert clamp_retry_after(headers) == 90.0
    assert "retry-after" not in headers
    assert float(headers["retry-after-ms"]) / 1000 <= 1.0

    # A short one is left exactly as the provider sent it.
    short = httpx.Headers({"retry-after": "0.5"})
    assert clamp_retry_after(short) is None
    assert short["retry-after"] == "0.5"

    assert clamp_retry_after(httpx.Headers({})) is None


# --- finding 4: the httpx client under the plugin is closed ---------------


async def test_the_shutdown_path_closes_the_llm_http_client():
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(), log=log)
    built = build_llm(make_settings(), agent.note_usage, agent.note_upstream_status)

    assert built.http_client.is_closed is False

    await shutdown_session(agent=agent, log=log, llm=built, stt_node=None)

    assert built.http_client.is_closed is True
    assert "session_end" in buf.getvalue()


async def test_the_shutdown_path_closes_the_events_bridge(tmp_path):
    """The session owns the bridge, so the session's teardown has to close it.

    Written against the real `shutdown_session` signature rather than the
    bridge alone, because the defect this catches is a wiring one: `agent.py`
    has `from __future__ import annotations`, so an unimported `EventsBridge`
    stays a lazy string in the signature and only fails when `entrypoint`
    actually runs - which no test reaches. Ruff caught it once; a test should
    be able to.
    """
    from adapter.events_bridge import EventsBridge

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(), log=log)
    built = build_llm(make_settings(), agent.note_usage)

    handshake = tmp_path / "bridge.json"
    bridge = EventsBridge(log, handshake_path=handshake)
    await bridge.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
    writer.write(bridge.token.encode() + b"\n")
    await writer.drain()
    # Read one event before tearing down, so the client is provably in the
    # fan-out. Without this the test races the handshake and asserts nothing.
    log.emit("session_start", config={})
    assert json.loads(await asyncio.wait_for(reader.readline(), 2))["event"] == (
        "session_start"
    )

    await shutdown_session(
        agent=agent, log=log, llm=built, stt_node=None, bridge=bridge
    )

    # The credential on disk goes with it: a handshake file outliving its
    # session is a token for a socket nobody is listening on, and the next
    # reader to find it learns the shape of the thing it protects.
    assert not handshake.exists()

    # The surface still sees the session end before the socket goes away.
    assert (
        json.loads(await asyncio.wait_for(reader.readline(), 2))["event"]
        == "session_end"
    )
    writer.close()


async def test_the_shutdown_path_drains_the_event_log():
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(), log=log)
    built = build_llm(make_settings(), agent.note_usage)
    log.emit("session_start", config={})

    await shutdown_session(agent=agent, log=log, llm=built, stt_node=None)

    events = [
        json.loads(line)["event"]
        for line in buf.getvalue().splitlines()
        if line.strip()
    ]
    # What this test is about is that the queued writer drains before the
    # process can exit, and drains in order. It used to assert the exact list,
    # which coupled it to whichever events construction happened to emit and
    # broke the moment the agent emitted one more at start-up.
    assert events[-1] == "session_end", events
    assert "session_start" in events
    assert events.index("session_start") < events.index("session_end")


# --- finding 7: barge-in marks the chunk incomplete -----------------------
#
# The ordering in these tests is the real one, and it is the opposite of the
# obvious one. livekit-agents 1.7.0 defaults to resume_false_interruption=True
# with a 2.0s false_interruption_timeout (voice/turn.py
# `_INTERRUPTION_DEFAULTS`). Under those defaults a VAD barge-in takes the
# pause branch of `_interrupt_by_audio_activity` (voice/agent_activity.py):
# audio_output.pause(), then _update_agent_state("listening"), and NOTHING
# touches the speech handle. `interrupt()` lands later, from
# `_cancel_speech_pause(interrupt=True)`, and only when the interruption is
# confirmed; a false interruption resumes playout and the handle completes
# uninterrupted instead.
#
# So "listening" arrives while `handle.interrupted` is still False on every
# real barge-in. Each test below therefore fires finish_turn FIRST and resolves
# the handle afterwards, which is the sequence the framework actually produces.


async def settle() -> None:
    """Let the handle's done callback run: `Future.add_done_callback` schedules
    through `call_soon`, so the seal lands on the next loop iteration."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_a_barge_in_confirmed_after_listening_still_marks_the_chunk():
    """The refuted ordering: seal-time `handle.interrupted` is False here, and
    a turn sealed at the state change would record the chunk as complete."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)

    # The pause branch: "listening" while the handle is untouched.
    assert handle.interrupted is False
    agent.finish_turn(ctx)
    assert log.turns == []  # nothing sealed yet, and that is the fix

    # The interruption is confirmed and playout unwinds.
    handle.interrupt()
    handle._mark_done()
    await settle()

    assert len(log.turns) == 1
    record = log.turns[0]
    assert record.spoken_chunks
    assert record.spoken_chunks[-1].completed is False


async def test_a_false_interruption_that_resumes_audits_as_completed():
    """The mirror case, and the reason the audit may not simply assume the
    worst at "listening": the pause resolves into a resume and the handle
    completes uninterrupted, so the buyer did hear the whole chunk."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    assert log.turns == []

    # false_interruption_timeout elapses, audio_output.resume(), playout ends.
    handle._mark_done()
    await settle()

    assert len(log.turns) == 1
    assert log.turns[0].spoken_chunks
    assert all(c.completed for c in log.turns[0].spoken_chunks)


async def test_a_handle_that_never_resolves_is_audited_as_incomplete():
    """Session teardown mid-speech. The handle will never call back, so the
    turn is sealed on what is known and flagged rather than guessed at."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    assert log.turns == []

    built = build_llm(make_settings(), agent.note_usage)
    await shutdown_session(agent=agent, log=log, llm=built, stt_node=None)

    assert len(log.turns) == 1
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    complete = [e for e in events if e["event"] == "turn_complete"]
    assert len(complete) == 1
    assert complete[0]["audit_incomplete"] is True


async def test_a_turn_whose_handle_resolved_is_not_flagged_incomplete():
    """The negative control for the marker: a handle that resolved before
    teardown produces a complete audit, so the flag means what it says."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle._mark_done()
    await settle()

    built = build_llm(make_settings(), agent.note_usage)
    await shutdown_session(agent=agent, log=log, llm=built, stt_node=None)

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    complete = [e for e in events if e["event"] == "turn_complete"]
    assert len(complete) == 1
    assert complete[0]["audit_incomplete"] is False


async def test_the_agents_own_exit_hook_seals_a_pending_turn():
    """`AgentSession.aclose` drains the activity and awaits `on_exit`, so a
    session closed without the adapter's own shutdown path still books the
    turn. The spike drives the session directly and relies on this."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    assert log.turns == []

    await agent.on_exit()

    assert len(log.turns) == 1
    await log.aclose()


async def test_a_false_interruption_passing_through_listening_twice_seals_once():
    """Pause, "listening", resume, then "listening" again when playout really
    ends. One buyer utterance is still exactly one TurnRecord."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)  # the pause branch
    agent.finish_turn(ctx)  # the real end of the turn, after the resume
    assert log.turns == []

    handle._mark_done()
    await settle()

    assert len(log.turns) == 1
    assert all(c.completed for c in log.turns[0].spoken_chunks)


async def test_an_interruption_does_not_leak_into_the_next_turn():
    agent, log, _, _ = make_agent(
        [
            HealthyStream([f"A studio is AED {GROUNDED}. "]),
            HealthyStream([f"A studio is AED {GROUNDED}. "]),
        ]
    )
    ctx = user_ctx()

    await run_llm_node(agent, ctx)
    interrupted = SpeechHandle.create()
    agent.note_speech_handle(interrupted)
    agent.finish_turn(ctx)
    interrupted.interrupt()
    interrupted._mark_done()
    await settle()

    await run_llm_node(agent, ctx)
    clean = SpeechHandle.create()
    agent.note_speech_handle(clean)
    agent.finish_turn(ctx)
    clean._mark_done()
    await settle()

    assert len(log.turns) == 2
    assert log.turns[0].spoken_chunks[-1].completed is False
    assert all(c.completed for c in log.turns[1].spoken_chunks)


async def test_a_new_turn_does_not_strand_an_unresolved_previous_one():
    """A handle that never resolves must not park its turn forever and swallow
    the next one. The stranded turn is sealed, and flagged as incomplete.

    Each turn gets its OWN chat context, with content that tells them apart.
    Sharing one ctx object across both turns hides the defect this test exists
    for: the forced seal used to take the context off the call that forced it,
    which is the NEW turn's, and file the OLD turn's brief against it.
    """
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"A studio is AED {GROUNDED}. "]),
            HealthyStream([f"A studio is AED {GROUNDED}. "]),
        ]
    )
    scheduled: list[tuple[int, list[dict[str, str]]]] = []
    agent.brief_extractor.schedule = (  # type: ignore[method-assign]
        lambda transcript, turn: scheduled.append((turn, transcript))
    )

    first_ctx = user_ctx("What does a studio at Skyrise cost?")
    second_ctx = user_ctx("And what is the handover date?")

    await run_llm_node(agent, first_ctx)
    stranded = SpeechHandle.create()
    agent.note_speech_handle(stranded)
    agent.finish_turn(first_ctx)  # never resolves

    await run_llm_node(agent, second_ctx)
    second = SpeechHandle.create()
    agent.note_speech_handle(second)
    agent.finish_turn(second_ctx)  # forces the stranded turn closed
    second._mark_done()
    await settle()

    assert [t.turn_index for t in log.turns] == [1, 2]
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    flags = [e["audit_incomplete"] for e in events if e["event"] == "turn_complete"]
    assert flags == [True, False]

    # Each turn's brief was extracted from its own transcript.
    assert [turn for turn, _ in scheduled] == [1, 2]
    first_text = " ".join(m["content"] for m in scheduled[0][1])
    second_text = " ".join(m["content"] for m in scheduled[1][1])
    assert "studio at Skyrise" in first_text
    assert "handover date" not in first_text
    assert "handover date" in second_text
    assert first_text != second_text


# Alibaba caching through OpenRouter is explicit-only: the top-level parameter
# is silently ignored and only a breakpoint on the system content block
# engages it. Verified live 2026-08-29 - 1043 of 1069 prompt tokens served
# from cache from the second call on, prompt cost down 81%. The rewrite is on
# the wire because the plugin has no path to a content block (ADR-016), so
# these pin the shape rather than the effect.
def test_the_system_prompt_is_marked_cacheable():
    import json as _json

    from adapter.llm_openrouter import mark_system_prompt_cacheable

    body = _json.dumps(
        {
            "messages": [
                {"role": "system", "content": "INVENTORY"},
                {"role": "user", "content": "hi"},
            ]
        }
    ).encode()
    out = _json.loads(mark_system_prompt_cacheable(body))
    system = out["messages"][0]["content"]
    assert system == [
        {"type": "text", "text": "INVENTORY", "cache_control": {"type": "ephemeral"}}
    ]
    # Everything else is untouched.
    assert out["messages"][1] == {"role": "user", "content": "hi"}


def test_a_body_it_cannot_understand_is_passed_through_unchanged():
    from adapter.llm_openrouter import mark_system_prompt_cacheable

    # A missed cache costs latency and money; a corrupted request costs the
    # turn. Every unexpected shape must return the original bytes.
    for body in (
        b"not json at all",
        b"{}",
        b'{"messages": []}',
        b'{"messages": [{"role": "user", "content": "no system message"}]}',
        b'{"messages": [{"role": "system", "content": [{"type": "text", "text": "already blocks"}]}]}',
    ):
        assert mark_system_prompt_cacheable(body) is body


def test_the_rewrite_can_be_switched_off():
    from adapter.llm_openrouter import UsageTappingTransport

    transport = UsageTappingTransport(lambda _u: None, cache_system_prompt=False)
    assert transport._cache_system_prompt is False


# --- the opening disclosure ------------------------------------------------
#
# `AmbassadorAgent` resolves its opening in __init__ and speaks it in
# on_enter. Both halves matter: resolving early is what makes an uncertified
# language a start-up error rather than a discovery made while a buyer is
# already on the line, and `allow_interruptions=False` is the whole reason the
# disclosure is system speech instead of a line in the prompt.


@dataclass
class _RecordedSay:
    text: str
    allow_interruptions: Any


class _FakeHandle:
    """A speech handle whose playout the caller must await, like the real one.

    `interrupted` is settable, because the close path branches on it: a
    farewell the buyer talked over must not end the call.
    """

    def __init__(self, interrupted: bool = False) -> None:
        self.interrupted = interrupted
        self.waited = False

    async def wait_for_playout(self) -> None:
        self.waited = True


class _FakeSession:
    def __init__(self, interrupt_farewell: bool = False) -> None:
        self.said: list[_RecordedSay] = []
        self.handles: list[_FakeHandle] = []
        self.interrupts = 0
        self._interrupt_farewell = interrupt_farewell

    def say(self, text, *, allow_interruptions=NOT_GIVEN, **kwargs):
        self.said.append(_RecordedSay(text, allow_interruptions))
        handle = _FakeHandle(interrupted=self._interrupt_farewell)
        self.handles.append(handle)
        return handle

    def interrupt(self) -> None:
        self.interrupts += 1


def _attach(
    monkeypatch, agent: AmbassadorAgent, *, interrupt_farewell: bool = False
) -> _FakeSession:
    """A real AgentSession needs a room, a worker and live credentials."""
    session = _FakeSession(interrupt_farewell=interrupt_farewell)
    monkeypatch.setattr(
        AmbassadorAgent, "session", property(lambda self: session), raising=False
    )
    return session


async def test_the_disclosure_is_spoken_and_cannot_be_barged_over(monkeypatch):
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(language="en"), log=log)
    session = _attach(monkeypatch, agent)

    await agent.on_enter()

    assert len(session.said) == 1
    spoken = session.said[0]
    # The specific claim the copy has to make, not just "something was said".
    assert "transcribed" in spoken.text.lower()
    assert spoken.allow_interruptions is False


async def test_an_uncertified_language_refuses_to_build_the_agent_at_all():
    """The failure lands in __init__, before a room is ever connected."""
    log = EventLog("sess_test", stream=StringIO(), verbose=False)
    with pytest.raises(UncertifiedLanguageError, match="'ar'"):
        AmbassadorAgent(settings=make_settings(language="ar"), log=log)


async def test_the_override_opens_in_english_and_the_event_stream_says_so(monkeypatch):
    """The degradation has to be visible in the record, not just in the audio.

    An Arabic call that opened with an English disclosure and logged
    `language: ar` would be a false audit of a compliance step.
    """
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(
        settings=make_settings(language="ar", allow_uncertified_language=True),
        log=log,
    )
    session = _attach(monkeypatch, agent)

    await agent.on_enter()
    # The log queues writes to a single writer task, so the buffer is only
    # authoritative once it has been drained.
    await log.aclose()

    assert "transcribed" in session.said[0].text.lower()  # the English copy
    emitted = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    disclosures = [e for e in emitted if e["event"] == "disclosure"]
    assert len(disclosures) == 1, emitted
    event = disclosures[0]
    assert event["language"] == "en"
    assert event["requested_language"] == "ar"
    assert event["uncertified_fallback"] is True


# --- the lexicon reaches the synthesiser -----------------------------------
#
# The module had full unit coverage while `tts_node` ignored it, which is the
# original defect one layer up: a respelling that exists and never arrives.
# A mutation that replaced the wiring with a pass-through killed no test until
# this one existed.
#
# The mirror case - an unauthored language must be handed the original
# words, never an English respelling - is covered in test_lexicon.py rather
# than here, because constructing an `ar` agent will stop being possible
# once the disclosure gate lands and this test would become a landmine.


async def test_the_respelling_is_applied_to_the_text_handed_to_tts(monkeypatch):
    log = EventLog("sess_test", stream=StringIO(), verbose=False)
    agent = AmbassadorAgent(settings=make_settings(language="en"), log=log)

    handed_to_tts: list[str] = []

    async def capture(agent_, text, model_settings):
        async for chunk in text:
            handed_to_tts.append(chunk)
        return
        yield  # makes this an async generator; never reached

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(capture))

    async def source():
        yield "Binghatti Skyrise is ready."

    assert [frame async for frame in agent.tts_node(source(), None)] == []

    joined = "".join(handed_to_tts)
    assert "bin-GAH-tee" in joined, joined
    assert "Binghatti" not in joined, joined


# --- level matching reaches the audio, not just the table ------------------
#
# `levels.py` has full unit coverage and would keep it while `tts_node` ignored
# it, which is exactly the shape of the lexicon defect one section up: a
# correction that exists and never arrives. These are the tests that fail if
# the wiring is removed.


def _frame(amplitude: int, samples: int = 480):
    from array import array

    data = array("h", [amplitude, -amplitude] * (samples // 2)).tobytes()
    return rtc.AudioFrame(
        data=data, sample_rate=24000, num_channels=1, samples_per_channel=samples
    )


def _amplitude(frame) -> int:
    from array import array

    out = array("h")
    out.frombytes(bytes(frame.data))
    return max(abs(v) for v in out)


def _tts_node_frames(monkeypatch, settings, frames):
    """Run `tts_node` over fabricated synthesiser output and collect what leaves."""
    log = EventLog("sess_test", stream=StringIO(), verbose=False)
    agent = AmbassadorAgent(settings=settings, log=log)

    async def emit(agent_, text, model_settings):
        async for _ in text:
            pass
        for frame in frames:
            yield frame

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(emit))

    async def source():
        yield "Handover is in the third quarter."

    return agent, source


async def test_the_loud_voice_is_attenuated_on_the_way_out(monkeypatch):
    """The Hindi voice measured about 3.9x English and peaked within 7% of full
    scale. What a caller hears has to come out quieter, not just the number in
    the table."""
    settings = make_settings(
        language="hi",
        tts_voice_id_hi=PROVISIONAL_VOICE_ID_HI,
        allow_uncertified_language=True,
    )
    agent, source = _tts_node_frames(monkeypatch, settings, [_frame(30000)])
    out = [frame async for frame in agent.tts_node(source(), None)]

    assert len(out) == 1
    # gain_for(hi) is about 0.257, so a 30000 peak lands near 7700.
    assert _amplitude(out[0]) == pytest.approx(
        round(30000 * gain_for(PROVISIONAL_VOICE_ID_HI)), abs=2
    )
    assert _amplitude(out[0]) < 30000


async def test_the_quietest_voice_is_handed_through_untouched(monkeypatch):
    """English is the voice the others are matched down to, so it is at unity
    for the whole call. Not merely equal - the SAME frame object, because a
    per-frame copy on the hot path would be a cost paid for nothing."""
    settings = make_settings(language="en", tts_voice_id_en=PROVISIONAL_VOICE_ID_EN)
    original = _frame(20000)
    agent, source = _tts_node_frames(monkeypatch, settings, [original])
    out = [frame async for frame in agent.tts_node(source(), None)]

    assert out == [original]


async def test_an_unconfigured_voice_is_not_touched_either(monkeypatch):
    """The failure direction at the seam: a voice with no measurement sounds
    exactly as it did before this existed."""
    settings = make_settings(language="en", tts_voice_id_en="a-voice-nobody-measured")
    original = _frame(30000)
    agent, source = _tts_node_frames(monkeypatch, settings, [original])
    assert [frame async for frame in agent.tts_node(source(), None)] == [original]


async def test_the_first_audio_mark_is_taken_before_the_gain_is_applied(monkeypatch):
    """`tts_first_audio` measures when Fish's audio ARRIVED, and it is a row in
    the latency budget (`docs/04-`). Marking after the gain would fold our own
    work into that number, quietly inflating a measurement by the amount of the
    thing being measured against.

    Ordering against `apply_gain` itself, not merely against the first yield: a
    mark that moved below the gain but stayed above the yield would still be
    wrong and would pass a test that only watched what was emitted.
    """
    settings = make_settings(
        language="hi",
        tts_voice_id_hi=PROVISIONAL_VOICE_ID_HI,
        allow_uncertified_language=True,
    )
    agent, source = _tts_node_frames(monkeypatch, settings, [_frame(30000)])

    order: list[str] = []
    real_apply = adapter_agent.apply_gain

    def spy(pcm, gain):
        order.append("gain")
        return real_apply(pcm, gain)

    monkeypatch.setattr(adapter_agent, "apply_gain", spy)

    class Tracker:
        def mark_tts_first_audio(self) -> None:
            order.append("mark")

    agent._tracker = Tracker()  # noqa: SLF001
    emitted = [frame async for frame in agent.tts_node(source(), None)]

    assert order[:2] == ["mark", "gain"], order
    assert len(emitted) == 1
    # The spy is a pass-through, so the attenuation still happened.
    assert _amplitude(emitted[0]) < 30000


# --- ADR-011: the confirmation takes the turn away from the model ----------
#
# Prompt constraint 8 already asked the model to confirm a budget, and ADR-007
# is explicit that asking reduces violations without eliminating them. What
# makes this deterministic is that the model never runs on the turn where a
# confirmation is owed.


def _budget_agent(**overrides):
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(**overrides), log=log)
    return agent, log, buf


async def test_an_ambiguous_budget_is_confirmed_and_the_model_never_runs():
    """The twenty-times error, stopped before it can be made.

    "2 crore" is INR 2 crore (about AED 880k) or AED 2 crore (20 million).
    The SpyLLM would raise IndexError if it were called, so this also proves
    no LLM request was made.
    """
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])  # any call pops from an empty list

    reply = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))

    assert "2 crore" in reply
    assert "dirhams or in rupees" in reply


async def test_the_confirmation_is_not_repeated_once_the_budget_is_settled():
    """A policy that asks every turn is one the operator switches off."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "dirhams or in rupees" in first

    agent._tracker = None
    second = spoken(await run_llm_node(agent, user_ctx("Dirhams.")))
    # Settled, so the model gets this turn. Asserting on its words rather than
    # on GROUNDED: the reply is verbalised on the way out, so the digits are
    # already gone by here.
    assert "A studio is" in second
    assert "dirhams or in rupees" not in second


async def test_a_non_aed_budget_hands_over_rather_than_converting():
    """No confirmed exchange rate ships, and a converted figure derived from a
    guess is the same class of error as a fabricated price."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    agent._tracker = None
    reply = spoken(await run_llm_node(agent, user_ctx("Rupees.")))

    assert "ambassadors" in reply
    assert "convert" in reply.lower()


async def test_the_buyers_budget_never_reaches_the_emitted_stream():
    """The leak this test exists for was real.

    The confirmation was first routed through `record_fallback`, whose event
    carries its text in the clear - safe for fixed copy from a data file, and
    not safe for a line that interpolates the buyer's own budget. docs/03-
    validator 4 keeps buyer-derived content off stdout and the file sink
    entirely.
    """
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    await log.aclose()

    emitted = buf.getvalue()
    assert "2 crore" not in emitted, emitted
    assert "dirhams or in rupees" not in emitted, emitted
    # The fact of it is still auditable.
    assert "budget_confirmation" in emitted


async def test_the_audit_still_records_what_the_buyer_heard():
    """Keeping it off the stream must not blind the in-process record, which
    is what the ambassador view and the audit read."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))

    chunks = [c.text for c in agent.tracker.spoken_chunks]
    assert any("2 crore" in c for c in chunks), chunks


async def test_a_language_with_no_confirmation_copy_does_not_speak_english():
    """Better to skip the policy than to answer an Arabic buyer in English."""
    agent, _, _ = _budget_agent(language="ar", allow_uncertified_language=True)
    agent._llm = SpyLLM([HealthyStream(["مرحبا. "])])

    reply = spoken(await run_llm_node(agent, user_ctx("2 crore")))
    assert "dirhams or in rupees" not in reply


# --- the rework the blocking review demanded --------------------------------
#
# The first version shipped eight defects behind green tests because every
# test named a currency on turn two or repeated the figure verbatim, and
# asserted on Decision objects rather than on what the buyer hears. These
# drive whole exchanges through llm_node and assert on the spoken text.


async def test_a_reply_that_answers_nothing_is_reasked_not_handed_to_the_model():
    """The fail-open defect. The re-ask composes against the transcript the
    mention CAME FROM; checking it against the current turn made every re-ask
    fail its own echo guard and silently gave the model the turn."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])  # any model call would raise IndexError

    first = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "dirhams or in rupees" in first

    agent._tracker = None
    second = spoken(
        await run_llm_node(agent, user_ctx("I did not catch that, can you repeat?"))
    )
    assert "2 crore" in second
    assert "dirhams or in rupees" in second


async def test_no_to_a_read_back_asks_for_the_amount_out_loud():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    first = spoken(
        await run_llm_node(agent, user_ctx("My budget is 5 million dirhams."))
    )
    assert "have I got that right" in first

    agent._tracker = None
    second = spoken(await run_llm_node(agent, user_ctx("No, that's wrong.")))
    assert "what is the budget" in second


async def test_a_handover_actually_notifies_a_human():
    """`hands_over` was a log field with no consequence: the buyer heard "let
    me put you through" and nobody was put through - the exact anti-pattern
    escalate_to_human's own docstring names."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    agent._tracker = None
    reply = spoken(await run_llm_node(agent, user_ctx("Rupees.")))
    assert "ambassadors" in reply

    assert "escalate_to_human" in agent.tracker.actions
    await log.aclose()
    emitted = buf.getvalue()
    assert '"escalation"' in emitted
    assert "human_ambassador" in emitted


async def test_giving_up_notifies_a_human_too():
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    for reply in ("What?", "Sorry?"):
        agent._tracker = None
        await run_llm_node(agent, user_ctx(reply))
    agent._tracker = None
    final = spoken(await run_llm_node(agent, user_ctx("Lovely weather.")))
    assert "ambassadors" in final

    assert "escalate_to_human" in agent.tracker.actions
    await log.aclose()
    assert '"escalation"' in buf.getvalue()


async def test_denying_dirhams_is_not_recorded_as_dirhams():
    """ "Not dirhams" settled AED and went permanently silent. There are two
    currencies, so the denial names rupees - which without a confirmed rate
    is a handover, not a conversion."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    agent._tracker = None
    reply = spoken(await run_llm_node(agent, user_ctx("Not dirhams.")))

    assert "ambassadors" in reply
    assert agent._budget.currency == "INR"


async def test_no_llm_request_is_logged_on_a_confirmation_turn():
    """The audit meters model calls off the event stream; a request line with
    no llm_ttft or llm_usage behind it over-counts them."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    await log.aclose()
    assert "llm_request" not in buf.getvalue()


async def test_a_tool_split_turn_reads_the_policy_once():
    """One buyer utterance, two llm_node invocations (a tool call splits the
    turn). The second read must not burn a second attempt on the same reply."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "dirhams or in rupees" in first
    assert agent._budget._attempts == 0

    # Same tracker, same turn: the second half of a tool-using turn.
    second = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "dirhams or in rupees" not in second
    assert agent._budget._attempts == 0


async def test_a_broken_template_hands_over_rather_than_freeing_the_model():
    """The failure direction, pinned. A confirmation that cannot be composed
    must go to a human; returning the turn to the model is the fail-open
    defect the review caught. `{ammount}` is the reviewer's own example of a
    translator typo the old `except ValueError` did not even catch."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])
    agent._confirmations = ConfirmationCopy(
        by_language={
            "en": {
                "ask_currency": "{ammount} - dirhams or rupees?",
                "confirm_amount": "{amount} - right?",
                "ask_amount": "What is the budget?",
                "cannot_convert": "An ambassador will help.",
                "give_up": "Let me put you through to one of our ambassadors.",
            }
        }
    )

    reply = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))

    assert "ambassadors" in reply
    assert agent._budget.settled
    assert "escalate_to_human" in agent.tracker.actions
    await log.aclose()
    assert '"escalation"' in buf.getvalue()


async def test_settling_emits_its_own_event_name():
    """`budget_settled`, not `budget_confirmed`: the brief extractor puts a
    model-inferred `budget.confirmed` on the same stream, and the two must
    not share a name."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    agent._tracker = None
    await run_llm_node(agent, user_ctx("Dirhams."))
    await log.aclose()
    assert '"budget_settled"' in buf.getvalue()


async def test_guardrail_timing_is_unmeasured_not_zero_on_a_confirmation_turn():
    """events.py's own rule: a missing measurement and a zero-latency stage
    must not look the same on the meter. No guardrail runs on a turn the
    policy takes."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    record = agent.tracker.finish()
    assert record.timings_ms.guardrail is None


def test_the_prompt_keeps_the_model_confirming_where_the_policy_is_off():
    """Telling the model "the system owns confirmation" in a language whose
    copy is unauthored left NOBODY asking - a regression from the prompt-only
    days. The old wording survives exactly there."""
    on = AmbassadorAgent(
        settings=make_settings(),
        log=EventLog("sess_test", stream=StringIO(), verbose=False),
    )
    assert "The budget confirmation is handled by the system" in on.instructions

    off = AmbassadorAgent(
        settings=make_settings(language="ar", allow_uncertified_language=True),
        log=EventLog("sess_test", stream=StringIO(), verbose=False),
    )
    assert "confirm the amount AND the currency" in off.instructions
    assert "The budget confirmation is handled by the system" not in off.instructions


# --- the second independent review's seam findings, pinned ------------------


def test_every_format_failure_is_unspeakable_not_an_escape():
    """str.format raises more than ValueError and KeyError: "{amount.foo}"
    is an AttributeError and "{amount[x]}" a TypeError, and both escaped the
    first curated except into a silent turn."""
    from adapter.confirmations import UnspeakableConfirmation
    from adapter.confirmations import compose as compose_confirmation

    for template in ("{amount.foo} - dirhams or rupees?", "{amount[x]} - right?"):
        with pytest.raises(UnspeakableConfirmation):
            compose_confirmation(template, echoed="2 crore", said="2 crore budget")


async def test_a_template_attribute_error_still_hands_over_out_loud():
    """The full fail-closed path for an exception class the curated catch
    missed: the turn must speak the give-up line, notify a human, and never
    fall through to the model - including on a same-turn second invocation."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    agent._confirmations = ConfirmationCopy(
        by_language={
            "en": {
                "ask_currency": "{amount.foo} - dirhams or rupees?",
                "confirm_amount": "{amount} - right?",
                "ask_amount": "What is the budget?",
                "cannot_convert": "An ambassador will help.",
                "give_up": "Let me put you through to one of our ambassadors.",
            }
        }
    )

    reply = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "ambassadors" in reply
    assert agent._budget.settled
    assert "escalate_to_human" in agent.tracker.actions

    # The same-turn retry (the observe-once gate) must not reach the model
    # with the budget unconfirmed - the policy is settled and escalated, so
    # the model turn that follows is a normal post-handover turn.
    second = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "{" not in second

    await log.aclose()
    assert '"escalation"' in buf.getvalue()


def test_the_give_up_line_may_carry_no_slot_of_any_kind(tmp_path):
    """give_up is spoken verbatim on the failure path, so any format slot in
    it - not just a well-formed {amount} - is a loader error."""
    from adapter.confirmations import load_confirmations

    bad = tmp_path / "confirmations.yaml"
    bad.write_text(
        "en:\n"
        '  ask_currency: "{amount} - dirhams or rupees?"\n'
        '  confirm_amount: "{amount} - right?"\n'
        '  ask_amount: "What is the budget?"\n'
        '  cannot_convert: "An ambassador will help."\n'
        '  give_up: "Let me put you through {amount.foo}."\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="give_up"):
        load_confirmations(bad)


# --- issue #18: the Fish socket a barge-in discards ------------------------
#
# The mechanism itself is proven against the framework's own ConnectionPool in
# test_tts_pool.py. What is under test here is the wiring: that the adapter
# reaches the hook at the one moment the framework has settled that the
# interruption was real, and nowhere else. The failure this guards is silent -
# every turn still speaks, the ones after a barge-in are just slower - so
# nothing but an assertion on the pool's own state catches it.


class PooledTTS:
    """Shaped like `fishaudio.TTS` where the adapter touches it: a real
    `ConnectionPool`, built with the plugin's own arguments, over fake sockets."""

    def __init__(self) -> None:
        self.opened: list[object] = []
        self._pool: ConnectionPool[object] = ConnectionPool(
            connect_cb=self._connect,
            close_cb=self._close,
            max_session_duration=300,
            mark_refreshed_on_get=True,
        )

    async def _connect(self, timeout: float) -> object:
        sock = object()
        self.opened.append(sock)
        return sock

    async def _close(self, sock: object) -> None:
        pass


async def until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the pool")
        await asyncio.sleep(0.005)


async def attach_warm_tts(agent: AmbassadorAgent) -> PooledTTS:
    """A TTS in the state `AgentActivity.start` leaves it in: prewarmed once."""
    tts = PooledTTS()
    agent._tts = tts  # the session normally supplies this
    tts._pool.prewarm()
    await until(lambda: len(tts.opened) == 1)
    return tts


async def synthesise_and_barge_in(tts: PooledTTS) -> None:
    """`SynthesizeStream._run` holding the pooled socket, cancelled mid-audio."""
    holding = asyncio.Event()

    async def synthesise() -> None:
        async with tts._pool.connection(timeout=1.0):
            holding.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(synthesise())
    await holding.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_utterance_after_a_barge_in_speaks_off_a_pooled_socket():
    """The user-facing claim: interrupting the agent does not make its next
    reply wait on a TCP + TLS + WebSocket handshake to Fish."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    tts = await attach_warm_tts(agent)
    ctx = user_ctx()
    await run_llm_node(agent, ctx)
    await synthesise_and_barge_in(tts)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle.interrupt()
    handle._mark_done()
    await settle()

    # The replacement is opened during the silence, not on the next reply.
    await until(lambda: len(tts.opened) == 2)
    await tts._pool.get(timeout=1.0)
    assert tts._pool.last_connection_reused is True
    assert len(tts.opened) == 2, "the post-barge-in turn still paid a connect"

    await log.aclose()
    reprewarms = [ln for ln in json_lines(buf) if ln["event"] == "tts_pool_reprewarm"]
    assert [ln["outcome"] for ln in reprewarms] == ["requested"]
    assert reprewarms[0]["turn"] == 1


async def test_a_turn_that_played_out_does_not_touch_the_pool():
    """The pool did not lose anything, so nothing should be asked of it. A hook
    that fired on every turn would look identical on a live call and quietly
    open a socket per turn."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    tts = await attach_warm_tts(agent)
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle._mark_done()
    await settle()
    await asyncio.sleep(0.05)

    await log.aclose()
    assert len(tts.opened) == 1
    emitted = [ln["event"] for ln in json_lines(buf)]
    # The turn did seal, so the absence below is a fact about the stream rather
    # than a fact about an undrained buffer.
    assert "turn_complete" in emitted
    assert "tts_pool_reprewarm" not in emitted


async def test_teardown_mid_barge_in_does_not_open_a_socket_for_a_dead_call():
    """`finalise_pending_turn` seals an interrupted turn at shutdown. Opening a
    Fish connection there races `tts.aclose()` and pays for a call nobody is
    on."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    tts = await attach_warm_tts(agent)
    ctx = user_ctx()
    await run_llm_node(agent, ctx)
    await synthesise_and_barge_in(tts)

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle.interrupt()  # confirmed, but the handle never resolves
    agent.finalise_pending_turn()
    await asyncio.sleep(0.05)

    assert len(log.turns) == 1  # the turn is still sealed
    assert log.turns[0].spoken_chunks[-1].completed is False
    assert len(tts.opened) == 1

    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    assert "interrupted" in emitted  # the seal ran; the hook was the part skipped
    assert "tts_pool_reprewarm" not in emitted


async def test_first_audio_records_whether_the_socket_was_pooled(monkeypatch):
    """Issue #18 asks for the measurement before the fix, and the framework
    cannot supply it: `livekit-plugins-fishaudio` 1.7.0 never fills in
    `TTSMetrics.acquire_time`, so it is a constant 0.0 on this stack. This is
    the line a human reads off a live call instead."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    tts = await attach_warm_tts(agent)
    await run_llm_node(agent, user_ctx())

    async def one_frame(agent_, text, model_settings):
        async for _ in text:
            pass
        yield object()

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(one_frame))

    async def source():
        yield "A studio is nine hundred and eighty five thousand dirhams. "

    # A cold connect, the way the turn after a barge-in gets one.
    await synthesise_and_barge_in(tts)
    await tts._pool.get(timeout=1.0)
    assert [frame async for frame in agent.tts_node(source(), None)] != []
    await log.aclose()

    line = [ln for ln in json_lines(buf) if ln["event"] == "tts_connection"][-1]
    assert line["turn"] == 1
    assert line["reused"] is False
    assert isinstance(line["connect_ms"], float)


# --- issue #7: the endpointing measurement --------------------------------


async def test_the_frameworks_end_of_utterance_metrics_land_on_the_turn():
    """Endpointing happens before `on_user_turn_completed`, which is where the
    tracker's clock starts, so the adapter cannot time it and does not try."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    agent.note_metrics(
        EOUMetrics(
            timestamp=time.time(),
            end_of_utterance_delay=0.44,
            transcription_delay=0.29,
            on_user_turn_completed_delay=0.001,
        )
    )

    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle._mark_done()
    await settle()

    await log.aclose()
    assert log.turns[0].timings_ms.endpoint == 440.0
    assert log.turns[0].timings_ms.stt == 290.0
    emitted = [ln for ln in json_lines(buf) if ln["event"] == "endpointing"]
    assert emitted[0]["after_transcript_ms"] == 150.0


async def test_metrics_that_are_not_end_of_utterance_are_left_alone():
    """`metrics_collected` carries every stage. The others are already recorded
    from closer to the source, and taking them twice would double-count."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    ctx = user_ctx()
    await run_llm_node(agent, ctx)

    agent.note_metrics(
        TTSMetrics(
            timestamp=time.time(),
            request_id="req",
            ttfb=0.3,
            duration=1.0,
            audio_duration=1.0,
            cancelled=False,
            characters_count=10,
            streamed=True,
            label="fish",
        )
    )
    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    assert "llm_request" in emitted
    assert "endpointing" not in emitted
    assert agent.tracker is not None
    assert agent.tracker.endpoint is None


# --- eval F2: the composed fallback promises a human ----------------------
#
# `data/fallbacks.yaml` describes the fallback as "the line that hands the
# buyer to a human" and the copy says "let me put you through to one of our
# ambassadors". Recording the chunk and emitting the event is not putting
# anyone through, and this is the one path where the model definitively did NOT
# call `escalate_to_human` - the fallback only speaks because the model's own
# output was unusable. So nothing else notifies anybody, and the buyer waits for
# a call that was never booked.
#
# Same anti-pattern class as PR #20's defect 2 on the budget policy's
# hands_over path, and `_route_to_human`'s own docstring names it.
#
# The bridge is deliberately excluded: its copy ("let me be precise about that
# figure rather than guess") promises nothing and the turn carries on.

FABRICATED = "1,450,000"


def escalations(buf: StringIO) -> list[dict[str, Any]]:
    return [ln for ln in json_lines(buf) if ln["event"] == "escalation"]


async def test_a_fallback_after_the_retry_is_spent_notifies_a_human():
    """The eval row `unknown.en.fabricated-twice-reaches-the-fallback`, through
    the shipping path rather than the harness twin: the model fabricates, is
    told why, fabricates again, and the composed fallback becomes the reply."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Binghatti Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([f"The price at Sapphire Bay is AED {FABRICATED}. "]),
        ]
    )

    text = spoken(await run_llm_node(agent, user_ctx("What does Sapphire Bay cost?")))
    assert FALLBACK_COPY["en"] in text
    assert FABRICATED not in text

    assert agent.tracker is not None
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    assert [e["routed_to"] for e in escalations(buf)] == ["human_ambassador"]


async def test_an_llm_failure_fallback_notifies_a_human_too():
    """The other route into the same copy. Both ends of `record_fallback` speak
    the line that promises a human, so both have to book one."""
    agent, log, buf, _ = make_agent([FailingStream([])])

    assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, user_ctx()))
    assert agent.tracker is not None
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    assert len(escalations(buf)) == 1


async def test_a_bridge_notifies_nobody():
    """Audio has already played, the bridge replaces one sentence, and the turn
    carries on. Its copy promises nothing, so paging a human here would notify
    someone on every recovered sentence."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream(
                [
                    f"A studio at Skyrise is AED {GROUNDED}. ",
                    f"Sapphire Bay is AED {FABRICATED}. ",
                ]
            )
        ]
    )

    text = spoken(await run_llm_node(agent, user_ctx()))
    assert BRIDGE_COPY["en"] in text
    assert FALLBACK_COPY["en"] not in text

    assert agent.tracker is not None
    assert agent.tracker.actions == []

    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    assert "bridge" in emitted  # the recovery did happen
    assert "escalation" not in emitted


async def test_the_escalation_belongs_to_the_turn_that_fell_back():
    """Multi-turn, because a per-session notification would credit every later
    turn with an escalation it did not earn - and because the ambassador view
    reads `actions` per turn."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
            HealthyStream([f"A studio at Skyrise is AED {GROUNDED}. "]),
        ]
    )

    first_ctx = user_ctx("What does Sapphire Bay cost?")
    assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, first_ctx))
    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(first_ctx)
    handle._mark_done()
    await settle()

    second_ctx = user_ctx("And a studio at Skyrise?")
    second = spoken(await run_llm_node(agent, second_ctx))
    assert GROUNDED.replace(",", "") not in second  # verbalised, not digits
    assert FALLBACK_COPY["en"] not in second
    handle2 = SpeechHandle.create()
    agent.note_speech_handle(handle2)
    agent.finish_turn(second_ctx)
    handle2._mark_done()
    await settle()

    await log.aclose()
    assert [r.actions for r in log.turns] == [["escalate_to_human"], []]
    assert [e["turn"] for e in json_lines(buf) if e["event"] == "tool_call"] == [1]


# --- eval F8: the regeneration names the tool, and pages one human -------
#
# `REGENERATION_INSTRUCTION` told a blocked model to "offer a human ambassador"
# and never named `escalate_to_human`. English called the tool from habit;
# Arabic and Hindi satisfied the words and routed nobody, so the buyer was
# promised a colleague on the regeneration path with no notification - the same
# promise-without-routing shape as F2, one layer up from the fallback copy.
#
# Naming the tool there makes the double-page combination expected rather than
# rare: a retry that calls the tool AND still states an unallowed figure gets
# the composed fallback too, so two paths ask for a human in one turn. The
# STUB behind `_route_to_human` is a CRM write, and two writes for one buyer
# turn is two tasks in an ambassador's queue.


async def test_the_regeneration_instruction_names_the_tool_it_wants_called():
    """The wording shape is a pipeline fact and belongs here; whether the model
    obeys it is Pam's live measurement, not this suite's."""
    agent, _, _, spy = make_agent(
        [
            HealthyStream([f"Sapphire Bay is AED {FABRICATED}. "]),
            HealthyStream([f"A studio at Skyrise is AED {GROUNDED}. "]),
        ]
    )

    await run_llm_node(agent, user_ctx())

    # The retry is the second call, and the instruction rides on it as a system
    # message appended to a copy of the context.
    assert len(spy.chat_ctxs) == 2
    added = [
        item.text_content or ""
        for item in spy.chat_ctxs[1].items
        if getattr(item, "role", None) == "system"
    ]
    assert len(added) == 1
    instruction = added[0]
    assert "call the escalate_to_human tool" in instruction
    # Not the trailing position that measured 0/3 live.
    assert not instruction.rstrip().endswith("tool.")
    assert instruction.rstrip().endswith("Never restate the figure that was blocked.")
    # Recovering correctly still comes first, or the model refuses figures it holds.
    assert instruction.index("Reply again") < instruction.index("escalate_to_human")


async def test_two_paths_asking_for_a_human_hand_over_once():
    """The order the framework produces: the composed fallback routes while the
    stream is still unwinding, and the model's tool call executes after it."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay is AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
        ]
    )

    assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, user_ctx()))
    await agent.escalate_to_human(None, "the figure is not in the inventory")

    await log.aclose()
    # One handover for the turn, and the second attempt is visible rather than
    # dropped silently.
    assert len(escalations(buf)) == 1
    suppressed = [
        ln for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"
    ]
    assert [ln["turn"] for ln in suppressed] == [1]
    assert suppressed[0]["reason"] == "[redacted]"

    # But BOTH requests are still recorded: which path asked for a human and
    # when is the hook-2 claim, and the model really did call the tool.
    assert agent.tracker is not None
    assert agent.tracker.actions == ["escalate_to_human", "escalate_to_human"]


async def test_the_model_calling_the_tool_first_still_leaves_one_handover():
    """The mirror order, in case the framework ever executes tools earlier. The
    property is one handover per turn, not one per source."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay is AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
        ]
    )

    ctx = user_ctx()
    agent._ensure_tracker(ctx)
    await agent.escalate_to_human(None, "the figure is not in the inventory")
    assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, ctx))

    await log.aclose()
    assert len(escalations(buf)) == 1
    assert escalations(buf)[0]["reason"] == "[redacted]"
    assert (
        len([ln for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"])
        == 1
    )


async def test_a_later_turn_can_hand_over_again():
    """The guard is per turn, not per session. A buyer who needs a human twice
    in one call has to be handed over twice, or the second ask vanishes."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay is AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
            HealthyStream([f"Marina Heights is AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
        ]
    )

    for buyer in ("What does Sapphire Bay cost?", "And Marina Heights?"):
        ctx = user_ctx(buyer)
        assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, ctx))
        handle = SpeechHandle.create()
        agent.note_speech_handle(handle)
        agent.finish_turn(ctx)
        handle._mark_done()
        await settle()

    await log.aclose()
    assert len(escalations(buf)) == 2
    assert [r.actions for r in log.turns] == [
        ["escalate_to_human"],
        ["escalate_to_human"],
    ]


# --- issue #33: the regeneration's promise becomes structural -------------
#
# #31 named `escalate_to_human` in the regeneration instruction's leading
# imperative and it measured 3/3 English, 3/3 Hindi and 1/3 ARABIC - and the
# three Arabic samples were byte-identical requests at temperature 0 that
# disagreed, so wording cannot close the gap. Same move as F2 and the budget
# handovers: a regenerated reply that ends the turn stating no figure has
# refused, and a refusal promises a colleague, so code keeps the promise.

REFUSAL = "I do not have that project in our current listings. "


async def test_a_regeneration_that_refuses_in_words_hands_the_buyer_over():
    """The Arabic residual, at the product level: the model refuses correctly,
    promises a colleague in words, and calls nothing."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([REFUSAL]),
        ]
    )

    text = spoken(await run_llm_node(agent, user_ctx("What does Sapphire Bay cost?")))
    # The buyer hears the model's own refusal, not the composed fallback: the
    # retry was speakable, so there was nothing to recover from.
    assert "current listings" in text
    assert FALLBACK_COPY["en"] not in text
    assert FABRICATED not in text

    assert agent.tracker is not None
    assert agent.tracker.actions == ["escalate_to_human"]

    await log.aclose()
    assert [e["routed_to"] for e in escalations(buf)] == ["human_ambassador"]


async def test_a_regeneration_that_corrects_itself_does_not_hand_over():
    """The designed happy recovery. The model was told which figure was not in
    the inventory and came back with one that is - nobody needs paging, and an
    agent that escalates on everything is as broken as one that never does."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([f"A studio at Skyrise is AED {GROUNDED}. "]),
        ]
    )

    text = spoken(await run_llm_node(agent, user_ctx()))
    assert FALLBACK_COPY["en"] not in text
    assert agent.tracker is not None
    assert agent.tracker.actions == []

    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    assert "regeneration" in emitted  # the retry did happen
    assert "escalation" not in emitted


async def test_a_first_pass_reply_with_no_figure_does_not_hand_over():
    """Scope. Most of a conversation carries no figure - "which areas do you
    cover" is not a refusal - so the backstop is the regeneration path only."""
    agent, log, buf, _ = make_agent(
        [HealthyStream(["We have towers across Business Bay and Dubai Marina. "])]
    )

    await run_llm_node(agent, user_ctx("Which areas do you cover?"))
    assert agent.tracker is not None
    assert agent.tracker.actions == []

    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    assert "regeneration" not in emitted
    assert "escalation" not in emitted


async def test_a_regeneration_blocked_twice_hands_over_exactly_once():
    """The composed fallback already routes (F2). The backstop must not page a
    second ambassador for the same refusal."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([f"Still AED {FABRICATED}. "]),
        ]
    )

    assert FALLBACK_COPY["en"] in spoken(await run_llm_node(agent, user_ctx()))
    assert agent.tracker is not None
    assert agent.tracker.actions == ["escalate_to_human"]

    await log.aclose()
    assert len(escalations(buf)) == 1
    assert not [ln for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"]


async def test_a_refusing_regeneration_that_also_calls_the_tool_pages_one_human():
    """The 1-in-3 Arabic case and the 3-in-3 English case land on the same turn
    shape. #31's notify-once guard is what keeps it one handover; both requests
    stay in the record, because which path asked is the hook-2 claim."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream([REFUSAL]),
        ]
    )

    await run_llm_node(agent, user_ctx())
    # The framework executes the model's tool call after the node finishes.
    await agent.escalate_to_human(None, "the figure is not in the inventory")

    await log.aclose()
    assert len(escalations(buf)) == 1
    assert [
        ln["turn"] for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"
    ] == [1]
    assert agent.tracker is not None
    assert agent.tracker.actions == ["escalate_to_human", "escalate_to_human"]


async def test_a_conversational_count_in_a_regeneration_is_not_a_corrected_figure():
    """The 0-12 count exemption is not an answer about money. A retry that says
    "there are 2 layouts" and quotes nothing has still refused."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay starts from AED {FABRICATED}. "]),
            HealthyStream(["I cannot confirm that price. There are 2 layouts. "]),
        ]
    )

    await run_llm_node(agent, user_ctx())
    assert agent.tracker is not None
    assert agent.tracker.actions == ["escalate_to_human"]

    await log.aclose()
    assert len(escalations(buf)) == 1


# --- ADR-011 trigger 2: the project-name confirmation -----------------------
#
# Same seam as the budget policy, same reason: the model never runs on a turn
# where a confirmation is owed, so it cannot skip the question or answer it on
# the buyer's behalf. What is different is the slot - a project name is bound
# to inventory, not to the transcript, because reading the buyer's own mangled
# words back ("did you mean Bint Jbeil Sky Rise?") confirms nothing.


async def test_a_marginal_project_name_is_confirmed_and_the_model_never_runs():
    """The transcript ADR-015 actually measured. Every recogniser mangled the
    client's own name, and `Skyrise` and `Aquarise` are different prices."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])  # any model call pops from an empty list

    reply = spoken(
        await run_llm_node(agent, user_ctx("Tell me about Binghatti Skyrize"))
    )
    assert "did you mean Binghatti Skyrise" in reply


async def test_a_confident_project_name_is_not_read_back():
    """A read-back nobody needs is the fastest way to have the policy switched
    off, so a clean name goes straight to the model."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    reply = spoken(
        await run_llm_node(agent, user_ctx("Tell me about Binghatti Skyrise"))
    )
    assert "did you mean" not in reply
    assert "A studio is" in reply


async def test_yes_settles_the_name_and_it_is_never_read_back_again():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean Binghatti Skyrise" in first

    agent._tracker = None
    second = spoken(await run_llm_node(agent, user_ctx("Yes, that's right")))
    assert "did you mean" not in second
    assert "A studio is" in second


async def test_no_to_a_name_asks_which_project_out_loud():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    first = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean Binghatti Skyrise" in first

    agent._tracker = None
    second = spoken(await run_llm_node(agent, user_ctx("No, that's not it")))
    assert "which project was that" in second
    # And the rejected one is out of the running, not re-offered.
    assert "Binghatti Skyrise" not in second


async def test_a_corrected_name_in_the_reply_replaces_the_offer():
    """The stale-mention defect in its project form: settling the name we
    guessed against a reply that named a different one."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("what about Binghatti Rise")))
    assert "did you mean Binghatti Skyrise" in first

    agent._tracker = None
    second = spoken(await run_llm_node(agent, user_ctx("no, the Aquarise")))
    assert "Binghatti Skyrise" not in second
    assert agent._project.confirmed == frozenset({"binghatti-aquarise"})


async def test_three_answerless_replies_about_a_name_notify_a_human():
    """`hands_over` must be a notification, not a log field: saying "let me put
    you through" with nobody put through is the anti-pattern the escalation
    tool's own docstring names."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    for reply in ("Can you repeat that?", "Sorry, the line is bad"):
        agent._tracker = None
        assert "did you mean" in spoken(await run_llm_node(agent, user_ctx(reply)))

    agent._tracker = None
    last = spoken(await run_llm_node(agent, user_ctx("What was that")))
    assert "ambassadors" in last
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    assert '"escalation"' in buf.getvalue()


async def test_a_project_name_can_only_come_out_of_inventory():
    """The bound on this slot. `compose` bounds the budget echo to the buyer's
    transcript; this bounds the name to `data/inventory.json`, which is
    invariant 1's single source of project facts."""
    from adapter.confirmations import NameNotInInventory, compose_project

    assert (
        compose_project(
            "did you mean {project}?",
            project="Binghatti Skyrise",
            inventory_names=("Binghatti Skyrise",),
        )
        == "did you mean Binghatti Skyrise?"
    )
    for invented in ("Binghatti Skyriser", "", "Emaar Beachfront"):
        with pytest.raises(NameNotInInventory):
            compose_project(
                "did you mean {project}?",
                project=invented,
                inventory_names=("Binghatti Skyrise",),
            )


async def test_a_name_outside_inventory_hands_over_rather_than_being_spoken():
    """Fail closed, out loud. If the slot and the inventory it is bound to ever
    disagree, the buyer goes to a person - the model does not get the turn
    with the project unconfirmed."""
    from ambassador.projects import NameIndex

    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    # The policy still holds the real index; the adapter's bound no longer
    # recognises what it returns.
    agent._name_index = NameIndex(keys=(), names={}, decoys=())

    reply = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "ambassadors" in reply
    assert "{" not in reply
    assert agent._project.handed_over
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    assert '"escalation"' in buf.getvalue()


async def test_a_broken_project_template_hands_over_rather_than_freeing_the_model():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])
    agent._confirmations = ConfirmationCopy(
        by_language={
            "en": dict(
                agent._confirmations.by_language["en"],
                confirm_project="did you mean {project.foo}?",
            )
        }
    )

    reply = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "ambassadors" in reply
    assert "{" not in reply
    assert agent._project.handed_over


async def test_a_name_the_guardrail_refuses_hands_over_and_still_speaks():
    """This line goes through `SentenceGuard.compose()` rather than round it,
    so a project name the numeric guardrail objects to (a digit in a name is
    in no allowed set) blocks the sentence. That must fail closed AND still
    speak: AGENTS.md is absolute that a turn never ends in silence."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    def refuse(text: str) -> str:
        raise AssertionError(f"composed copy violates numeric_claims: {text}")

    agent._guard.compose = refuse  # type: ignore[method-assign]

    reply = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "ambassadors" in reply
    assert agent._project.handed_over


async def test_the_budget_owns_the_turn_when_both_policies_could_speak():
    """One deterministic question per turn, and the twenty-times currency
    error is the more expensive one. The cost is stated in docs/04-: the name
    is not confirmed until the buyer says it again."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    reply = spoken(
        await run_llm_node(
            agent, user_ctx("My budget is 2 crore for Binghatti Skyrize")
        )
    )
    assert "dirhams or in rupees" in reply
    assert "did you mean" not in reply


async def test_a_language_with_no_project_copy_does_not_speak_english():
    agent, _, _ = _budget_agent(language="ar", allow_uncertified_language=True)
    agent._llm = SpyLLM([HealthyStream(["مرحبا. "])])

    reply = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean" not in reply


async def test_no_llm_request_is_logged_on_a_project_confirmation_turn():
    """Anything costing model calls off the event stream is wrong, and a
    confirmation turn makes none."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    await log.aclose()
    assert "llm_request" not in buf.getvalue()


async def test_the_buyers_mangled_words_never_reach_the_emitted_stream():
    """The spoken line is fixed copy plus an inventory name, so the project id
    is emitted and the sentence is not - the audit reads it from the in-memory
    record instead."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])

    await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    chunks = [c.text for c in agent.tracker.spoken_chunks]
    await log.aclose()

    emitted = buf.getvalue()
    assert "did you mean" not in emitted, emitted
    assert "project_confirmation" in emitted
    assert '"project": "binghatti-skyrise"' in emitted
    assert any("did you mean Binghatti Skyrise" in c for c in chunks), chunks


async def test_a_tool_split_turn_reads_the_project_policy_once():
    """A tool call splits one buyer turn across two llm_node invocations. The
    policies read each turn once, or one reply burns two of three attempts."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean" in first
    # Same tracker, so the same buyer utterance: this is the second half of
    # one turn and the model, not the policy, owns it.
    second = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean" not in second


async def test_a_confident_match_is_recorded_even_though_nothing_is_said():
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await run_llm_node(agent, user_ctx("Tell me about Binghatti Skyrise"))
    await log.aclose()
    assert '"event": "project_settled"' in buf.getvalue()


def test_the_prompt_keeps_the_model_confirming_names_where_the_policy_is_off():
    """The ar/hi regression, in its project form: telling the model the system
    owns a question the system will not ask leaves nobody asking."""
    on = AmbassadorAgent(
        settings=make_settings(),
        log=EventLog("sess_test", stream=StringIO(), verbose=False),
    )
    assert "Checking which project the buyer means is handled by the system" in (
        on.instructions
    )

    off = AmbassadorAgent(
        settings=make_settings(language="ar", allow_uncertified_language=True),
        log=EventLog("sess_test", stream=StringIO(), verbose=False),
    )
    assert "read it back before answering" in off.instructions
    assert "Checking which project the buyer means" not in off.instructions


# --- ADR-011 trigger 3: three consecutive failed recognitions ---------------


async def test_three_unusable_turns_in_a_row_hand_the_buyer_over():
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM(
        [HealthyStream(["Sorry, could you repeat that? "]) for _ in range(2)]
    )

    for unusable in ("", "   "):
        agent._tracker = None
        reply = spoken(await run_llm_node(agent, user_ctx(unusable)))
        assert "ambassadors" not in reply

    agent._tracker = None
    third = spoken(await run_llm_node(agent, user_ctx("uh, hmm")))
    assert "not hearing you clearly" in third
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    emitted = buf.getvalue()
    assert '"escalation"' in emitted
    assert '"consecutive": 3' in emitted


async def test_a_real_turn_resets_the_recognition_count():
    """Three failures spread over a good call are three ordinary "could you
    repeat that" moments, not a broken line."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM(
        [HealthyStream([f"A studio is AED {GROUNDED}. "]) for _ in range(5)]
    )

    for utterance in ("", "", "What does a studio cost?", "", ""):
        agent._tracker = None
        reply = spoken(await run_llm_node(agent, user_ctx(utterance)))
        assert "not hearing you clearly" not in reply
    assert not agent._recognition.handed_over


async def test_an_unheard_turn_re_asks_the_open_budget_question():
    """ADR-011's central property is that the model never takes the turn while
    a confirmation is owed. A turn nobody could hear answered nothing, so the
    question is asked again - and no attempt is consumed, because a reply that
    was never heard is not a reply that was wrong."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])  # any model call would raise IndexError

    first = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "dirhams or in rupees" in first

    for _ in range(2):
        agent._tracker = None
        again = spoken(await run_llm_node(agent, user_ctx("")))
        assert "2 crore" in again
        assert "dirhams or in rupees" in again

    # Two unheard turns, and the buyer still has all three attempts.
    agent._tracker = None
    assert "dirhams or in rupees" in spoken(
        await run_llm_node(agent, user_ctx("Can you repeat that?"))
    )
    agent._tracker = None
    assert "dirhams or in rupees" in spoken(
        await run_llm_node(agent, user_ctx("Sorry?"))
    )
    agent._tracker = None
    assert "ambassadors" in spoken(await run_llm_node(agent, user_ctx("What?")))


async def test_an_unheard_turn_re_asks_the_open_project_question_too():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([])

    assert "did you mean Binghatti Skyrise" in spoken(
        await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    )
    agent._tracker = None
    again = spoken(await run_llm_node(agent, user_ctx("...")))
    assert "did you mean Binghatti Skyrise" in again
    assert not agent._project.handed_over


async def test_an_unheard_turn_with_nothing_owed_goes_to_the_model():
    """The first two failures are not the policy's business: the model can say
    "sorry, could you repeat that" perfectly well, and inventing deterministic
    copy for a case nobody has authored would be worse."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry, could you repeat that? "])])

    reply = spoken(await run_llm_node(agent, user_ctx("")))
    assert "could you repeat" in reply


async def test_the_escalation_is_spoken_once_not_on_every_crackle():
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM(
        [HealthyStream(["Sorry, could you repeat that? "]) for _ in range(6)]
    )

    spoken_lines = []
    for _ in range(6):
        agent._tracker = None
        spoken_lines.append(spoken(await run_llm_node(agent, user_ctx(""))))
    assert sum("not hearing you clearly" in line for line in spoken_lines) == 1


async def test_a_language_with_no_escalation_copy_does_not_speak_english():
    agent, _, _ = _budget_agent(language="ar", allow_uncertified_language=True)
    agent._llm = SpyLLM([HealthyStream(["مرحبا. "]) for _ in range(3)])

    for _ in range(3):
        agent._tracker = None
        reply = spoken(await run_llm_node(agent, user_ctx("")))
        assert "ambassadors" not in reply


async def test_the_recognition_escalation_is_audited_without_its_text():
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry? "]) for _ in range(2)])

    for utterance in ("", "", "hmm"):
        agent._tracker = None
        await run_llm_node(agent, user_ctx(utterance))
    chunks = [c.text for c in agent.tracker.spoken_chunks]
    await log.aclose()

    emitted = buf.getvalue()
    assert '"event": "recognition_escalation_spoken"' in emitted
    assert "not hearing you clearly" not in emitted
    assert any("not hearing you clearly" in c for c in chunks), chunks


def test_every_terminal_line_may_carry_no_slot_of_any_kind(tmp_path):
    """`give_up` was not the only line spoken verbatim on a failure path once
    the project and recognition triggers landed. All three are checked, or the
    two new ones are a hole the loader used to close."""
    from adapter.confirmations import load_confirmations

    authored = {
        "ask_currency": "{amount} - dirhams or rupees?",
        "confirm_amount": "{amount} - right?",
        "ask_amount": "What is the budget?",
        "cannot_convert": "An ambassador will help.",
        "give_up": "Let me put you through.",
        "confirm_project": "Did you mean {project}?",
        "ask_project": "Which project was that?",
        "project_give_up": "Let me put you through.",
        "recognition_escalation": "Let me bring someone in.",
    }
    for key in ("give_up", "project_give_up", "recognition_escalation"):
        bad = tmp_path / f"{key}.yaml"
        broken = dict(authored, **{key: authored[key] + " {amount.foo}"})
        bad.write_text(
            "en:\n" + "".join(f'  {k}: "{v}"\n' for k, v in broken.items()),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=key):
            load_confirmations(bad)


# --- PR #23 review round: Meredith's five reproduced classes ----------------
#
# Each test below is her transcript, verbatim, written before its fix. The
# discipline is the budget rework's: a finding lands as a failing test that
# reproduces the exchange, not as a patch plus an assertion about the patch.


async def test_a_mangled_name_beside_a_valid_area_still_confirms():
    """Finding 1 (CRITICAL). The decoy comparison used absolute coverage, so
    an EXACT area out-covered the FUZZY project phrase in the same utterance
    and suppressed the trigger - in exactly the situation it exists for, a
    mangled project name accompanied by a real area."""
    for utterance, expected in (
        ("Tell me about Binghatti Skyrize in Business Bay", "Binghatti Skyrise"),
        (
            "Tell me about Binghatti Aquarize in Dubai Maritime City",
            "Binghatti Aquarise",
        ),
    ):
        agent, _, _ = _budget_agent()
        agent._llm = SpyLLM([])  # a model call would raise IndexError
        reply = spoken(await run_llm_node(agent, user_ctx(utterance)))
        assert f"did you mean {expected}" in reply, utterance


async def test_each_reply_is_read_by_the_question_it_answers():
    """Finding 2 (HIGH). Budget precedence stole the project's answer and then
    the project policy consumed the budget's answer as a failed attempt.

    Meredith's exchange, turn for turn. The "Yes" settles the name; "Dirhams"
    settles the currency and is not read as a project reply at all.
    """
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    first = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrize")))
    assert "did you mean Binghatti Skyrise" in first

    agent._tracker = None
    second = spoken(
        await run_llm_node(agent, user_ctx("Yes, and my budget is 2 crore."))
    )
    assert "dirhams or in rupees" in second
    # The Yes was an answer, and answers are not discarded by precedence.
    assert agent._project.confirmed == frozenset({"binghatti-skyrise"})

    agent._tracker = None
    third = spoken(await run_llm_node(agent, user_ctx("Dirhams.")))
    assert "did you mean" not in third, (
        "the budget's answer was read as a project reply"
    )
    assert "A studio is" in third


async def test_a_budget_answer_is_not_a_failed_project_attempt():
    """Finding 2, the half that cost the buyer two attempts.

    With both questions open, the reply belongs to the one asked most
    recently. The project question is suspended, not answered - so it consumes
    nothing and is asked again once the budget settles.
    """
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    agent._tracker = None
    # No agreement word, so this does not answer the project question - but it
    # does carry a budget, and losing it would leave the model acting on an
    # unconfirmed 2 crore.
    assert "dirhams or in rupees" in spoken(
        await run_llm_node(agent, user_ctx("It is about 2 crore."))
    )
    agent._tracker = None
    # The currency answer settles the budget and the suspended project
    # question is asked again, having consumed no attempt.
    assert "did you mean Binghatti Skyrise" in spoken(
        await run_llm_node(agent, user_ctx("Dirhams."))
    )
    agent._tracker = None
    assert "A studio is" in spoken(await run_llm_node(agent, user_ctx("Yes")))
    assert agent._project.confirmed == frozenset({"binghatti-skyrise"})
    # The point of the exchange: the buyer answered both questions correctly,
    # so the project policy spent none of its three attempts on the turns that
    # belonged to the budget.
    assert agent._project._attempts == 0


async def test_a_project_question_does_not_resume_after_a_handover_either():
    """Finding 3, the other pending policy. Quiescing is by construction -
    every policy is abandoned - rather than a guard at the budget's read site,
    because a guard is one site away from being forgotten."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry? "]) for _ in range(4)])

    assert "did you mean Binghatti Skyrise" in spoken(
        await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    )
    for _ in range(2):
        agent._tracker = None
        assert "did you mean Binghatti Skyrise" in spoken(
            await run_llm_node(agent, user_ctx(""))
        )
    agent._tracker = None
    assert "not hearing you clearly" in spoken(await run_llm_node(agent, user_ctx("")))
    for _ in range(2):
        agent._tracker = None
        assert "did you mean" not in spoken(await run_llm_node(agent, user_ctx("")))
    assert agent._policies.quiesced


async def test_a_budget_give_up_quiesces_the_project_question_too():
    """A handover by ANY deterministic policy closes the others. The model's own
    escalate_to_human tool deliberately does NOT: it fires routinely (an unknown
    project, a branded price) and its own docstring tells the model to keep
    speaking normally, so treating that as terminal would silence the
    confirmations on an ordinary call."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry? "]) for _ in range(4)])

    await run_llm_node(agent, user_ctx("Binghatti Skyrize"))
    agent._tracker = None
    await run_llm_node(agent, user_ctx("It is about 2 crore."))
    for reply in ("What?", "Sorry?"):
        agent._tracker = None
        spoken(await run_llm_node(agent, user_ctx(reply)))
    agent._tracker = None
    last = spoken(await run_llm_node(agent, user_ctx("Pardon?")))
    assert "ambassadors" in last
    assert agent._policies.quiesced

    agent._tracker = None
    assert "did you mean" not in spoken(await run_llm_node(agent, user_ctx("")))


async def test_nothing_resumes_after_recognition_hands_the_call_over():
    """Finding 3 (HIGH). A pending budget question resumed on the turn AFTER
    the recognition escalation. Once a human has been brought in, no policy
    speaks again - terminal state has to be coordinated, not per-policy."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry? "]) for _ in range(4)])

    assert "dirhams or in rupees" in spoken(
        await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    )
    for _ in range(2):
        agent._tracker = None
        assert "dirhams or in rupees" in spoken(await run_llm_node(agent, user_ctx("")))

    agent._tracker = None
    assert "not hearing you clearly" in spoken(await run_llm_node(agent, user_ctx("")))
    assert agent._recognition.handed_over

    for _ in range(2):
        agent._tracker = None
        after = spoken(await run_llm_node(agent, user_ctx("")))
        assert "2 crore" not in after, "a stale question resumed after a handover"
        assert "dirhams or in rupees" not in after


async def test_ordinary_language_does_not_speak_a_project_name():
    """Finding 4 (HIGH). "arise" scores 0.769 against "aquarise" - above the
    floor, below confident, unsuppressed - so ordinary English asked the buyer
    about a tower they never mentioned."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Prices are current as of today. "])])

    reply = spoken(await run_llm_node(agent, user_ctx("When did prices arise?")))
    assert "did you mean" not in reply
    assert "Prices are current" in reply


def test_a_generic_word_in_a_project_name_is_not_a_project_mention():
    """Finding 4, the silent half: `residences` is an exact individual key, so
    "What residences are available?" was classified as a CONFIDENT mention of
    Bugatti Residences - wrong in the audit, and it excluded that project from
    every later read-back."""
    from ambassador.inventory import load_inventory
    from ambassador.projects import build_name_index, match_project_name

    index = build_name_index(load_inventory())
    assert match_project_name("What residences are available?", index) is None


def test_the_fixed_terminal_copy_is_validated_before_the_call_starts():
    """Finding 5 (MEDIUM). `_fixed_line()` caught a rejecting guard and spoke
    the raw string, which is a literal bypass of the single public speech path
    and contradicted the PR's own claim.

    The fix moves the check earlier rather than choosing a direction at
    runtime: every slot-free terminal line is composed through the guard once,
    at construction, and an agent whose own copy fails its own guardrails
    refuses to start - the same precedent as an unauthored disclosure blocking
    its language. There is then no runtime guard call on this path to fail
    open, and none to fail closed either.
    """
    log = EventLog("sess_test", stream=StringIO(), verbose=False)
    with pytest.raises(RuntimeError, match="guardrail"):
        AmbassadorAgent(
            settings=make_settings(),
            log=log,
            guard_factory=_rejecting_guard,
        )


def _rejecting_guard(**kwargs):
    """A SentenceGuard whose compose() refuses everything, standing in for copy
    that fails our own guardrails."""
    guard = SentenceGuard(**kwargs)

    def refuse(text: str) -> str:
        raise AssertionError(f"composed copy violates numeric_claims: {text}")

    guard.compose = refuse  # type: ignore[method-assign]
    return guard


async def test_no_guard_call_happens_on_the_fixed_line_at_turn_time():
    """The other half of finding 5: a guard that starts rejecting mid-session
    cannot produce unvalidated speech here, because the line was composed and
    validated before the call opened."""
    agent, _, _ = _budget_agent()
    agent._llm = SpyLLM([HealthyStream(["Sorry? "]) for _ in range(2)])

    calls: list[str] = []
    real = agent._guard.compose

    def counting(text: str) -> str:
        calls.append(text)
        return real(text)

    agent._guard.compose = counting  # type: ignore[method-assign]

    for utterance in ("", "", "hmm"):
        agent._tracker = None
        reply = spoken(await run_llm_node(agent, user_ctx(utterance)))
    assert "not hearing you clearly" in reply
    assert not [c for c in calls if "not hearing you clearly" in c], calls


async def test_a_crash_in_the_policies_themselves_hands_over():
    """The outer catch on the seam. Everything inside the confirmation
    machinery fails CLOSED - including the part that decides WHICH policy acts,
    which is the one piece no individual policy's own catch covers."""
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    def explode(utterance: str):
        raise RuntimeError("ownership is undecidable today")

    agent._policies.observe = explode  # type: ignore[method-assign]

    reply = spoken(await run_llm_node(agent, user_ctx("My budget is 2 crore.")))
    assert "ambassadors" in reply
    assert "A studio is" not in reply, "the model took a turn with a budget unconfirmed"
    assert "escalate_to_human" in agent.tracker.actions

    await log.aclose()
    assert '"escalation"' in buf.getvalue()


async def test_a_fresh_project_name_does_not_hand_the_buyer_over(monkeypatch):
    """The re-review's transcript, through llm_node.

    A confident project name offered while a budget question is open was
    accepted by the project policy and ALSO charged to the budget as its third
    failure, so a valid project selection ended the call with a hand-over. Only
    a reply nobody claims may be charged to the owner.
    """
    agent, log, buf = _budget_agent()
    agent._llm = SpyLLM([])  # a model call would raise IndexError

    assert "dirhams or in rupees" in spoken(
        await run_llm_node(agent, user_ctx("My budget is 2 crore."))
    )
    for reply in ("What?", "Sorry?"):
        agent._tracker = None
        assert "dirhams or in rupees" in spoken(
            await run_llm_node(agent, user_ctx(reply))
        )

    agent._tracker = None
    heard = spoken(await run_llm_node(agent, user_ctx("Binghatti Skyrise.")))
    assert "ambassadors" not in heard, "a valid project name handed the buyer over"
    assert "dirhams or in rupees" in heard
    assert agent._project.confirmed == frozenset({"binghatti-skyrise"})
    assert not agent._policies.quiesced
    assert "escalate_to_human" not in agent.tracker.actions

    await log.aclose()
    assert '"escalation"' not in buf.getvalue()


# --- one buyer turn, one TurnRecord, with preemptive generation ON ---------
#
# `preemptive_generation` defaults to enabled (livekit/agents/voice/turn.py,
# `_PREEMPTIVE_GENERATION_DEFAULTS`): the framework starts LLM work on the
# PARTIAL transcript, then calls `on_user_turn_completed` with the final one. So
# on a real voice turn `llm_node` runs FIRST and `on_user_turn_completed`
# second - the reverse of the text path - and a hook that unconditionally opened
# a tracker produced two TurnRecords per utterance: one holding the LLM and
# guardrail work, one holding the endpointing and audio marks.
#
# Measured live in the first synthetic-audio run (#51): `turn_complete` reported
# `sentences: 0` on the audio half, `total_ms: 20103.6` on a turn that took
# seconds, and `tts_first_audio.since_first_sentence_ms` was null on every
# single turn - which is the metric issue #18's barge-in delta is defined in.
#
# These tests live at the hook seam rather than behind the VAD, because that is
# where the reordering happens and it is the only place a test can see it
# without a room. The whole suite was blind to this class twice in one day.


def eou(endpoint: float = 0.44, transcription: float = 0.29) -> EOUMetrics:
    return EOUMetrics(
        timestamp=time.time(),
        end_of_utterance_delay=endpoint,
        transcription_delay=transcription,
        on_user_turn_completed_delay=0.001,
    )


async def preemptive_turn(
    agent: AmbassadorAgent,
    *,
    partial: str,
    final: str,
    interrupted: bool = False,
) -> SpeechHandle:
    """One buyer turn in the order the framework actually produces it.

    `interrupted` marks the turn's audio as cut off, which is what the buyer
    talking over the reply produces - and the state both hosted near misses
    were sealed in.
    """
    ctx = user_ctx(partial)
    await run_llm_node(agent, ctx)  # the preemptive generation, on the partial
    message = lk_llm.ChatMessage(role="user", content=[final])
    await agent.on_user_turn_completed(ctx, message)
    agent.note_metrics(eou())
    handle = SpeechHandle.create()
    if interrupted:
        handle.interrupt()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle._mark_done()
    await settle()
    return handle


def test_preemptive_generation_is_still_on_by_default():
    """The pin. If a livekit release ever defaults this off, the split becomes
    unreachable and the adoption below is dead weight - this is what says so
    first, rather than the tests below quietly passing for a new reason."""
    from livekit.agents.voice.turn import _PREEMPTIVE_GENERATION_DEFAULTS

    assert _PREEMPTIVE_GENERATION_DEFAULTS["enabled"] is True


async def test_a_partial_and_its_final_transcript_seal_as_one_turn():
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await preemptive_turn(
        agent,
        partial="What does a studio at Skyrise",
        final="What does a studio at Skyrise cost?",
    )

    assert len(log.turns) == 1
    record = log.turns[0]
    # docs/02-: buyer_utterance is the FINAL STT text, not the partial the model
    # was allowed to start on.
    assert record.buyer_utterance == "What does a studio at Skyrise cost?"
    # The two halves that used to land on different records.
    assert record.generated_sentences
    assert record.timings_ms.llm_first_sentence is not None
    assert record.timings_ms.endpoint == 440.0

    await log.aclose()
    assert [ln["event"] for ln in json_lines(buf)].count("turn_complete") == 1


async def test_the_barge_in_delta_is_measurable_with_preemptive_generation_on(
    monkeypatch,
):
    """The metric issue #18's delta is defined in. It was null on every live
    turn because `mark_first_sentence` and `mark_tts_first_audio` landed on
    different trackers."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    ctx = user_ctx("What does a studio at Skyrise")
    await run_llm_node(agent, ctx)
    await agent.on_user_turn_completed(
        ctx, lk_llm.ChatMessage(role="user", content=["What does a studio cost?"])
    )

    async def one_frame(agent_, text, model_settings):
        async for _ in text:
            pass
        yield object()

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(one_frame))

    async def source():
        yield "A studio is nine hundred and eighty five thousand dirhams. "

    assert [frame async for frame in agent.tts_node(source(), None)] != []
    await log.aclose()

    first_audio = [ln for ln in json_lines(buf) if ln["event"] == "tts_first_audio"]
    assert len(first_audio) == 1
    assert first_audio[0]["since_first_sentence_ms"] is not None


async def test_the_sealed_turn_measures_the_buyers_wait_not_the_gap_to_the_next():
    """`total_ms: 20103.6` on a seconds-long turn was the second tracker being
    opened early and sealed only when a later turn displaced it."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await preemptive_turn(
        agent, partial="What does a studio", final="What does it cost?"
    )

    total = log.turns[0].timings_ms.total
    assert total is not None
    assert total < 5000, total


async def test_the_text_path_still_opens_its_own_turn():
    """No preemptive generation without VAD, so `on_user_turn_completed` never
    fires and `llm_node` must still open the turn itself - console --text, the
    eval harness and session.run all depend on it."""
    agent, log, _, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    ctx = user_ctx()
    await run_llm_node(agent, ctx)
    handle = SpeechHandle.create()
    agent.note_speech_handle(handle)
    agent.finish_turn(ctx)
    handle._mark_done()
    await settle()

    assert len(log.turns) == 1
    assert log.turns[0].generated_sentences


async def test_two_buyer_turns_do_not_collapse_into_one():
    """Adoption must recognise the SAME utterance, not swallow the next one."""
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"A studio is AED {GROUNDED}. "]),
            HealthyStream([f"Handover is Q4 {ALLOWED_YEAR}. "]),
        ]
    )

    await preemptive_turn(
        agent, partial="What does a studio", final="What does it cost?"
    )
    await preemptive_turn(
        agent, partial="And when does it", final="And when is handover?"
    )

    assert [r.turn_index for r in log.turns] == [1, 2]
    await log.aclose()
    assert [ln["event"] for ln in json_lines(buf)].count("user_turn") == 2


# --- an invalidated preemptive generation leaves nothing behind ------------
#
# The residual #52 flagged and did not fix. `preemptive_generation` starts the
# model on a partial transcript; if the final transcript turns out not to be
# equivalent, `AgentActivity` cancels that speech handle and generates again
# (agent_activity.py: `preemptive.speech_handle._cancel()` then
# `self._generate_reply(...)`). The buyer never hears the first generation - but
# its guardrail decisions and spoken chunks were already recorded on the tracker
# the final transcript is then adopted onto, so the audit claimed sentences that
# were never played.
#
# WHAT THE TEST STANDS IN FOR. The framework's equivalence check cannot be
# forced from outside - no `preemptive generation invalidated` warning fired in
# the whole 8-turn live session - so the test drives the OBSERVABLE of it
# instead: `_generate_reply` emits `speech_created` for the preemptive handle
# (agent_activity.py:1550, reached from :2321) and emits it AGAIN for the
# replacement (:2574). On the happy path the framework reuses
# `preemptive.speech_handle` and no second event fires. So a second speech
# handle inside one buyer turn IS invalidation, and that is the seam the adapter
# can see.


async def test_an_invalidated_preemptive_generation_leaves_no_records_behind():
    agent, log, buf, _ = make_agent(
        [
            HealthyStream([f"A studio is AED {GROUNDED}. "]),  # discarded
            HealthyStream([f"Handover is Q4 {ALLOWED_YEAR}. "]),  # what plays
        ]
    )
    ctx = user_ctx("What does a studio")

    # The preemptive generation: llm_node runs on the partial, and the framework
    # announces its speech handle.
    await run_llm_node(agent, ctx)
    discarded = SpeechHandle.create()
    agent.note_speech_handle(discarded)
    assert agent.tracker is not None
    assert agent.tracker.generated_sentences  # the discarded reply is recorded

    # The final transcript arrives and is adopted onto the same turn (#52).
    await agent.on_user_turn_completed(
        ctx, lk_llm.ChatMessage(role="user", content=["When is handover?"])
    )

    # The framework finds them not equivalent: it cancels that handle and
    # generates again, which announces a second one.
    discarded._cancel()
    replacement = SpeechHandle.create()
    agent.note_speech_handle(replacement)
    await run_llm_node(agent, ctx)

    agent.finish_turn(ctx)
    replacement._mark_done()
    await settle()

    assert len(log.turns) == 1
    record = log.turns[0]
    # Only the generation the buyer actually heard.
    assert [x.strip() for x in record.generated_sentences] == [
        f"Handover is Q4 {ALLOWED_YEAR}."
    ]
    assert len(record.spoken_chunks) == 1
    assert GROUNDED not in " ".join(c.text for c in record.spoken_chunks)

    await log.aclose()
    emitted = [ln["event"] for ln in json_lines(buf)]
    # The discarded sentences stay on the stream - they were inspected, and the
    # claim that every sentence is inspected rests on that - but the stream says
    # they were dropped, so a consumer counting spoken sentences is not misled.
    assert "generation_discarded" in emitted
    assert emitted.count("turn_complete") == 1


async def test_the_discarded_generations_guardrail_violations_go_too():
    """A blocked sentence from a generation nobody heard must not be counted
    against the turn: `violations` is the number the meeting reads."""
    agent, log, _, _ = make_agent(
        [
            HealthyStream([f"Sapphire Bay is AED {FABRICATED}. "]),  # blocked
            HealthyStream(["Sapphire Bay is not in my list. "]),  # discarded retry
            HealthyStream([f"Handover is Q4 {ALLOWED_YEAR}. "]),  # what plays
        ]
    )
    ctx = user_ctx("What does Sapphire Bay")

    await run_llm_node(agent, ctx)
    discarded = SpeechHandle.create()
    agent.note_speech_handle(discarded)
    assert agent.tracker is not None
    assert agent.tracker.violations  # the discarded generation was blocked

    await agent.on_user_turn_completed(
        ctx, lk_llm.ChatMessage(role="user", content=["When is handover?"])
    )
    discarded._cancel()
    replacement = SpeechHandle.create()
    agent.note_speech_handle(replacement)
    await run_llm_node(agent, ctx)
    agent.finish_turn(ctx)
    replacement._mark_done()
    await settle()

    record = log.turns[0]
    assert record.guardrail_decisions == []
    assert [x.strip() for x in record.generated_sentences] == [
        f"Handover is Q4 {ALLOWED_YEAR}."
    ]


async def test_a_turn_whose_preemptive_generation_survived_drops_nothing():
    """The happy path, which is the common one: the framework reuses the
    preemptive speech handle, so no second one is announced and there is nothing
    to discard. A fix that dropped records here would erase every normal turn."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    await preemptive_turn(
        agent, partial="What does a studio", final="What does it cost?"
    )

    assert len(log.turns) == 1
    assert [x.strip() for x in log.turns[0].generated_sentences] == [
        f"A studio is AED {GROUNDED}."
    ]
    await log.aclose()
    assert "generation_discarded" not in [ln["event"] for ln in json_lines(buf)]


async def test_the_opening_disclosure_is_not_mistaken_for_a_replacement():
    """`on_enter` speaks the disclosure through `session.say`, which announces
    its own speech handle before any turn exists. Nothing to discard, and no
    tracker to discard it from."""
    agent, log, buf, _ = make_agent([HealthyStream([f"A studio is AED {GROUNDED}. "])])

    agent.note_speech_handle(SpeechHandle.create())  # the disclosure
    await preemptive_turn(
        agent, partial="What does a studio", final="What does it cost?"
    )

    assert [x.strip() for x in log.turns[0].generated_sentences] == [
        f"A studio is AED {GROUNDED}."
    ]
    await log.aclose()
    assert "generation_discarded" not in [ln["event"] for ln in json_lines(buf)]


# --- the startup preflight, and which commands it applies to ---------------
#
# `docs/09-deploy.md`: startup "says which one during preflight rather than
# failing on the first sentence of a call". It did not. `missing_for_voice` ran
# inside `entrypoint`, which only runs once a job is dispatched, so a worker
# with LiveKit credentials and no FISH_API_KEY registered, passed every check
# the platform could see, and failed on the first buyer.
#
# And the transport credentials cannot be left to the framework: with none set
# it logs "worker failed", drains, and exits ZERO, so a restart-on-failure
# policy never trips and a misconfigured deploy stops quietly.


def test_a_worker_command_demands_transport_and_provider_keys(monkeypatch):
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="", livekit_api_key="", livekit_api_secret="", fish_api_key=""
        ),
    )
    for command in ("start", "dev", "connect"):
        missing = adapter_agent.preflight([command])
        assert "LIVEKIT_URL" in missing, command
        assert "FISH_API_KEY" in missing, command


def test_console_is_not_asked_for_credentials_it_never_uses(monkeypatch):
    """Console runs a mock job in a `console-room` and dials nothing - verified
    by running it with no transport credentials at all, which reached
    `session_end` normally. Demanding them would refuse to start the venue plan
    B (docs/06- text-mode fallback) over keys it does not use.

    Its provider keys are still checked, by `entrypoint`, which its mock job
    dispatches immediately - so nothing there becomes later or quieter.
    """
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="", livekit_api_key="", livekit_api_secret="", fish_api_key=""
        ),
    )
    assert adapter_agent.preflight(["console"]) is None
    assert adapter_agent.preflight(["console", "--text"]) is None


def test_download_files_is_not_asked_either(monkeypatch):
    """It runs in an image build, where no credential exists yet. Demanding one
    would break the Dockerfile that bakes the plugin models in."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent, "load_settings", lambda: make_settings(livekit_url="")
    )
    assert adapter_agent.preflight(["download-files"]) is None


def test_flags_before_the_command_do_not_hide_it(monkeypatch):
    """The command is the first non-flag argument, so a global option in front
    of it must not make a worker look like a console session."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent, "load_settings", lambda: make_settings(livekit_url="")
    )
    assert "LIVEKIT_URL" in adapter_agent.preflight(["--log-level", "debug", "start"])


def test_a_configured_worker_passes_preflight(monkeypatch):
    """The boundary that matters most: no behaviour change when the keys are
    there. A preflight that refused a working deploy would be worse than the
    silence it replaces."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="wss://x",
            livekit_api_key="k",
            livekit_api_secret="s",
            fish_api_key="k",
            openrouter_api_key="k",
            stt_enabled=True,
            stt_provider="deepgram",
            deepgram_api_key="k",
        ),
    )
    assert adapter_agent.preflight(["start"]) is None


def test_an_empty_argv_is_not_a_worker(monkeypatch):
    """`python -m adapter.agent` with no command prints usage and exits; it must
    not be refused for credentials first."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent, "load_settings", lambda: make_settings(livekit_url="")
    )
    assert adapter_agent.preflight([]) is None


# --- the preflight refuses a worker that never chose to hear ---------------
#
# The measured hole this closes: a hosted worker with every secret set
# registered and could not hear, because STT_ENABLED defaults False and with it
# off the recogniser's key is never asked for. The refusal is scoped exactly
# like the credential check - connecting subcommands only.


def _configured(**overrides):
    base = dict(
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
        fish_api_key="k",
        openrouter_api_key="k",
        stt_enabled=True,
        stt_provider="deepgram",
        deepgram_api_key="k",
        stt_enabled_explicit=True,
    )
    base.update(overrides)
    return make_settings(**base)


def test_a_worker_that_never_chose_stt_is_refused(monkeypatch):
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent, "load_settings", lambda: _configured(stt_enabled_explicit=False)
    )
    for command in ("start", "dev", "connect"):
        refusal = adapter_agent.preflight([command])
        assert refusal is not None, command
        assert "STT_ENABLED" in refusal, command


def test_choosing_to_run_deaf_still_starts(monkeypatch):
    """The boundary that decides whether this check is safe. A card about
    refusing to start must not refuse the deliberate text-mode worker."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: _configured(
            stt_enabled=False, stt_enabled_explicit=True, deepgram_api_key=""
        ),
    )
    assert adapter_agent.preflight(["start"]) is None


def test_console_is_not_asked_to_choose_either(monkeypatch):
    """Console dials nothing and `download-files` runs in an image build where
    no configuration exists. Scoped exactly like the credential check, or this
    would break the venue plan B and the Dockerfile that bakes the models in."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent, "load_settings", lambda: _configured(stt_enabled_explicit=False)
    )
    assert adapter_agent.preflight(["console"]) is None
    assert adapter_agent.preflight(["console", "--text"]) is None
    assert adapter_agent.preflight(["download-files"]) is None
    assert adapter_agent.preflight([]) is None


def test_both_kinds_of_problem_are_reported_at_once(monkeypatch):
    """An operator on a platform pays a rebuild and a deploy per cycle, so
    learning about the second problem after fixing the first costs a round trip
    for nothing."""
    from adapter import agent as adapter_agent

    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: _configured(fish_api_key="", stt_enabled_explicit=False),
    )
    refusal = adapter_agent.preflight(["start"])
    assert refusal is not None
    assert "FISH_API_KEY" in refusal
    assert "STT_ENABLED" in refusal


def test_a_fully_configured_worker_still_passes(monkeypatch):
    from adapter import agent as adapter_agent

    monkeypatch.setattr(adapter_agent, "load_settings", lambda: _configured())
    assert adapter_agent.preflight(["start"]) is None


def test_the_refusal_echoes_no_value(monkeypatch):
    """Names only, never values: the message is printed by whatever supervisor
    restarted the process."""
    from adapter import agent as adapter_agent

    secret = "sk-or-v1-must-not-appear"
    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: _configured(
            openrouter_api_key=secret, fish_api_key="", stt_enabled_explicit=False
        ),
    )
    refusal = adapter_agent.preflight(["start"])
    assert refusal is not None
    assert secret not in refusal


def _capturing_log() -> tuple[EventLog, list[dict[str, Any]]]:
    """An EventLog plus the FULL records it emits.

    `add_observer` is the documented in-process way to see the unredacted
    stream, and the stream sink is a StringIO so the tests do not write to
    stdout. Asserting on the records rather than on the JSON lines is
    deliberate here: these three events exist to be READ by an operator, and
    the redacted rendering is already covered by tests/test_events.py.
    """
    records: list[dict[str, Any]] = []
    log = EventLog(session_id="sess_test", stream=StringIO())
    log.add_observer(records.append)
    return log, records


# --- per-call language, from room metadata --------------------------------
#
# `LANGUAGE` used to be the whole answer and made a worker speak one language
# for its life. The hosted demo lets a visitor pick (docs/09-), and the choice
# arrives as a room-metadata string written by another service - so the input is
# untrusted, and the matrix below is mostly about what happens when it is wrong.
# Every failure falls back to the worker default and NAMES itself, because a
# call that refuses to start teaches an unattended visitor nothing.


def test_metadata_selects_each_language_the_product_offers():
    """Parametrised off `Language` rather than a typed list, so a fourth
    language added to the product is covered here the day it lands."""
    from adapter.agent import language_from_metadata

    for code in get_args(Language):
        chosen = language_from_metadata(f'{{"v":1,"language":"{code}"}}', "en")
        assert chosen.language == code
        assert chosen.source == "room_metadata"
        assert chosen.reason == ""


def test_unknown_keys_are_ignored_so_the_writer_can_add_a_field():
    """The contract says unknown keys are ignored. Without that, the web route
    could not add a field without a coordinated deploy of both services."""
    from adapter.agent import language_from_metadata

    chosen = language_from_metadata(
        '{"v":1,"language":"hi","greeting":"hello","nested":{"a":1}}', "en"
    )
    assert chosen.language == "hi"
    assert chosen.source == "room_metadata"


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        (None, "no_metadata"),
        ("", "no_metadata"),
        ("   \n", "no_metadata"),
        ("not json at all", "not_json"),
        ('{"v":1,"language":"ar"', "not_json"),
        ("[]", "not_an_object"),
        ('"ar"', "not_an_object"),
        ("7", "not_an_object"),
        ("null", "not_an_object"),
        ('{"v":1}', "no_language_key"),
        ('{"v":1,"language":null}', "no_language_key"),
        ('{"v":1,"language":"fr"}', "unsupported_language"),
        ('{"v":1,"language":"EN"}', "unsupported_language"),
        ('{"v":1,"language":""}', "unsupported_language"),
        ('{"v":1,"language":"en-GB"}', "unsupported_language"),
        ('{"v":1,"language":1}', "unsupported_language"),
        ('{"v":2,"language":"ar"}', "unsupported_version"),
        ('{"v":"1","language":"ar"}', "unsupported_version"),
        ('{"v":null,"language":"ar"}', "unsupported_version"),
    ],
)
def test_every_bad_metadata_falls_back_and_says_which_failure_it_was(metadata, reason):
    """The reason is the point, not just the fallback. A hosted call in the
    wrong language is a support question, and the only way to answer it after
    the fact is an event that distinguishes "the web route sent nothing" from
    "the web route sent something this build cannot read"."""
    from adapter.agent import language_from_metadata

    chosen = language_from_metadata(metadata, "en")
    assert chosen.language == "en"
    assert chosen.source == "worker_default"
    assert chosen.reason == reason


def test_the_fallback_is_the_workers_own_language_not_english():
    """`en` is the default default, which makes it easy to write a fallback
    that only looks right. An operator who sets LANGUAGE=hi and gets no usable
    metadata must get Hindi."""
    from adapter.agent import language_from_metadata

    assert language_from_metadata(None, "hi").language == "hi"
    assert language_from_metadata("rubbish", "ar").language == "ar"
    assert language_from_metadata('{"v":9}', "ar").language == "ar"


def test_a_missing_version_is_read_as_version_one():
    """The one place leniency is deliberate, and the asymmetry is the design.

    An absent `v` cannot be a FUTURE contract - only a v1 writer who left out a
    constant - so rejecting it would turn a harmless omission into a
    wrong-language call for a client we are not in the room with. A `v` that is
    present and not 1 is refused, because then `language` may not mean what it
    means here.
    """
    from adapter.agent import language_from_metadata

    chosen = language_from_metadata('{"language":"ar"}', "en")
    assert chosen.language == "ar"
    assert chosen.source == "room_metadata"

    assert (
        language_from_metadata('{"v":2,"language":"ar"}', "en").reason
        == "unsupported_version"
    )


def test_the_reason_never_carries_the_metadata_itself():
    """The string is written by another service. A diagnostic that quotes it
    back would put a foreign service's free text on the emitted event stream,
    which is the one thing `events.CLEAR_EVENTS` classifies against."""
    from adapter.agent import language_from_metadata

    secret = "MUST-NOT-APPEAR-abc123"
    chosen = language_from_metadata(f'{{"v":1,"language":"{secret}"}}', "en")
    assert secret not in chosen.reason
    assert secret not in chosen.language


class _FakeJobRoom:
    def __init__(self, metadata):
        self.metadata = metadata


class _FakeJob:
    def __init__(self, room):
        self.room = room


class _FakeCtx:
    """Only the two attributes `job_room_metadata` and the cap timer read."""

    def __init__(self, job=None):
        self.job = job
        self.shutdown_calls: list[str] = []

    def shutdown(self, reason: str = "user requested") -> None:
        self.shutdown_calls.append(reason)


def test_the_metadata_comes_off_the_jobs_room_not_the_connected_one():
    from adapter.agent import job_room_metadata

    ctx = _FakeCtx(_FakeJob(_FakeJobRoom('{"v":1,"language":"ar"}')))
    assert job_room_metadata(ctx) == '{"v":1,"language":"ar"}'


@pytest.mark.parametrize(
    "ctx",
    [
        _FakeCtx(None),
        _FakeCtx(_FakeJob(None)),
        _FakeCtx(_FakeJob(_FakeJobRoom(""))),
        _FakeCtx(_FakeJob(_FakeJobRoom(None))),
        _FakeCtx(_FakeJob(_FakeJobRoom(123))),
    ],
)
def test_a_job_with_no_readable_metadata_reads_as_none(ctx):
    """The console runs a mock job, so this is the laptop demo's path and it
    must behave exactly as it did before per-call language existed: no
    metadata, worker default, nothing raised."""
    from adapter.agent import job_room_metadata, language_from_metadata

    assert job_room_metadata(ctx) == ""
    assert language_from_metadata(job_room_metadata(ctx), "en").source == (
        "worker_default"
    )


# --- the per-call duration cap --------------------------------------------


def test_no_cap_is_configured_by_default():
    """Zero is off, and off has to arm nothing at all: the laptop demo and the
    console must not acquire a timer they never asked for."""
    from adapter.agent import start_call_duration_cap

    log, records = _capturing_log()
    ctx = _FakeCtx()
    assert start_call_duration_cap(ctx, log, 0) is None
    assert start_call_duration_cap(ctx, log, -5) is None
    assert [record["event"] for record in records] == []
    assert ctx.shutdown_calls == []


async def test_arming_the_cap_is_visible_before_it_fires():
    """A cap that is configured but never reached leaves no trace otherwise, so
    an env var that failed to reach the container looks identical to a call
    that simply ended early. The armed event is what distinguishes them."""
    from adapter.agent import start_call_duration_cap

    log, records = _capturing_log()
    task = start_call_duration_cap(_FakeCtx(), log, 30)
    assert task is not None
    try:
        armed = [r for r in records if r["event"] == "call_duration_cap_armed"]
        assert [r["limit_seconds"] for r in armed] == [30]
    finally:
        task.cancel()


async def test_the_cap_shuts_the_call_down_and_says_so_first():
    """One real second, on the real clock, because the mechanism under test IS
    a sleep. The event must precede the shutdown: `ctx.shutdown` runs the
    callback that closes the log, so an event emitted afterwards is an event
    nobody receives, and the audit would show a call that stopped for no
    reason.
    """
    from adapter.agent import start_call_duration_cap

    log, records = _capturing_log()

    seen_at_shutdown: list[list[str]] = []

    class _RecordingCtx(_FakeCtx):
        def shutdown(self, reason: str = "user requested") -> None:
            seen_at_shutdown.append([r["event"] for r in records])
            super().shutdown(reason)

    ctx = _RecordingCtx()
    task = start_call_duration_cap(ctx, log, 1)
    assert task is not None
    await asyncio.wait_for(task, timeout=10)

    fired = [r for r in records if r["event"] == "call_duration_cap"]
    assert [r["limit_seconds"] for r in fired] == [1]
    assert fired[0]["action"] == "shutdown"

    assert len(ctx.shutdown_calls) == 1
    # The reason reaches the framework's own shutdown record, so it has to say
    # what happened rather than leaving "user requested" to imply the visitor
    # hung up.
    assert "cap" in ctx.shutdown_calls[0] and "1" in ctx.shutdown_calls[0]

    assert seen_at_shutdown and "call_duration_cap" in seen_at_shutdown[0]


@pytest.mark.parametrize("let_it_start", [False, True])
async def test_a_cancelled_cap_never_shuts_anything_down(let_it_start):
    """The reason `_shutdown` cancels it. A call that ends on its own must not
    leave a timer behind that fires into a closed session, and cancelling has
    to be silent: a `call_duration_cap` event on a call that was not capped
    would misreport why it ended.

    Both parameters are needed and the second is the one that matters. Cancel
    before the loop has scheduled the task and the coroutine never runs at all,
    so nothing inside it is under test - a mutation that swallowed
    `CancelledError` and shut the call down anyway survived a version of this
    test that only did that. `let_it_start=True` yields first, so the
    cancellation lands where it does in production: inside the sleep, on a call
    that has been running.
    """
    from adapter.agent import start_call_duration_cap

    log, records = _capturing_log()
    ctx = _FakeCtx()
    task = start_call_duration_cap(ctx, log, 1)
    assert task is not None

    if let_it_start:
        # One loop turn is enough to get from "created" to "awaiting sleep".
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()

    # Past the deadline it would have fired at.
    await asyncio.sleep(1.2)
    assert ctx.shutdown_calls == []
    assert [r["event"] for r in records if r["event"] == "call_duration_cap"] == []


# --- the transport credentials the FRAMEWORK reads ------------------------
#
# `load_settings()` reads agent/.env, `cli.run_app` reads os.environ, and the
# half that works hid the half that did not: `connect` dispatched no job, logged
# nothing after "HTTP server listening", and the room it was supposed to join
# had only the hosted worker's agent in it - which reads exactly like a working
# local run until you check the identity.


TRANSPORT_NAMES = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


@pytest.fixture
def clean_transport_env():
    """Snapshot and restore the three names, whatever the test leaves behind.

    `monkeypatch.delenv(..., raising=False)` is NOT enough and the difference
    is silent: when the name is already absent it deletes nothing, so it
    records nothing, so the value `export_transport_env` then writes straight
    into `os.environ` survives the test. That leak is what turned an unrelated
    credential-redaction test in tests/test_config.py red, several files later,
    with an error that pointed at neither test.
    """
    saved = {name: os.environ.get(name) for name in TRANSPORT_NAMES}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_the_three_transport_names_reach_the_environment(
    monkeypatch, clean_transport_env
):
    from adapter import agent as adapter_agent

    for name in TRANSPORT_NAMES:
        os.environ.pop(name, None)
    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="wss://from-the-env-file",
            livekit_api_key="key-from-file",
            livekit_api_secret="secret-from-file",
        ),
    )

    assert adapter_agent.export_transport_env(["connect", "--room", "r"]) == [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]
    assert os.environ["LIVEKIT_URL"] == "wss://from-the-env-file"
    assert os.environ["LIVEKIT_API_KEY"] == "key-from-file"
    assert os.environ["LIVEKIT_API_SECRET"] == "secret-from-file"


def test_an_environment_value_already_set_is_never_overwritten(
    monkeypatch, clean_transport_env
):
    """The hosted deploy sets these as Railway service variables and must be
    untouched. A local `.env` left over in an image would otherwise quietly
    point production at somebody's laptop project."""
    from adapter import agent as adapter_agent

    os.environ["LIVEKIT_URL"] = "wss://the-platform-set-this"
    os.environ["LIVEKIT_API_KEY"] = "platform-key"
    os.environ.pop("LIVEKIT_API_SECRET", None)
    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="wss://from-the-env-file",
            livekit_api_key="key-from-file",
            livekit_api_secret="secret-from-file",
        ),
    )

    # Only the one that was genuinely absent is filled in.
    assert adapter_agent.export_transport_env(["start"]) == ["LIVEKIT_API_SECRET"]
    assert os.environ["LIVEKIT_URL"] == "wss://the-platform-set-this"
    assert os.environ["LIVEKIT_API_KEY"] == "platform-key"
    assert os.environ["LIVEKIT_API_SECRET"] == "secret-from-file"


def test_a_non_connecting_command_exports_nothing_and_loads_nothing(monkeypatch):
    """Console mode dials nothing, so it must not start reading settings any
    earlier than it does today - the `load_settings` here explodes to prove it
    is never called."""
    from adapter import agent as adapter_agent

    def explode() -> None:
        raise AssertionError("settings were loaded for a non-connecting command")

    monkeypatch.setattr(adapter_agent, "load_settings", explode)
    assert adapter_agent.export_transport_env(["console"]) == []
    assert adapter_agent.export_transport_env([]) == []


def test_an_empty_environment_value_counts_as_unset(monkeypatch, clean_transport_env):
    """`config._resolve` already treats an empty process value as absent and
    falls back to the file. This has to agree, or `LIVEKIT_URL=` in a shell
    would leave the framework with an empty url and the silent `connect`
    failure back."""
    from adapter import agent as adapter_agent

    os.environ["LIVEKIT_URL"] = ""
    os.environ.pop("LIVEKIT_API_KEY", None)
    os.environ.pop("LIVEKIT_API_SECRET", None)
    monkeypatch.setattr(
        adapter_agent,
        "load_settings",
        lambda: make_settings(
            livekit_url="wss://from-the-env-file",
            livekit_api_key="k",
            livekit_api_secret="s",
        ),
    )

    assert "LIVEKIT_URL" in adapter_agent.export_transport_env(["dev"])
    assert os.environ["LIVEKIT_URL"] == "wss://from-the-env-file"


# --- ending the call ------------------------------------------------------
#
# The client asked for this after their first hosted call: nothing ended a call
# but the buyer closing the tab, so a client who said goodbye heard silence.
# The order is the whole of the feature - a close that races the audio is a
# hang-up, not a goodbye.


def _closing_agent(monkeypatch, **session_kwargs):
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    closed: list[str] = []

    async def close_call(reason: str) -> None:
        closed.append(reason)

    agent = AmbassadorAgent(
        settings=make_settings(language="en"), log=log, close_call=close_call
    )
    session = _attach(monkeypatch, agent, **session_kwargs)
    return agent, session, buf, closed, log


async def test_the_farewell_is_spoken_and_awaited_before_the_call_ends(monkeypatch):
    agent, session, buf, closed, log = _closing_agent(monkeypatch)

    await agent.say_farewell_and_close("buyer_farewell")

    assert len(session.said) == 1
    # The authored copy, not something composed on the way out.
    assert "thank you" in session.said[0].text.lower()
    # Interruptible, unlike the disclosure: the buyer may still have something
    # to say, and hanging up mid-sentence is worse than the silence this
    # replaces.
    assert session.said[0].allow_interruptions is True
    # The close waited for the audio to actually play.
    assert session.handles[0].waited is True
    assert closed == ["buyer_farewell"]


async def test_call_ended_is_emitted_before_the_close_is_asked_for(monkeypatch):
    """`ctx.shutdown` runs the callback that seals the audit, so `call_ended`
    has to be on the stream before the close is requested or the seal and the
    reason race each other."""
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    order: list[str] = []

    async def close_call(reason: str) -> None:
        order.append("closed")

    agent = AmbassadorAgent(
        settings=make_settings(language="en"), log=log, close_call=close_call
    )
    _attach(monkeypatch, agent)

    await agent.say_farewell_and_close("buyer_farewell")

    await log.aclose()
    events = [e["event"] for e in json_lines(buf)]
    assert "call_ended" in events
    assert order == ["closed"]
    # And nothing was emitted after the close was requested.
    assert events[-1] == "call_ended"


async def test_the_duration_cap_still_hangs_up_when_talked_over(monkeypatch):
    """The asymmetry that matters, stated as a test.

    A buyer who talks over their OWN goodbye was not finished asking, and that
    cancels the close - a request they can withdraw. The duration cap is not a
    request: it bounds spend on a public URL, so talking over it must not
    extend the call. The audit still records that it was talked over.
    """
    agent, session, buf, closed, log = _closing_agent(
        monkeypatch, interrupt_farewell=True
    )

    await agent.say_farewell_and_close("duration_cap")

    assert closed == ["duration_cap"]
    await log.aclose()
    events = [e["event"] for e in json_lines(buf)]
    assert "farewell_interrupted" in events
    assert "call_ended" in events


async def test_only_one_close_per_call(monkeypatch):
    """Two paths can ask - the buyer's goodbye and the duration cap - and the
    buyer must not hear the farewell twice."""
    agent, session, buf, closed, log = _closing_agent(monkeypatch)

    await agent.say_farewell_and_close("buyer_farewell")
    await agent.say_farewell_and_close("duration_cap")

    assert len(session.said) == 1
    assert closed == ["buyer_farewell"]


async def test_the_duration_cap_uses_the_same_farewell(monkeypatch):
    agent, session, buf, closed, log = _closing_agent(monkeypatch)

    await agent.say_farewell_and_close("duration_cap")

    assert len(session.said) == 1
    assert closed == ["duration_cap"]
    await log.aclose()
    reasons = [e["reason"] for e in json_lines(buf) if e["event"] == "call_ended"]
    assert reasons == ["duration_cap"]


async def test_a_buyer_who_disconnects_gets_no_speech(monkeypatch):
    """Nothing to say and nobody to say it to - but the call is still recorded
    as ended, and closed, so a farewell cannot start into an empty room."""
    agent, session, buf, closed, log = _closing_agent(monkeypatch)

    agent.note_buyer_left()

    assert session.said == []
    # And no farewell afterwards.
    await agent.say_farewell_and_close("buyer_farewell")
    assert session.said == []

    await log.aclose()
    reasons = [e["reason"] for e in json_lines(buf) if e["event"] == "call_ended"]
    assert reasons == ["buyer_left"]


async def test_the_model_never_gets_a_turn_the_buyer_ended():
    """The defect the first live run found, as a test.

    The first version detected the goodbye in `on_user_turn_completed` and
    called `session.interrupt()`. Nothing was speaking yet, so the interrupt
    was a no-op, the model then generated its own "Thank you for your time.
    Have a pleasant day." - and the buyer heard TWO farewells, the model's and
    the authored one. Taking the turn away from the model is the only version
    that cannot do that, so the assertion is about the model never being
    called, not about an interrupt being requested.
    """
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    chunks = await run_llm_node(agent, user_ctx("Thanks, that is all. Goodbye."))

    text = spoken(chunks)
    assert "ambassador can pick this up" in text
    assert "Anything else?" not in text
    # The model was never asked, so there is nothing to interrupt.
    assert spy.chat_ctxs == []
    await log.aclose()
    events = [e["event"] for e in json_lines(buf)]
    assert "llm_request" not in events


async def test_the_close_is_armed_by_the_farewell_turn_not_fired_by_it():
    """Firing when the copy is handed over would end the call while the
    farewell is still in the TTS pipeline - a hang-up, not a goodbye."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    await run_llm_node(agent, user_ctx("Goodbye."))

    assert agent._closing_turn == agent._tracker.turn_index
    assert agent._closing is False
    await log.aclose()
    assert "call_ended" not in buf.getvalue()


async def test_an_ordinary_turn_arms_nothing():
    agent, log, buf, spy = make_agent([HealthyStream(["A studio is AED 985,000. "])])

    await run_llm_node(agent, user_ctx("What does a studio at Skyrise cost?"))

    assert agent._closing_turn is None
    assert len(spy.chat_ctxs) == 1


async def test_a_goodbye_turn_costs_the_buyer_no_policy_attempt():
    """The farewell is checked BEFORE the confirmation policies read the
    utterance, and that ordering is load-bearing for a small reason: the
    policies count the buyer's attempts, and a goodbye is not an attempt at
    anything. Reading it would spend one of the three the recognition policy
    allows before it hands over."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    await run_llm_node(agent, user_ctx("Thanks, that is all. Goodbye."))
    await log.aclose()

    # The per-turn policy readings, not the once-per-session capability lines.
    events = [e["event"] for e in json_lines(buf)]
    for policy_event in (
        "budget_confirmation",
        "budget_confirmation_spoken",
        "project_confirmation",
        "recognition_escalation",
    ):
        assert policy_event not in events, policy_event
    # And the recognition policy's attempt counter is untouched, so a goodbye
    # cannot spend one of the three it allows before handing over.
    assert agent._recognition.consecutive == 0


async def test_the_farewell_copy_is_validated_at_construction(monkeypatch):
    """Copy that fails our own guardrails is a defect in the copy, so it is
    caught in front of whoever started the process - the same rule the
    confirmation terminal lines already follow."""
    log = EventLog("sess_test", stream=StringIO(), verbose=False)

    class _RejectsOnlyTheFarewell:
        """Scoped to the farewell, or the confirmation terminal lines fail
        first and the test proves nothing about this copy."""

        def __init__(self, *args, **kwargs) -> None:
            pass

        def compose(self, raw: str) -> str:
            if "ambassador can pick this up" in raw:
                raise ValueError("an ungrounded figure")
            return raw

    with pytest.raises(RuntimeError, match="farewell copy"):
        AmbassadorAgent(
            settings=make_settings(language="en"),
            log=log,
            guard_factory=_RejectsOnlyTheFarewell,
        )


async def test_an_invalidated_preemptive_generation_does_not_hand_the_goodbye_back():
    """The second defect the local runs found, and the reason the farewell is
    checked above the observed-turn gate.

    `preemptive_generation` runs `llm_node` on the PARTIAL. When the final
    transcript is not equivalent the framework cancels that generation and
    calls `llm_node` again - and with the check below the gate, the second call
    fell through to the model. Measured live: the authored farewell was
    cancelled, the model's own "Have a pleasant day." played in its place, and
    the call still ended. The buyer got a goodbye nobody authored.
    """
    agent, log, buf, spy = make_agent(
        [HealthyStream(["Anything else? "]), HealthyStream(["Anything else? "])]
    )

    # The partial is not a farewell, so the first pass runs the model.
    ctx = user_ctx("Thanks")
    await run_llm_node(agent, ctx)
    assert len(spy.chat_ctxs) == 1

    # The final is, and the framework asks again.
    message = lk_llm.ChatMessage(role="user", content=["Thanks, that is all."])
    await agent.on_user_turn_completed(ctx, message)
    chunks = await run_llm_node(agent, user_ctx("Thanks, that is all."))

    assert "ambassador can pick this up" in spoken(chunks)
    # And the second pass did NOT go to the model.
    assert len(spy.chat_ctxs) == 1
    assert agent._closing_turn == agent._tracker.turn_index


async def test_the_audit_records_what_the_buyer_heard_on_the_last_turn():
    """A farewell turn runs no model, so `generated_sentences` is empty by
    design - the same as the other deterministic lines. The copy therefore has
    to reach `spoken_chunks`, or the record for the final turn of every polite
    call claims nothing was said."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    await run_llm_node(agent, user_ctx("Thanks, that is all."))

    chunks = agent._tracker.spoken_chunks
    assert len(chunks) == 1
    assert "ambassador can pick this up" in chunks[0].text
    assert chunks[0].completed is True
    await log.aclose()
    assert "farewell_spoken" in buf.getvalue()


async def test_a_farewell_the_buyer_talked_over_is_audited_as_incomplete():
    """`mark_interrupted` flips the chunk, and the same signal cancels the
    close - one fact, two consequences, and they must not disagree."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    await run_llm_node(agent, user_ctx("Thanks, that is all."))
    agent._tracker.mark_interrupted()

    assert agent._tracker.spoken_chunks[-1].completed is False


# --- the hosted goodbye that did not end the call -------------------------
#
# A real client, first hosted call: "Jane does the warm farewell but the call
# didn't auto-end." The log had six ordinary model turns, no farewell_spoken,
# and call_ended reason=buyer_left when they pressed the button. Two defects,
# and the tests below are one each.


async def test_a_near_miss_is_recorded_without_the_buyers_words():
    """The tuning signal the hosted call did not leave behind. The utterance is
    redacted on that stream, so what has to survive is the SHAPE of the miss."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    await run_llm_node(agent, user_ctx("Thanks Jane that was really helpful, goodbye"))
    await log.aclose()

    events = [e for e in json_lines(buf) if e["event"] == "farewell_candidate"]
    assert len(events) == 1
    assert events[0]["unexplained"] == 1
    assert events[0]["named_ambassador"] is True
    # The words themselves are the buyer's and do not go on this stream.
    assert "helpful" not in buf.getvalue()


async def test_the_ambassadors_name_no_longer_costs_a_goodbye():
    """The likeliest single reason a real goodbye missed: people say the name
    they were introduced to."""
    agent, log, buf, spy = make_agent([HealthyStream(["Anything else? "])])

    chunks = await run_llm_node(agent, user_ctx("Thanks Jane, that is all, goodbye"))

    assert "ambassador can pick this up" in spoken(chunks)
    assert spy.chat_ctxs == []


async def test_a_near_miss_the_model_answered_with_a_goodbye_ends_the_call():
    """The hybrid. The buyer's turn carried a closing phrase but not cleanly
    enough for the strict rule, so the model got the turn - and answered with a
    goodbye of its own. Two independent readings agree, so the call ends on the
    goodbye the buyer already heard, and nothing further is spoken."""
    agent, log, buf, spy = make_agent(
        [HealthyStream(["Thank you for your time. ", "Goodbye. "])]
    )

    await preemptive_turn(
        agent,
        partial="Thanks Jane that was really",
        final="Thanks Jane that was really helpful, goodbye",
    )
    await log.aclose()

    events = [e for e in json_lines(buf)]
    reasons = [e["reason"] for e in events if e["event"] == "call_ended"]
    assert reasons == ["agent_farewell"]
    # The model's farewell WAS the farewell; the authored copy is not added.
    assert "farewell_spoken" not in [e["event"] for e in events]


async def test_a_near_miss_the_model_answered_normally_does_not_end_the_call():
    """The other half, and the one that keeps the no-false-hang-up rule: a
    goodbye inside a question is a question, and the model answering it is the
    proof."""
    agent, log, buf, spy = make_agent(
        [HealthyStream(["The down payment is AED 197,000. "])]
    )

    await preemptive_turn(
        agent,
        partial="before we say goodbye",
        final="before we say goodbye, what is the down payment",
    )
    await log.aclose()

    assert "call_ended" not in buf.getvalue()


async def test_the_model_cannot_end_a_call_the_buyer_did_not_close():
    """The model's own goodbye is never enough on its own. Without a closing
    phrase in the BUYER's turn there is no candidate, so a chatty sign-off in
    the middle of a call cannot hang up on anyone."""
    agent, log, buf, spy = make_agent(
        [HealthyStream(["Thank you for your time. ", "Goodbye. "])]
    )

    await preemptive_turn(
        agent, partial="what does a studio", final="what does a studio cost"
    )
    await log.aclose()

    assert "call_ended" not in buf.getvalue()
    assert agent._candidate_turn is None


async def test_ending_a_call_deletes_the_room_not_just_the_job():
    """The second half of the hosted defect. `ctx.shutdown()` ends the JOB; a
    room with a browser still in it lives on, because `departureTimeout` only
    starts once the LAST participant has gone - so the page, which ends on
    ROOM_DELETED, saw nothing and the client pressed the button themselves.
    The local runs could not show it: buyer_publisher disconnects itself."""
    from adapter import agent as adapter_agent

    deleted: list[str] = []
    shutdowns: list[str] = []

    class _Room:
        name = "room-1"

    class _RoomService:
        async def delete_room(self, request):  # noqa: ANN001
            deleted.append(request.room)

    class _Api:
        room = _RoomService()

    class _Ctx:
        room = _Room()
        api = _Api()

        def shutdown(self, reason: str) -> None:
            shutdowns.append(reason)

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    await adapter_agent.end_call(_Ctx(), log, "buyer_farewell")
    await log.aclose()

    assert deleted == ["room-1"]
    assert shutdowns == ["buyer_farewell"]
    events = [e["event"] for e in json_lines(buf)]
    # Deleted BEFORE the shutdown, because deletion is the signal the other
    # participant is waiting for.
    assert events.index("room_deleted") < len(events)


async def test_a_room_that_will_not_delete_still_ends_the_call():
    """A transient API error must not leave the call up with nobody able to end
    it. The failure is recorded and the job still shuts down."""
    from adapter import agent as adapter_agent

    shutdowns: list[str] = []

    class _RoomService:
        async def delete_room(self, request):  # noqa: ANN001
            raise RuntimeError("boom")

    class _Api:
        room = _RoomService()

    class _Room:
        name = "room-1"

    class _Ctx:
        room = _Room()
        api = _Api()

        def shutdown(self, reason: str) -> None:
            shutdowns.append(reason)

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    await adapter_agent.end_call(_Ctx(), log, "duration_cap")
    await log.aclose()

    assert shutdowns == ["duration_cap"]
    events = [e["event"] for e in json_lines(buf)]
    assert "room_delete_failed" in events
    # The error text goes to the process log, not the redacted stream.
    assert "boom" not in buf.getvalue()


async def test_one_goodbye_is_recorded_once_however_often_it_is_asked_for():
    """A live run recorded two `farewell_spoken` for one goodbye. The farewell
    check sits above the observed-turn gate so an invalidated preemptive
    generation cannot hand the goodbye back to the model - which means
    `llm_node` can legitimately ask for the same farewell twice. The buyer
    heard one; the audit has to say one."""
    agent, log, buf, spy = make_agent(
        [HealthyStream(["Anything else? "]), HealthyStream(["Anything else? "])]
    )

    await run_llm_node(agent, user_ctx("Thanks, that is all. Goodbye."))
    await run_llm_node(agent, user_ctx("Thanks, that is all. Goodbye."))
    await log.aclose()

    spoken_events = [e for e in json_lines(buf) if e["event"] == "farewell_spoken"]
    assert len(spoken_events) == 1
    assert len(agent._tracker.spoken_chunks) == 1


# --- a goodbye the buyer had to say twice ---------------------------------
#
# Both hosted near misses were INTERRUPTED - the client talked over Jane's
# reply - so the hybrid never reached the seal. A near miss the buyer then
# repeats is a buyer trying to leave and being cut off, and two closing phrases
# in two consecutive buyer turns is a stronger signal than either alone.
#
# It needs a bound, and the bound is not a guess: two NOT_ENDINGS entries are
# themselves candidates ("before we say goodbye, what about the payment plan"
# reads unexplained=6, "that is all I need for now, what about Skyrise" reads
# 4), while the real tail misses read 1-2. A pair rule with no threshold would
# hang up on a repeated question.


async def test_a_near_miss_repeated_after_an_interruption_ends_the_call():
    agent, log, buf, spy = make_agent(
        [HealthyStream(["Anything else? "]), HealthyStream(["Anything else? "])]
    )

    # A tails-only near miss that SURVIVES the widening - "really" is not a
    # courtesy and will not become one, so this stays a near miss and the pair
    # rule is what has to close it. ("that's it from my end", the shape the
    # client used, now closes strictly on its own; the widening subsumed it,
    # which is why this rule is for the residual rather than for that call.)
    handle = await preemptive_turn(
        agent,
        partial="Thanks Jane that was really",
        final="Thanks Jane that was really helpful, goodbye",
        interrupted=True,
    )
    assert handle.interrupted is True

    # Turn two: they say it again.
    await preemptive_turn(
        agent,
        partial="Thanks Jane that was really",
        final="Thanks Jane that was really helpful, goodbye",
    )
    await log.aclose()

    reasons = [e["reason"] for e in json_lines(buf) if e["event"] == "call_ended"]
    assert reasons == ["buyer_farewell_repeated"]


async def test_a_repeated_question_is_not_a_repeated_goodbye():
    """The regression this rule could cause, asserted rather than hoped for. A
    buyer who twice embeds a closing phrase in a question is asking twice."""
    agent, log, buf, spy = make_agent(
        [HealthyStream(["The plan is 40/60. "]), HealthyStream(["The plan is 40/60. "])]
    )

    for _ in range(2):
        await preemptive_turn(
            agent,
            partial="before we say goodbye",
            final="before we say goodbye, what about the payment plan",
            interrupted=True,
        )
    await log.aclose()

    assert "call_ended" not in buf.getvalue()


async def test_the_tail_threshold_is_what_separates_the_two_pair_cases():
    """The boundary itself, so a later widening cannot move it by accident.

    A tail miss is a missing courtesy; anything wider is an utterance carrying
    content, which on this path means a question. The real misses read 1-2 and
    the question-shaped NOT_ENDINGS candidates read 4 and 6, so the line sits
    between - and both sides of it are asserted here rather than in a comment.
    """
    from adapter.agent import AmbassadorAgent as _Agent
    from ambassador.ambassadors import load_ambassadors
    from ambassador.farewell import load_farewells, read_farewell

    farewells = load_farewells()
    names = frozenset(
        load_ambassadors().name_for(language) for language in load_ambassadors().named
    )

    tail = read_farewell(
        "Thanks Jane that was really helpful, goodbye", farewells, "en", names=names
    )
    question = read_farewell(
        "before we say goodbye, what about the payment plan", farewells, "en"
    )
    assert tail.unexplained <= _Agent._TAIL_MISS
    assert question.unexplained > _Agent._TAIL_MISS
