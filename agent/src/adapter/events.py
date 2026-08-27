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
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from ambassador.schemas import (
    GuardrailViolation,
    Language,
    SpokenChunk,
    Timings,
    TurnRecord,
)

# Optional second sink, so a demo or a verification run can capture clean JSON
# without the console UI interleaved. stdout is always written.
_FILE_SINK_ENV = "AMBASSADOR_EVENT_LOG"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


class EventLog:
    """Session-scoped event sink and in-memory turn store.

    In-memory only, by ADR-012. The web tier reads this stream; nothing here
    is durable, and PII redaction is a PHASE-2 concern (docs/03- validator 4).
    """

    def __init__(
        self,
        session_id: str,
        *,
        stream: TextIO | None = None,
        file_path: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self._stream = stream if stream is not None else sys.stdout
        self._turns: list[TurnRecord] = []
        self._file: TextIO | None = None

        path = file_path or (
            Path(os.environ[_FILE_SINK_ENV]) if os.environ.get(_FILE_SINK_ENV) else None
        )
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": _now_iso(), "session": self.session_id, "event": event}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        print(line, file=self._stream, flush=True)
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()
        return record

    def add_turn(self, record: TurnRecord) -> None:
        self._turns.append(record)

    @property
    def turns(self) -> list[TurnRecord]:
        return list(self._turns)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


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
        """Audio already played, so the violating sentence is replaced by a
        composed bridge rather than a silent retry (docs/01- regeneration
        policy)."""
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("bridge", turn=self.turn_index, text=text)

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

    def finish(self) -> TurnRecord:
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
        )
        return record
