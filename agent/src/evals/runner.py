"""Driving one case through the real pipeline.

The seam is `guardrails.pipeline.process_sentence` - the single public producer
of speakable text (AGENTS.md invariant 4) - and everything around it here is a
faithful, non-streaming twin of what `adapter/interception.py` and
`adapter/agent.py` do on a live turn:

  1. The deterministic budget policy reads the buyer's utterance first and, if
     it has a question, SPEAKS INSTEAD of running the turn (ADR-011). Any
     failure on that path hands over to a human; it never falls through to the
     model, which is the fail-open defect PR #20's review removed.
  2. Otherwise the model replies, sentences are split on the core's boundaries,
     and each one goes through `process_sentence`.
  3. A blocked sentence with nothing yet spoken regenerates ONCE with the
     violation named; a blocked sentence after audio has played gets a composed
     bridge. Either way the rest of that reply is dropped, exactly as the
     streaming version halts.

## What this twin does NOT reproduce, and why that is stated rather than hidden

It is not the streaming path: no chunk boundaries arriving mid-figure, no
`FlushSentinel`, no barge-in, no TTS. Those are `adapter/interception.py`'s
tests and the human-verified rows of docs/05-. The one piece of logic both
paths share - where a sentence ends - is imported from `ambassador.sentences`
rather than restated, and `test_evals_runner.py` pins the composed copy to the
same `data/fallbacks.yaml` the adapter speaks from.

## The fallback notifies a human, and the bridge does not

`adapter/agent.py` routes the composed fallback through `_speak_fallback`, which
records the chunk AND calls `_route_to_human`, so a turn that ends in "let me
put you through to one of our ambassadors" has actually put someone through.
This harness credits the same escalation for the same reason string, because a
harness that scores a promise the product does not keep - which this one did,
deliberately, until the wiring landed - is worse than no harness.

The BRIDGE is not routed on either side. Its copy promises nothing and the turn
carries on; an escalation there would fire on every recovered sentence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from adapter.confirmations import (
    ConfirmationCopy,
    UnspeakableConfirmation,
    load_confirmations,
)
from adapter.confirmations import compose as compose_confirmation
from adapter.fallbacks import FallbackCopy, load_fallback_copy
from ambassador.budget import BudgetPolicy, CurrencyVocabulary, load_currency_vocabulary
from ambassador.figures import states_a_figure
from ambassador.guardrails.pipeline import process_sentence
from ambassador.guardrails.prohibited import ProhibitedPattern, load_patterns
from ambassador.inventory import (
    build_allowed_figures,
    load_inventory,
    serialise_for_prompt,
)
from ambassador.prompts import REGENERATION_INSTRUCTION, build_ambassador_prompt
from ambassador.schemas import (
    AllowedFigures,
    GuardrailViolation,
    Language,
    Project,
    SpeakableText,
)
from ambassador.sentences import split_sentences
from ambassador.verbalise import SpokenForms, load_spoken_forms

from .backends import ESCALATE_TOOL, BackendError, ModelBackend, ModelRequest
from .cases import EvalCase, ModelFixture
from .outcome import Observed, Spoken, TurnOutcome

@dataclass(frozen=True)
class Harness:
    """Everything loaded once, shared by every case.

    Built from the production data files, not from fixtures: the whole point of
    the exercise is that the figures the guardrail allows are the figures
    `data/inventory.json` holds (AGENTS.md - never invent inventory).
    """

    projects: list[Project]
    allowed: AllowedFigures
    patterns: list[ProhibitedPattern]
    forms: SpokenForms
    vocabulary: CurrencyVocabulary
    confirmations: ConfirmationCopy
    fallbacks: FallbackCopy
    inventory_block: str

    @classmethod
    def load(cls) -> Harness:
        projects = load_inventory()
        return cls(
            projects=projects,
            allowed=build_allowed_figures(projects),
            patterns=load_patterns(),
            forms=load_spoken_forms(),
            vocabulary=load_currency_vocabulary(),
            confirmations=load_confirmations(),
            fallbacks=load_fallback_copy(),
            inventory_block=serialise_for_prompt(projects),
        )

    def policy_runs(self, language: Language) -> bool:
        """Whether the deterministic budget policy owns the confirmation in
        this language, which is exactly the condition the prompt is built
        against."""
        return self.confirmations.covers(language)

    def prompt(self, language: Language) -> str:
        return build_ambassador_prompt(
            self.inventory_block,
            language,
            system_confirms_budget=self.policy_runs(language),
        )

    def prompt_fingerprint(self, language: Language = "en") -> str:
        """A short digest of the prompt under test, printed in the report.

        docs/05- makes the eval mandatory on every prompt change; a report that
        does not say which prompt produced it cannot be held to that.
        """
        digest = hashlib.sha256(self.prompt(language).encode("utf-8")).hexdigest()
        return digest[:12]


def run_case(case: EvalCase, harness: Harness, backend: ModelBackend) -> Observed:
    """Every turn of one case, in order, with the state the buyer's call has."""
    policy = BudgetPolicy(harness.vocabulary, case.language)
    policy_runs = harness.policy_runs(case.language)
    system_prompt = harness.prompt(case.language)
    history: list[tuple[str, str]] = []
    turns: list[TurnOutcome] = []

    for turn in case.turns:
        history.append(("user", turn.buyer))
        try:
            outcome = _run_turn(
                case=case,
                turn_index=len(turns),
                buyer=turn.buyer,
                fixture=turn.model,
                harness=harness,
                backend=backend,
                policy=policy,
                policy_runs=policy_runs,
                system_prompt=system_prompt,
                history=tuple(history),
            )
        except BackendError as exc:
            return Observed(
                language=case.language,
                forms=harness.forms,
                turns=tuple(turns),
                error=str(exc),
            )
        turns.append(outcome)
        # What the buyer heard is what the model sees next turn, not the raw
        # reply: a blocked sentence never reached them and must not reach the
        # context either, or the next turn is grounded in speech nobody said.
        spoken = " ".join(segment.spoken for segment in outcome.heard)
        if spoken:
            history.append(("assistant", spoken))

    return Observed(
        language=case.language, forms=harness.forms, turns=tuple(turns)
    )


