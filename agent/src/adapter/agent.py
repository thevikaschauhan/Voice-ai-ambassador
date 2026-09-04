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
import contextlib
import functools
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Final, Literal, get_args

from livekit import api, rtc
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
from livekit.plugins import silero

from ambassador.budget import BudgetPolicy, Decision, load_currency_vocabulary
from ambassador.confirmation import ConfirmationCoordinator, Step
from ambassador.contact import ContactPolicy, load_contact_copy
from ambassador.farewell import (
    FarewellReading,
    contains_closing_phrase,
    load_farewells,
    read_farewell,
)
from ambassador.guardrails.prohibited import languages_covered, load_patterns
from ambassador.knowledge import KnowledgeContext
from ambassador.schemas import ContactCapture, KnowledgeUse, LeadSnapshot
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
from ambassador.projects import (
    ProjectDecision,
    ProjectNamePolicy,
    agreement_words,
    build_name_index,
)
from ambassador.ambassadors import load_ambassadors
from ambassador.recognition import RecognitionMonitor, load_noise_words
from ambassador.schemas import Language
from ambassador.verbalise import load_spoken_forms
from evals.runner import Harness

from .brief import BriefExtractor
from .config import (
    Settings,
    load_settings,
    missing_credentials_error,
    worker_refusal,
)
from .confirmations import (
    PROJECT_KEYS,
    RECOGNITION_KEYS,
    UnspeakableConfirmation,
    load_confirmations,
)
from .confirmations import compose as compose_confirmation
from .confirmations import compose_project as compose_project_confirmation
from .disclosure import load_disclosures, resolve_opening
from .events import EventLog, TurnTracker, _now_iso
from .retrieval import KnowledgeRetriever, repository_when_ready
from .events_bridge import EventsBridge, bridge_from_env
from .interception import FALLBACK_COPY, SentenceGuard, _Sink, guarded_stream
from .levels import apply_gain, gain_for
from .analysis import Ask, analysis_ask, finalise_analysis
from .persist import LeadWriter, _failure_code, build_lead_writer
from .lexicon import load_lexicon, respell_stream
from .llm_openrouter import CONN_OPTIONS, BuiltLLM, UsageFrame, build_llm
from .stt_factory import build_stt, describe
from .tts_factory import build_tts
from .tts_factory import describe as describe_tts
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


@dataclass(frozen=True)
class _OwedTurn:
    """Copy one of the ADR-011 policies speaks INSTEAD of running the turn.

    `policy` exists so the audit records the right thing: the three lines are
    recorded by three tracker methods with three event names, because a
    budget echo carries the buyer's own figure and the other two do not.
    """

    text: str
    policy: Literal["budget", "project", "recognition", "farewell", "contact"]
    action: str


def _unresolved(handle: SpeechHandle | None) -> bool:
    """True only when a handle exists and never finished, which is the one case
    where whether the audio played out is genuinely unknown."""
    return handle is not None and not handle.done()


