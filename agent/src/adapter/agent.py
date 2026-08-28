"""The LiveKit adapter: the thin layer that wraps the pure core into the
framework's hooks (ADR-002, design principle 1).

Everything differentiating lives in `ambassador/`. This file is wiring, and it
proves the three day-1 integration points the architecture depends on
(docs/06- day 1):

  hook 1  text interception between LLM and TTS  -> `llm_node` override,
          delegating to `interception.guarded_stream`
  hook 2  function tools firing mid-turn         -> `@function_tool` methods
  hook 3  post-turn async task                   -> `BriefExtractor.schedule`
          on the framework's `agent_state_changed` event

All three are the framework's own documented extension points. Nothing here
reaches around the framework: the LLM is the OpenAI plugin pointed at
OpenRouter, the TTS is the Fish plugin, VAD is Silero, and the custom STT node
implements the framework's `STT` interface so `StreamAdapter` wraps it.

Run it:

    uv run python -m adapter.agent console --text   # typed input, spoken output
    uv run python -m adapter.agent console          # microphone
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AgentStateChangedEvent,
    ErrorEvent,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    SpeechCreatedEvent,
    WorkerOptions,
    cli,
    function_tool,
    utils,
)
from livekit.agents import (
    llm as lk_llm,
)
from livekit.agents.types import NOT_GIVEN
from livekit.agents.utils import is_given
from livekit.agents.voice import SpeechHandle
from livekit.plugins import fishaudio, silero

from ambassador.guardrails.prohibited import load_patterns
from ambassador.inventory import (
    build_allowed_figures,
    load_inventory,
    serialise_for_prompt,
)
from ambassador.prompts import NAIVE_PROMPT, build_ambassador_prompt
from ambassador.verbalise import load_spoken_forms

from .brief import BriefExtractor
from .config import Settings, load_settings
from .events import EventLog, TurnTracker
from .interception import FALLBACK_COPY, SentenceGuard, _Sink, guarded_stream
from .llm_openrouter import CONN_OPTIONS, BuiltLLM, UsageFrame, build_llm
from .stt_openrouter import OpenRouterSTT

logger = logging.getLogger("ambassador.agent")

_REGENERATION_INSTRUCTION = (
    "Your previous reply was blocked before it was spoken because it failed a "
    "grounding check: {detail}. Every figure you state must appear verbatim in "
    "the INVENTORY block. Reply again, using only figures from the inventory, "
    "or say you cannot confirm the figure and offer a human ambassador."
)


@dataclass
class _PendingTurn:
    """A turn that has left the conversation but not yet its own audio.

    The framework's "listening" transition is not the end of the turn (see
    `AmbassadorAgent.finish_turn`), so the tracker is parked here alongside the
    speech handle it is waiting on and the live chat context the post-turn
    brief will be extracted from.
    """

    tracker: TurnTracker
    handle: SpeechHandle | None
    chat_ctx: lk_llm.ChatContext | None
    sealed: bool = False


def _unresolved(handle: SpeechHandle | None) -> bool:
    """True only when a handle exists and never finished, which is the one case
    where whether the audio played out is genuinely unknown."""
    return handle is not None and not handle.done()


class AmbassadorAgent(Agent):
    def __init__(self, *, settings: Settings, log: EventLog) -> None:
        projects = load_inventory()
        self._projects = projects
        self._project_ids = [p.id for p in projects]
        self._settings = settings
        self._log = log

        instructions = (
            NAIVE_PROMPT
            if settings.prompt_mode == "naive"
            else build_ambassador_prompt(serialise_for_prompt(projects), settings.language)
        )
        super().__init__(instructions=instructions)

        self._guard = SentenceGuard(
            language=settings.language,
            allowed=build_allowed_figures(projects),
            patterns=load_patterns(),
            forms=load_spoken_forms(),
            mode=settings.guardrail_mode,
        )
        self._brief = BriefExtractor(
            api_key=settings.openrouter_api_key,
            model=settings.brief_model,
            base_url=settings.llm_base_url,
            project_ids=self._project_ids,
            language=settings.language,
            on_event=log.emit,
            thinking_disabled=settings.thinking_disabled,
        )
        self._turn_index = 0
        self._tracker: TurnTracker | None = None
        self._speech_handle: SpeechHandle | None = None
        self._pending: _PendingTurn | None = None

    @property
    def brief_extractor(self) -> BriefExtractor:
        return self._brief

    @property
    def tracker(self) -> TurnTracker | None:
        return self._tracker

    # -- turn lifecycle ---------------------------------------------------

    def _start_tracker(self, buyer_utterance: str) -> TurnTracker:
        self._turn_index += 1
        self._tracker = TurnTracker(
            self._log,
            turn_index=self._turn_index,
            buyer_utterance=buyer_utterance,
            language=self._settings.language,
            model=self._settings.llm_model,
            prompt_mode=self._settings.prompt_mode,
            guardrail_mode=self._settings.guardrail_mode,
            inventory_version=f"{len(self._projects)}-records",
        )
        self._log.emit("user_turn", turn=self._turn_index, text=buyer_utterance)
        return self._tracker

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        # Fires on the STT path. Text-driven turns (console --text, the eval
        # harness, session.run) never reach this hook, so llm_node opens a
        # tracker lazily instead of relying on it.
        self._start_tracker(new_message.text_content or "")

    def _ensure_tracker(self, chat_ctx: lk_llm.ChatContext) -> TurnTracker:
        if self._tracker is not None:
            return self._tracker
        last_user = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                last_user = item.text_content or ""
                break
        return self._start_tracker(last_user)

    def note_upstream_status(self, status: int) -> None:
        """A non-2xx the SDK is about to retry. Logged so the latency meter can
        attribute a slow turn to pool congestion rather than to the model."""
        self._log.emit(
            "llm_upstream_error",
            turn=None if self._tracker is None else self._tracker.turn_index,
            status=status,
            note="retried with backoff by the provider SDK",
        )

    def note_usage(self, usage: UsageFrame) -> None:
        """Called by the usage-tapping transport under the LLM plugin."""
        tracker = self._tracker
        if tracker is None:
            return
        tracker.record_usage(
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            cached_tokens=usage["cached_tokens"],
        )

    def note_speech_handle(self, handle: SpeechHandle) -> None:
        """Keep the handle this turn's audio belongs to.

        It is read when the handle RESOLVES, not when it is stored and not at
        the "listening" transition: `finish_turn` explains why those are too
        early. One handle spans a tool-using turn's two generations, so this is
        still one handle per buyer utterance.
        """
        self._speech_handle = handle

    # -- hook 1: interception between LLM and TTS -------------------------

    async def llm_node(
        self,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[Any]:
        # A tool call splits one buyer turn across two llm_node invocations;
        # the tracker spans both, so it is only created when absent.
        tracker = self._ensure_tracker(chat_ctx)
        # The LLM is configured on the session, not the agent, so Agent.llm is
        # NotGiven here; the session's model is what the default node resolves
        # to at runtime.
        activity_llm = self.llm if is_given(self.llm) else self.session.llm
        if not isinstance(activity_llm, lk_llm.LLM):
            raise RuntimeError(
                "the ambassador llm_node requires a streaming LLM, not a realtime model"
            )
        tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
        opened: list[Any] = []
        self._log.emit(
            "llm_request",
            turn=tracker.turn_index,
            tools=[getattr(t, "name", None) or getattr(t, "__name__", "?") for t in tools],
            tool_choice=str(tool_choice),
        )

        async def open_stream(extra_instruction: str | None = None) -> AsyncIterable[Any]:
            ctx = chat_ctx
            if extra_instruction:
                ctx = chat_ctx.copy()
                ctx.add_message(role="system", content=extra_instruction)
            stream = activity_llm.chat(
                chat_ctx=ctx,
                tools=tools,
                tool_choice=tool_choice,
                # Explicit, because the default (max_retry=3, retry_interval=2.0)
                # stacks on top of the SDK's own retries under the plugin.
                conn_options=CONN_OPTIONS,
            )
            opened.append(stream)
            return stream

        async def regenerate(detail: str) -> AsyncIterable[Any]:
            return await open_stream(_REGENERATION_INSTRUCTION.format(detail=detail))

        sink = _tracker_sink(tracker)
        spoke_anything = False
        try:
            source = await open_stream()
            async for out in guarded_stream(
                source, guard=self._guard, sink=sink, regenerate=regenerate
            ):
                if isinstance(out, str):
                    spoke_anything = True
                yield out
        except (asyncio.CancelledError, GeneratorExit):
            # Both derive from BaseException, so `except Exception` below would
            # miss them anyway. Named explicitly because it is a decision, not
            # an accident: barge-in and shutdown cancel this generator, that is
            # not a failure, and speaking over it would be wrong.
            raise
        except Exception as exc:
            # Retries are exhausted and LLMStream has re-raised through
            # __anext__. Nothing has reached TTS from this point on, and
            # AGENTS.md is absolute: a turn never ends in silence.
            for text in self._terminal_failure_speech(tracker, exc, spoke_anything):
                yield text
        finally:
            for stream in opened:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        # Not fatal, but a stream left open leaks a connection
                        # and is worth seeing without turning DEBUG on.
                        logger.warning("llm stream close failed", exc_info=True)

    def _terminal_failure_speech(
        self, tracker: TurnTracker, exc: BaseException, spoke_anything: bool
    ) -> list[str]:
        """Composed speech for an LLM failure the retries could not absorb.

        The fallback copy, not the bridge: the model produced nothing usable,
        so there is no half-answer to bridge away from, and the fallback is the
        line that hands the buyer to a human. `spoken_before` on the event says
        whether the buyer had already heard part of a reply.
        """
        self._log.emit(
            "llm_failure",
            turn=tracker.turn_index,
            error=type(exc).__name__,
            detail=str(exc)[:200],
            spoken_before=spoke_anything,
        )
        raw = FALLBACK_COPY[self._settings.language]
        try:
            composed = self._guard.compose(raw)
        except AssertionError:  # pragma: no cover - a defect in the copy itself
            logger.warning("fallback copy failed its own guardrails", exc_info=True)
            composed = raw
        tracker.record_fallback(composed, "llm_failure")
        return [composed if composed.endswith((" ", "\n")) else composed + " "]

    # -- TTS timing (the Fish first-byte measurement) ---------------------

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        tracker = self._tracker
        first = True
        async for frame in Agent.default.tts_node(self, text, model_settings):
            if first:
                first = False
                if tracker is not None:
                    tracker.mark_tts_first_audio()
            yield frame

    # -- hook 2: function tools firing mid-turn ---------------------------

    @function_tool
    async def escalate_to_human(self, context: RunContext, reason: str) -> str:
        """Notify a human ambassador so they pick this buyer up.

        Call this tool - do not merely mention a colleague in your reply -
        whenever ANY of these is true. Saying "I can connect you with an
        ambassador" without calling the tool means no one is actually notified.

        1. The buyer asks about a project that is not in the inventory.
        2. The buyer asks the price of a branded collection (price on enquiry).
        3. The buyer asks about unit availability.
        4. The buyer wants to negotiate.
        5. The buyer raises contractual or legal terms (SPA, escrow, Oqood,
           refunds, visas, mortgages).
        6. The buyer explicitly asks for a person.
        7. The buyer complains or is distressed.
        8. Recognition has failed three times.

        Call it in the same turn as your spoken reply; keep speaking normally.

        Args:
            reason: Why the escalation is needed, in a few words.
        """
        if self._tracker is not None:
            self._tracker.record_tool("escalate_to_human", reason=reason)
        else:
            self._log.emit("tool_call", tool="escalate_to_human", args={"reason": reason})
        # STUB: the CRM/routing write is a console log behind this interface.
        self._log.emit("escalation", reason=reason, routed_to="human_ambassador")
        return (
            "An ambassador has been notified and will pick this up. "
            "Tell the buyer a colleague will confirm this directly."
        )

    @function_tool
    async def offer_booking(self, context: RunContext, slot_description: str) -> str:
        """Offer the buyer a viewing or a call with an ambassador.

        Args:
            slot_description: The slot in the buyer's own words, for read-back.
        """
        if self._tracker is not None:
            self._tracker.record_tool("offer_booking", slot=slot_description)
        else:
            self._log.emit("tool_call", tool="offer_booking", args={"slot": slot_description})
        # STUB: spoken read-back only; no calendar API in the POC (docs/06-).
        self._log.emit("booking_offered", slot=slot_description)
        return (
            f"Slot noted as: {slot_description}. "
            "Read it back to the buyer and ask them to confirm."
        )

    # -- hook 3: post-turn async task -------------------------------------

    def finish_turn(self, chat_ctx: lk_llm.ChatContext) -> None:
        """Park the turn against its speech handle. Sealing waits for that.

        "listening" is not proof the turn ended, and reading
        `handle.interrupted` here is wrong on the main barge-in path. The
        framework defaults to `resume_false_interruption=True` with a 2.0s
        `false_interruption_timeout` (livekit/agents/voice/turn.py,
        `_INTERRUPTION_DEFAULTS`). With those on, a VAD barge-in takes the
        pause branch of `_interrupt_by_audio_activity`
        (livekit/agents/voice/agent_activity.py): it pauses the audio output
        and moves the agent to "listening" WITHOUT touching the speech handle.
        `interrupt()` is called later and only if the interruption is confirmed
        real; a false interruption resumes playout and the handle completes
        uninterrupted. So at this moment `handle.interrupted` is False on every
        real barge-in, and sealing here would claim every chunk played out.

        That pause-and-resume behaviour is wanted - it is why a cough does not
        kill the reply - so the audit adapts to it rather than the reverse.
        This method only parks the turn; `_seal` runs from the handle's own
        done callback, the one moment both facts are settled.
        """
        pending = self._pending
        if pending is not None and not pending.sealed:
            if self._tracker is None or self._tracker is pending.tracker:
                # Still the same turn. One speech can pass through "listening"
                # more than once - a false interruption pauses, transitions,
                # then resumes - so take the newer context and let the handle
                # say when the turn is actually over.
                pending.chat_ctx = chat_ctx
                return
            # A new turn opened while the old speech never resolved. Seal the
            # old one rather than losing it, on the context IT was parked with:
            # `chat_ctx` here belongs to the NEW turn, and extracting it under
            # the old turn's index would file a brief against an utterance that
            # turn never heard.
            self._seal(pending, audit_incomplete=_unresolved(pending.handle))

        tracker = self._tracker
        if tracker is None:
            return
        self._tracker = None
        handle, self._speech_handle = self._speech_handle, None
        pending = _PendingTurn(tracker=tracker, handle=handle, chat_ctx=chat_ctx)
        self._pending = pending
        if handle is None or handle.done():
            # Text-driven turns have no handle at all, and a handle that has
            # already resolved will never call back.
            self._seal(pending)
            return
        handle.add_done_callback(self._on_speech_handle_done)

    def _on_speech_handle_done(self, handle: SpeechHandle) -> None:
        """The framework's own signal that this turn's audio is over, whether it
        played out or was cut off. Scheduled on the loop, never on the hot path."""
        pending = self._pending
        if pending is None or pending.handle is not handle:
            return
        self._seal(pending)

    def _seal(self, pending: _PendingTurn, *, audit_incomplete: bool = False) -> None:
        """Write the turn record and fire brief extraction without awaiting it."""
        if pending.sealed:
            return
        pending.sealed = True
        handle = pending.handle
        if handle is not None:
            handle.remove_done_callback(self._on_speech_handle_done)
            if handle.interrupted:
                # Confirmed barge-in: the last chunk handed to TTS did not
                # finish playing, so the audit must not claim it did (docs/04-).
                pending.tracker.mark_interrupted()
        pending.tracker.finish(audit_incomplete=audit_incomplete)
        if self._pending is pending:
            self._pending = None
        if pending.chat_ctx is None:
            return
        transcript = [
            {"role": item.role, "content": item.text_content or ""}
            for item in pending.chat_ctx.items
            if getattr(item, "type", None) == "message"
            and item.role in ("user", "assistant")
            and (item.text_content or "").strip()
        ]
        if transcript:
            self._brief.schedule(transcript, pending.tracker.turn_index)

    def finalise_pending_turn(self) -> None:
        """Close the books at teardown.

        A session that goes down mid-speech leaves a handle that will never
        resolve. The turn is sealed on what is known - marked interrupted if
        the interrupt did land - and flagged `audit_incomplete` so nobody reads
        completion out of a record that never saw the end of its own audio.
        """
        pending = self._pending
        if pending is not None and not pending.sealed:
            self._seal(pending, audit_incomplete=_unresolved(pending.handle))
            return
        tracker = self._tracker
        if tracker is None:
            return
        # Teardown before the turn ever reached "listening": there is no
        # settled transcript to extract a brief from, but the record itself is
        # still worth keeping, and it is incomplete by construction.
        self._tracker = None
        handle, self._speech_handle = self._speech_handle, None
        self._seal(
            _PendingTurn(tracker=tracker, handle=handle, chat_ctx=None),
            audit_incomplete=True,
        )

    async def on_exit(self) -> None:
        """The framework's own end-of-agent hook: `AgentSession.aclose` drains
        the activity and awaits this. Anything still waiting on a speech handle
        is sealed here rather than vanishing with the session."""
        self.finalise_pending_turn()


def _tracker_sink(tracker: TurnTracker | None) -> _Sink:
    if tracker is None:
        return _Sink()
    return _Sink(
        on_decision=lambda d: tracker.record_guardrail(
            raw=d.raw,
            outcome=d.outcome,
            guardrail_ms=d.elapsed_ms,
            spoken=d.spoken,
            violation=d.violation,
        ),
        on_first_content=tracker.mark_llm_ttft,
        on_first_sentence=tracker.mark_first_sentence,
        on_regeneration=tracker.record_regeneration,
        on_bridge=tracker.record_bridge,
        on_fallback=tracker.record_fallback,
    )


async def shutdown_session(
    *,
    agent: AmbassadorAgent,
    log: EventLog,
    llm: BuiltLLM,
    stt_node: OpenRouterSTT | None,
) -> None:
    """Close everything the session owns, in order.

    Module level rather than a closure inside `entrypoint` so the lifecycle is
    testable without a live room (tests/test_agent.py). The LLM's httpx client
    is closed here because the plugin will not: it was handed the client, so it
    sets `_owns_client = False`.
    """
    # Before the drain, or a brief scheduled by the last turn is never awaited.
    # Idempotent with `AmbassadorAgent.on_exit`, which fires first when the
    # session closes cleanly; this covers a shutdown that skips it.
    agent.finalise_pending_turn()
    await agent.brief_extractor.drain()
    await agent.brief_extractor.aclose()
    if stt_node is not None:
        await stt_node.aclose()
    await llm.aclose()
    log.emit("session_end", turns=len(log.turns))
    await log.aclose()


def prewarm(proc: JobProcess) -> None:
    """Load Silero once per worker process, not once per call."""
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    settings = load_settings()
    log = EventLog(session_id=utils.shortuuid("sess_"))

    missing = settings.missing_for_voice()
    if missing:
        raise RuntimeError(
            "missing credentials for the voice path: " + ", ".join(missing)
        )

    log.emit("session_start", config=settings.redacted())

    agent = AmbassadorAgent(settings=settings, log=log)

    stt_node = None
    if settings.stt_enabled:
        stt_node = OpenRouterSTT(
            api_key=settings.openrouter_api_key,
            model=settings.stt_model(settings.language),
            language=settings.language,
        )
        log.emit("stt_enabled", model=stt_node.model, provider=stt_node.provider)
    else:
        # OpenRouter rejects audio under a $0.50 balance (402); text mode and
        # the console's typed input both work without it.
        log.emit("stt_disabled", reason="STT_ENABLED is not set")

    tts = fishaudio.TTS(
        api_key=settings.fish_api_key,
        model=settings.fish_tts_model,
        voice_id=settings.voice_id(settings.language) or fishaudio.tts.DEFAULT_VOICE_ID,
        latency_mode="low",
    )

    llm = build_llm(settings, agent.note_usage, agent.note_upstream_status)

    session: AgentSession = AgentSession(
        stt=stt_node,
        vad=ctx.proc.userdata.get("vad") or silero.VAD.load(),
        llm=llm.llm,
        tts=tts,
    )

    @session.on("speech_created")
    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        # The handle carries the framework's interruption state and its own
        # completion. `finish_turn` parks the turn against it and the audit is
        # sealed from the handle's done callback, not from this event.
        agent.note_speech_handle(ev.speech_handle)

    @session.on("agent_state_changed")
    def _on_state(ev: AgentStateChangedEvent) -> None:
        # "listening" is the framework's own end-of-conversation-turn signal,
        # and the only one that survives a tool call: a running tool holds the
        # agent in "thinking", so one buyer utterance still yields exactly one
        # TurnRecord even when the turn spans two generations. Sealing on
        # `conversation_item_added` instead splits a tool-using turn in two.
        # It is NOT end-of-audio, though - a false interruption passes through
        # here mid-speech - so `finish_turn` only parks the turn. The handler
        # stays synchronous and cheap.
        if ev.new_state == "listening":
            agent.finish_turn(session.history)

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        log.emit("session_error", error=str(ev.error))

    async def _shutdown() -> None:
        await shutdown_session(agent=agent, log=log, llm=llm, stt_node=stt_node)

    ctx.add_shutdown_callback(_shutdown)

    await ctx.connect()
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