def _run_turn(
    *,
    case: EvalCase,
    turn_index: int,
    buyer: str,
    fixture: ModelFixture | None,
    harness: Harness,
    backend: ModelBackend,
    policy: BudgetPolicy,
    policy_runs: bool,
    system_prompt: str,
    history: tuple[tuple[str, str], ...],
) -> TurnOutcome:
    confirmation = _budget_confirmation(policy, policy_runs, buyer, harness, case.language)
    if confirmation is not None:
        text, action, hands_over = confirmation
        return TurnOutcome(
            buyer=buyer,
            model_text="",
            # The confirmation deliberately bypasses verbalisation (see
            # adapter/confirmations.py): it echoes the buyer's transcript
            # verbatim, and verbalising it would assert a currency on the exact
            # turn whose purpose is to ask which one they meant.
            heard=(Spoken(validated=text, spoken=text, origin="confirmation"),),
            confirmed=action in ("ask_currency", "confirm_amount", "ask_amount"),
            escalation_reasons=(
                (f"budget policy: {action}",) if hands_over else ()
            ),
        )

    request = ModelRequest(
        case=case,
        system_prompt=system_prompt,
        messages=history,
        fixture=fixture,
    )
    reply = backend.reply(request)
    segments: list[Spoken] = []
    blocked: list[GuardrailViolation] = []
    reasons: list[str] = []
    retried: list[str] = []
    if ESCALATE_TOOL in reply.tools:
        reasons.append(f"{ESCALATE_TOOL} (turn {turn_index})")
    regenerated = _speak(
        text=reply.text,
        harness=harness,
        language=case.language,
        segments=segments,
        blocked=blocked,
        reasons=reasons,
        retried=retried,
        backend=backend,
        request=request,
        turn_index=turn_index,
        already_regenerated=False,
    )
    _backstop_regeneration(
        regenerated=regenerated,
        segments=segments,
        reasons=reasons,
        turn_index=turn_index,
    )
    return TurnOutcome(
        buyer=buyer,
        model_text=reply.text,
        heard=tuple(segments),
        blocked=tuple(blocked),
        regenerated_text=retried[0] if retried else "",
        escalation_reasons=tuple(reasons),
        regenerated=regenerated,
    )


def _backstop_regeneration(
    *,
    regenerated: bool,
    segments: list[Spoken],
    reasons: list[str],
    turn_index: int,
) -> None:
    """Mirrors `adapter/agent.py:_backstop_regeneration`, which owns the
    reasoning: a regenerated reply that ends the turn stating no figure has
    refused, and a refusal promises a colleague, so it routes one
    deterministically rather than depending on the model calling the tool
    (issue #33 - Arabic fired 1/3 and temperature 0 did not repeat).

    Every segment present after a regeneration came FROM the regeneration: the
    retry branch only runs while nothing has been spoken yet, on both sides of
    the twin.

    Skipped when the composed fallback already routed, which is this harness's
    equivalent of the adapter reading `tracker.handed_over`. A tool call the
    model made is not checked, for the reason the adapter states: the live path
    cannot see it yet, and one handover per turn is enforced there.
    """
    if not regenerated:
        return
    if any(segment.origin == "fallback" for segment in segments):
        return
    if any(
        segment.origin == "model" and states_a_figure(segment.validated)
        for segment in segments
    ):
        return
    reasons.append(f"regenerated reply stated no inventory figure (turn {turn_index})")


