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
              sentence, no tool argument, no validator detail (docs/02-,
              docs/03- validator 4). One table decides this, `_REDACTED_FIELDS`
              below, and the rule is stated above it.
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


def _redact_brief(brief: Any) -> Any:
    """Reduce a serialised `LeadBrief` to its non-PII fields."""
    if not isinstance(brief, dict):
        return brief
    out: dict[str, Any] = {k: brief[k] for k in _BRIEF_EMITTED_FIELDS if k in brief}
    budget = brief.get("budget")
    # The confirmed flag is a conversation-state fact; the amount is not.
    out["budget_confirmed"] = None if not isinstance(budget, dict) else budget.get("confirmed")
    out["redacted"] = True
    return out


# --- the redaction table --------------------------------------------------
#
# THE RULE. Any free-text field that can carry model-spoken or buyer-derived
# content is redacted by default: what the buyer said, what the model wrote, a
# model paraphrase of either, or a validator detail that quotes the text back.
# Enumerated and numeric telemetry is not redacted: timings, outcomes, counts,
# event names, tool names, validator names, token usage, booleans. None of
# that can carry a sentence.
#
# The trap this table exists to close is that "it is only the agent's own
# words" is not a reason to emit something. The system prompt has the model
# read the buyer's budget back for confirmation, so a buyer-stated amount
# routinely appears inside an agent sentence, inside the validator detail that
# objects to it, and inside the figures list that names it.
#
# When a new event type is added, classify its fields here. A new free-text
# field with no entry in this table is a leak.
#
# Deliberately NOT redacted, and why:
#   bridge.text / fallback.text  fixed composed copy from interception.py's
#       BRIDGE_COPY and FALLBACK_COPY. Never model-generated, never
#       buyer-derived, and showing which designed line was spoken is the whole
#       point of the event.
#   guardrail.validator          the validator's name, an enum in practice.
#   llm_failure.detail           the transport exception, no conversation in it.
#   brief / brief_fallback       a record with both kinds of field in it, so it
#       is filtered field by field by `_redact_brief` instead.
_REDACTED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "user_turn": ("text",),
    # `raw` is the model's attempted brief; the validation `error` quotes the
    # offending input value back inside its own message.
    "brief_invalid": ("raw", "error"),
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

    A field that is absent or None is left alone. `spoken: null` on a blocked
    sentence is a fact about the turn rather than content, and rewriting it to
    "[redacted]" would report a sentence that was never spoken.
    """
    event = record.get("event")
    if event in ("brief", "brief_fallback"):
        return {**record, "brief": _redact_brief(record.get("brief"))}
    fields = _REDACTED_FIELDS.get(event) if isinstance(event, str) else None
    if not fields:
        return record
    out = dict(record)
    for field in fields:
        if out.get(field) is None:
            continue
        out[field] = _redact_value(out[field])
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
        self.verbose = (
            _verbose_from_env() if verbose is None else verbose
        )

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
    return os.environ.get(_VERBOSE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


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
        self.llm_ttft: float | None = None
        self.llm_first_sentence: float | None = None
        self.tts_first_audio: float | None = None
        self.guardrail_total: float = 0.0

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

    def mark_llm_ttft(self) -> None:
        if self.llm_ttft is None:
            self.llm_ttft = self.elapsed()
            self._log.emit(
                "llm_ttft", turn=self.turn_index, ms=_ms(self.llm_ttft),
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
        self.guardrail_total += guardrail_ms
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
                llm_first_sentence=_ms(self.llm_first_sentence),
                guardrail=round(self.guardrail_total, 2),
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
            llm_ttft_ms=_ms(self.llm_ttft),
            llm_first_sentence_ms=_ms(self.llm_first_sentence),
            guardrail_ms=round(self.guardrail_total, 2),
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
