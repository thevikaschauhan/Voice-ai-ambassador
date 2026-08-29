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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

import httpx
import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents import APIConnectOptions  # noqa: E402
from livekit.agents import llm as lk_llm  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN  # noqa: E402
from livekit.agents.voice import SpeechHandle  # noqa: E402

from adapter.agent import AmbassadorAgent, shutdown_session  # noqa: E402
from adapter.config import Settings  # noqa: E402
from adapter.disclosure import UncertifiedLanguageError  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from adapter.interception import FALLBACK_COPY  # noqa: E402
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