def _budget_confirmation(
    policy: BudgetPolicy,
    policy_runs: bool,
    buyer: str,
    harness: Harness,
    language: Language,
) -> tuple[str, str, bool] | None:
    """(copy, action, hands_over) to speak instead of running the turn, or None.

    Mirrors `adapter/agent.py:_budget_confirmation`, including its failure
    direction: ANY exception in the confirmation machinery is a handover, never
    a model turn and never silence. The catch is deliberately `Exception` for
    the reason recorded there - a curated list let AttributeError and TypeError
    escape into a silent turn, which the retry then converted into an
    unconfirmed model answer.
    """
    if not policy_runs:
        return None
    try:
        decision = policy.observe(buyer)
        if not decision.speaks:
            return None
        template = harness.confirmations.line(language, decision.action)
        if decision.mention is not None:
            text = compose_confirmation(
                template,
                echoed=decision.mention.surface,
                said=decision.mention.utterance,
            )
        elif "{" in template:
            raise UnspeakableConfirmation(
                f"{decision.action!r} copy carries a slot and the decision has "
                "nothing to fill it with"
            )
        else:
            text = template
        if not text.strip():
            raise UnspeakableConfirmation(
                f"no confirmation copy composed for {decision.action!r}"
            )
        return text, decision.action, decision.hands_over
    except Exception:
        policy.abandon()
        return (
            harness.confirmations.line(language, "give_up"),
            "give_up",
            True,
        )


def _speak(
    *,
    text: str,
    harness: Harness,
    language: Language,
    segments: list[Spoken],
    blocked: list[GuardrailViolation],
    reasons: list[str],
    retried: list[str],
    backend: ModelBackend,
    request: ModelRequest,
    turn_index: int,
    already_regenerated: bool,
) -> bool:
    """Push one model reply through the guardrails. Returns whether a
    regeneration was spent.

    The recovery policy is docs/01-'s, verbatim: nothing spoken yet means
    cancel and regenerate once with the violation named, then the composed
    fallback; audio already played means a composed bridge and no retry,
    because a blind mid-turn retry repeats or contradicts what the buyer just
    heard. Both halts drop the rest of the reply.
    """
    regenerated = already_regenerated
    sentences, remainder = split_sentences(text)
    if remainder.strip():
        # The stream has ended, so the trailing fragment is a whole sentence.
        sentences = [*sentences, remainder.strip()]

    for sentence in sentences:
        result = process_sentence(
            sentence, language, harness.allowed, harness.patterns, harness.forms
        )
        if isinstance(result, SpeakableText):
            segments.append(
                Spoken(validated=sentence, spoken=result.text, origin="model")
            )
            continue

        blocked.append(result)
        if not segments and not regenerated:
            detail = REGENERATION_INSTRUCTION.format(detail=result.detail)
            retry = backend.reply(replace(request, regeneration_detail=detail))
            retried.append(retry.text)
            # The regeneration is a fresh generation: its tool calls reach the
            # framework exactly as the first attempt's would, so an escalation
            # decided on the second try counts. Missing this credited nothing to
            # a model that refused correctly the moment it was told why.
            if ESCALATE_TOOL in retry.tools:
                reasons.append(f"{ESCALATE_TOOL} (turn {turn_index}, regenerated)")
            return _speak(
                text=retry.text,
                harness=harness,
                language=language,
                segments=segments,
                blocked=blocked,
                reasons=reasons,
                retried=retried,
                backend=backend,
                request=request,
                turn_index=turn_index,
                already_regenerated=True,
            )

        bridging = bool(segments)
        copy = (
            harness.fallbacks.bridge[language]
            if bridging
            else harness.fallbacks.fallback[language]
        )
        if not bridging:
            # Verbatim from `adapter/agent.py:_speak_fallback`: the fallback copy
            # is the line that promises a human, so speaking it books one. The
            # bridge promises nothing and routes nobody, which is why this sits
            # inside the branch rather than above it.
            reasons.append(f"composed fallback: guardrail (turn {turn_index})")
        composed = process_sentence(
            copy, language, harness.allowed, harness.patterns, harness.forms
        )
        if not isinstance(composed, SpeakableText):
            # Composed copy that fails our own guardrails is a defect in the
            # copy, not something to speak anyway - the same assertion
            # SentenceGuard.compose() makes.
            raise AssertionError(
                f"composed {'bridge' if bridging else 'fallback'} copy violates "
                f"{composed.validator}: {composed.detail}"
            )
        segments.append(
            Spoken(
                validated=copy,
                spoken=composed.text,
                origin="bridge" if bridging else "fallback",
            )
        )
        return regenerated
    return regenerated
