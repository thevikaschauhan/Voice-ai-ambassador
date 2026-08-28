"""Hook 1: the guardrail interception between LLM and TTS.

The claim under test is the one the demo is built on - a fabricated figure
cannot reach synthesis. So the assertions are made where it matters: on what a
fake TTS sink actually received, not on the return value of a validator that
something downstream might ignore.

A fake LLM stream stands in for the provider, so the guarantee is exercised
with no network, no audio device and no framework session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

# ADR-002: the core stays installable and testable with no voice stack present
# (`uv sync --no-group voice`). These adapter tests need the framework, so they
# skip rather than turn that guarantee into a collection error.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents.voice.io import FlushSentinel  # noqa: E402

from adapter.interception import (  # noqa: E402
    BRIDGE_COPY,
    FALLBACK_COPY,
    SentenceGuard,
    _Sink,
    guarded_stream,
    split_sentences,
)

# 800,000 is the planted-false-premise figure from the docs/03- trap. It is not
# in the inventory, so it must never be spoken.
FABRICATED = "800,000"
GROUNDED = "985,000"


# --- fakes ----------------------------------------------------------------


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[Any] = field(default_factory=list)


@dataclass
class FakeChatChunk:
    """Shaped like livekit.agents.llm.ChatChunk for the fields the interception
    reads: `.delta.content`, `.delta.tool_calls`, `.usage`."""

    delta: FakeDelta | None = None
    usage: Any = None


async def fake_llm_stream(deltas: list[str]) -> AsyncIterator[FakeChatChunk]:
    for piece in deltas:
        yield FakeChatChunk(delta=FakeDelta(content=piece))


class FakeTTS:
    """Stands in for the framework's TTS node: it only ever sees strings."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def push(self, chunk: Any) -> None:
        if isinstance(chunk, str):
            self.spoken.append(chunk)

    @property
    def text(self) -> str:
        return " ".join(self.spoken)


async def drain(stream: AsyncIterator[Any]) -> tuple[FakeTTS, list[Any]]:
    """Split the guarded stream the way the framework does: strings go to TTS,
    everything else (tool calls, usage, flush markers) is control flow."""
    tts = FakeTTS()
    passthrough: list[Any] = []
    async for chunk in stream:
        tts.push(chunk)
        if not isinstance(chunk, str):
            passthrough.append(chunk)
    return tts, passthrough


def without_flushes(chunks: list[Any]) -> list[Any]:
    return [c for c in chunks if not isinstance(c, FlushSentinel)]


@pytest.fixture
def guard(allowed, patterns, forms):
    def _make(mode: str = "enforce", language: str = "en") -> SentenceGuard:
        return SentenceGuard(
            language=language,
            allowed=allowed,
            patterns=patterns,
            forms=forms,
            mode=mode,
        )

    return _make


# --- sentence chunking ----------------------------------------------------


def test_splits_on_terminal_punctuation_followed_by_space():
    complete, remainder = split_sentences("One. Two! Three? And a frag")
    assert complete == ["One.", "Two!", "Three?"]
    assert remainder == "And a frag"


def test_does_not_split_a_decimal_or_a_thousands_separator():
    # "AED 1.5 million" and "985,000" must survive chunking intact, or the
    # guardrail would inspect half a figure.
    complete, remainder = split_sentences("It is AED 1.5 million and 985,000 total")
    assert complete == []
    assert remainder == "It is AED 1.5 million and 985,000 total"


def test_trailing_sentence_is_not_emitted_until_the_stream_ends():
    # No whitespace after the full stop yet: the model may still be writing a
    # decimal, so the boundary is not final.
    complete, remainder = split_sentences("The studio is 985,000.")
    assert complete == []
    assert remainder == "The studio is 985,000."


# --- hook 1: enforce mode -------------------------------------------------


async def test_fabricated_figure_never_reaches_tts_in_enforce_mode(guard):
    stream = fake_llm_stream(
        [
            "Marina Heights ",
            "starts at ",
            f"AED {FABRICATED}. ",
            "It is a fine building.",
        ]
    )
    tts, _ = await drain(guarded_stream(stream, guard=guard("enforce")))

    assert FABRICATED not in tts.text
    assert "800" not in tts.text
    # The turn still ends in composed speech: never silence (AGENTS.md).
    assert tts.text.strip() == FALLBACK_COPY["en"]


