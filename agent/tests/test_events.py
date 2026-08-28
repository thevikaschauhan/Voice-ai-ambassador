"""The event stream: what leaves the process, and what it costs to write.

Two separate claims are under test here, and they pull in opposite directions,
which is why they are asserted together:

  redaction   docs/02- and docs/03- say PII never lands in an emitted or
              durable stream, and the rule the adapter applies is wider than
              "PII": any free-text field that can carry model-spoken or
              buyer-derived content goes, because the prompt has the model read
              the buyer's budget back and that number then travels through the
              agent's own sentence, the validator's detail and the tool
              arguments. The in-memory `TurnRecord` still has to carry the
              buyer's exact words, because the ambassador view and the audit
              are built on them. So the assertions are made on BOTH sides: the
              text is absent from the emitted line and present in the record.
  no blocking two of these emits are the TTFT and TTS-first-audio marks. A
              synchronous print plus a file flush on that path is latency the
              latency meter itself is causing, so writes are queued.

No framework imports: `events.py` is core-adjacent and these tests run in
core-only mode (`uv sync --no-group voice`) as well as the full one.
"""

from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

from adapter.events import EventLog, TurnTracker
from ambassador.schemas import ExtractedFigure, GuardrailViolation

UTTERANCE = "My budget is about two million and I am buying from Mumbai"

BRIEF = {
    "intent": "invest",
    "budget": {"amount": 2000000.0, "currency": "AED", "confirmed": True},
    "unit_preference": "a high floor with a marina view",
    "timeline": "6 months",
    "buyer_location": "Mumbai",
    "golden_visa_interest": True,
    "hesitations": ["worried about the handover date"],
    "shortlist_ids": ["binghatti-skyrise"],
    "stage": "discovery",
    "language": "en",
}


def make_log(**kwargs) -> tuple[EventLog, StringIO]:
    buf = StringIO()
    return EventLog("sess_test", stream=buf, **kwargs), buf