class AmbassadorAgent(Agent):
    def __init__(
        self,
        *,
        settings: Settings,
        log: EventLog,
        guard_factory: Callable[..., SentenceGuard] = SentenceGuard,
        close_call: Callable[[str], Awaitable[None]] | None = None,
        knowledge: KnowledgeRetriever | None = None,
        contact: ContactPolicy | None = None,
    ) -> None:
        projects = load_inventory()
        self._projects = projects
        self._project_ids = [p.id for p in projects]
        self._settings = settings
        self._log = log
        # ADR-019. None until the pool is wired, and None is a working
        # configuration: a call with no knowledge base speaks the
        # inventory figures and nothing else.
        self._knowledge = knowledge
        self._knowledge_context: KnowledgeContext | None = None

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

        # ADR-011's other two triggers, built on the same seam: the project
        # name confirmed when the fuzzy match against inventory is marginal,
        # and the escalation after three consecutive turns nobody could hear.
        # Each has its own copy group, so a language can run one and not the
        # other, and each reports itself off rather than speaking English into
        # a call that is not in English.
        self._name_index = build_name_index(projects)
        self._agreement_words = agreement_words(self._budget_vocabulary)
        self._project = ProjectNamePolicy(
            self._name_index, self._agreement_words, settings.language
        )
        self._project_policy_runs = confirmations.covers(
            settings.language, PROJECT_KEYS
        )

        self._noise_words = load_noise_words()
        self._recognition = RecognitionMonitor(self._noise_words, settings.language)
        self._recognition_policy_runs = confirmations.covers(
            settings.language, RECOGNITION_KEYS
        )

        # Which policy owns which reply is pure core, shared with the eval
        # harness: an ordering rule kept in two places drifts, and this one
        # already shipped two defects as an ordering rule kept in one.
        self._policies = ConfirmationCoordinator(
            budget=self._budget,
            project=self._project,
            recognition=self._recognition,
            budget_runs=self._budget_policy_runs,
            project_runs=self._project_policy_runs,
            recognition_runs=self._recognition_policy_runs,
        )

        # The turn on which the MODEL said goodbye without the call ending -
        # the 08:32Z call did this twice. A courtesy-only reply on the very
        # next turn is the buyer answering that goodbye, and letting them say
        # it again is the defect. One turn only: two turns later the
        # conversation has moved on and "thank you" is an ordinary thank you.
        self._signed_off_turn: int | None = None
        # The reason to close with when a turn armed the close for something
        # other than the buyer's own farewell. Read at the seal.
        self._closing_reason: str | None = None

        # The turn the policies last read, so a tool call splitting one buyer
        # turn across two llm_node invocations cannot make the same utterance
        # count as two of the buyer's three attempts. One gate for all three
        # policies: the reason is the framework's, not any one policy's.
        self._policy_observed_turn: int | None = None

        # The one contact ask (P2-S05), INJECTED rather than built here: the
        # defect this fixes was a correct policy nobody constructed, and a
        # keyword the production entry has to pass is a keyword an ast test can
        # assert it passes. None is the old behaviour - no ask, contact stays
        # `not_asked` - which is what the eval harness and every existing test
        # get.
        self._contact = contact
        # Which step of the ask we are in. The policy's own state cannotanswer  this:
        # `unconfirmed` means BOTH "a read-back is pending" and "the buyer
        # contradicted the number", and those two need opposite handling of the
        # next utterance.
        self._contact_awaiting_reply = False
        self._contact_awaiting_confirmation = False
        # `llm_node` can legitimately run twice for one turn (a preemptive
        # generation invalidated by the final transcript), and reading one
        # reply twice would spend the ask twice and emit two `contact_settled`.
        # So the decision is made once per turn index and replayed.
        self._contact_turn_index: int | None = None
        self._contact_owed: _OwedTurn | None = None

        # Who this ambassador is, by language. Empty is "unnamed" and is a
        # working state, not a gap: the prompt and the disclosure both fall
        # back to the wording that shipped before names existed.
        self._ambassadors = load_ambassadors()
        self._ambassador_name = self._ambassadors.name_for(settings.language)

        instructions = (
            NAIVE_PROMPT
            if settings.prompt_mode == "naive"
            else build_ambassador_prompt(
                serialise_for_prompt(projects),
                settings.language,
                system_confirms_budget=self._budget_policy_runs,
                system_confirms_project=self._project_policy_runs,
                ambassador_name=self._ambassador_name,
            )
        )
        super().__init__(instructions=instructions)

        patterns = load_patterns()
        covered = languages_covered(patterns)
        self._guard = guard_factory(
            language=settings.language,
            allowed=build_allowed_figures(projects),
            patterns=patterns,
            forms=load_spoken_forms(),
            mode=settings.guardrail_mode,
        )
        # ADR-011's terminal lines, composed ONCE, here, in front of whoever
        # started the process.
        #
        # These are slot-free lines spoken verbatim on a failure path, and the
        # first version composed them per turn and caught the failure by
        # speaking the raw string - which is a literal bypass of the one
        # public speech path, on the one path that cannot afford one. Choosing
        # a direction at runtime was the wrong question: copy that fails our
        # own guardrails is a defect in the copy, so it belongs at startup,
        # where the precedent is already set - a language with no authored
        # disclosure refuses to open a call rather than degrading quietly
        # (docs/04-). There is then no runtime guard call here to fail open,
        # and none to fail closed either.
        self._fixed_lines = self._compose_fixed_lines()
        # Stated, not assumed. English patterns apply in every language, so a
        # reply that code-switches into English is checked whatever language
        # the call is in - but a violation written wholly in Arabic or
        # Devanagari script matches nothing until someone authors patterns for
        # it. `covered` is therefore AUTHORSHIP, not protection. Without this
        # line the record shows a guardrail that looks equally strong in all
        # three languages.
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
        log.emit(
            "project_policy",
            active=self._project_policy_runs,
            call_language=settings.language,
            copy_languages=sorted(confirmations.languages_covered(PROJECT_KEYS)),
            agreement_languages=sorted(self._agreement_words.languages_covered()),
            projects_indexed=len(self._name_index.names),
        )
        if not self._project_policy_runs:
            logger.warning(
                "project-name confirmation is OFF for %r: no confirmation copy "
                "authored, so a marginal name match is acted on unconfirmed",
                settings.language,
            )
        log.emit(
            "recognition_policy",
            active=self._recognition_policy_runs,
            call_language=settings.language,
            copy_languages=sorted(confirmations.languages_covered(RECOGNITION_KEYS)),
            noise_languages=sorted(self._noise_words.languages_covered()),
        )
        if not self._recognition_policy_runs:
            logger.warning(
                "failed-recognition escalation is OFF for %r: no escalation copy "
                "authored, so three unheard turns in a row escalate only if the "
                "model calls the tool",
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
        # The turn the stored handle was announced for, so a handle left over
        # from the disclosure - or from a turn that has since sealed - is not
        # mistaken for this turn's generation being replaced.
        self._speech_handle_turn: int | None = None
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
            names=self._ambassadors.names,
        )
        # The name actually SPOKEN, which is the one for the language the
        # opening ends up in. On a degraded call that is the English name
        # inside English copy, not the empty Arabic one.
        self._spoken_ambassador_name = self._ambassadors.name_for(
            self._opening_language
        )

        # The end of the call. Loaded and guard-checked HERE, after
        # `resolve_opening` has settled which language is actually being
        # spoken, so a degraded call validates the line it will really use
        # rather than one for a language it is not speaking.
        self._farewells = load_farewells()
        # Every ambassador's given name counts as a courtesy, not just this
        # call's: a buyer types or says whichever name they saw. They are
        # passed in rather than copied into farewells.yaml, because one name in
        # two files is a name that can disagree with itself.
        self._ambassador_names = frozenset(
            self._ambassadors.name_for(language) for language in self._ambassadors.named
        )
        # Detection is per requested language and may simply be off: a
        # language with no authored closing phrases ends its calls the way it
        # did before this existed, which is the safe direction. The farewell
        # SPEECH is loaded regardless, because the duration cap can end a call
        # the buyer never said goodbye in.
        self._farewell_detects = self._farewells.detects(settings.language)
        self._farewell_line = self._compose_farewell()
        self._close_call = close_call
        # One close per call, whichever path asks for it. A second farewell
        # over the first would be the same defect class as the double
        # handover `record_escalation` exists to prevent.
        self._closing = False
        # Recorded for the durable lead: the reason is set by whichever
        # close path ran, and `buyer_left` is the honest default for a
        # session that simply stopped.
        self._call_end_reason: str = "buyer_left"
        self._started_at = _now_iso()
        # The turn whose audio, once over, ends the call. Set when the farewell
        # is handed the turn; read in `_seal`.
        self._closing_turn: int | None = None
        # A turn whose utterance carried a closing phrase but failed the
        # strict rule. Read at the seal, when the model's own reply is
        # known, never before.
        self._candidate_turn: int | None = None
        # A near miss the buyer was cut off in the middle of, carried forward
        # for exactly one turn. Both hosted near misses were interrupted, so
        # the hybrid never reached the seal and the call ran on.
        self._interrupted_candidate_turn: int | None = None
        self._candidate_is_tail = False
        self._interrupted_candidate_was_tail = False
        # Held so a close cannot be garbage-collected while it waits.
        self._closing_task: asyncio.Task[None] | None = None

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
            # The name in the sentence the buyer is about to hear. Reported
            # beside the language because on a degraded call the two come from
            # different languages, and `session_start`'s name is the one for
            # the language that was ASKED for.
            ambassador_name=self._spoken_ambassador_name,
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
        """The final transcript for this buyer turn.

        Fires on the STT path only. Text-driven turns (console --text, the eval
        harness, session.run) never reach this hook, so `llm_node` opens a
        tracker lazily instead of relying on it.

        AND ON THE VOICE PATH IT FIRES SECOND. `preemptive_generation` is
        enabled by default, so the framework runs `llm_node` on the partial
        transcript first and this hook arrives afterwards with the final one -
        the reverse of the text path. Starting a tracker unconditionally here
        therefore split every real turn in two, which the first live audio run
        measured (#51). When a turn is already open on a partial, the final
        transcript is adopted onto it instead.
        """
        text = new_message.text_content or ""
        tracker = self._tracker
        if tracker is not None and tracker.opened_on_partial and not tracker.adopted:
            tracker.adopt_final_utterance(text)
            self._note_closing_from_final(text, tracker)
            return
        self._start_tracker(text)

    def _note_closing_from_final(self, text: str, tracker: TurnTracker) -> None:
        """Read the FINAL transcript for a closing, after the model has the turn.

        `_deterministic_turn` reads whatever the tracker held when `llm_node`
        ran, and on the voice path that is the PARTIAL. If the framework judges
        the final equivalent it reuses that generation and never calls
        `llm_node` again - so a goodbye that only appears in the final was
        never read at all. That is the likeliest reading of the hosted call
        where six turns went to the model and no candidate was ever recorded.

        The turn is not taken back. The model is already answering, and
        cancelling its speech to insert our own is the double-goodbye this
        policy exists to prevent. What this does is arm the close, so the call
        ends after the reply the buyer is about to hear.
        """
        if self._closing or self._closing_turn == tracker.turn_index:
            return
        if not self._farewell_detects:
            return
        reading = self._read_farewell(text)
        if reading.closes:
            self._closing_turn = tracker.turn_index
        elif (
            reading.courtesy_only
            and self._signed_off_turn is not None
            and tracker.turn_index == self._signed_off_turn + 1
        ):
            # The same rule as `_answers_the_agents_goodbye`, on the transcript
            # that actually carries the whole sentence. On the voice path the
            # partial can be just "Okay" while the final is "Okay. Thank you.",
            # and the 08:32Z call is why this seam is checked at all: a goodbye
            # that only appears in the final was never read.
            self._closing_turn = tracker.turn_index
            self._closing_reason = "agent_farewell"
        elif reading.has_phrase and self._candidate_turn != tracker.turn_index:
            self._note_candidate(tracker.turn_index, reading)

    def knowledge_use(self) -> list[KnowledgeUse]:
        """This call's buffered retrieval records, for the snapshot.

        Empty when nothing was ever retrieved, which is the normal shape of a
        call that asked no knowledge question.
        """
        if self._knowledge is None:
            return []
        return self._knowledge.buffered()

    def _ensure_tracker(self, chat_ctx: lk_llm.ChatContext) -> TurnTracker:
        """The turn `llm_node` is running for, opening one if nothing has yet.

        Opening here marks the tracker `opened_on_partial`: whatever the chat
        context holds is the best transcript available, and on the voice path it
        is a PARTIAL that `on_user_turn_completed` will supersede. That flag is
        what lets the final transcript be adopted rather than start a second
        turn, and it is cleared by adoption so one partial can only ever be
        superseded once.
        """
        if self._tracker is not None:
            return self._tracker
        last_user = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                last_user = item.text_content or ""
                break
        tracker = self._start_tracker(last_user)
        tracker.opened_on_partial = True
        return tracker

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

        A SECOND HANDLE INSIDE ONE TURN IS AN INVALIDATED PREEMPTIVE
        GENERATION. The framework announces a handle for the generation it
        starts on the partial transcript; if the final transcript is not
        equivalent it cancels that one and calls `_generate_reply` again, which
        announces another (agent_activity.py:2321 and :2574, both reaching the
        `speech_created` emit at :1550). On the happy path it reuses the
        preemptive handle and nothing is announced twice. So this is the only
        seam where the adapter can see that a recorded generation was thrown
        away - the equivalence check itself is private - and it is where the
        discarded records come off the turn.
        """
        previous, previous_turn = self._speech_handle, self._speech_handle_turn
        tracker = self._tracker
        self._speech_handle = handle
        self._speech_handle_turn = None if tracker is None else tracker.turn_index
        if tracker is None or previous is None or previous is handle:
            return
        if previous_turn != tracker.turn_index:
            # The opening disclosure announces a handle before any turn exists,
            # and it is still stored when the first turn's generation announces
            # its own. That is not a replacement, and discarding on it would
            # erase the first reply of every call.
            return
        tracker.discard_generation()

    # -- ADR-011: the deterministic confirmation policies ------------------
    #
    # WHICH policy acts on a turn is decided by `ConfirmationCoordinator`,
    # which is pure core and shared with the eval harness. Everything below is
    # the half that needs a framework: composing the copy, speaking it,
    # notifying a human, and recording what happened.
    #
    # The failure direction is decided once, here, and it is CLOSED: a crash
    # anywhere in this machinery hands the buyer to a person, never returns the
    # turn to the model with a confirmation outstanding, and never ends in
    # silence. The catch is deliberately `Exception` rather than a curated
    # list: the first version curated (ValueError) and failed open; the rework
    # widened it (KeyError, IndexError) and a review found AttributeError and
    # TypeError still escaping into a silent turn, which the observe-once gate
    # then converted into a model turn on the same-turn retry.

    def _compose_fixed_lines(self) -> dict[str, str]:
        """Every slot-free terminal line, validated through the guardrails now.

        Raises rather than degrades. A language whose own handover copy fails
        its own guardrails cannot hand over, and finding that out mid-call is
        the whole problem with finding it out lazily.
        """
        keys: list[str] = []
        if self._budget_policy_runs:
            keys.append("give_up")
        if self._project_policy_runs:
            keys.append("project_give_up")
        if self._recognition_policy_runs:
            keys.append("recognition_escalation")
        composed: dict[str, str] = {}
        for key in keys:
            raw = self._confirmations.line(self._settings.language, key)
            if not raw:
                continue
            try:
                composed[key] = self._guard.compose(raw)
            except Exception as exc:
                raise RuntimeError(
                    f"confirmation copy {key!r} for {self._settings.language!r} "
                    f"fails our own guardrails: {exc}. It is spoken verbatim on "
                    "a failure path, so it is checked here rather than on a "
                    "live call - fix the copy in data/confirmations.yaml."
                ) from exc
        return composed

    def lead_snapshot(self, *, ended_at: str) -> LeadSnapshot:
        """Freeze this call for the durable lead (docs/10- step 3).

        Built from the FULL-FIDELITY turns and the last accepted brief, never
        from the emitted stream, which is redacted precisely so that it cannot
        be used this way.

        `ended_cleanly` is the call-level answer and is false when any turn was
        sealed with an unresolved handle - a truncated call must not read like a
        complete one. Per-turn `audit_incomplete` stays on the turns as well,
        because the call-level flag cannot say WHICH turn was stranded.
        """
        turns = self._log.turns
        return LeadSnapshot(
            # Buffered during the call because `knowledge_use` has a
            # composite FK to `lead_turns`, which do not exist until
            # persist writes them.
            knowledge_use=self.knowledge_use(),
            session_id=self._log.session_id,
            started_at=self._started_at,
            ended_at=ended_at,
            call_end_reason=self._call_end_reason,
            ended_cleanly=not any(turn.audit_incomplete for turn in turns),
            language=self._settings.language,
            requested_language=self._settings.language,
            uncertified_fallback=(self._opening_language != self._settings.language),
            inventory_version=f"{len(self._projects)}-records",
            ambassador_name=self._ambassador_name,
            turns=turns,
            # A property, not a method. Calling it returned None and then
            # called None - which no test could see until something invoked
            # `lead_snapshot` for real, because #111 wired the snapshot into a
            # branch that never ran.
            brief=self._brief.last_good,
            # The reason this card exists: the policy held a settled
            # ContactCapture and nothing carried it into the record. Absent
            # policy is `not_asked`, which is the honest reading of a call
            # where nobody asked.
            contact=(
                ContactCapture(status="not_asked")
                if self._contact is None
                else self._contact.state
            ),
        )

    def _compose_farewell(self) -> str:
        """The authored farewell, validated through the guardrails now.

        Same reasoning as `_compose_fixed_lines`, and the same refusal: this is
        spoken verbatim at the end of every call that ends politely, so copy
        that fails our own guardrails is a defect in the copy and belongs in
        front of whoever started the process. There is then no runtime guard
        call on the close path to fail open, and none to fail closed either.
        """
        raw = self._farewells.farewell_speech(self._opening_language)
        try:
            return self._guard.compose(raw)
        except Exception as exc:
            raise RuntimeError(
                f"the farewell copy for {self._opening_language!r} fails our "
                f"own guardrails: {exc}. It is spoken verbatim when a call "
                "ends, so it is checked here rather than on a live call - fix "
                "the copy in data/farewells.yaml."
            ) from exc

    def _read_farewell(self, utterance: str) -> FarewellReading:
        """One reading of one utterance, for the two questions asked of it:
        is this a closing, and is it an answer to a goodbye already said."""
        return read_farewell(
            utterance,
            self._farewells,
            self._settings.language,
            names=self._ambassador_names,
        )

    def _answers_the_agents_goodbye(self, tracker: TurnTracker) -> bool:
        """Did the buyer just agree to a goodbye the MODEL already said?

        The shape the 08:32Z call ended on twice: the model signed off, the
        buyer said "Okay. Thank you.", and the call stayed open - so they had
        to say goodbye a third time to a service that had already said it.

        NOT the model's decision alone. A model that signs off spontaneously
        must never hang up on a live buyer, which is the asymmetric failure
        this whole policy is built around, so this still takes two readings:
        the model's goodbye, and a reply from the buyer that is nothing but
        courtesy. A question after the sign-off is a question, and the state
        lasts exactly one turn.
        """
        if self._closing or self._signed_off_turn is None:
            return False
        if tracker.turn_index != self._signed_off_turn + 1:
            return False
        return self._read_farewell(tracker.buyer_utterance).courtesy_only

    def _is_closing_line(self, utterance: str, tracker: TurnTracker) -> bool:
        """Has the buyer ended the conversation?

        Off entirely for a language with no authored closing phrases, which is
        the safe direction: the call then ends the way it did before this
        existed, rather than on a guessed phrase list.

        A NEAR MISS IS RECORDED. The first hosted call ended with the buyer
        saying goodbye, the model answering with a goodbye of its own, and the
        call staying open - and nothing on the stream said how close the rule
        came or what was in the way, because the utterance itself is redacted.
        `farewell_candidate` carries the shape of the miss and none of the
        words, so the tables can be widened from evidence instead of guesses.
        """
        if self._closing or not self._farewell_detects:
            return False
        reading = self._read_farewell(utterance)
        if reading.closes:
            return True
        if reading.has_phrase:
            self._note_candidate(tracker.turn_index, reading)
        return False

    # A near miss this size is a MISSING COURTESY; wider than this and the
    # utterance is carrying content, which on this path means a question. Not a
    # guess: the real tail misses read 1-2 ("that's it from my end" reads 2),
    # while the two NOT_ENDINGS entries that are themselves candidates read 4
    # and 6 ("before we say goodbye, what about the payment plan"). The pair
    # rule below would hang up on a repeated question without this line.
    _TAIL_MISS: Final = 2

    def _note_candidate(self, turn: int, reading: FarewellReading) -> None:
        self._candidate_turn = turn
        self._candidate_is_tail = reading.unexplained <= self._TAIL_MISS
        self._log.emit(
            "farewell_candidate",
            turn=turn,
            unexplained=reading.unexplained,
            named_ambassador=reading.named_ambassador,
        )

    def _repeats_an_interrupted_closing(self, turn: int) -> bool:
        """Is this the same goodbye, said again after being cut off?

        Two closing phrases in two consecutive buyer turns, where the first was
        talked over, is a buyer trying to leave and being interrupted - a
        stronger signal than either turn alone. Both hosted near misses were
        sealed interrupted, which is why neither reached the hybrid.

        Both turns must be TAIL misses. A buyer who twice embeds a closing
        phrase in a question is asking twice, and `_TAIL_MISS` is what tells
        those apart.
        """
        return (
            self._interrupted_candidate_turn == turn - 1
            and self._candidate_turn == turn
            and self._candidate_is_tail
            and self._interrupted_candidate_was_tail
        )

    def _agent_said_goodbye(self, tracker: TurnTracker) -> bool:
        """Did the model answer this turn with a closing of its own?

        The second signal in the hybrid, and it is never trusted alone. The
        buyer's turn must already have carried a closing PHRASE - that is what
        `_candidate_turn` records - so this only decides between "they said
        goodbye and the model agreed" and "they said goodbye inside a question
        the model then answered". A model that decides to hang up by itself is
        the failure this whole policy is built to avoid.
        """
        return any(
            contains_closing_phrase(sentence, self._farewells, self._settings.language)
            for sentence in tracker.generated_sentences
        )

    def _close_after_farewell_turn(
        self, interrupted: bool, reason: str = "buyer_farewell"
    ) -> None:
        """End the call now that the farewell turn's audio is over.

        Called from `_seal`, which is the framework's own "this turn's audio is
        finished" signal and the only place that knows whether it played out or
        was cut off. Sealing first is deliberate: the turn record is written
        before the call ends, so the audit cannot lose the farewell to the
        shutdown.

        An interrupted farewell CANCELS the close. A buyer who talks over the
        goodbye is not finished, and hanging up mid-sentence is the one outcome
        worse than the silence this replaces. It also covers the risk of
        deciding on a transcript the recogniser was still revising: if the
        buyer really was mid-sentence, they interrupt, and the call continues.
        """
        self._closing_turn = None
        if interrupted:
            self._log.emit("farewell_interrupted", reason=reason)
            return
        if self._closing:
            return
        self._closing = True
        self._call_end_reason = reason
        self._log.emit("call_ended", reason=reason)
        if self._close_call is not None:
            self._closing_task = asyncio.create_task(self._close_call(reason))

    async def say_farewell_and_close(self, reason: str) -> None:
        """Say goodbye, wait for it to be HEARD, then end the call.

        The order is the whole of it. Shutting the job down while the farewell
        is still in the TTS pipeline is a hang-up rather than a goodbye, so the
        close waits on the speech handle's playout.

        An interrupted farewell CANCELS the close. A buyer who talks over the
        goodbye is not finished, and hanging up on someone mid-sentence is
        worse than the silence this whole card exists to remove - so the
        farewell is interruptible, unlike the opening disclosure, which has the
        opposite requirement (it must complete because it is a notice).
        `_closing` is released on that path so a later goodbye still works.

        `call_ended` is emitted before the close is asked for, so the audit
        seal that `shutdown_session` writes stays the LAST event of the session
        rather than racing this one.

        Unlike the buyer's own goodbye, THIS close insists. An interrupted
        farewell cancels the buyer's close because a buyer who talks over it
        was not finished asking - a request they can withdraw. The duration cap
        is not a request: it bounds spend on a public URL (docs/09-), so
        talking over the goodbye must not extend the call. The audit still
        records that it was talked over.
        """
        if self._closing:
            return
        self._closing = True
        handle = self.session.say(self._farewell_line, allow_interruptions=True)
        await handle.wait_for_playout()
        if handle.interrupted:
            self._log.emit("farewell_interrupted", reason=reason)
        self._log.emit("call_ended", reason=reason)
        if self._close_call is not None:
            await self._close_call(reason)

    def note_buyer_left(self) -> None:
        """The buyer disconnected. Nothing to say and nobody to say it to.

        Recorded rather than spoken, and it still marks the call closed so a
        farewell cannot be started into an empty room.
        """
        if self._closing:
            return
        self._closing = True
        self._call_end_reason = "buyer_left"
        self._log.emit("call_ended", reason="buyer_left")

    def _fixed_line(self, key: str) -> str:
        """A terminal line, already composed and validated at construction."""
        return self._fixed_lines.get(
            key, self._confirmations.line(self._settings.language, key)
        )

    def _contact_turn(self, tracker: TurnTracker, *, closing: bool) -> _OwedTurn | None:
        """The contact ask, its read-back, or the line that closes the call.

        Decided once per turn and replayed, because `llm_node` can run twice
        for one buyer turn when a preemptive generation is invalidated by the
        final transcript. Reading one reply twice would spend the single ask
        twice and put two `contact_settled` on the stream for one answer - the
        same shape as the double `farewell_spoken` a live run recorded.
        """
        if self._contact is None:
            return None
        if self._contact_turn_index == tracker.turn_index:
            return self._contact_owed
        owed = self._contact_decision(tracker, closing=closing)
        self._contact_turn_index = tracker.turn_index
        self._contact_owed = owed
        return owed

    def _contact_decision(
        self, tracker: TurnTracker, *, closing: bool
    ) -> _OwedTurn | None:
        assert self._contact is not None
        text = tracker.buyer_utterance
        turn = tracker.turn_index

        if self._contact_awaiting_reply:
            # The ONE reply eligible for extraction, or the yes/no on the
            # digits. Nothing else in the call is offered to the policy, which
            # is what keeps a number mentioned about a listing from being read
            # as consent to be called.
            outcome = (
                self._contact.observe_confirmation(text, turn)
                if self._contact_awaiting_confirmation
                else self._contact.observe_reply(text, turn)
            )
            if not outcome.settled:
                # A phone went to the read-back. The call is not closing yet:
                # ending here would leave the buyer having read out a number
                # nobody echoed.
                self._contact_awaiting_confirmation = True
                assert outcome.speaks is not None
                return _OwedTurn(outcome.speaks, "contact", "read_back")

            self._contact_awaiting_reply = False
            self._contact_awaiting_confirmation = False
            # Settled - captured, declined or unconfirmed - so the authored
            # farewell takes THIS turn with the thanks in front of it. Spoken
            # as one line rather than two turns because the alternative is
            # waiting for another buyer utterance to say goodbye, and a buyer
            # who has just declined is already leaving.
            #
            # Returned as the `farewell` policy on purpose: that branch records
            # the farewell and arms the close on this turn's seal, so contact
            # capture adds no second closing mechanism.
            line = self._farewell_line
            if outcome.speaks:
                line = f"{outcome.speaks.strip()} {line}"
            return _OwedTurn(line, "farewell", "buyer_farewell")

        if not closing:
            return None
        step = self._contact.on_farewell(turn)
        if step is None:
            # A second goodbye, an already-settled contact, or a language with
            # no reviewed ask. All three mean "let the farewell happen", and
            # the caller does exactly that.
            return None
        self._contact_awaiting_reply = True
        return _OwedTurn(step.speaks, "contact", "ask")

    def _deterministic_turn(self, tracker: TurnTracker) -> _OwedTurn | None:
        """Copy to speak instead of running this turn, or None to carry on."""
        # The farewell is checked ABOVE the observed-turn gate, and that
        # placement is the whole of a bug the first local runs found twice.
        # `preemptive_generation` runs this on the PARTIAL transcript; if the
        # final is not equivalent the framework cancels that generation and
        # calls `llm_node` again, and below the gate the second call fell
        # through to the model - so the authored farewell was cancelled, the
        # model's own reply played instead, and the call still ended. Re-asking
        # costs nothing: the question is idempotent and spends none of the
        # buyer's policy attempts, which is the only thing the gate protects.
        # Read ONCE, and passed down. `_is_closing_line` records a near miss
        # as `farewell_candidate`, so calling it twice for one utterance would
        # put two candidates on the stream for one goodbye.
        closing = self._is_closing_line(tracker.buyer_utterance, tracker)

        if not closing and self._answers_the_agents_goodbye(tracker):
            # ARMED, and the turn is NOT taken back: the model is already
            # answering this utterance, and cancelling that to insert our own
            # goodbye is the double-goodbye the farewell policy exists to
            # avoid. The buyer hears the reply they are owed and the call ends
            # on its seal. The reason is the agent's, because the agent's
            # goodbye is what ended this call - the buyer only agreed to it,
            # and the authored farewell is not spoken for the same reason the
            # hybrid does not speak it: the model's goodbye WAS the goodbye.
            self._closing_turn = tracker.turn_index
            self._closing_reason = "agent_farewell"
            return None

        # The one contact ask sits between the goodbye and the farewell
        # (docs/10- 'Contact capture'): the FIRST farewell is intercepted for
        # it, and a second is honoured immediately. It also comes ahead of the
        # closing line because the reply to the ask is frequently itself a
        # goodbye - "sure, 050..., bye" - and that utterance belongs to the
        # policy, not to the farewell.
        owed_contact = self._contact_turn(tracker, closing=closing)
        if owed_contact is not None:
            return owed_contact

        if closing:
            # Ahead of the policies too: a buyer who has said goodbye is not
            # owed a budget question, and asking one would be the agent talking
            # past the end of the conversation.
            return _OwedTurn(self._farewell_line, "farewell", "buyer_farewell")
        if self._policy_observed_turn == tracker.turn_index:
            # The second half of a tool-using turn. The policies already read
            # this utterance; reading it again would burn a second attempt on
            # one reply.
            return None
        self._policy_observed_turn = tracker.turn_index
        try:
            steps = self._policies.observe(tracker.buyer_utterance)
        except Exception:
            logger.error("confirmation policies failed; handing over", exc_info=True)
            return self._handover(tracker, "budget", "confirmation policy failure")

        for step in steps:
            self._record_step(tracker, step)
            if not step.speaks:
                continue
            try:
                return self._speak(tracker, step)
            except Exception:
                logger.error(
                    "%s confirmation failed; handing over", step.policy, exc_info=True
                )
                return self._handover(
                    tracker, step.policy, f"{step.policy} confirmation failure"
                )
        return None

    def _record_step(self, tracker: TurnTracker, step: Step) -> None:
        """The audit line for one policy reading, spoken or not."""
        if step.recognition is not None:
            if step.recognition.failed:
                self._log.emit(
                    "recognition_failed",
                    turn=tracker.turn_index,
                    consecutive=step.recognition.consecutive,
                    hands_over=step.recognition.hands_over,
                )
            return
        if step.budget is not None and not step.speaks:
            if step.budget.currency is not None:
                self._log.emit(
                    "budget_settled",
                    turn=tracker.turn_index,
                    currency=step.budget.currency,
                )
            return
        if step.project is not None and not step.speaks:
            if step.project.settled:
                self._log.emit(
                    "project_settled",
                    turn=tracker.turn_index,
                    project=step.project.project_id,
                    band=step.project.band,
                    similarity=(
                        None
                        if step.project.similarity is None
                        else round(step.project.similarity, 3)
                    ),
                )

    def _speak(self, tracker: TurnTracker, step: Step) -> _OwedTurn:
        """Compose one speaking step. Raises; the caller owns the direction."""
        if step.policy == "recognition":
            text = self._fixed_line("recognition_escalation")
            self._route_to_human("three consecutive failed recognitions")
            return _OwedTurn(text, "recognition", "escalate")
        if step.policy == "budget":
            assert step.budget is not None
            return _OwedTurn(
                self._speak_budget_decision(tracker, step.budget, reask=step.reask),
                "budget",
                step.budget.action,
            )
        assert step.project is not None
        return _OwedTurn(
            self._speak_project_decision(tracker, step.project, reask=step.reask),
            "project",
            step.project.action,
        )

    def _handover(self, tracker: TurnTracker, policy: str, reason: str) -> _OwedTurn:
        """The fail-closed end of every confirmation path.

        Closes every policy, not just the one that broke: a buyer being handed
        to a person must not hear an unrelated question resume two turns later.
        """
        self._policies.quiesce()
        self._route_to_human(reason)
        if policy == "project":
            self._log.emit(
                "project_confirmation",
                turn=tracker.turn_index,
                action="project_give_up",
                project=None,
                band=None,
                hands_over=True,
            )
            return _OwedTurn(
                self._fixed_line("project_give_up"), "project", "project_give_up"
            )
        self._log.emit(
            "budget_confirmation",
            turn=tracker.turn_index,
            action="give_up",
            currency=None,
            hands_over=True,
        )
        return _OwedTurn(self._fixed_line("give_up"), "budget", "give_up")

    def _speak_budget_decision(
        self, tracker: TurnTracker, decision: Decision, *, reask: bool = False
    ) -> str:
        """Compose one budget decision into speech. Raises on any failure."""
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

        self._log.emit(
            "budget_confirmation",
            turn=tracker.turn_index,
            action=decision.action,
            currency=decision.currency,
            hands_over=decision.hands_over,
            reask=reask,
        )
        return text

    def _speak_project_decision(
        self,
        tracker: TurnTracker,
        decision: ProjectDecision,
        *,
        reask: bool = False,
    ) -> str:
        """Compose one project decision into speech. Raises on any failure."""
        template = self._confirmations.line(self._settings.language, decision.action)
        if decision.name is not None:
            # Bound to inventory, not to the transcript: reading the buyer's
            # own mangled words back would confirm nothing. Every
            # name-bearing decision goes through compose_project(), slot or
            # no slot, for the misspelled-slot reason above.
            text = compose_project_confirmation(
                template,
                project=decision.name,
                inventory_names=tuple(self._name_index.names.values()),
            )
        elif "{" in template:
            raise UnspeakableConfirmation(
                f"{decision.action!r} copy carries a slot and the decision "
                "has nothing to fill it with"
            )
        else:
            text = template
        # And then the one public path, which this line has no reason to skip.
        text = self._guard.compose(text)
        if not text.strip():
            raise UnspeakableConfirmation(
                f"no confirmation copy composed for {decision.action!r}"
            )

        if decision.hands_over:
            self._route_to_human(f"project confirmation: {decision.action}")

        self._log.emit(
            "project_confirmation",
            turn=tracker.turn_index,
            action=decision.action,
            project=decision.project_id,
            band=decision.band,
            hands_over=decision.hands_over,
            reask=reask,
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

        # Set by the retrieval seam below, read here at call time. One fixed
        # system message on a COPY of `chat_ctx`, never on the live context:
        # session history that accumulated every turn's excerpts would grow
        # without bound and would let turn three's document answer turn nine.
        knowledge_message: str | None = None

        async def open_stream(
            extra_instruction: str | None = None,
        ) -> AsyncIterable[Any]:
            additions = [m for m in (knowledge_message, extra_instruction) if m]
            ctx = chat_ctx
            if additions:
                ctx = chat_ctx.copy()
                for message in additions:
                    ctx.add_message(role="system", content=message)
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
        owed = self._deterministic_turn(tracker)
        if owed is not None:
            if owed.policy == "farewell":
                # Recorded ONCE per turn. The check that hands the farewell the
                # turn sits above the observed-turn gate so an invalidated
                # preemptive generation cannot give the goodbye back to the
                # model - which means `llm_node` can legitimately ask for the
                # same farewell twice, and a live run recorded two
                # `farewell_spoken` for one goodbye that way. The buyer heard
                # one; the audit has to say one.
                if self._closing_turn != tracker.turn_index:
                    tracker.record_farewell(owed.text)
                # Armed, not fired. The close happens when this turn's audio is
                # over, which is `_seal` - the framework's own signal, and the
                # only place that knows whether the farewell played out or was
                # talked over.
                self._closing_turn = tracker.turn_index
            elif owed.policy == "contact":
                # `ask` and `read_back` only: the line that settles the contact
                # comes back as the farewell above, thanks included.
                tracker.record_contact_line(owed.text, owed.action)
            elif owed.policy == "budget":
                tracker.record_confirmation(owed.text, owed.action)
            elif owed.policy == "project":
                tracker.record_project_confirmation(owed.text, owed.action)
            else:
                tracker.record_recognition_escalation(owed.text)
            yield (owed.text if owed.text.endswith((" ", "\n")) else (owed.text + " "))
            return

        # ADR-019's prompt seam, and its position is the contract: AFTER the
        # deterministic policy declined the turn, BEFORE the model opens. A
        # confirmation turn returns above this line, so it never spends the
        # retrieval budget on a prompt nobody builds.
        guard = self._guard
        if self._knowledge is not None:
            context = await self._knowledge.for_turn(
                turn_index=tracker.turn_index,
                query=tracker.buyer_utterance,
                base=self._guard.allowed,
                project_ids=self._project_ids,
            )
            self._knowledge_context = context
            knowledge_message = context.system_message
            # A COPY for this turn. `extend_allowed_figures` returns `base`
            # itself when nothing qualified, so the common case does not build
            # a second guard at all.
            if context.allowed is not self._guard.allowed:
                guard = self._guard.with_allowed(context.allowed)
        else:
            self._log.emit(
                "knowledge_retrieval_skipped",
                turn=tracker.turn_index,
                reason="no_retriever",
                elapsed_ms=0,
            )

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
                source, guard=guard, sink=sink, regenerate=regenerate
            ):
                if isinstance(out, str):
                    spoke_anything = True
                yield out
            # The reply is complete and nothing raised. Issue #33: if this turn
            # regenerated and came back with no figure, it refused, and a
            # refusal promises a colleague.
            self._backstop_regeneration(tracker)
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
        # Level matching, applied per frame as the audio leaves (adapter/levels).
        # The three shipping voices differ by nearly 4x in loudness, so without
        # this a language change is also a volume change. Resolved once per
        # turn rather than per frame, and unity for the quietest voice - which
        # every other voice is matched down to - so the common path is the
        # identical object it was before.
        gain = gain_for(self._settings.voice_id(self._settings.language))
        async for frame in Agent.default.tts_node(self, spoken, model_settings):
            if first:
                first = False
                # Marked before the gain is applied: this is a measurement of
                # when Fish's first audio ARRIVED, and it must not start
                # including our own work on the frame.
                if tracker is not None:
                    tracker.mark_tts_first_audio()
                self._note_tts_connection(tracker)
            if gain < 1.0:
                frame = rtc.AudioFrame(
                    data=apply_gain(bytes(frame.data), gain),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                )
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

    def _backstop_regeneration(self, tracker: TurnTracker) -> None:
        """Make the regeneration's promise structural, not sampled (issue #33).

        #31 named `escalate_to_human` in the regeneration instruction's leading
        imperative and it measured 3/3 English, 3/3 Hindi and 1/3 ARABIC - and
        the three Arabic samples were byte-identical requests at temperature 0
        that disagreed with each other, so no amount of rewording can make the
        promise provable. This is the F2 and budget-handover move applied one
        layer up: stop asking the model to keep the promise and keep it in code.

        A regenerated reply that ends the turn stating no figure is
        refusal-shaped by construction. The model has just been told the figure
        it used is not in the inventory; if it comes back WITH a figure it
        corrected itself, which is the designed happy recovery and must not
        escalate, and if it comes back without one it has refused - which is
        exactly the turn that tells the buyer a colleague will confirm.

        Scoped to the regeneration invocation, deliberately. A first-pass reply
        with no figure is ordinary conversation: "which areas do you cover"
        carries no figure and needs no human.

        Whether the MODEL called the tool is not checked, and does not need to
        be. Its tool call executes after this generator finishes, so it cannot
        be observed here, and #31's notify-once-per-turn guard collapses the two
        requests into one handover. Requiring a tool call we cannot yet see
        would be the fail-open direction.

        FAILURE DIRECTION: towards routing. A false positive costs one redundant
        ambassador task, deduplicated to one per turn. A false negative is the
        defect itself - a buyer promised a colleague with nobody coming. So
        every unreadable case counts as "no figure stated", including the figure
        detection raising on itself (see `TurnTracker.record_guardrail`).
        """
        if not tracker.regenerated:
            return
        if tracker.handed_over:
            # The composed fallback, which routed while the stream was still
            # unwinding. The model's own tool call cannot have landed yet, so
            # this is the only thing `handed_over` can mean at this point.
            return
        if tracker.regenerated_stated_figure:
            return
        self._route_to_human("regenerated reply stated no inventory figure")

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
        self._speech_handle_turn = None
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
        # After the record is written, so `turn_complete` precedes `call_ended`
        # and the audit keeps the farewell turn even as the call ends.
        interrupted = handle is not None and handle.interrupted
        if self._closing_turn == pending.tracker.turn_index:
            self._close_after_farewell_turn(
                interrupted=interrupted,
                reason=self._closing_reason or "buyer_farewell",
            )
        elif self._candidate_turn == pending.tracker.turn_index:
            # The hybrid, resolved here because the model's reply is only known
            # now. The buyer's turn carried a closing phrase but not cleanly
            # enough for the strict rule; if the model ALSO answered with a
            # closing, two independent readings agree and the call ends on the
            # goodbye the buyer already heard. Nothing further is spoken - the
            # model's own farewell was the farewell.
            turn = pending.tracker.turn_index
            repeats = self._repeats_an_interrupted_closing(turn)
            self._candidate_turn = None
            if interrupted:
                # Cut off mid-reply, so the model's half of the hybrid never
                # happened. Carried for exactly one turn: if they say it again,
                # the repeat is the second signal instead.
                self._interrupted_candidate_turn = turn
                self._interrupted_candidate_was_tail = self._candidate_is_tail
            elif repeats:
                self._closing_turn = turn
                self._close_after_farewell_turn(
                    interrupted=False, reason="buyer_farewell_repeated"
                )
            elif self._agent_said_goodbye(pending.tracker):
                self._closing_turn = turn
                self._close_after_farewell_turn(
                    interrupted=False, reason="agent_farewell"
                )
        elif self._agent_said_goodbye(pending.tracker):
            # The model said goodbye and nothing ended the call: no candidate
            # to pair it with, because the buyer's utterance carried no closing
            # phrase. Recorded rather than acted on - see
            # `_answers_the_agents_goodbye`. The buyer heard a goodbye, so the
            # next reply may be nothing but courtesy and still be an ending.
            self._signed_off_turn = pending.tracker.turn_index
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
        self._speech_handle_turn = None
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


def remaining_budget(deadline: float, *, cap: float) -> float:
    """Seconds a stage may take: whatever is left, never more than its cap.

    Returns 0.0 rather than a negative number once the deadline has passed,
    because `asyncio.wait_for(..., timeout=-1)` is not a refusal - it is an
    immediate cancellation with a confusing traceback.
    """
    return max(0.0, min(cap, deadline - time.monotonic()))


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
    lead_writer: "LeadWriter | None" = None,
    # The task `entrypoint` started at `session_start`. Awaited HERE rather
    # than by the caller, because the await itself can raise - a
    # half-configured store used to surface its ValueError on that line, one
    # above this function, outside every try. `lead_writer` remains for callers
    # that already hold one, which is every test that does not need the task.
    lead_store: "asyncio.Task[LeadWriter | None] | None" = None,
    ask: "Ask | None" = None,
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
    deadline = time.monotonic() + LEAD_SECTION_BUDGET_SECONDS
    # Capped here, not in the extractor: see BRIEF_DRAIN_BUDGET_SECONDS.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            agent.brief_extractor.drain(timeout=BRIEF_DRAIN_BUDGET_SECONDS),
            timeout=BRIEF_DRAIN_BUDGET_SECONDS + 0.5,
        )
    await agent.brief_extractor.aclose()
    # AFTER the drain and before the providers close: the drain exists so the
    # last turn's brief is awaited, and persisting earlier would lose the best
    # brief of the call - the field the admin side is for (docs/10- step 3-4).
    # `persist_or_report` cannot raise, so nothing here can stop the job
    # ending: ADR-018 is explicit that an unavailable database never blocks a
    # call, and by this point the farewell has already been heard.
    if lead_writer is None and lead_store is not None:
        try:
            lead_writer = await asyncio.wait_for(
                lead_store, timeout=remaining_budget(deadline, cap=3.0)
            )
        except Exception as exc:
            # Reported with `connect` as the stage, the same as a failed
            # handshake: from operations' side a store that refused its
            # configuration and one that could not be reached are the same
            # event - a call with no lead - and one query should find both.
            # The exception message can name a variable, so only the code goes
            # on the stream.
            log.emit("lead_persist_failed", stage="connect", code=_failure_code(exc))
            lead_writer = None

    if lead_writer is not None:
        # Inside the boundary. Building the snapshot is not a database call, so
        # it looked like the safe line - but it validates a model, and a model
        # that will not validate raised straight out of this callback and lost
        # the closes, `session_end` and the seal. Nothing between here and the
        # seal may raise, whatever it is doing.
        try:
            snapshot = agent.lead_snapshot(ended_at=_now_iso())
        except Exception as exc:
            # Through the same closed set as every other failure here, rather
            # than a literal: one vocabulary, so one query finds every lost
            # lead whatever stage lost it.
            log.emit("lead_persist_failed", stage="snapshot", code=_failure_code(exc))
            snapshot = None
        lead_id = None
        if snapshot is not None:
            try:
                lead_id = await asyncio.wait_for(
                    lead_writer.persist_or_report(snapshot, log=log),
                    timeout=remaining_budget(deadline, cap=5.0),
                )
            except TimeoutError:
                # `persist_or_report` cannot raise, so this is the budget and
                # not the write failing. Same event either way: from
                # operations' side it is a call with no lead.
                log.emit("lead_persist_failed", stage="write", code="timeout")
        if lead_id is not None and ask is not None:
            # BOUNDED, and the bound is not a style choice. The worker gathers
            # shutdown callbacks with no per-callback timeout and force-closes
            # the process at `shutdown_process_timeout` (10.0s in
            # livekit-agents 1.7.0), so an unbounded model call here does not
            # merely slow the shutdown - it loses the rest of it, including the
            # audit seal and `session_end`. A timeout is a failed analysis,
            # which is a state the admin API can retry; a lost seal is not.
            try:
                await asyncio.wait_for(
                    finalise_analysis(
                        snapshot=snapshot,
                        lead_id=lead_id,
                        writer=lead_writer,
                        ask=ask,
                        log=log,
                    ),
                    timeout=remaining_budget(deadline, cap=ANALYSIS_BUDGET_SECONDS),
                )
            except TimeoutError:
                log.emit("analysis_failed", stage="ask", code="timeout")
                await lead_writer.mark_analysis_failed(lead_id)
    if stt_node is not None:
        await stt_node.aclose()
    await llm.aclose()
    log.emit("session_end", turns=len(log.turns))
    # After the last event so the surface sees the session end, before the log
    # closes so the observer is gone by the time the writer stops.
    if bridge is not None:
        await bridge.aclose()
    await log.aclose()


# The cross-service contract with the web talk route (docs/09-, "Per-call
# language"). The room's metadata is this JSON string and nothing else:
#
#     {"v":1,"language":"en"|"ar"|"hi"}
#
# The codes are `Language` in ambassador/schemas.py, read from it rather than
# restated so a fourth language cannot be added to the product and silently
# rejected here. Unknown keys are ignored, so the writer can add a field
# without a coordinated deploy.
_METADATA_VERSION: Final = 1
_LANGUAGE_CODES: Final[frozenset[str]] = frozenset(get_args(Language))


@dataclass(frozen=True)
class LanguageSelection:
    """Which language this call runs in, and where that came from.

    `reason` is one of a fixed set of adapter-authored words, never the
    metadata itself: the string is written by another service, and a foreign
    service's free text has no business on the emitted event stream.
    """

    language: Language
    source: Literal["room_metadata", "worker_default"]
    reason: Literal[
        "",
        "no_metadata",
        "not_json",
        "not_an_object",
        "no_language_key",
        "unsupported_language",
        "unsupported_version",
    ] = ""


def language_from_metadata(
    metadata: str | None, default: Language
) -> LanguageSelection:
    """Read the call's language out of room metadata, or fall back.

    EVERY failure falls back to the worker's `LANGUAGE` and says which failure
    it was. That direction is deliberate and it is the whole design: this runs
    on a public URL, so the input is untrusted and unversioned in practice, and
    a call that refuses to start teaches the client nothing. A call in the
    wrong language is visible to them in one sentence and visible to us in one
    event.

    The version field is the one place this is not purely lenient. A `v` that
    is present and not 1 means the writer is speaking a contract this code has
    not seen, so `language` may not mean what it means here, and guessing is
    how you ship a demo that speaks Arabic to an English visitor. A `v` that is
    ABSENT is accepted as 1, because an absent version cannot be a future
    version - it can only be a v1 writer who left out a constant - and
    rejecting that would turn a harmless omission into a wrong-language call.
    """
    if not metadata or not metadata.strip():
        return LanguageSelection(default, "worker_default", "no_metadata")
    try:
        parsed = json.loads(metadata)
    except (ValueError, TypeError):
        return LanguageSelection(default, "worker_default", "not_json")
    if not isinstance(parsed, dict):
        return LanguageSelection(default, "worker_default", "not_an_object")
    version = parsed.get("v", _METADATA_VERSION)
    if version != _METADATA_VERSION:
        return LanguageSelection(default, "worker_default", "unsupported_version")
    language = parsed.get("language")
    if language is None:
        return LanguageSelection(default, "worker_default", "no_language_key")
    if language not in _LANGUAGE_CODES:
        return LanguageSelection(default, "worker_default", "unsupported_language")
    return LanguageSelection(language, "room_metadata")


def job_room_metadata(ctx: JobContext) -> str:
    """The metadata string off the JOB's room message, not the connected room.

    `ctx.room` is an `rtc.Room` that only has metadata once `ctx.connect()` has
    run, and `entrypoint` connects last, after STT, TTS and the LLM have been
    built from the settings this value decides. `ctx.job.room` is the
    server-sent `Room` proto and carries `metadata: str` from the moment the
    job arrives, so reading it needs no reordering.

    Defensive because the console runs a mock job: anything missing reads as no
    metadata, which falls back to the worker default and leaves the laptop
    demo behaving exactly as it did.
    """
    job = getattr(ctx, "job", None)
    room = getattr(job, "room", None)
    metadata = getattr(room, "metadata", "")
    return metadata if isinstance(metadata, str) else ""


async def end_call(ctx: JobContext, log: EventLog, reason: str) -> None:
    """Hand the close to the framework once the farewell has been heard.

    THE ROOM IS DELETED, NOT JUST THE JOB, and the difference is the whole
    of a defect the human hit on the first hosted call. `ctx.shutdown()`
    ends the JOB: the agent leaves, and a room with a browser still in it
    lives on, because `departureTimeout` only starts once the LAST
    participant has gone. The page ends a call on `ROOM_DELETED` /
    `ROOM_CLOSED` / `PARTICIPANT_REMOVED` (#78), so it saw nothing and the
    client had to press End call after a perfectly good goodbye. The local
    runs could not show it: `buyer_publisher` disconnects itself when its
    script ends, so the room emptied either way.

    Deleting first, then shutting down, because deletion is the signal the
    other participant is waiting for; a failure to delete must still leave
    the job shutting down, or a transient API error would leave the call up
    with nobody able to end it.
    """
    try:
        await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        log.emit("room_deleted", reason=reason)
    except Exception as exc:  # the call must end even if this does not
        logger.warning("could not delete the room: %s", exc)
        log.emit("room_delete_failed", reason=reason)
    ctx.shutdown(reason=reason)


def start_call_duration_cap(
    ctx: JobContext,
    log: EventLog,
    seconds: int,
    farewell: Callable[[], Awaitable[None]] | None = None,
) -> asyncio.Task[None] | None:
    """Shut the call down after `seconds`. Returns None when there is no cap.

    Zero is no cap, which is the default and what the laptop demo and the
    console run with. The hosted service sets a number because its URL is
    public and its providers are metered (docs/09-).

    Module level rather than a closure so the timer is testable without a live
    room, the same reason `shutdown_session` is out here. The caller must keep
    the returned task referenced for as long as the job runs.
    """
    if seconds <= 0:
        return None

    async def _expire() -> None:
        await asyncio.sleep(seconds)
        # Emitted BEFORE the shutdown call, so the audit says why the call
        # ended even though `ctx.shutdown` runs the callback that closes the
        # log. Reversing these two loses the one event that distinguishes a
        # capped call from a visitor hanging up.
        log.emit("call_duration_cap", limit_seconds=seconds, action="shutdown")
        if farewell is not None:
            # The same authored copy the buyer's own goodbye speaks, and the
            # same wait for playout - a cap that cuts the audio mid-word is the
            # hang-up this whole card exists to remove, whoever ended the call.
            # `farewell` owns the shutdown, so `call_ended` still precedes it.
            await farewell()
            return
        ctx.shutdown(reason=f"demo call duration cap reached ({seconds}s)")

    task = asyncio.create_task(_expire(), name="call-duration-cap")
    log.emit("call_duration_cap_armed", limit_seconds=seconds)
    return task


def prewarm(proc: JobProcess) -> None:
    """Load Silero once per worker process, not once per call."""
    proc.userdata["vad"] = silero.VAD.load()


def _session_start_fields(settings: Settings) -> dict[str, object]:
    """The reader-facing session contract, beside the full audit config.

    Text mode owns the existing contract, including the prompt fingerprint.
    Calling the same source here keeps the two transports from inventing
    different meanings for ``inventory_version``.
    """
    return {
        "config": settings.redacted(),
        "model": settings.llm_model,
        "language": settings.language,
        "prompt_mode": settings.prompt_mode,
        "guardrail_mode": settings.guardrail_mode,
        "inventory_version": Harness.load().prompt_fingerprint(settings.language),
        # Who this call runs as. From data/ambassadors.yaml rather than the
        # config, because it is product identity the client chose and not an
        # environment setting - and the web surface reads the same file for the
        # orb label, so a mismatch between the page and the voice shows up in
        # one place. Empty means unnamed, which is a working state.
        "ambassador_name": load_ambassadors().name_for(settings.language),
        # Whether this call can ask for contact at all. Arabic and Hindi have
        # no reviewed ask in data/contact.yaml, and the policy stays silent
        # rather than saying an English sentence in a call that is not in
        # English (AGENTS.md:52). Reported here because silence has to be
        # READABLE: an operator seeing `contact_status=not_asked` on every
        # Arabic lead needs to know it was off rather than broken.
        "contact_ask": load_contact_copy().enabled(settings.language),
    }


def build_contact_policy(settings: Settings, log: EventLog) -> ContactPolicy:
    """The one contact ask, for this call's language.

    A factory rather than a constructor call in the entrypoint so the wiring
    line stays one line, and so the policy is built from the SETTLED language -
    room metadata has already been read by the time this runs, and a policy
    built from the worker default would ask in the wrong language or refuse to
    ask in the right one.
    """
    return ContactPolicy(load_contact_copy(), settings.language, log)


async def entrypoint(ctx: JobContext) -> None:
    settings = load_settings()
    log = EventLog(session_id=utils.shortuuid("sess_"))

    # Per-call language, before anything is built from it. `LANGUAGE` used to
    # be the whole answer, which made a worker speak one language for its life;
    # the hosted demo lets a visitor pick, and it arrives in room metadata.
    # Resolved here so `session_start` below already reports the language the
    # call will actually run in, rather than the worker's default plus a
    # correction three events later.
    worker_default = settings.language
    selection = language_from_metadata(job_room_metadata(ctx), worker_default)
    settings = replace(settings, language=selection.language)

    missing = settings.missing_for_voice()
    if missing:
        raise RuntimeError(missing_credentials_error(missing))

    # Started before the first event, so a surface that connects mid-call
    # replays the whole session rather than joining from wherever it arrived.
    # None unless AMBASSADOR_BRIDGE_HANDSHAKE names a path: a listening socket
    # carrying buyer transcripts is not on by default.
    bridge = bridge_from_env(log)
    if bridge is not None:
        await bridge.start()
        log.emit("events_bridge", host="127.0.0.1", port=bridge.port)

    log.emit("session_start", **_session_start_fields(settings))

    # Started as a TASK, not awaited: the pool handshake to Frankfurt must not
    # sit between the job starting and the buyer hearing the disclosure. It is
    # awaited in the shutdown callback, by which time the call is over and the
    # connection has had the whole conversation to complete. Per job rather
    # than per worker, because a pool held across idle hours is what Supabase's
    # inactivity pause takes away.
    lead_store = asyncio.create_task(
        build_lead_writer(settings, log), name="lead-store-connect"
    )
    # After session_start, because it explains a value that event already
    # carried. `reason` is empty when the metadata was read successfully.
    log.emit(
        # Not "call_language": that is already a FIELD name on three other
        # events, and an event and a field sharing a name makes a grep for
        # either one useless.
        "language_selected",
        language=selection.language,
        source=selection.source,
        reason=selection.reason,
        worker_default=worker_default,
    )

    agent = AmbassadorAgent(
        settings=settings,
        log=log,
        close_call=functools.partial(end_call, ctx, log),
        # ADR-019. The retriever reads through the SAME pool the lead store
        # connects, rather than opening a second one: one pool per job is what
        # keeps this inside Supabase's client limit. It resolves the task per
        # turn and never awaits it, so a turn that arrives before the
        # handshake finishes is a skip, not a wait.
        knowledge=KnowledgeRetriever(
            repository_when_ready(lead_store),
            log=log,
            # The RESOLVED language, so the query drops the stopwords of
            # the language the buyer is actually speaking.
            language=settings.language,
        ),
        contact=build_contact_policy(settings, log),
    )

    stt_node = build_stt(settings)
    if stt_node is not None:
        log.emit("stt_enabled", **describe(stt_node))
    else:
        # Text mode and the console's typed input both work without a
        # recogniser, which is what kept the day-1 gate provable while
        # OpenRouter rejected audio under a $0.50 balance.
        log.emit("stt_disabled", reason="STT_ENABLED is not set")

    tts = build_tts(settings)
    log.emit("tts_enabled", **describe_tts(tts))

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

    # The per-call hard stop (docs/09-, "Abuse controls"). A cancelled sleeper
    # and nothing else: `ctx.shutdown()` is synchronous and the shutdown
    # callback below already seals the audit, so this adds no failure mode of
    # its own. Counting starts here, before `connect`, so setup counts against
    # the cap rather than being free - the number bounds a call's cost, and
    # everything after this point costs.
    async def _cap_farewell() -> None:
        await agent.say_farewell_and_close("duration_cap")

    cap = start_call_duration_cap(
        ctx, log, settings.demo_max_call_seconds, _cap_farewell
    )

    async def _shutdown() -> None:
        # Cancel first: a call that ends on its own must not leave a timer
        # behind that fires into a closed session. Referencing `cap` from this
        # closure is also what keeps the task ALIVE - `entrypoint` returns
        # while the job runs on, asyncio holds only a weak reference to a
        # running task, and a garbage-collected timer is a cap that silently
        # never fires.
        if cap is not None:
            cap.cancel()
        # The TASK, not an awaited writer: `shutdown_session` owns the await
        # because the await can raise, and nothing above the seal may.
        try:
            await shutdown_session(
                agent=agent,
                log=log,
                llm=llm,
                stt_node=stt_node,
                bridge=bridge,
                lead_store=lead_store,
                ask=analysis_ask(settings),
            )
        finally:
            if lead_store.done() and not lead_store.cancelled():
                writer = None if lead_store.exception() else lead_store.result()
                if writer is not None:
                    await writer.close()

    ctx.add_shutdown_callback(_shutdown)

    # A buyer who simply closes the tab gets no farewell - there is nobody to
    # say it to - but the call is still marked closed, so a goodbye cannot be
    # started into an empty room and the audit records why the call ended.
    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        agent.note_buyer_left()

    await ctx.connect()
    await session.start(agent=agent, room=ctx.room)


# The subcommands that dial LiveKit. `console` is deliberately absent: it runs a
# mock job in a `console-room` and never connects, verified by running it with no
# transport credentials at all, so demanding them would refuse to start the venue
# plan B over keys it does not use. `download-files` is absent for the same
# reason - it runs in an image build, where no credential exists yet.
# Well under the worker's `shutdown_process_timeout` (10.0s in livekit-agents
# 1.7.0), because the brief drain, the persist, the provider closes and the log
# drain all come out of the same 10 seconds. Four leaves room for a slow model
# without putting the audit seal at risk.
# THE SHUTDOWN BUDGET.
#
# `shutdown_process_timeout` decides how long the framework waits before
# force-closing this job process, and it is absent from the CURRENT public
# documentation - the server-options page documents `drain_timeout` and not
# this one. Its value is therefore whatever the pinned version carries (10.0 in
# livekit-agents 1.7.0), and callbacks are gathered with no per-callback
# timeout, so an overrun loses the provider closes, `session_end` and the audit
# seal rather than merely delaying them.
#
# So we set it deliberately rather than inherit it. 20s is not "more room for
# slow code": it is the smallest value that lets a worst-case lead write finish
# AND leaves the seal a margin it cannot lose. The cost is bounded and paid
# only on redeploys - Railway's own drain is `--drain-timeout 600`, so ten
# extra seconds per in-flight job is noise against it.
SHUTDOWN_PROCESS_TIMEOUT: Final = 20.0

# The lead work - connect, persist, analysis - shares ONE deadline rather than
# holding fixed per-stage caps. Fixed caps would make a fast persist and a slow
# analysis fail as often as the reverse; a shared deadline lets a stage that
# finishes early hand its slack to the next. 12s of 20 leaves 8 for the drain,
# the provider closes and the seal.
LEAD_SECTION_BUDGET_SECONDS: Final = 12.0

# The largest single claimant before this card: `drain(timeout=10.0)` is the
# whole 1.7.0 budget on its own. Capped at the CALL SITE rather than by moving
# the default, because 10s is right for a caller with time and wrong for one
# holding the audit seal. What 2s can cost is the very last turn's brief; the
# last accepted one is already durable, and a missing brief is a retryable
# state.
BRIEF_DRAIN_BUDGET_SECONDS: Final = 2.0

# Unchanged. It is the one stage whose work is a network round trip we cannot
# shorten, and it now draws from the shared deadline like the others.
ANALYSIS_BUDGET_SECONDS: Final = 4.0

_CONNECTING_COMMANDS: Final = frozenset({"start", "dev", "connect"})


def preflight(argv: list[str] | None = None) -> str | None:
    """Why this invocation must not start, ready to print, or None to proceed.

    Returns the composed message rather than a list of names because there are
    now two kinds of refusal - a credential that is missing and a setting nobody
    chose - and they read differently. Reporting BOTH at once is the same
    reasoning `missing_for_worker` already applies to credentials: an operator
    on a platform pays a rebuild and a deploy per cycle, so learning about the
    second problem after fixing the first costs a round trip for nothing.

    Runs BEFORE `cli.run_app`, which is the only anchor that precedes the
    framework's own argument check - and that check cannot be relied on: with no
    transport credentials the framework logs "worker failed", drains, and exits
    ZERO, so a restart-on-failure policy never trips and a misconfigured deploy
    stops quietly on the dashboard (measured at the #64 gate).

    `docs/09-deploy.md` says startup "says which one during preflight rather than
    failing on the first sentence of a call". That was true of LiveKit
    credentials only by accident and not true of the provider keys at all:
    `missing_for_voice()` ran inside `entrypoint`, which only runs once a job is
    dispatched, so a worker missing FISH_API_KEY registered, looked healthy, and
    failed on the first buyer. This is the sentence made true.

    Only the connecting subcommands are checked. A console session still gets the
    provider-key check from `entrypoint`, which its mock job dispatches
    immediately, so nothing there becomes later or quieter.
    """
    arguments = sys.argv[1:] if argv is None else argv
    # Any argument matching a connecting command, not "the first non-flag one":
    # a global option takes its value in that position, so `--log-level debug
    # start` would have read the invocation as `debug` and skipped the check -
    # which its own test caught. The loose match can only misfire on a flag
    # VALUE literally spelled `start`, `dev` or `connect`, and it errs towards
    # checking credentials, which is the direction that fails safe.
    if not _CONNECTING_COMMANDS.intersection(arguments):
        return None
    settings = load_settings()
    return worker_refusal(
        settings.missing_for_worker(), settings.undeclared_for_worker()
    )


def export_transport_env(argv: list[str] | None = None) -> list[str]:
    """Put the transport credentials where the FRAMEWORK looks for them.

    `load_settings()` reads `agent/.env` itself, so this repository's own code
    works from that file alone. The framework does not: `cli.run_app` reads
    `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` out of the process
    environment. The half that works hides the half that does not, and the way
    it fails is the problem. `dev` at least says so ("ws_url is required, or set
    LIVEKIT_URL environment variable"); `connect` is SILENT, because its job
    simulation builds a `LiveKitAPI` with an empty url inside a fire-and-forget
    task whose exception nobody awaits, so the worker logs "HTTP server
    listening" and then sits there having dispatched nothing. That cost an hour
    of reading a room that had only the hosted agent in it.

    Only on the connecting subcommands, matching `preflight`. Console mode
    dials nothing and must not start loading settings earlier than it does now.

    `setdefault` in spirit but not by name: an environment variable set to the
    empty string counts as unset here, which is what `config._resolve` already
    does with the process environment. A real value set by a platform always
    wins, so the hosted deploy is untouched.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if not _CONNECTING_COMMANDS.intersection(arguments):
        return []
    settings = load_settings()
    exported: list[str] = []
    for name, value in (
        ("LIVEKIT_URL", settings.livekit_url),
        ("LIVEKIT_API_KEY", settings.livekit_api_key),
        ("LIVEKIT_API_SECRET", settings.livekit_api_secret),
    ):
        if value and not os.environ.get(name):
            os.environ[name] = value
            exported.append(name)
    return exported


def worker_options() -> WorkerOptions:
    """The worker's options, in one place so the budget is testable.

    `shutdown_process_timeout` is set rather than inherited: it is undocumented
    publicly, so the default can move between versions, and it is the value
    that decides whether the audit seal survives a slow lead write.
    """
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        shutdown_process_timeout=SHUTDOWN_PROCESS_TIMEOUT,
    )


if __name__ == "__main__":
    _refusal = preflight()
    if _refusal:
        # stderr and a non-zero exit, so the platform sees a failed start rather
        # than a clean one. Names only, never values: the message is printed by
        # whatever supervisor restarted the process.
        print(_refusal, file=sys.stderr)
        raise SystemExit(1)
    # After preflight, so a run that is going to be refused is refused before
    # anything is written to the environment.
    export_transport_env()
    cli.run_app(worker_options())
