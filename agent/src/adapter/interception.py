"""Hook 1: the guardrail interception between the LLM and TTS.

This is step 6 of the turn flow (docs/01-) and the answer to "how do you stop
it speaking a price it made up". Text streaming out of the model is buffered
to sentence boundaries, and every completed sentence goes through
`ambassador.guardrails.pipeline.process_sentence()` - the single public
producer of `SpeakableText` - before anything reaches TTS.

Deliberately written as a standalone async generator rather than inline in the
Agent subclass, so the guarantee can be tested against a fake LLM stream and a
fake TTS sink with no framework session, no audio device, and no network.

Regeneration policy, verbatim from docs/01-:

  - nothing synthesised yet -> cancel, regenerate once with the violation
    named, then composed fallback
  - audio already played  -> no regeneration; a composed bridge plus the
    escalation, because a blind mid-turn retry repeats or contradicts what the
    buyer already heard

`GUARDRAIL_MODE=warn` logs the violation and passes the sentence through
unmodified. That pairing with `PROMPT_MODE=naive` is the defence-in-depth
demonstration (docs/03-), so warn mode must be genuinely inert - it may not
quietly repair anything.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from livekit.agents.voice.io import FlushSentinel

from ambassador.guardrails.pipeline import process_sentence
from ambassador.guardrails.prohibited import ProhibitedPattern
from ambassador.schemas import (
    AllowedFigures,
    GuardrailViolation,
    Language,
    SpeakableText,
)
from ambassador.verbalise import SpokenForms

# Composed failure speech. Never model-generated: a bridge that varies is not a
# bridge. English is the only language this build team self-certifies
# (AGENTS.md), so ar/hi carry VERIFY: markers and must not ship unreviewed.
#
# These belong in a reviewable data file next to data/prerolls.yaml rather than
# in adapter code; they are here only because day 1 must not modify data/.
BRIDGE_COPY: dict[Language, str] = {
    "en": "Let me be precise about that figure rather than guess.",
    # VERIFY: native-authored Arabic bridge copy (day 3 native review)
    "ar": "Let me be precise about that figure rather than guess.",
    # VERIFY: native-authored Hindi bridge copy (day 3 native review)
    "hi": "Let me be precise about that figure rather than guess.",
}

FALLBACK_COPY: dict[Language, str] = {
    "en": (
        "I do not want to quote you anything I cannot confirm. "
        "Let me put you through to one of our ambassadors."
    ),
    # VERIFY: native-authored Arabic fallback copy (day 3 native review)
    "ar": (
        "I do not want to quote you anything I cannot confirm. "
        "Let me put you through to one of our ambassadors."
    ),
    # VERIFY: native-authored Hindi fallback copy (day 3 native review)
    "hi": (
        "I do not want to quote you anything I cannot confirm. "
        "Let me put you through to one of our ambassadors."
    ),
}

# A boundary is terminal punctuation followed by whitespace. Requiring the
# whitespace is what keeps "AED 1.5 million" and "Q4 2026" intact while the
# stream is still arriving; the trailing fragment is flushed when the stream
# ends.
_BOUNDARY = re.compile(r"(?<=[.!?؟।])\s+")


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split a streaming buffer into completed sentences plus the remainder."""
    if not buffer:
        return [], ""
    parts = _BOUNDARY.split(buffer)
    remainder = parts.pop() if parts else ""
    complete = [p.strip() for p in parts if p.strip()]
    return complete, remainder


def _with_separator(text: str) -> str:
    """Sentences are yielded one at a time and the framework concatenates them
    verbatim, so the boundary whitespace the split consumed has to come back.
    Without it TTS receives "...dirhams.Would you like" and reads it as one
    word."""
    return text if text.endswith((" ", "\n")) else text + " "


@dataclass(frozen=True)
class GuardDecision:
    outcome: str  # "pass" | "violation_blocked" | "violation_warned"
    raw: str
    spoken: str | None
    violation: GuardrailViolation | None
    elapsed_ms: float