async def test_grounded_figure_is_spoken_and_verbalised(guard):
    stream = fake_llm_stream(["A studio at Skyrise is ", f"AED {GROUNDED}. "])
    tts, _ = await drain(guarded_stream(stream, guard=guard("enforce")))

    # Verbalisation has run (ADR-009): digits are gone, the spoken form is not.
    assert GROUNDED not in tts.text
    assert "nine hundred and eighty-five thousand" in tts.text.lower()


async def test_only_the_violating_sentence_is_withheld_when_it_comes_first(guard):
    stream = fake_llm_stream([f"Marina Heights is AED {FABRICATED}. ", "Shall I check?"])
    tts, _ = await drain(guarded_stream(stream, guard=guard("enforce")))
    assert FABRICATED not in tts.text


# --- hook 1: warn mode ----------------------------------------------------


async def test_warn_mode_passes_the_violating_sentence_through_unmodified(guard):
    """The defence-in-depth demo (docs/03-) depends on warn mode being inert.
    If warn quietly repaired anything, the toggle would prove nothing."""
    sentence = f"Marina Heights starts at AED {FABRICATED}. "
    stream = fake_llm_stream([sentence])
    tts, _ = await drain(guarded_stream(stream, guard=guard("warn")))

    assert FABRICATED in tts.text
    assert tts.text.strip() == sentence.strip()


async def test_warn_and_enforce_differ_only_in_the_outcome(guard, allowed, patterns, forms):
    decisions: list[str] = []
    for mode in ("enforce", "warn"):
        g = guard(mode)
        decision = g.check(f"It is AED {FABRICATED}.")
        decisions.append(decision.outcome)
        # Both modes inspected the sentence and named the same validator.
        assert decision.violation is not None
        assert decision.violation.validator == "numeric_claims"
    assert decisions == ["violation_blocked", "violation_warned"]


# --- hook 1: regeneration policy (docs/01-) -------------------------------


async def test_nothing_spoken_yet_regenerates_once_with_the_violation_named(guard):
    named: list[str] = []

    async def regenerate(detail: str):
        named.append(detail)
        return fake_llm_stream([f"A studio at Skyrise is AED {GROUNDED}. "])

    stream = fake_llm_stream([f"Marina Heights is AED {FABRICATED}. "])
    tts, _ = await drain(
        guarded_stream(stream, guard=guard("enforce"), regenerate=regenerate)
    )

    assert len(named) == 1
    assert FABRICATED in named[0]  # the retry prompt names the bad figure
    assert FABRICATED not in tts.text
    assert "nine hundred and eighty-five thousand" in tts.text.lower()


async def test_audio_already_played_gets_a_bridge_and_no_regeneration(guard):
    """A blind mid-turn retry repeats or contradicts what the buyer already
    heard; the bridge cannot (docs/01- regeneration policy)."""
    attempts: list[str] = []

    async def regenerate(detail: str):
        attempts.append(detail)
        return fake_llm_stream(["should not be reached"])

    stream = fake_llm_stream(
        [
            "Skyrise is a strong choice. ",  # spoken first, no figures
            f"Marina Heights is AED {FABRICATED}. ",
        ]
    )
    tts, _ = await drain(
        guarded_stream(stream, guard=guard("enforce"), regenerate=regenerate)
    )

    assert attempts == []  # regeneration skipped entirely
    assert FABRICATED not in tts.text
    assert BRIDGE_COPY["en"] in tts.text
    assert "Skyrise is a strong choice." in tts.text


async def test_a_second_violation_after_regeneration_falls_back(guard):
    async def regenerate(detail: str):
        return fake_llm_stream([f"Still AED {FABRICATED}. "])

    stream = fake_llm_stream([f"Marina Heights is AED {FABRICATED}. "])
    tts, _ = await drain(
        guarded_stream(stream, guard=guard("enforce"), regenerate=regenerate)
    )

    assert FABRICATED not in tts.text
    assert tts.text.strip() == FALLBACK_COPY["en"]


# --- the two recoveries are different events (docs/01-) -------------------


