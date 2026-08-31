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
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

import httpx
import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents import Agent, APIConnectOptions  # noqa: E402
from livekit.agents import llm as lk_llm  # noqa: E402
from livekit.agents.metrics import EOUMetrics, TTSMetrics  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN  # noqa: E402
from livekit.agents.utils import ConnectionPool  # noqa: E402
from livekit.agents.voice import SpeechHandle  # noqa: E402

from adapter.agent import AmbassadorAgent, shutdown_session  # noqa: E402
from adapter.config import Settings  # noqa: E402
from adapter.confirmations import ConfirmationCopy  # noqa: E402
from adapter.disclosure import UncertifiedLanguageError  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from adapter.interception import BRIDGE_COPY, FALLBACK_COPY  # noqa: E402
from adapter.llm_openrouter import build_llm, clamp_retry_after  # noqa: E402

FAKE_KEY = "test-key-not-a-real-credential"
GROUNDED = "985,000"


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
    )
    base.update(overrides)
    return Settings(**base)


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
    assert json.loads(await asyncio.wait_for(reader.readline(), 2))["event"] == "session_end"
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


class _FakeSession:
    def __init__(self) -> None:
        self.said: list[_RecordedSay] = []

    def say(self, text, *, allow_interruptions=NOT_GIVEN, **kwargs):
        self.said.append(_RecordedSay(text, allow_interruptions))
        return None


def _attach(monkeypatch, agent: AmbassadorAgent) -> _FakeSession:
    """A real AgentSession needs a room, a worker and live credentials."""
    session = _FakeSession()
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
    """"Not dirhams" settled AED and went permanently silent. There are two
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
    suppressed = [ln for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"]
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
    assert len([ln for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"]) == 1


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
    assert [ln["turn"] for ln in json_lines(buf) if ln["event"] == "escalation_suppressed"] == [1]
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
    agent._llm = SpyLLM([HealthyStream(["Sorry, could you repeat that? "]) for _ in range(2)])

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
            "en:\n"
            + "".join(f'  {k}: "{v}"\n' for k, v in broken.items()),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=key):
            load_confirmations(bad)