class SentenceGuard:
    """Binds the core pipeline to one session's language, inventory and mode."""

    def __init__(
        self,
        *,
        language: Language,
        allowed: AllowedFigures,
        patterns: list[ProhibitedPattern],
        forms: SpokenForms,
        mode: str = "enforce",
    ) -> None:
        self.language = language
        self.allowed = allowed
        self.patterns = patterns
        self.forms = forms
        self.mode = mode

    def check(self, raw: str) -> GuardDecision:
        started = time.perf_counter()
        result = process_sentence(
            raw, self.language, self.allowed, self.patterns, self.forms
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        if isinstance(result, SpeakableText):
            return GuardDecision("pass", raw, result.text, None, elapsed_ms)

        if self.mode == "warn":
            # Inert by design: the sentence goes to TTS exactly as the model
            # wrote it, and only the log records that it would have been
            # blocked.
            return GuardDecision("violation_warned", raw, raw, result, elapsed_ms)

        return GuardDecision("violation_blocked", raw, None, result, elapsed_ms)

    def compose(self, text: str) -> str:
        """Route composed speech through the same single public path, so the
        bridge and fallback are held to the invariant they exist to uphold."""
        result = process_sentence(
            text, self.language, self.allowed, self.patterns, self.forms
        )
        if isinstance(result, SpeakableText):
            return result.text
        # Composed copy that fails our own guardrails is a defect in the copy,
        # not something to speak anyway.
        raise AssertionError(
            f"composed fallback copy violates {result.validator}: {result.detail}"
        )


# A regeneration factory: given the violation detail, produce a fresh stream.
Regenerator = Callable[[str], Coroutine[Any, Any, AsyncIterable[Any]]]


class _Sink:
    """Callbacks the caller supplies so this module stays free of the event
    log and the framework's chunk types."""

    def __init__(
        self,
        *,
        on_decision: Callable[[GuardDecision], None] | None = None,
        on_first_content: Callable[[], None] | None = None,
        on_first_sentence: Callable[[], None] | None = None,
        on_regeneration: Callable[[str], None] | None = None,
        on_bridge: Callable[[str], None] | None = None,
        on_fallback: Callable[[str], None] | None = None,
    ) -> None:
        self.on_decision = on_decision
        self.on_first_content = on_first_content
        self.on_first_sentence = on_first_sentence
        self.on_regeneration = on_regeneration
        # The two recoveries are distinct (docs/01-): a bridge means audio had
        # already played, a fallback means nothing had.
        self.on_bridge = on_bridge
        self.on_fallback = on_fallback


def _content_of(chunk: Any) -> str | None:
    """Text carried by a chunk, whether it is a plain str or a ChatChunk."""
    if isinstance(chunk, str):
        return chunk
    delta = getattr(chunk, "delta", None)
    if delta is None:
        return None
    return getattr(delta, "content", None)


def _is_passthrough(chunk: Any) -> bool:
    """Tool calls and usage frames must reach the framework untouched: hook 2
    depends on tool-call deltas arriving exactly as the provider sent them."""
    if isinstance(chunk, str):
        return False
    if getattr(chunk, "usage", None) is not None:
        return True
    delta = getattr(chunk, "delta", None)
    if delta is None:
        return True
    return bool(getattr(delta, "tool_calls", None))


async def guarded_stream(
    source: AsyncIterable[Any],
    *,
    guard: SentenceGuard,
    sink: _Sink | None = None,
    regenerate: Regenerator | None = None,
    flush_per_sentence: bool = True,
) -> AsyncIterator[Any]:
    """Yield only text that has passed `process_sentence()`, plus untouched
    tool-call and usage chunks.

    `flush_per_sentence` emits the framework's `FlushSentinel` after each
    approved sentence, which closes that speech segment and lets synthesis
    start immediately instead of waiting on the rest of the generation. This is
    the documented mechanism for it, and measured across live runs it moved
    Fish's time-to-first-audio from a 371-612ms spread (p50 492ms) to 385-405ms
    (p50 389ms) - about 100ms off the voice-to-voice budget, and far less
    variance, which matters more for a rehearsed demo than the median does.
    """
    sink = sink or _Sink()
    spoken_anything = False
    regenerated = False
    saw_content = False

    async def run(stream: AsyncIterable[Any]) -> AsyncIterator[Any]:
        nonlocal spoken_anything, regenerated, saw_content
        buffer = ""

        async for chunk in stream:
            if _is_passthrough(chunk):
                yield chunk
                # A tool-call chunk may also carry text; fall through to it.
                content = _content_of(chunk)
                if not content:
                    continue
            else:
                content = _content_of(chunk)
                if not content:
                    continue

            if not saw_content:
                saw_content = True
                if sink.on_first_content:
                    sink.on_first_content()

            buffer += content
            sentences, buffer = split_sentences(buffer)
            for sentence in sentences:
                async for out in handle(sentence):
                    yield out
                if _halted:
                    return

        tail = buffer.strip()
        if tail:
            async for out in handle(tail):
                yield out

    _halted = False

    async def handle(sentence: str) -> AsyncIterator[Any]:
        nonlocal spoken_anything, regenerated, _halted

        if sink.on_first_sentence:
            sink.on_first_sentence()

        decision = guard.check(sentence)
        if sink.on_decision:
            sink.on_decision(decision)

        if decision.spoken is not None:
            spoken_anything = True
            yield _with_separator(decision.spoken)
            if flush_per_sentence:
                yield FlushSentinel()
            return

        # Blocked. Which recovery applies depends only on whether the buyer has
        # already heard something this turn.
        if not spoken_anything and not regenerated and regenerate is not None:
            regenerated = True
            detail = decision.violation.detail if decision.violation else "unknown"
            if sink.on_regeneration:
                sink.on_regeneration(detail)
            retry = await regenerate(detail)
            async for out in run(retry):
                yield out
            _halted = True
            return

        # Audio already played, or the retry has been spent: composed speech.
        bridging = spoken_anything
        text = BRIDGE_COPY[guard.language] if bridging else FALLBACK_COPY[guard.language]
        composed = guard.compose(text)
        if bridging:
            if sink.on_bridge:
                sink.on_bridge(composed)
        elif sink.on_fallback:
            sink.on_fallback(composed)
        spoken_anything = True
        yield _with_separator(composed)
        if flush_per_sentence:
            yield FlushSentinel()
        _halted = True

    async for out in run(source):
        yield out