async def test_nothing_spoken_yet_reports_a_fallback_not_a_bridge(guard):
    """docs/01- distinguishes them and the audit has to as well: a bridge means
    the buyer heard a seam, a fallback means the composed copy WAS the reply."""
    bridges: list[str] = []
    fallbacks: list[str] = []
    sink = _Sink(on_bridge=bridges.append, on_fallback=fallbacks.append)

    stream = fake_llm_stream([f"Marina Heights is AED {FABRICATED}. "])
    tts, _ = await drain(guarded_stream(stream, guard=guard("enforce"), sink=sink))

    assert bridges == []
    assert fallbacks == [FALLBACK_COPY["en"]]
    assert tts.text.strip() == FALLBACK_COPY["en"]


async def test_audio_already_played_reports_a_bridge_not_a_fallback(guard):
    bridges: list[str] = []
    fallbacks: list[str] = []
    sink = _Sink(on_bridge=bridges.append, on_fallback=fallbacks.append)

    stream = fake_llm_stream(
        [
            "Skyrise is a strong choice. ",
            f"Marina Heights is AED {FABRICATED}. ",
        ]
    )
    tts, _ = await drain(guarded_stream(stream, guard=guard("enforce"), sink=sink))

    assert fallbacks == []
    assert bridges == [BRIDGE_COPY["en"]]
    assert BRIDGE_COPY["en"] in tts.text


async def test_a_spent_retry_with_nothing_spoken_is_still_a_fallback(guard):
    """The regeneration also failed, so the turn ends on composed speech - but
    the buyer has still heard nothing, so it is not a bridge."""
    bridges: list[str] = []
    fallbacks: list[str] = []
    sink = _Sink(on_bridge=bridges.append, on_fallback=fallbacks.append)

    async def regenerate(detail: str):
        return fake_llm_stream([f"Still AED {FABRICATED}. "])

    stream = fake_llm_stream([f"Marina Heights is AED {FABRICATED}. "])
    await drain(
        guarded_stream(
            stream, guard=guard("enforce"), sink=sink, regenerate=regenerate
        )
    )

    assert bridges == []
    assert fallbacks == [FALLBACK_COPY["en"]]


# --- hook 2's dependency: tool chunks must pass through untouched ---------


async def test_tool_call_chunks_pass_through_unaltered(guard):
    """The interception rewrites text. If it also touched tool-call deltas,
    hook 2 would break, so the identity of those objects is asserted."""
    tool_chunk = FakeChatChunk(delta=FakeDelta(content=None, tool_calls=["escalate"]))
    usage_chunk = FakeChatChunk(delta=None, usage={"completion_tokens": 12})

    async def mixed() -> AsyncIterator[FakeChatChunk]:
        yield FakeChatChunk(delta=FakeDelta(content="Let me check that. "))
        yield tool_chunk
        yield usage_chunk

    tts, passthrough = await drain(guarded_stream(mixed(), guard=guard("enforce")))

    assert without_flushes(passthrough) == [tool_chunk, usage_chunk]
    assert without_flushes(passthrough)[0] is tool_chunk
    assert "Let me check that." in tts.text


# --- segment flushing (TTS latency) ---------------------------------------


async def test_each_approved_sentence_closes_its_speech_segment(guard):
    """The flush is what lets Fish start synthesising sentence one instead of
    waiting for the whole generation; measured live it removed ~100ms and most
    of the variance from time-to-first-audio."""
    stream = fake_llm_stream(["First sentence here. ", "Second one follows. "])
    _, passthrough = await drain(guarded_stream(stream, guard=guard("enforce")))

    assert len(passthrough) == 2
    assert all(isinstance(c, FlushSentinel) for c in passthrough)


async def test_flushing_can_be_turned_off(guard):
    stream = fake_llm_stream(["First sentence here. ", "Second one follows. "])
    tts, passthrough = await drain(
        guarded_stream(stream, guard=guard("enforce"), flush_per_sentence=False)
    )

    assert passthrough == []
    assert "First sentence here." in tts.text
    assert "Second one follows." in tts.text


# --- composed copy is held to the same invariant --------------------------


def test_composed_bridge_and_fallback_pass_the_guardrails(guard):
    g = guard("enforce")
    # compose() raises if the copy would itself violate; a silent pass here is
    # the assertion.
    assert g.compose(BRIDGE_COPY["en"])
    assert g.compose(FALLBACK_COPY["en"])
