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
from collections.abc import AsyncIterable, Callable
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
    MetricsCollectedEvent,
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
from livekit.agents import stt
from livekit.agents.metrics import EOUMetrics
from livekit.agents.types import NOT_GIVEN
from livekit.agents.utils import is_given
from livekit.agents.voice import SpeechHandle
from livekit.plugins import fishaudio, silero

from ambassador.budget import BudgetPolicy, load_currency_vocabulary
from ambassador.guardrails.prohibited import languages_covered, load_patterns
from ambassador.inventory import (
    build_allowed_figures,
    load_inventory,
    serialise_for_prompt,
)
from ambassador.prompts import (
    NAIVE_PROMPT,
    REGENERATION_INSTRUCTION,
    build_ambassador_prompt,
)
from ambassador.verbalise import load_spoken_forms

from .brief import BriefExtractor
from .config import Settings, load_settings
from .confirmations import UnspeakableConfirmation, load_confirmations
from .confirmations import compose as compose_confirmation
from .disclosure import load_disclosures, resolve_opening
from .events import EventLog, TurnTracker
from .events_bridge import EventsBridge, bridge_from_env
from .interception import FALLBACK_COPY, SentenceGuard, _Sink, guarded_stream
from .lexicon import load_lexicon, respell_stream
from .llm_openrouter import CONN_OPTIONS, BuiltLLM, UsageFrame, build_llm
from .stt_factory import build_stt, describe
from .tts_pool import connection_state, reprewarm