def lines(buf: StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def make_tracker(log: EventLog, utterance: str = UTTERANCE) -> TurnTracker:
    return TurnTracker(
        log,
        turn_index=1,
        buyer_utterance=utterance,
        language="en",
        model="qwen/qwen3.7-flash",
        prompt_mode="ambassador",
        guardrail_mode="enforce",
        inventory_version="2-records",
    )


# --- redaction is the default --------------------------------------------


def test_the_buyer_utterance_never_reaches_the_emitted_stream():
    log, buf = make_log(verbose=False)
    record = log.emit("user_turn", turn=1, text=UTTERANCE)
    log.close()

    # The caller still gets the full record; only the stream is redacted.
    assert record["text"] == UTTERANCE
    assert lines(buf)[0]["text"] == "[redacted]"
    assert "Mumbai" not in buf.getvalue()


def test_the_in_memory_turn_record_keeps_the_utterance_the_stream_dropped():
    """The audit and the ambassador view are built on the full text. Redacting
    the stream must not cost them anything."""
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    log.emit("user_turn", turn=1, text=UTTERANCE)
    tracker.finish()
    log.close()

    assert log.turns[0].buyer_utterance == UTTERANCE
    assert UTTERANCE not in buf.getvalue()


def test_brief_events_emit_only_the_non_pii_fields():
    log, buf = make_log(verbose=False)
    log.emit("brief", turn=1, attempt="first", brief=BRIEF, model="m")
    log.close()

    emitted = lines(buf)[0]["brief"]
    assert emitted == {
        "intent": "invest",
        "stage": "discovery",
        "language": "en",
        "shortlist_ids": ["binghatti-skyrise"],
        "budget_confirmed": True,
        "redacted": True,
    }
    # The amount, where they live, what they want and what worries them: gone.
    for leaked in ("2000000", "Mumbai", "marina view", "handover date"):
        assert leaked not in buf.getvalue()


def test_a_rejected_brief_does_not_leak_through_its_raw_text_or_its_error():
    """`brief_invalid` carries the model's attempted brief verbatim, so it
    carries the same fields the accepted one would have - and a pydantic
    validation message quotes the offending input value back inside itself."""
    log, buf = make_log(verbose=False)
    log.emit(
        "brief_invalid",
        turn=1,
        attempt="first",
        error="budget.amount: input should be a valid number [input_value='two million']",
        raw=json.dumps(BRIEF),
    )
    log.close()

    emitted = lines(buf)[0]
    assert emitted["raw"] == "[redacted]"
    assert emitted["error"] == "[redacted]"
    # The attempt number says which retry failed without saying what it said.
    assert emitted["attempt"] == "first"
    assert "Mumbai" not in buf.getvalue()
    assert "two million" not in buf.getvalue()


def test_guardrail_decisions_emit_their_telemetry_and_redact_their_text():
    """A guardrail line proves a sentence was inspected without reprinting it.

    The figures used to be emitted on the theory that they are inventory data.
    They are not, reliably: the prompt has the model read the buyer's budget
    back for confirmation, so a buyer-stated amount lands in the sentence, in
    the spoken form of it, and in the validator's account of both. What the
    audit needs is the decision, and the decision is enumerated.
    """
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_guardrail(
        raw="A studio is AED 985,000.",
        outcome="pass",
        guardrail_ms=0.4,
        spoken="A studio is nine hundred and eighty-five thousand dirhams.",
    )
    log.close()

    emitted = lines(buf)[0]
    # The decision survives in full.
    assert emitted["outcome"] == "pass"
    assert emitted["mode"] == "enforce"
    assert emitted["turn"] == 1
    assert emitted["sentence_index"] == 0
    assert emitted["ms"] == 0.4
    assert emitted["validator"] is None
    # The sentence does not.
    assert emitted["raw"] == "[redacted]"
    assert emitted["spoken"] == "[redacted]"
    assert "985,000" not in buf.getvalue()
    assert "eighty-five thousand" not in buf.getvalue()
    # In memory, the full sentence is still there for the audit and the UI.
    assert tracker.generated_sentences == ["A studio is AED 985,000."]
    assert tracker.spoken_chunks[0].text.startswith("A studio is nine hundred")


def test_a_violation_detail_quoting_an_amount_never_reaches_the_emitted_line():
    """The detail and the figures list are the validator's account of the
    sentence, and they name the figure it objected to - which, on the budget
    read-back path, is the buyer's own number."""
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    violation = GuardrailViolation(
        validator="numeric_claims",
        detail="AED 2,000,000 does not appear in the inventory",
        figures=[ExtractedFigure(surface="AED 2,000,000", value=2000000.0, kind="amount")],
    )
    tracker.record_guardrail(
        raw="Your two million budget covers a two bedroom.",
        outcome="violation_blocked",
        guardrail_ms=0.6,
        spoken=None,
        violation=violation,
    )
    log.close()

    emitted = lines(buf)[0]
    assert emitted["outcome"] == "violation_blocked"
    # The validator NAME is enumerated telemetry and stays.
    assert emitted["validator"] == "numeric_claims"
    assert emitted["detail"] == "[redacted]"
    assert emitted["figures"] == "[redacted]"
    # A sentence that was blocked was never spoken; that stays a null, not a
    # "[redacted]" that would imply speech.
    assert emitted["spoken"] is None
    for leaked in ("2,000,000", "2000000", "two million"):
        assert leaked not in buf.getvalue()
    # The in-memory record keeps the whole violation.
    assert tracker.violations[0].detail == "AED 2,000,000 does not appear in the inventory"
    assert tracker.violations[0].figures[0].value == 2000000.0


def test_tool_calls_emit_the_name_and_redact_the_argument_values():
    """Which tool fired and when is the hook-2 claim. The arguments are model
    free text: `reason` paraphrases what the buyer said, `slot` is explicitly
    "the slot in the buyer's own words"."""
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_tool("escalate_to_human", reason="buyer is angry about the Mumbai handover")
    tracker.record_tool("offer_booking", slot="Saturday after my flight from Mumbai")
    log.close()

    calls = [line_ for line_ in lines(buf) if line_["event"] == "tool_call"]
    assert [c["tool"] for c in calls] == ["escalate_to_human", "offer_booking"]
    assert [c["turn"] for c in calls] == [1, 1]
    # The keys are enumerable telemetry; the values are not.
    assert calls[0]["args"] == {"reason": "[redacted]"}
    assert calls[1]["args"] == {"slot": "[redacted]"}
    assert "Mumbai" not in buf.getvalue()
    assert tracker.actions == ["escalate_to_human", "offer_booking"]


def test_the_escalation_reason_and_the_booking_slot_are_redacted():
    """Both events carry a separate copy of the same tool argument, so
    redacting only `tool_call` would leave the leak intact one line down."""
    log, buf = make_log(verbose=False)
    log.emit(
        "escalation",
        reason="buyer is distressed about the Mumbai handover date",
        routed_to="human_ambassador",
    )
    log.emit("booking_offered", slot="Saturday after my flight from Mumbai")
    log.close()

    by_event = {line_["event"]: line_ for line_ in lines(buf)}
    assert by_event["escalation"]["reason"] == "[redacted]"
    # Where it was routed is enumerated: it is the proof a human was notified.
    assert by_event["escalation"]["routed_to"] == "human_ambassador"
    assert by_event["booking_offered"]["slot"] == "[redacted]"
    assert "Mumbai" not in buf.getvalue()


def test_verbose_restores_the_guardrail_escalation_and_booking_text():
    """The dev-only escape hatch is the only way to see any of it on stdout."""
    log, buf = make_log(verbose=True)
    tracker = make_tracker(log)
    tracker.record_guardrail(
        raw="A studio is AED 985,000.",
        outcome="pass",
        guardrail_ms=0.4,
        spoken="A studio is nine hundred and eighty-five thousand dirhams.",
    )
    tracker.record_tool("escalate_to_human", reason="asked for a person")
    log.emit("escalation", reason="asked for a person", routed_to="human_ambassador")
    log.emit("booking_offered", slot="Saturday after my flight")
    log.close()

    by_event = {line_["event"]: line_ for line_ in lines(buf)}
    assert by_event["guardrail"]["raw"] == "A studio is AED 985,000."
    assert by_event["guardrail"]["spoken"].startswith("A studio is nine hundred")
    assert by_event["tool_call"]["args"] == {"reason": "asked for a person"}
    assert by_event["escalation"]["reason"] == "asked for a person"
    assert by_event["booking_offered"]["slot"] == "Saturday after my flight"


def test_timings_and_usage_are_emitted_unchanged():
    """Numeric telemetry is the half of the stream that is meant to be read."""
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_usage(
        prompt_tokens=100, completion_tokens=20, reasoning_tokens=0, cached_tokens=64
    )
    tracker.mark_llm_ttft()
    log.close()

    by_event = {line_["event"]: line_ for line_ in lines(buf)}
    assert by_event["llm_usage"]["thinking_off"] is True
    assert by_event["llm_usage"]["cached_tokens"] == 64
    assert by_event["llm_usage"]["prompt_tokens"] == 100
    assert by_event["llm_usage"]["completion_tokens"] == 20
    assert by_event["llm_ttft"]["ms"] is not None
    assert by_event["llm_ttft"]["model"] == "qwen/qwen3.7-flash"


def test_a_regeneration_reason_is_the_violation_detail_and_is_redacted():
    """`regeneration.reason` is the same string as `guardrail.detail`, handed
    to the retry instruction. Redacting one and not the other closes nothing."""
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_regeneration("AED 2,000,000 does not appear in the inventory")
    log.close()

    assert lines(buf)[0]["reason"] == "[redacted]"
    assert "2,000,000" not in buf.getvalue()
    assert tracker.regenerated is True


def test_every_event_the_adapter_emits_is_classified():
    """The table is the whole defence, so a new event type with a free-text
    field and no entry has to be a visible failure rather than a silent leak.

    This asserts the inventory of event names, not the fields: adding an event
    forces a decision here about which half of the rule it falls under.
    """
    from adapter.events import _REDACTED_FIELDS

    redacted = set(_REDACTED_FIELDS)
    # Classified as carrying free text.
    assert redacted == {
        "user_turn",
        "brief_invalid",
        "guardrail",
        "regeneration",
        "tool_call",
        "escalation",
        "booking_offered",
    }
    # Classified as safe, each for a reason recorded next to the table.
    for event in ("bridge", "fallback", "llm_failure", "llm_usage", "turn_complete"):
        assert event not in redacted


# --- the dev-only escape hatch -------------------------------------------


def test_the_verbose_flag_restores_full_emission():
    log, buf = make_log(verbose=True)
    log.emit("user_turn", turn=1, text=UTTERANCE)
    log.emit("brief", turn=1, brief=BRIEF)
    log.close()

    emitted = lines(buf)
    assert emitted[0]["text"] == UTTERANCE
    assert emitted[1]["brief"] == BRIEF


def test_the_verbose_flag_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AMBASSADOR_EVENT_VERBOSE", "true")
    log, buf = make_log()
    log.emit("user_turn", turn=1, text=UTTERANCE)
    log.close()
    assert lines(buf)[0]["text"] == UTTERANCE

    monkeypatch.setenv("AMBASSADOR_EVENT_VERBOSE", "")
    log, buf = make_log()
    log.emit("user_turn", turn=1, text=UTTERANCE)
    log.close()
    assert lines(buf)[0]["text"] == "[redacted]"


# --- the file sink gets the same stream -----------------------------------


async def test_the_file_sink_receives_the_same_redacted_stream(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    log, buf = make_log(file_path=path, verbose=False)
    log.emit("user_turn", turn=1, text=UTTERANCE)
    log.emit("brief", turn=1, brief=BRIEF)
    await log.aclose()

    written = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert written == lines(buf)
    assert UTTERANCE not in path.read_text()
    assert "Mumbai" not in path.read_text()


# --- the write is off the hot path ----------------------------------------


async def test_events_emitted_from_async_context_arrive_in_order():
    log, buf = make_log(verbose=False)
    for i in range(50):
        log.emit("guardrail", turn=1, sentence_index=i)
    # Nothing has been written yet: the writer task has not been given the loop.
    assert buf.getvalue() == ""

    await log.aclose()
    assert [line_["sentence_index"] for line_ in lines(buf)] == list(range(50))


async def test_the_queue_drains_on_shutdown():
    log, buf = make_log(verbose=False)
    log.emit("session_start", config={})
    log.emit("session_end", turns=0)
    await log.aclose()

    assert [line_["event"] for line_ in lines(buf)] == ["session_start", "session_end"]


def test_emit_falls_back_to_a_direct_write_with_no_running_loop():
    """Spikes and sync tests have no loop. The event still has to land."""
    log, buf = make_log(verbose=False)
    log.emit("session_start", config={})
    assert lines(buf)[0]["event"] == "session_start"
    log.close()


async def test_an_overflowing_queue_drops_the_oldest_and_reports_the_count():
    """A full queue means the writer is behind, which is exactly when the voice
    path must not wait. A drop is allowed; a silent drop is not."""
    log, buf = make_log(verbose=False)
    total = 1200  # _QUEUE_MAX is 1024
    for i in range(total):
        log.emit("guardrail", turn=1, sentence_index=i)
    await log.aclose()

    emitted = lines(buf)
    backpressure = [line_ for line_ in emitted if line_["event"] == "event_log_backpressure"]
    assert len(backpressure) == 1
    dropped = backpressure[0]["dropped"]
    assert dropped == total - backpressure[0]["queue_max"]

    kept = [line_["sentence_index"] for line_ in emitted if line_["event"] == "guardrail"]
    # The oldest went, the newest survived, and the order held.
    assert kept == list(range(dropped, total))


async def test_emitting_after_shutdown_still_writes():
    log, buf = make_log(verbose=False)
    log.emit("session_start", config={})
    await log.aclose()
    log.emit("late", note="after the writer stopped")

    assert [line_["event"] for line_ in lines(buf)] == ["session_start", "late"]


async def test_a_broken_sink_never_raises_into_the_voice_path():
    class Exploding(StringIO):
        def write(self, s: str) -> int:
            raise OSError("sink is gone")

    log = EventLog("sess_test", stream=Exploding(), verbose=False)
    log.emit("llm_ttft", turn=1, ms=420.0)  # must not raise
    await log.aclose()


# --- bridge and fallback are different claims (docs/01-) ------------------


def test_a_bridge_and_a_fallback_emit_different_events():
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_bridge("Let me be precise about that figure rather than guess.")
    tracker.record_fallback("Let me put you through to one of our ambassadors.")
    log.close()

    assert [line_["event"] for line_ in lines(buf)] == ["bridge", "fallback"]
    assert lines(buf)[1]["reason"] == "guardrail"
    assert [c.text for c in tracker.spoken_chunks] == [
        "Let me be precise about that figure rather than guess.",
        "Let me put you through to one of our ambassadors.",
    ]


def test_mark_interrupted_flags_the_last_chunk_only():
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_guardrail(raw="One.", outcome="pass", guardrail_ms=0.1, spoken="One.")
    tracker.record_guardrail(raw="Two.", outcome="pass", guardrail_ms=0.1, spoken="Two.")
    tracker.mark_interrupted()
    record = tracker.finish()
    log.close()

    assert [c.completed for c in record.spoken_chunks] == [True, False]
    assert "interrupted" in [line_["event"] for line_ in lines(buf)]


def test_the_writer_survives_a_burst_from_several_coroutines():
    async def scenario() -> list[dict]:
        log, buf = make_log(verbose=False)

        async def burst(tag: str) -> None:
            for i in range(20):
                log.emit("guardrail", turn=1, tag=tag, sentence_index=i)
                await asyncio.sleep(0)

        await asyncio.gather(burst("a"), burst("b"))
        await log.aclose()
        return lines(buf)

    emitted = asyncio.run(scenario())
    assert len(emitted) == 40
    for tag in ("a", "b"):
        indexes = [line_["sentence_index"] for line_ in emitted if line_["tag"] == tag]
        assert indexes == list(range(20))
