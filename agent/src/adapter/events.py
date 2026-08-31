"""Structured per-turn events: JSON lines on stdout, one object per line.

This is the audit trail described in docs/03- and the data behind the day-4
latency meter. It records two different things and never conflates them:

  generated_sentences  what the model produced (digits intact)
  spoken_chunks        what was actually handed to TTS, and whether playback
                       completed (barge-in marks a chunk incomplete)

The distinction matters because the interception hook rewrites text between
the two - a guardrail can block a sentence, and verbalisation replaces digits
with spoken forms. A record that stored only one of them could not answer
"what did the buyer actually hear".

Timings are milliseconds, measured, never estimated. A field that was not
observed on a given turn stays None rather than defaulting to zero: a missing
measurement and a zero-latency stage must not look the same on the meter.

Two properties of the emitted stream are load-bearing and easy to lose:

  redaction   the in-memory `TurnRecord` keeps full fidelity because the
              ambassador view and the audit need it, and the demo UI reads
              in-process state rather than stdout. The stdout stream and the
              optional file sink are the parts that leave the process, so they
              carry no free text at all: no buyer utterance, no model
              sentence, no tool argument, no validator detail, no upstream
              response body (docs/02-, docs/03- validator 4). Two tables decide
              this and every emitted event is in exactly one of them:
              `_REDACTED_FIELDS` and `CLEAR_EVENTS`, with the rule stated above
              them and a test that reads the event names out of the source.
  no blocking every emit lands on the hot path - two of them are the TTFT and
              TTS-first-audio marks - so a write is queued to a single writer
              task rather than performed inline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from ambassador.schemas import (
    GuardrailViolation,
    Language,
    SpokenChunk,
    Timings,
    TurnRecord,
)

logger = logging.getLogger("ambassador.events")

# Optional second sink, so a demo or a verification run can capture clean JSON
# without the console UI interleaved. stdout is always written. The file sink
# receives exactly the same redacted stream as stdout.
_FILE_SINK_ENV = "AMBASSADOR_EVENT_LOG"

# Dev-only escape hatch, documented in config.py: restores full emission,
# buyer utterances and all. Never set for a demo or a deployment.
_VERBOSE_ENV = "AMBASSADOR_EVENT_VERBOSE"

REDACTED: Final = "[redacted]"

# The only brief fields that may leave the process. Everything else on a
# LeadBrief describes the buyer: budget amount, where they live, what they are
# hesitant about, the free text of what they want.
_BRIEF_EMITTED_FIELDS: Final = ("intent", "stage", "language", "shortlist_ids")

# A full queue means the writer is behind, which is exactly when the voice path
# must not wait. Oldest lines lose; the count is emitted once the writer keeps
# up again, so a drop is never silent.
_QUEUE_MAX: Final = 1024

_STOP: Final = object()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _unflatten(seconds: float | None) -> float | None:
    """Read a framework delay that has already had its None flattened to 0.0.

    `EOUMetrics` builds its fields as `info.metrics.<field> or 0.0`, so an
    unmeasurable stage arrives as a zero. None of these stages can genuinely be
    zero - each is a wait the framework sat through - so a zero is the missing
    measurement, and this module's rule says the two must not look the same.
    """
    return None if seconds is None or seconds <= 0.0 else seconds


def _redact_brief(brief: Any) -> Any:
    """Reduce a serialised `LeadBrief` to its non-PII fields."""
    if not isinstance(brief, dict):
        return brief
    out: dict[str, Any] = {k: brief[k] for k in _BRIEF_EMITTED_FIELDS if k in brief}
    budget = brief.get("budget")
    # The confirmed flag is a conversation-state fact; the amount is not.
    out["budget_confirmed"] = (
        None if not isinstance(budget, dict) else budget.get("confirmed")
    )
    out["redacted"] = True
    return out


# --- the classification tables ---------------------------------------------
#
# THE RULE. Any free-text field that can carry model-spoken or buyer-derived
# content is redacted by default: what the buyer said, what the model wrote, a
# model paraphrase of either, a validator detail that quotes the text back, or
# an upstream response body from a request whose payload was the transcript.
# Enumerated and numeric telemetry is not redacted: timings, outcomes, counts,
# event names, tool names, validator names, token usage, booleans, and fixed
# literals the adapter itself wrote. None of that can carry a sentence.
#
# The trap this rule exists to close is that "it is only the agent's own words"
# is not a reason to emit something. The system prompt has the model read the
# buyer's budget back for confirmation, so a buyer-stated amount routinely
# appears inside an agent sentence, inside the validator detail that objects to
# it, and inside the figures list that names it. The second trap is an
# exception message: `str(exc)` on a provider error carries up to a couple of
# hundred characters of response body, and the request that produced it had the
# transcript in its payload.
#
# EVERY event name emitted from src/adapter/ must appear in exactly one of the
# two tables below. `test_every_event_the_adapter_emits_is_classified` reads
# the event names straight out of the source and fails on any name that is in
# neither, so a new event type cannot be added without a decision being made
# about it here.
_REDACTED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "user_turn": ("text",),
    # `raw` is the model's sentence and `spoken` is what TTS was handed.
    # `detail` and `figures` are the validator's account of that same sentence
    # and name the figure it objected to, which is routinely the buyer's.
    "guardrail": ("raw", "spoken", "detail", "figures"),
    # The same violation detail, on its way into the retry instruction.
    "regeneration": ("reason",),
    # Tool arguments are model free text. The tool NAME stays: which tool fired
    # and when is the hook-2 claim, and a name cannot carry an utterance.
    "tool_call": ("args",),
    # A model paraphrase of what the buyer complained about.
    "escalation": ("reason",),
    # "The slot in the buyer's own words", by the tool's own docstring.
    "booking_offered": ("slot",),
    # `detail` is `str(exc)` on a provider error, so it can carry a response
    # body. The `error` field beside it is the exception CLASS name and stays.
    "llm_failure": ("detail",),
    # Same hazard, from the framework's own error event.
    "session_error": ("error",),
    # A brief is a record with both kinds of field in it, so `brief` is reduced
    # rather than blanked (see `_FIELD_REDUCERS`) and the emitted stream still
    # shows intent, stage, language, shortlist and whether a budget was
    # confirmed. `reason` on the fallback is a fixed literal and stays.
    "brief": ("brief",),
    "brief_fallback": ("brief", "error"),
    # `raw` is the model's attempted brief; the validation `error` quotes the
    # offending input value back inside its own message.
    "brief_invalid": ("raw", "error"),
    # `error` here is `f"{type(e).__name__}: {e}"` over a transport failure,
    # and brief.py builds that message from up to 200 characters of the raw
    # upstream response body.
    "brief_error": ("error",),
}

# Events with no free-text field at all. The value is the reason, and it is
# there to be read in review: "I could not think of one" is not a reason.
CLEAR_EVENTS: Final[dict[str, str]] = {
    "session_start": "the already-redacted config summary: model names, modes, booleans",
    "session_end": "a turn count",
    "disclosure": "two language codes and a boolean, all from configuration",
    "budget_policy": "language codes and booleans, all from configuration",
    # An enum action, a currency code and booleans - and deliberately NOT the
    # text. A confirmation echoes the buyer's own budget, so its text is
    # buyer-derived free content; `record_confirmation` keeps it in the
    # in-memory TurnRecord, where the audit and the ambassador view need it,
    # and never puts it on the emitted stream.
    "budget_confirmation": "an enum action, a currency code and booleans",
    # "Settled", not "confirmed": the brief extractor's model-inferred record
    # carries its own `budget.confirmed` field on the same stream, and the two
    # must not share a name - this one is the deterministic policy's verdict
    # and wins wherever they disagree.
    "budget_settled": "a turn index and a currency code",
    "budget_confirmation_spoken": "a turn index and an enum action, never the text",
    "lexicon": "language codes and a boolean, all from a static data file",
    "prohibited_coverage": "language codes, a boolean and a pattern count",
    "stt_enabled": "the STT model and provider names",
    "stt_disabled": "a fixed literal reason the adapter wrote",
    "llm_request": "turn index, the tool names offered, the tool-choice mode",
    "llm_upstream_error": "turn index, an HTTP status, a fixed literal note",
    "llm_ttft": "turn index, milliseconds, the model name",
    "llm_usage": "token counts and the thinking-off boolean",
    "endpointing": "turn index and four millisecond marks, all from the framework's own EOUMetrics",
    "tts_first_audio": "turn index and two millisecond marks",
    "tts_connection": "turn index, a boolean, milliseconds and a pooled-socket count",
    "tts_pool_reprewarm": "turn index and an enum outcome the adapter itself wrote",
    "interrupted": "a turn index",
    "turn_complete": "timings, counts, booleans and the list of tool names fired",
    "bridge": "fixed composed copy from data/fallbacks.yaml (bridge), never generated",
    "fallback": "fixed composed copy from data/fallbacks.yaml (fallback), plus an enum reason",
    # Both of the above are clear ONLY because their text is fixed copy from a
    # data file. Anything that interpolates the buyer - the budget
    # confirmation did, through record_fallback, until this was caught - must
    # not be routed through them. Use record_confirmation instead.
    "brief_retry": "an attempt number, a delay in seconds, an HTTP status",
    "brief_stale_dropped": "turn indexes and a fixed literal reason",
    "event_log_backpressure": "a dropped-line count and the queue bound",
}

# The default reduction is to blank a field. A field listed here is reduced by
# its own function instead, because some of what it holds is worth emitting.
_FIELD_REDUCERS: Final[dict[str, Callable[[Any], Any]]] = {
    "brief": _redact_brief,
}


def _redact_value(value: Any) -> Any:
    """Keep the shape, drop the content.

    A mapping keeps its keys: which arguments a tool was called with is
    enumerable telemetry, what was in them is not.
    """
    if isinstance(value, dict):
        return {key: REDACTED for key in value}
    return REDACTED


def redact_event(record: dict[str, Any]) -> dict[str, Any]:
    """The emitted view of one event record, per `_REDACTED_FIELDS`.

    One mechanism, no bypass paths: every event goes through the same table
    lookup, including the brief events, whose `brief` field is reduced by
    `_FIELD_REDUCERS` rather than blanked. An early return for a special case
    is how `brief_fallback.error` stayed in the clear while a table entry for
    it would have looked like it was doing something.

    A field that is absent or None is left alone. `spoken: null` on a blocked
    sentence is a fact about the turn rather than content, and rewriting it to
    "[redacted]" would report a sentence that was never spoken.
    """
    event = record.get("event")
    fields = _REDACTED_FIELDS.get(event) if isinstance(event, str) else None
    if not fields:
        return record
    out = dict(record)
    for field in fields:
        if out.get(field) is None:
            continue
        out[field] = _FIELD_REDUCERS.get(field, _redact_value)(out[field])
    return out


class EventLog:
    """Session-scoped event sink and in-memory turn store.

    In-memory only, by ADR-012. The web tier reads this stream; nothing here is
    durable. The in-memory `TurnRecord`s hold the full utterance; the emitted
    stream does not.
    """

    def __init__(
        self,
        session_id: str,
        *,
        stream: TextIO | None = None,
        file_path: Path | None = None,
        verbose: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self._stream = stream if stream is not None else sys.stdout
        self._turns: list[TurnRecord] = []
        self._file: TextIO | None = None
        self.verbose = _verbose_from_env() if verbose is None else verbose

        path = file_path or (
            Path(os.environ[_FILE_SINK_ENV]) if os.environ.get(_FILE_SINK_ENV) else None
        )
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")

        self._queue: asyncio.Queue[Any] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._dropped = 0
        # Once the writer has been drained and stopped, later events go direct
        # rather than into a fresh queue nobody will ever drain.
        self._writer_stopped = False

    # -- emission ---------------------------------------------------------

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Record an event and queue its emitted (redacted) form.

        Returns the FULL record: callers that keep state in memory get the
        unredacted view, the stream does not. Never raises and never blocks.
        """
        record: dict[str, Any] = {
            "ts": _now_iso(),
            "session": self.session_id,
            "event": event,
        }
        record.update(fields)
        emitted = record if self.verbose else redact_event(record)
        self._enqueue(json.dumps(emitted, ensure_ascii=False, default=str))
        return record

    def _enqueue(self, line: str) -> None:
        queue = self._ensure_writer()
        if queue is None:
            # No running loop (spikes, sync tests) or the writer is gone: the
            # direct write is the fallback, not an error.
            self._write(line)
            return
        while True:
            try:
                queue.put_nowait(line)
                return
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - drained under us
                    self._write(line)
                    return
                self._dropped += 1

    def _ensure_writer(self) -> asyncio.Queue[Any] | None:
        if self._writer_stopped:
            return None
        if self._queue is not None:
            if self._writer is not None and not self._writer.done():
                return self._queue
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._writer = loop.create_task(self._writer_loop(), name="event_log_writer")
        return self._queue

    async def _writer_loop(self) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            item = await queue.get()
            if item is _STOP:
                # Report before stopping, or a drop that happened on the final
                # burst would never be reported at all.
                self._report_backpressure()
                return
            self._write(item)
            if queue.empty():
                self._report_backpressure()

    def _report_backpressure(self) -> None:
        if not self._dropped:
            return
        dropped, self._dropped = self._dropped, 0
        self._write(
            json.dumps(
                {
                    "ts": _now_iso(),
                    "session": self.session_id,
                    "event": "event_log_backpressure",
                    "dropped": dropped,
                    "queue_max": _QUEUE_MAX,
                },
                ensure_ascii=False,
            )
        )

    def _write(self, line: str) -> None:
        try:
            print(line, file=self._stream, flush=True)
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:  # a broken sink must not take the voice path with it
            logger.warning("event log write failed", exc_info=True)

    # -- turn store -------------------------------------------------------

    def add_turn(self, record: TurnRecord) -> None:
        self._turns.append(record)

    @property
    def turns(self) -> list[TurnRecord]:
        return list(self._turns)

    # -- shutdown ---------------------------------------------------------

    async def aclose(self, timeout: float = 5.0) -> None:
        """Drain the queue, stop the writer, close the file sink."""
        writer, queue = self._writer, self._queue
        if writer is not None and queue is not None and not writer.done():
            await queue.put(_STOP)
            try:
                await asyncio.wait_for(asyncio.shield(writer), timeout=timeout)
            except TimeoutError:  # pragma: no cover - a wedged stdout
                writer.cancel()
                logger.warning("event log writer did not drain in %.1fs", timeout)
        self._writer_stopped = True
        self._writer = None
        self._queue = None
        self.close()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _verbose_from_env() -> bool:
    return os.environ.get(_VERBOSE_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class TurnTracker:
    """Accumulates one turn's timings, text and decisions.

    Created when the LLM node starts and sealed when the turn completes. The
    tracker owns the clock: every duration is derived from a single `t0` taken
    at turn start, so stage timings are comparable within a turn.
    """

    def __init__(
        self,
        log: EventLog,
        *,
        turn_index: int,
        buyer_utterance: str,
        language: Language,
        model: str,
        prompt_mode: str,
        guardrail_mode: str,
        inventory_version: str,
    ) -> None:
        self._log = log
        self.turn_index = turn_index
        self.buyer_utterance = buyer_utterance
        self.language = language
        self.model = model
        self.prompt_mode = prompt_mode
        self.guardrail_mode = guardrail_mode
        self.inventory_version = inventory_version

        self.t0 = time.perf_counter()
        # The two stages that happen BEFORE t0: the buyer had stopped speaking
        # by the time this tracker existed. They are measured by the framework
        # and handed over by `record_endpointing`, so they stay None on any
        # turn the voice path did not produce - a typed turn has no endpoint.
        self.endpoint: float | None = None
        self.stt: float | None = None
        self.turn_committed: float | None = None
        self.llm_ttft: float | None = None
        self.llm_first_sentence: float | None = None
        self.tts_first_audio: float | None = None
        # None until the guardrail actually runs. A confirmation turn never
        # runs it, and events.py's own rule is that a missing measurement and
        # a zero-latency stage must not look the same on the meter.
        self.guardrail_total: float | None = None

        self.generated_sentences: list[str] = []
        self.spoken_chunks: list[SpokenChunk] = []
        self.violations: list[GuardrailViolation] = []
        self.actions: list[str] = []
        self.regenerated: bool = False
        self.reasoning_tokens: int | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.cached_tokens: int | None = None

    # -- timing marks -----------------------------------------------------

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def record_endpointing(
        self,
        *,
        end_of_utterance: float | None,
        transcription: float | None,
        turn_committed: float | None,
    ) -> None:
        """The framework's own end-of-utterance measurement (issue #7).

        LiveKit measures this and we do not. `EOUMetrics.end_of_utterance_delay`
        is the gap between VAD's end of the buyer's speech and the decision to
        end their turn: the "endpointing" row of the budget table, the one
        budgeted at up to 500ms and never measured. `transcription_delay` is the
        "STT after endpoint" row, taken from the SAME anchor
        (`_compute_end_of_turn_metrics` in voice/audio_recognition.py), so it is
        a COMPONENT of the endpoint figure and the two must never be added
        together. `after_transcript_ms` is the difference: what the turn
        detector spent waiting once the words were already in hand, which is the
        part of the budget that optimising endpointing could actually recover.

        ZERO IS NOT A MEASUREMENT HERE. The framework returns None from
        `_compute_end_of_turn_metrics` whenever the VAD anchors are missing or
        inconsistent, and then flattens that to 0.0 building the metrics event
        (`info.metrics.end_of_turn_delay or 0.0`). A real endpointing delay
        cannot be 0.0 - it is a silence the detector waited out - so 0.0 is read
        back as "not measured" rather than reported as a zero-latency stage.
        That is events.py's own None-vs-zero rule, applied to a number that
        arrives already collapsed.

        Seconds in, milliseconds out: the framework's delays are seconds.
        """
        self.endpoint = _unflatten(end_of_utterance)
        self.stt = _unflatten(transcription)
        self.turn_committed = _unflatten(turn_committed)
        after_transcript = (
            None
            if self.endpoint is None or self.stt is None
            else max(self.endpoint - self.stt, 0.0)
        )
        self._log.emit(
            "endpointing",
            turn=self.turn_index,
            endpoint_ms=_ms(self.endpoint),
            stt_ms=_ms(self.stt),
            after_transcript_ms=_ms(after_transcript),
            turn_committed_ms=_ms(self.turn_committed),
        )

    def mark_llm_ttft(self) -> None:
        if self.llm_ttft is None:
            self.llm_ttft = self.elapsed()
            self._log.emit(
                "llm_ttft",
                turn=self.turn_index,
                ms=_ms(self.llm_ttft),
                model=self.model,
            )

    def mark_first_sentence(self) -> None:
        if self.llm_first_sentence is None:
            self.llm_first_sentence = self.elapsed()

    def mark_tts_first_audio(self) -> None:
        if self.tts_first_audio is None:
            self.tts_first_audio = self.elapsed()
            self._log.emit(
                "tts_first_audio",
                turn=self.turn_index,
                ms=_ms(self.tts_first_audio),
                since_first_sentence_ms=_ms(
                    None
                    if self.llm_first_sentence is None
                    else self.tts_first_audio - self.llm_first_sentence
                ),
            )

    # -- hook 1: guardrail decisions --------------------------------------

    def record_guardrail(
        self,
        *,
        raw: str,
        outcome: str,
        guardrail_ms: float,
        spoken: str | None = None,
        violation: GuardrailViolation | None = None,
        mode: str | None = None,
    ) -> None:
        """One line per sentence that passed through process_sentence().

        Emitted for passes as well as violations: the claim is that every
        sentence is inspected, and only a per-sentence record can evidence it.
        """
        self.guardrail_total = (self.guardrail_total or 0.0) + guardrail_ms
        self.generated_sentences.append(raw)
        if violation is not None:
            self.violations.append(violation)
        if spoken is not None:
            self.spoken_chunks.append(SpokenChunk(text=spoken, completed=True))
        self._log.emit(
            "guardrail",
            turn=self.turn_index,
            outcome=outcome,
            mode=mode or self.guardrail_mode,
            ms=round(guardrail_ms, 2),
            sentence_index=len(self.generated_sentences) - 1,
            raw=raw,
            spoken=spoken,
            validator=None if violation is None else violation.validator,
            detail=None if violation is None else violation.detail,
            figures=(
                None
                if violation is None
                else [f.model_dump() for f in violation.figures]
            ),
        )

    def record_regeneration(self, reason: str) -> None:
        self.regenerated = True
        self._log.emit("regeneration", turn=self.turn_index, reason=reason)

    def record_bridge(self, text: str) -> None:
        """Audio already played this turn, so the violating sentence is replaced
        by a composed bridge rather than a silent retry (docs/01- regeneration
        policy).

        Only ever the audio-already-played half of that policy. The
        nothing-spoken-yet half is `record_fallback`, and the two are separate
        events because they are separate claims: a bridge means the buyer heard
        a seam, a fallback means the composed copy WAS the reply.
        """
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("bridge", turn=self.turn_index, text=text)

    def record_fallback(self, text: str, reason: str = "guardrail") -> None:
        """Nothing was spoken this turn, so the composed fallback is the whole
        reply (docs/01-). There is nothing to bridge from."""
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("fallback", turn=self.turn_index, text=text, reason=reason)

    def record_confirmation(self, text: str, action: str) -> None:
        """A deterministic confirmation spoken instead of running the turn.

        Shaped like `record_fallback` and emitted differently on purpose. That
        event carries its text in the clear, which is safe for fixed copy out
        of a data file and NOT safe here: a confirmation interpolates the
        buyer's own budget, and routing it through `record_fallback` put a
        buyer-derived figure onto stdout and the file sink. The chunk still
        goes into the in-memory record, which is what the audit and the
        ambassador view read.
        """
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit(
            "budget_confirmation_spoken", turn=self.turn_index, action=action
        )

    def mark_interrupted(self) -> None:
        """Barge-in: the last chunk handed to TTS may not have finished
        playing. Chunk granularity is the claim (docs/04-)."""
        if self.spoken_chunks:
            last = self.spoken_chunks[-1]
            self.spoken_chunks[-1] = SpokenChunk(text=last.text, completed=False)
        self._log.emit("interrupted", turn=self.turn_index)

    # -- hook 2: tools ----------------------------------------------------

    def record_tool(self, name: str, **args: Any) -> None:
        self.actions.append(name)
        self._log.emit(
            "tool_call",
            turn=self.turn_index,
            tool=name,
            args=args,
            at_ms=_ms(self.elapsed()),
            audio_already_played=bool(self.spoken_chunks),
        )

    # -- LLM usage (ADR-016 gate) -----------------------------------------

    def record_usage(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        reasoning_tokens: int | None,
        cached_tokens: int | None,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.cached_tokens = cached_tokens
        self._log.emit(
            "llm_usage",
            turn=self.turn_index,
            model=self.model,
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            # ADR-016's named trap: thinking silently re-enabled through the
            # proxy shows up here and nowhere else.
            thinking_off=(reasoning_tokens == 0),
        )

    # -- seal -------------------------------------------------------------

    def finish(self, *, audit_incomplete: bool = False) -> TurnRecord:
        """Seal the turn.

        `audit_incomplete` says the turn's speech handle never resolved - a
        session torn down mid-speech - so whether the last chunk finished
        playing is unknown rather than known-good. The audit says so on the
        emitted line instead of guessing, the same way a missing timing stays
        None rather than defaulting to zero.
        """
        total = self.elapsed()
        record = TurnRecord(
            session_id=self._log.session_id,
            turn_index=self.turn_index,
            timestamp=_now_iso(),
            buyer_utterance=self.buyer_utterance,
            generated_sentences=self.generated_sentences,
            spoken_chunks=self.spoken_chunks,
            guardrail_decisions=self.violations,
            actions=self.actions,
            timings_ms=Timings(
                endpoint=_ms(self.endpoint),
                stt=_ms(self.stt),
                llm_first_sentence=_ms(self.llm_first_sentence),
                guardrail=(
                    None
                    if self.guardrail_total is None
                    else round(self.guardrail_total, 2)
                ),
                tts_first_audio=_ms(self.tts_first_audio),
                total=_ms(total),
            ),
            inventory_version=self.inventory_version,
            model=self.model,
            prompt_mode=self.prompt_mode,  # type: ignore[arg-type]
            guardrail_mode=self.guardrail_mode,  # type: ignore[arg-type]
        )
        self._log.add_turn(record)
        self._log.emit(
            "turn_complete",
            turn=self.turn_index,
            endpoint_ms=_ms(self.endpoint),
            stt_ms=_ms(self.stt),
            llm_ttft_ms=_ms(self.llm_ttft),
            llm_first_sentence_ms=_ms(self.llm_first_sentence),
            guardrail_ms=(
                None
                if self.guardrail_total is None
                else round(self.guardrail_total, 2)
            ),
            tts_first_audio_ms=_ms(self.tts_first_audio),
            total_ms=_ms(total),
            sentences=len(self.generated_sentences),
            violations=len(self.violations),
            regenerated=self.regenerated,
            actions=self.actions,
            reasoning_tokens=self.reasoning_tokens,
            audit_incomplete=audit_incomplete,
        )
        return record