logger = logging.getLogger("ambassador.agent")

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

        # ADR-011. The policy speaks INSTEAD of the model when it has a
        # question, which is what makes it deterministic: prompt constraint 8
        # asked the model to confirm, and ADR-007 is explicit that asking
        # reduces violations without eliminating them. Loaded before the
        # prompt is built because constraint 8 depends on the answer: for a
        # language whose copy nobody has authored the policy is off, and the
        # model must be told to confirm budgets itself - the old wording -
        # or nobody asks at all.
        confirmations = load_confirmations()
        self._confirmations = confirmations
        self._budget_vocabulary = load_currency_vocabulary()
        self._budget = BudgetPolicy(self._budget_vocabulary, settings.language)
        self._budget_policy_runs = confirmations.covers(settings.language)
        self._budget_last_action = "none"
        # The turn the policy last read, so a tool call splitting one buyer
        # turn across two llm_node invocations cannot make the same utterance
        # count as two of the buyer's three attempts.
        self._budget_observed_turn: int | None = None

        instructions = (
            NAIVE_PROMPT
            if settings.prompt_mode == "naive"
            else build_ambassador_prompt(
                serialise_for_prompt(projects),
                settings.language,
                system_confirms_budget=self._budget_policy_runs,
            )
        )
        super().__init__(instructions=instructions)

        patterns = load_patterns()
        covered = languages_covered(patterns)
        self._guard = SentenceGuard(
            language=settings.language,
            allowed=build_allowed_figures(projects),
            patterns=patterns,
            forms=load_spoken_forms(),
            mode=settings.guardrail_mode,
        )
        # Stated, not assumed. Every pattern runs against every sentence, so a
        # reply that code-switches into English is checked whatever language
        # the call is in - but a violation written wholly in Arabic or
        # Devanagari script matches nothing until someone authors patterns for
        # it. Without this line the record shows a guardrail that looks equally
        # strong in all three languages.
        log.emit(
            "prohibited_coverage",
            languages=sorted(covered),
            call_language=settings.language,
            native_patterns=settings.language in covered,
            pattern_count=len(patterns),
        )
        if settings.language not in covered:
            logger.warning(
                "no prohibited-language patterns authored for %r: only English "
                "and English code-switched violations are caught",
                settings.language,
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
        log.emit(
            "budget_policy",
            active=self._budget_policy_runs,
            call_language=settings.language,
            copy_languages=sorted(self._confirmations.languages_covered()),
            currency_languages=sorted(self._budget_vocabulary.languages_covered()),
            conversion_available=self._budget_vocabulary.rate.usable,
        )
        if not self._budget_policy_runs:
            logger.warning(
                "budget confirmation policy is OFF for %r: no confirmation copy "
                "authored, so a budget in this language is acted on unconfirmed",
                settings.language,
            )

        # Read once at construction: fixed data for the life of the process,
        # and a malformed file should fail in front of the operator rather
        # than on the first sentence of a call.
        self._lexicon = load_lexicon()
        log.emit(
            "lexicon",
            languages=sorted(self._lexicon.languages_covered()),
            call_language=settings.language,
            applied=settings.language in self._lexicon.languages_covered(),
        )

        self._turn_index = 0
        self._tracker: TurnTracker | None = None
        self._speech_handle: SpeechHandle | None = None
        self._pending: _PendingTurn | None = None
        # Set once teardown has begun, so the barge-in hook does not open a Fish
        # socket for a call that is already over (see `_reprewarm_tts`).
        self._closing = False

        # Resolved here rather than in on_enter, so a language with no
        # native-authored disclosure fails while the operator is still looking
        # at a terminal. Deciding this after the room connects means finding
        # out that the agent has nothing to disclose while a buyer is already
        # on the line.
        self._opening, self._opening_language = resolve_opening(
            load_disclosures(),
            settings.language,
            allow_uncertified=settings.allow_uncertified_language,
        )

    @property
    def brief_extractor(self) -> BriefExtractor:
        return self._brief

    @property
    def tracker(self) -> TurnTracker | None:
        return self._tracker

    # -- the opening disclosure -------------------------------------------

    async def on_enter(self) -> None:
        """Speak the AI disclosure before the model gets to say anything.

        `allow_interruptions=False` is the point of doing this here rather than
        leaving it to the prompt: the disclosure completes even if the buyer
        talks over it (docs/04-), which a model-generated greeting cannot
        promise. It goes into the chat context so the model can see it has
        already greeted the buyer and does not do it twice.
        """
        degraded = self._opening_language != self._settings.language
        self._log.emit(
            "disclosure",
            language=self._opening_language,
            requested_language=self._settings.language,
            # Loud on purpose. An English disclosure on an Arabic call is a
            # deliberate, documented degradation, and the record must never let
            # it be mistaken for a certified Arabic opening.
            uncertified_fallback=degraded,
        )
        if degraded:
            logger.warning(
                "opening in %r: no native-authored disclosure for %r",
                self._opening_language,
                self._settings.language,
            )
        self.session.say(self._opening, allow_interruptions=False)

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

    def note_metrics(self, metrics: Any) -> None:
        """Route the framework's own measurements onto the turn (issue #7).

        Only `EOUMetrics` is taken. Everything else on `metrics_collected` is
        either already recorded from closer to the source (LLM TTFT and usage
        come off the streaming transport, TTS first audio off `tts_node`) or is
        not a per-turn fact.

        Endpointing is the one stage the adapter cannot time itself: it happens
        before `on_user_turn_completed`, which is where the tracker's clock
        starts, and its anchor is a VAD timestamp inside the recognition loop.
        The framework already computes it, so this reads it rather than
        rebuilding it (AGENTS.md: do not build what the framework provides).

        The event lands while this turn's tracker is still open - LiveKit emits
        it at the end of `_user_turn_completed_task`, after
        `on_user_turn_completed` has opened the tracker and before the agent
        ever reaches "listening". A turn with no tracker is a turn with no
        record to attribute the measurement to, so it is dropped rather than
        filed against the wrong index.
        """
        if not isinstance(metrics, EOUMetrics):
            return
        tracker = self._tracker
        if tracker is None:
            logger.debug("end-of-utterance metrics arrived with no open turn")
            return
        tracker.record_endpointing(
            end_of_utterance=metrics.end_of_utterance_delay,
            transcription=metrics.transcription_delay,
            turn_committed=metrics.on_user_turn_completed_delay,
        )

    # -- issue #18: the Fish connection pool across a barge-in -------------

    def _active_tts(self) -> Any | None:
        """The TTS the session is actually synthesising through, or None.

        Same resolution order as `llm_node` uses for the LLM: the agent's own
        override if one was set, otherwise the session's. Outside a running
        session there is neither, and that is not an error - the console and the
        tests reach this with no activity attached.
        """
        if is_given(self.tts):
            return self.tts
        try:
            return self.session.tts
        except RuntimeError:
            return None

    def _reprewarm_tts(self, turn: int) -> None:
        """Restore a spare Fish socket after a confirmed barge-in (issue #18).

        The cancelled synthesis discarded its own socket, which is correct, and
        the pool will not open another on its own. Without this the next
        utterance opens a fresh TCP + TLS + WebSocket upgrade inline; with it
        the connect happens during the silence while the buyer is still talking.

        Skipped during teardown: a socket opened here would be closed again by
        `tts.aclose()` moments later, and the connect would race the shutdown.
        """
        if self._closing:
            return
        tts = self._active_tts()
        if tts is None:
            return
        self._log.emit("tts_pool_reprewarm", turn=turn, outcome=reprewarm(tts))

    def _note_tts_connection(self, tracker: TurnTracker | None) -> None:
        """Issue #18's measurement: did this turn's audio come off a pooled
        socket, or did the buyer wait for a handshake first?

        Emitted at first audio, once the pool has already handed the socket to
        this synthesis. `reused: false` on a turn that follows an `interrupted`
        event is the defect; `reused: true` on the same turn is the fix.
        """
        state = connection_state(self._active_tts())
        if state is None:
            return
        self._log.emit(
            "tts_connection",
            turn=None if tracker is None else tracker.turn_index,
            **state,
        )

    def note_speech_handle(self, handle: SpeechHandle) -> None:
        """Keep the handle this turn's audio belongs to.

        It is read when the handle RESOLVES, not when it is stored and not at
        the "listening" transition: `finish_turn` explains why those are too
        early. One handle spans a tool-using turn's two generations, so this is
        still one handle per buyer utterance.
        """
        self._speech_handle = handle

    # -- ADR-011: the deterministic budget confirmation --------------------

    def _budget_confirmation(self, tracker: TurnTracker) -> str | None:
        """Copy to speak instead of running this turn, or None to carry on.

        Returns None whenever the policy has nothing to say, which is every
        turn after the budget is settled and every turn in a language whose
        confirmation copy nobody has authored. Speaking English into an Arabic
        call would be worse than not asking.

        ANY failure fails CLOSED: the buyer goes to a human, and the model
        never gets the turn unconfirmed. The catch is deliberately Exception,
        not a curated list: the first version curated (ValueError) and failed
        open; the rework widened it (KeyError, IndexError) and a second
        review found AttributeError and TypeError still escaping into a
        silent turn - which the observe-once gate then converted into a
        model turn on the same-turn retry. The direction is decided once,
        here: a crash anywhere in the confirmation machinery is a handover,
        never a model turn and never silence.
        """
        if not self._budget_policy_runs:
            return None
        if self._budget_observed_turn == tracker.turn_index:
            # The second half of a tool-using turn. The policy already read
            # this utterance; reading it again would burn a second attempt on
            # one reply.
            return None
        self._budget_observed_turn = tracker.turn_index
        try:
            return self._composed_confirmation(tracker)
        except Exception:
            logger.error("budget confirmation failed; handing over", exc_info=True)
            self._budget.abandon()
            self._route_to_human("budget confirmation failure")
            self._budget_last_action = "give_up"
            self._log.emit(
                "budget_confirmation",
                turn=tracker.turn_index,
                action="give_up",
                currency=None,
                hands_over=True,
            )
            # Safe to speak verbatim: the loader guarantees give_up is
            # non-empty for any language the policy runs in, and slot-free.
            return self._confirmations.line(self._settings.language, "give_up")

    def _composed_confirmation(self, tracker: TurnTracker) -> str | None:
        """The happy path of `_budget_confirmation`; raises rather than
        recovers, because the caller owns the failure direction."""
        decision = self._budget.observe(tracker.buyer_utterance)
        if not decision.speaks:
            if decision.currency is not None:
                self._log.emit(
                    "budget_settled",
                    turn=tracker.turn_index,
                    currency=decision.currency,
                )
            return None

        template = self._confirmations.line(self._settings.language, decision.action)
        if decision.mention is not None:
            # Every mention-bearing decision goes through compose(), slot or
            # no slot: gating on a literal "{amount}" check let a template
            # with a MISSPELLED slot skip composition and reach TTS braces
            # and all.
            text = compose_confirmation(
                template,
                echoed=decision.mention.surface,
                # The transcript the mention came from, NOT this turn's: on a
                # re-ask the current turn is the reply that failed to answer,
                # and the number is not in it.
                said=decision.mention.utterance,
            )
        elif "{" in template:
            raise UnspeakableConfirmation(
                f"{decision.action!r} copy carries a slot and the decision "
                "has nothing to fill it with"
            )
        else:
            text = template
        if not text.strip():
            raise UnspeakableConfirmation(
                f"no confirmation copy composed for {decision.action!r}"
            )

        if decision.hands_over:
            # Speaking "let me put you through" without notifying anyone is
            # the anti-pattern escalate_to_human's own docstring names. This
            # is the same routing the tool performs.
            self._route_to_human(f"budget confirmation: {decision.action}")

        self._budget_last_action = decision.action
        self._log.emit(
            "budget_confirmation",
            turn=tracker.turn_index,
            action=decision.action,
            currency=decision.currency,
            hands_over=decision.hands_over,
        )
        return text

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

        async def open_stream(
            extra_instruction: str | None = None,
        ) -> AsyncIterable[Any]:
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
            return await open_stream(REGENERATION_INSTRUCTION.format(detail=detail))

        # ADR-011, before the model is given the turn at all. Asking the model
        # to confirm is what constraint 8 already did; this takes the turn away
        # from it, so the question cannot be skipped, reworded or answered on
        # the buyer's behalf.
        confirmation = self._budget_confirmation(tracker)
        if confirmation is not None:
            tracker.record_confirmation(confirmation, self._budget_last_action)
            yield (
                confirmation
                if confirmation.endswith((" ", "\n"))
                else (confirmation + " ")
            )
            return

        # Emitted here, after the confirmation check, because it claims a
        # model call is about to happen. On a confirmation turn none does, and
        # a request line with no llm_ttft or llm_usage behind it makes the
        # audit over-count model calls.
        self._log.emit(
            "llm_request",
            turn=tracker.turn_index,
            tools=[
                getattr(t, "name", None) or getattr(t, "__name__", "?") for t in tools
            ],
            tool_choice=str(tool_choice),
        )

        sink = _tracker_sink(
            tracker, on_fallback=lambda text: self._speak_fallback(tracker, text)
        )
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
        self._speak_fallback(tracker, composed, "llm_failure")
        return [composed if composed.endswith((" ", "\n")) else composed + " "]

    # -- TTS timing (the Fish first-byte measurement) ---------------------

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        # Respelling happens here and nowhere earlier. It destroys the word the
        # same way verbalisation destroys the digits, so everything that has to
        # read real words - the transcript, the audit, the ambassador view -
        # has already read them by this point, and only the synthesiser sees
        # "bin-GAH-tee". A language with no authored respellings gets the
        # stream back untouched, buffering included.
        spoken = respell_stream(text, self._lexicon, self._settings.language)
        tracker = self._tracker
        first = True
        async for frame in Agent.default.tts_node(self, spoken, model_settings):
            if first:
                first = False
                if tracker is not None:
                    tracker.mark_tts_first_audio()
                self._note_tts_connection(tracker)
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
        3. The buyer asks for a computation that is not listed in the
           inventory. Never work it out yourself.
        4. The buyer asks about unit availability.
        5. The buyer wants to negotiate.
        6. The buyer raises contractual or legal terms (SPA, escrow, Oqood,
           refunds, visas, mortgages).
        7. The buyer explicitly asks for a person.
        8. The buyer complains or is distressed.
        9. Recognition has failed three times.

        Call it in the same turn as your spoken reply; keep speaking normally.

        Args:
            reason: Why the escalation is needed, in a few words.
        """
        self._route_to_human(reason)
        return (
            "An ambassador has been notified and will pick this up. "
            "Tell the buyer a colleague will confirm this directly."
        )

    def _speak_fallback(
        self, tracker: TurnTracker, text: str, reason: str = "guardrail"
    ) -> None:
        """Record the composed fallback AND actually notify a human.

        `data/fallbacks.yaml` calls this copy "the line that hands the buyer to
        a human" and the English text is "let me put you through to one of our
        ambassadors". Recording the chunk and emitting the event is not putting
        anyone through, which left the buyer waiting for a call nobody booked -
        the anti-pattern `_route_to_human`'s own docstring names, found on this
        path by the eval harness (F2) after PR #20's review found the same shape
        on the budget policy's hands_over decision.

        This is the one path where the model definitively did NOT escalate: the
        fallback only speaks because the model's own output was unusable, either
        blocked twice or never produced at all. So there is nothing else to
        notify anybody, and the promise is the system's to keep.

        The BRIDGE is deliberately not routed. Its copy ("let me be precise
        about that figure rather than guess") promises nothing, the turn carries
        on and the buyer still gets an answer; paging a human there would fire
        on every recovered sentence. Route the lines that promise, and only
        those - if the bridge copy is ever reworded to offer a colleague, it
        joins this path.

        A model that DID call `escalate_to_human` and then had its sentence
        blocked twice routes twice, with different reasons. That is two real
        decisions and the audit shows both; routing once too often is the safe
        direction here, and routing zero times is the defect being fixed.
        """
        tracker.record_fallback(text, reason)
        self._route_to_human(f"composed fallback: {reason}")

    def _route_to_human(self, reason: str) -> None:
        """Notify a human ambassador. The one escalation mechanism, shared by
        the model's tool call, the budget policy's terminal actions and the
        composed fallback - a hands_over decision that only wrote a log field
        left the buyer hearing "let me put you through" with nobody put through.

        At most one handover per turn, however many of those paths ask for one.
        The request is always recorded; only the notification is deduplicated.
        `TurnTracker.record_escalation` owns that rule and says why.
        """
        tracker = self._tracker
        if tracker is None:
            # No open turn to deduplicate within, and nothing to attribute the
            # request to either.
            self._log.emit(
                "tool_call", tool="escalate_to_human", args={"reason": reason}
            )
            self._log.emit("escalation", reason=reason, routed_to="human_ambassador")
            return
        tracker.record_tool("escalate_to_human", reason=reason)
        # STUB: the CRM/routing write is a console log behind this interface.
        tracker.record_escalation(reason)

    @function_tool
    async def offer_booking(self, context: RunContext, slot_description: str) -> str:
        """Offer the buyer a viewing or a call with an ambassador.

        Args:
            slot_description: The slot in the buyer's own words, for read-back.
        """
        if self._tracker is not None:
            self._tracker.record_tool("offer_booking", slot=slot_description)
        else:
            self._log.emit(
                "tool_call", tool="offer_booking", args={"slot": slot_description}
            )
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
                # And the cancelled synthesis took the pooled Fish socket with
                # it (issue #18). This is the moment the framework itself
                # settles that the interruption was real, so it is the earliest
                # honest point to open a replacement.
                self._reprewarm_tts(pending.tracker.turn_index)
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
        # Every caller of this is teardown, and sealing an interrupted turn
        # here must not re-prewarm a pool that is about to be closed.
        self._closing = True
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


def _tracker_sink(
    tracker: TurnTracker | None, *, on_fallback: Callable[[str], None]
) -> _Sink:
    """Wire one turn's tracker into the interception hook's callbacks.

    `on_fallback` is required rather than defaulted to
    `tracker.record_fallback`: that default is exactly the defect eval F2 found
    (the copy promises a human and records a chunk), and a default here would
    let the routing be dropped by omission at a new call site.
    """
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
        on_fallback=on_fallback,
    )


async def shutdown_session(
    *,
    agent: AmbassadorAgent,
    log: EventLog,
    llm: BuiltLLM,
    bridge: EventsBridge | None = None,
    # Whatever `build_stt` selected: Deepgram's streaming node, the
    # whole-utterance OpenRouter one, or nothing in text mode. The annotation
    # named OpenRouterSTT concretely until the factory landed and stopped
    # importing it here; `from __future__ import annotations` kept that a
    # lazy string, so it never raised, it just stopped meaning anything.
    stt_node: stt.STT | None,
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
    # After the last event so the surface sees the session end, before the log
    # closes so the observer is gone by the time the writer stops.
    if bridge is not None:
        await bridge.aclose()
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

    # Started before the first event, so a surface that connects mid-call
    # replays the whole session rather than joining from wherever it arrived.
    # None unless AMBASSADOR_BRIDGE_HANDSHAKE names a path: a listening socket
    # carrying buyer transcripts is not on by default.
    bridge = bridge_from_env(log)
    if bridge is not None:
        await bridge.start()
        log.emit("events_bridge", host="127.0.0.1", port=bridge.port)

    log.emit("session_start", config=settings.redacted())

    agent = AmbassadorAgent(settings=settings, log=log)

    stt_node = build_stt(settings)
    if stt_node is not None:
        log.emit("stt_enabled", **describe(stt_node))
    else:
        # Text mode and the console's typed input both work without a
        # recogniser, which is what kept the day-1 gate provable while
        # OpenRouter rejected audio under a $0.50 balance.
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

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        # The framework's own per-stage measurements. Only end-of-utterance is
        # taken from here (issue #7): it is the one stage that happens before
        # the adapter's clock starts. Synchronous and cheap, like the others.
        agent.note_metrics(ev.metrics)

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        log.emit("session_error", error=str(ev.error))

    async def _shutdown() -> None:
        await shutdown_session(
            agent=agent, log=log, llm=llm, stt_node=stt_node, bridge=bridge
        )

    ctx.add_shutdown_callback(_shutdown)

    await ctx.connect()
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
