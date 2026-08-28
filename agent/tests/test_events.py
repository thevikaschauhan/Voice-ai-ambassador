"""The event stream: what leaves the process, and what it costs to write.

Two separate claims are under test here, and they pull in opposite directions,
which is why they are asserted together:

  redaction   docs/02- and docs/03- say PII never lands in an emitted or
              durable stream. The in-memory `TurnRecord` still has to carry the
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


def test_a_rejected_brief_does_not_leak_through_its_raw_text():
    """`brief_invalid` carries the model's attempted brief verbatim, so it
    carries the same fields the accepted one would have."""
    log, buf = make_log(verbose=False)
    log.emit("brief_invalid", turn=1, attempt="first", error="bad", raw=json.dumps(BRIEF))
    log.close()

    assert lines(buf)[0]["raw"] == "[redacted]"
    assert "Mumbai" not in buf.getvalue()


def test_guardrail_decisions_keep_their_figures():
    """Figures are inventory data, not PII, and the whole claim of the audit
    trail is that you can see which figure was inspected."""
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
    assert emitted["raw"] == "A studio is AED 985,000."
    assert "985,000" in buf.getvalue()


def test_timings_tools_and_usage_are_emitted_unchanged():
    log, buf = make_log(verbose=False)
    tracker = make_tracker(log)
    tracker.record_tool("escalate_to_human", reason="asked for a person")
    tracker.record_usage(
        prompt_tokens=100, completion_tokens=20, reasoning_tokens=0, cached_tokens=64
    )
    tracker.mark_llm_ttft()
    log.close()

    by_event = {line_["event"]: line_ for line_ in lines(buf)}
    assert by_event["tool_call"]["tool"] == "escalate_to_human"
    assert by_event["llm_usage"]["thinking_off"] is True
    assert by_event["llm_usage"]["cached_tokens"] == 64
    assert by_event["llm_ttft"]["ms"] is not None


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
