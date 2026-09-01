"""Text mode, asserted on the events a buyer's turn actually produces.

The claim under test is not "the module runs" - it is that the venue plan B
puts the SAME pipeline in front of the room as the call does. So every
assertion here is about what the surface would render: the sentence that was
spoken, the sentence that was blocked and why, and whether a human was
notified.

No model is called. A stub backend stands in for the LLM, because none of these
properties are properties of the model - they are properties of the guardrail,
the recovery policy and the escalation routing, and paying a provider to
re-prove them on every test run would be a tax with no evidence attached.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from adapter.textmode import TextSession
from evals.backends import BackendError, ModelReply, ModelRequest
from evals.runner import Harness


@dataclass
class StubBackend:
    """Replies from a script, and records what it was asked."""

    replies: list[str]
    name: str = "stub"
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def reply(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        text = self.replies.pop(0) if self.replies else "I cannot confirm that."
        return ModelReply(text=text, tools=self.tools)


class FailingBackend:
    name = "failing"

    def reply(self, request: ModelRequest) -> ModelReply:
        raise BackendError("the model could not be reached")


@pytest.fixture(scope="module")
def harness() -> Harness:
    return Harness.load()


def session(harness: Harness, backend: object) -> TextSession:
    # extractor=None runs without brief extraction; omitting it builds the real one.
    return TextSession(harness=harness, backend=backend, extractor=None)


def kinds(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


def only(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event["event"] == kind]


async def test_a_grounded_answer_reaches_the_buyer_verbalised(harness):
    talk = session(
        harness,
        StubBackend(["Binghatti Skyrise in Business Bay starts from AED 985,000."]),
    )
    events = await talk.turn("What does the Skyrise start at?")

    assert kinds(events)[0] == "user_turn"
    passed = only(events, "guardrail")
    assert [event["outcome"] for event in passed] == ["pass"]
    # The buyer hears the spoken form, and the audit keeps the digits.
    assert "985,000" in passed[0]["raw"]
    assert "985,000" not in passed[0]["spoken"]
    assert "eighty-five thousand" in passed[0]["spoken"]
    await talk.aclose()


async def test_a_fabricated_figure_never_reaches_the_buyer(harness):
    # 20 million is not in inventory, so it is not in the allowed set.
    talk = session(
        harness,
        StubBackend(
            [
                "Bugatti Residences start from around AED 20,000,000.",
                "I cannot confirm a price for that collection.",
            ]
        ),
    )
    events = await talk.turn("What does a Bugatti two-bedroom cost?")

    blocked = [e for e in only(events, "guardrail") if e["outcome"] == "blocked"]
    assert blocked, "the fabricated figure was not blocked"
    assert blocked[0]["spoken"] is None
    assert blocked[0]["validator"] == "numeric_claims"
    assert "20000000" in blocked[0]["detail"].replace(",", "")

    spoken = " ".join(
        e.get("text") or e.get("spoken") or ""
        for e in events
        if e["event"] in {"guardrail", "bridge", "fallback"}
    )
    assert "20,000,000" not in spoken
    assert "20000000" not in spoken.replace(",", "")
    await talk.aclose()


async def test_the_one_repair_retry_is_spent_and_reported(harness):
    talk = session(
        harness,
        StubBackend(
            [
                "Binghatti Skyrise starts from AED 950,000.",
                "Binghatti Skyrise starts from AED 985,000.",
            ]
        ),
    )
    events = await talk.turn("What does the Skyrise start at?")

    assert only(events, "regeneration"), "the retry was not reported"
    # And the corrected figure is the one the buyer heard.
    spoken = [e["spoken"] for e in only(events, "guardrail") if e["spoken"]]
    assert any("eighty-five thousand" in text for text in spoken)
    assert only(events, "turn_complete")[0]["regenerated"] is True
    await talk.aclose()


async def test_an_escalation_notifies_a_human_and_says_so(harness):
    backend = StubBackend(["Let me get an ambassador for you."])
    backend.tools = ("escalate_to_human",)
    talk = session(harness, backend)
    events = await talk.turn("I want to speak to a person.")

    assert only(events, "escalation"), "nobody was notified"
    assert only(events, "escalation")[0]["routed_to"] == "human_ambassador"
    # Both halves, the way the voice path emits them.
    assert only(events, "tool_call")[0]["tool"] == "escalate_to_human"
    assert only(events, "turn_complete")[0]["actions"] == ["escalate_to_human"]
    await talk.aclose()


async def test_a_turn_never_ends_in_silence_when_the_model_is_unreachable(harness):
    talk = session(harness, FailingBackend())
    events = await talk.turn("What does the Skyrise start at?")

    fallback = only(events, "fallback")
    assert fallback, "the buyer got nothing"
    assert "ambassador" in fallback[0]["text"].lower()
    # And the promise is kept: somebody is actually notified.
    assert only(events, "escalation")
    # The error is reported as an event rather than raised at the surface.
    assert only(events, "session_error")
    await talk.aclose()


async def test_a_typed_turn_reports_no_endpointing_rather_than_zero(harness):
    talk = session(harness, StubBackend(["Binghatti Skyrise starts from AED 985,000."]))
    events = await talk.turn("What does the Skyrise start at?")

    complete = only(events, "turn_complete")[0]
    # A typed turn has no end-of-utterance, no recogniser and no synthesis.
    # events.py's rule: a missing measurement and a zero-latency stage must not
    # look the same on the meter.
    for stage in ("endpoint_ms", "stt_ms", "tts_first_audio_ms", "llm_ttft_ms"):
        assert complete[stage] is None, f"{stage} should be absent, not zero"
    assert complete["total_ms"] > 0
    await talk.aclose()


async def test_the_buyer_hears_the_context_the_next_turn_is_grounded_in(harness):
    backend = StubBackend(
        [
            "Binghatti Skyrise starts from AED 950,000.",
            "Binghatti Skyrise starts from AED 985,000.",
            "The booking payment is 20%.",
        ]
    )
    talk = session(harness, backend)
    await talk.turn("What does the Skyrise start at?")
    await talk.turn("And upfront?")

    # The blocked sentence never reached the buyer, so it must not reach the
    # model's context either, or turn two is grounded in speech nobody said.
    history = "\n".join(
        content for role, content in backend.requests[-1].messages if role == "assistant"
    )
    assert "950,000" not in history
    assert "eighty-five thousand" in history
    await talk.aclose()
