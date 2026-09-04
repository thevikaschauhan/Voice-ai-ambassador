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
import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from ambassador.figures import states_a_figure
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
    # The same paraphrase, from a second path asking for a human on a turn that
    # is already handing over.
    "escalation_suppressed": ("reason",),
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
    "knowledge_retrieved": (
        "Counts and elapsed time for one turn's retrieval. No excerpt text, "
        "no chunk bodies and no buyer words: the identifiers live in the "
        "encrypted knowledge_use row, not on the event stream."
    ),
    "knowledge_retrieval_skipped": (
        "A turn that retrieved nothing, and why - a missing pool, a paused "
        "database or the 250ms budget. The reason is a fixed code, never an "
        "exception string, so a database error message cannot reach stdout."
    ),
    # The contact ask, its read-back and its outcome. `ContactPolicy` is CORE
    # and emits through the log it is given, so these three names are outside
    # the adapter scan in `test_events.py` - `test_contact_wiring.py` asserts
    # they are classified here, and that the policy keeps one emit funnel so
    # that check stays exhaustive.
    #
    # The values are the whole point: a turn index, and a status out of a
    # closed set. Never the number, never the name, never the reply they came
    # from. A phone number on this stream is a phone number in every sink the
    # stream reaches.
    "contact_asked": "one turn index",
    "contact_read_back": "one turn index; the digits stay in the sealed record",
    "contact_settled": "one turn index and an enum status",
    # Which fixed line was spoken, out of ask/read_back. Shaped like
    # `farewell_spoken`: the fact and the stage, not the sentence.
    "contact_line_spoken": "one turn index and an enum stage",
    # `config` has already masked credentials; `model` is the configured LLM
    # slug; `language`, `prompt_mode` and `guardrail_mode` are closed enums;
    # `inventory_version` is a 12-character SHA-256 prompt digest, never the
    # prompt or inventory itself. Each is configuration, not buyer free text.
    # `ambassador_name` joins them: product identity from data/ambassadors.yaml,
    # chosen by the client, and the same string the web surface labels the orb
    # with. Not a fact about the buyer.
    "session_start": (
        "a redacted config, configured enums, a prompt digest and the "
        "ambassador's given name"
    ),
    "session_end": "a turn count",
    # ADR-018's keep-alive, from the admin API. One boolean and nothing
    # else: deliberately NOT the exception, because a driver error can
    # quote a DSN and this event is on the durable stream. The service log
    # carries the detail; the audit carries whether it answered.
    "database_health_probe": "one boolean, whether the database answered",
    # Two language codes and two adapter-authored words. The room metadata this
    # reads is written by another service, and NONE of it is emitted: `reason`
    # is one of a closed set of literals in `agent.LanguageSelection`, chosen
    # precisely so a foreign service's free text cannot reach this stream by
    # being helpfully included in a diagnostic.
    "language_selected": "two language codes, an enum source and an enum reason",
    "call_duration_cap_armed": "one integer, the configured cap in seconds",
    "call_duration_cap": "one integer and a fixed literal action",
    # One of the six fixed reasons in `CallEndReason` - buyer_farewell,
    # buyer_farewell_repeated, agent_farewell, duration_cap, buyer_left and the
    # reserved session_error - and never the utterance that triggered it. The
    # buyer's closing words are their own text and stay in the TurnRecord like
    # every other utterance. The enumeration is worth keeping accurate because
    # it is the only place that says WHY the set is closed, and a test asserts
    # it names every value.
    "call_ended": "one of six fixed reasons",
    # An enum stage and an enum code, never an exception string and never
    # a buyer word (docs/10-). A lead that failed to save has to be
    # visible to operations without the log becoming a data leak.
    "lead_persisted": "a turn count and an elapsed time",
    # host:port, and deliberately not the DSN, which carries the password.
    # It is here because the pooler PORT is the difference between session
    # and transaction mode (ADR-018) - a real thing to get wrong and an
    # invisible one to diagnose without one startup line.
    "lead_store_connected": "a host and port, never the DSN",
    # Absence has to be readable. Without this line a log cannot tell "no
    # DATABASE_URL" from "the lead path is not wired", which is the state an
    # audit had to read source to diagnose.
    "lead_store_disabled": "one enum reason",
    # The analysis half of finalisation (docs/10- steps 5-6). Enum stages and
    # enum codes only: the model reads the transcript, so its errors and its
    # invalid answers can both quote a buyer, and neither goes on this stream.
    "analysis_complete": "a computed total and the rubric version",
    "analysis_attempt_failed": "which attempt, an enum stage and an enum code",
    "analysis_failed": "an enum stage and an enum code",
    # The failed MARK could not be written either - the database is usually
    # why we are here. The lead keeps its transcript and reads `pending`.
    "analysis_status_unwritten": "an enum stage and an enum code",
    "lead_persist_failed": "an enum stage and an enum code",
    # A decrypt that failed, from the admin API read path. The field PATH is
    # structural and safe; the value, the ciphertext and the exception are not,
    # and none of them is here. It is emitted because a failed open means
    # tampering, a restore across leads, or a key that moved - the one event
    # that must never be the one nobody sends.
    "envelope_unreadable": "a lead id and a structural field path",
    # The audit of a detail read. Counts and a lead id: how many sealed fields
    # were opened and how many would not open. One event per READ rather than
    # one per field, because a fifty-turn lead would otherwise emit fifty rows
    # for one page view and an audit nobody can read is an audit nobody reads.
    "lead_detail_read": "a lead id and three counts",
    # Counts and booleans about a near miss, never the utterance. The
    # buyer's closing words are their own text; what tuning needs is how
    # many tokens were in the way and whether one was the ambassador's
    # name, which is the likeliest single reason a real goodbye misses.
    "farewell_candidate": "a token count and a boolean",
    "room_deleted": "the same fixed reason",
    "room_delete_failed": "the same fixed reason; the error goes to the log, not here",
    "farewell_spoken": "a turn index; the copy itself is authored, not buyer-derived",
    "farewell_interrupted": "the same fixed reason, for a close the buyer talked over",
    "disclosure": (
        "two language codes and a boolean, all from configuration, plus the "
        "ambassador name actually spoken - a data-file value, never buyer text"
    ),
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
    # The project half of ADR-011. Every field is enumerated or numeric: the
    # action, the project ID and name out of data/inventory.json, the match
    # band and its similarity score. Deliberately NOT the buyer's words that
    # matched - a mangled project name is still a slice of their transcript.
    "project_policy": "language codes and booleans, all from configuration",
    "project_confirmation": "an enum action, an inventory project id and booleans",
    # "Settled" rather than "confirmed", for the same reason budget_settled is:
    # the brief extractor's model-inferred shortlist names projects too, and
    # this one is the deterministic policy's verdict.
    "project_settled": "a turn index, an inventory project id, a band and a score",
    "project_confirmation_spoken": "a turn index and an enum action, never the text",
    # The failed-recognition count (ADR-011 trigger 3). A count and two
    # booleans; the unusable transcript itself is emitted by `user_turn`,
    # where it is already redacted.
    "recognition_policy": "language codes and booleans, all from configuration",
    "recognition_failed": "a turn index, a consecutive count and a boolean",
    "recognition_escalation_spoken": "a turn index only, never the text",
    "lexicon": "language codes and a boolean, all from a static data file",
    "prohibited_coverage": "language codes, a boolean and a pattern count",
    "stt_enabled": "the STT model and provider names",
    "stt_disabled": "a fixed literal reason the adapter wrote",
    "tts_enabled": "the TTS provider and model names, the audio format, a sample rate and two enums",
    "llm_request": "turn index, the tool names offered, the tool-choice mode",
    "llm_upstream_error": "turn index, an HTTP status, a fixed literal note",
    "llm_ttft": "turn index, milliseconds, the model name",
    "llm_usage": "token counts and the thinking-off boolean",
    "endpointing": "turn index and four millisecond marks, all from the framework's own EOUMetrics",
    "tts_first_audio": "turn index and two millisecond marks",
    "tts_connection": "turn index, a boolean, milliseconds and a pooled-socket count",
    "generation_discarded": "a turn index and four counts of what was dropped",
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
    # The port, deliberately, and NEVER the token: the token reaches the local
    # consumer through a 0600 handshake file and nothing else. A token on the
    # emitted stream would put the credential for the unredacted surface into
    # the sink that exists because it is redacted.
    "events_bridge": "a loopback host and a port, both written by the adapter",
    # Bridge-only: written straight to the bridge's own socket by
    # events_bridge._Client, so it never passes through redact_event at all.
    # Listed here anyway because the rule is that every event name the adapter
    # emits is classified, and an unlisted name is the thing this table exists
    # to make impossible.
    "bridge_backpressure": "a dropped-line count and the queue bound",
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

        # In-process observers of the FULL record (events_bridge.py). Kept
        # separate from the sinks above because they receive what those
        # deliberately do not: the unredacted view. An observer must not block
        # and must not raise - `emit` runs on the voice path.
        self._observers: list[Callable[[dict[str, Any]], None]] = []

        self._queue: asyncio.Queue[Any] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._dropped = 0
        # Once the writer has been drained and stopped, later events go direct
        # rather than into a fresh queue nobody will ever drain.
        self._writer_stopped = False

    # -- emission ---------------------------------------------------------

    def add_observer(
        self, observer: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Watch the FULL records, in process. Returns the remover.

        The one way to see the unredacted stream without going through stdout,
        and the reason `AMBASSADOR_EVENT_VERBOSE` does not have to be set for
        the demo surface to work - that flag routes buyer text to a durable
        sink, which is what docs/03- forbids, and this does not.

        Contract on the observer, because this is called on the voice path: it
        must not block and it must not await. `events_bridge.py` satisfies it
        with a non-blocking put onto a bounded queue.
        """
        self._observers.append(observer)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._observers.remove(observer)

        return remove

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
        self._notify(record)
        return record

    def _notify(self, record: dict[str, Any]) -> None:
        """A broken observer must not take the voice path with it, the same way
        a broken sink does not."""
        for observer in self._observers:
            try:
                observer(record)
            except Exception:
                logger.warning("event observer failed", exc_info=True)

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
        # One handover per turn, however many paths ask for one. See
        # `record_escalation`.
        self.handed_over: bool = False
        self.regenerated: bool = False
        # `llm_node` reached this turn before its final transcript existed, so
        # the tracker was opened lazily on whatever the chat context held. True
        # on the voice path (preemptive generation) AND on the text path, where
        # there is no separate final transcript to arrive - `adopted` is what
        # distinguishes them.
        self.opened_on_partial: bool = False
        # A final transcript was adopted onto work already begun, which only
        # happens under preemptive generation.
        self.adopted: bool = False
        # Whether any sentence the REGENERATED reply actually spoke asserted a
        # figure. Read by `AmbassadorAgent._backstop_regeneration` (issue #33),
        # which documents why False is the routing direction.
        self.regenerated_stated_figure: bool = False
        self.reasoning_tokens: int | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.cached_tokens: int | None = None

    # -- timing marks -----------------------------------------------------

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def adopt_final_utterance(self, utterance: str) -> None:
        """Take the final transcript onto a turn the model already started.

        `preemptive_generation` is enabled by default
        (`livekit/agents/voice/turn.py`): the framework runs `llm_node` on the
        PARTIAL transcript and only then calls `on_user_turn_completed` with the
        final one. Opening a second tracker there split one buyer turn across
        two records - the LLM and guardrail work on the first, the endpointing
        and audio marks on the second - so `turn_complete` reported
        `sentences: 0` on the half that carried the timings and
        `since_first_sentence_ms` was null on every real turn, which is the
        metric issue #18's barge-in delta is defined in.

        The clock is deliberately NOT reset. `t0` is the moment the model began
        working, which is earlier than this hook and is the honest start for
        "how long did the buyer wait" - resetting it here would hide the head
        start that preemptive generation exists to buy.

        docs/02- says `buyer_utterance` is the final STT text, so the partial is
        replaced rather than kept alongside. The emitted stream redacts it
        either way; the in-memory record is what the audit and the ambassador
        view read.
        """
        self.buyer_utterance = utterance
        self.adopted = True

    def discard_generation(self) -> bool:
        """Drop the records of a generation whose audio was cancelled.

        `preemptive_generation` starts the model on a partial transcript. When
        the final transcript is not equivalent, `AgentActivity` cancels that
        speech handle and generates again - so the first generation's sentences
        were inspected but never played, and leaving them on the turn made the
        audit claim speech the buyer never heard. The audit claim is "what the
        buyer actually heard" (docs/02-), so they come off.

        WHAT IS NOT DROPPED, and why. Tool calls and the handover flag stay:
        a tool that fired had a real side effect, and clearing `handed_over`
        would let the replacement generation page a second ambassador for one
        buyer turn - the notify-once rule outranks tidiness. The clock is not
        reset either, for the same reason adoption does not reset it (#52): t0
        is when the model began, which is still the honest start of the buyer's
        wait.

        The emitted stream keeps the discarded `guardrail` lines, because the
        claim that every sentence is inspected rests on them. The event below is
        what stops a consumer counting them as spoken.

        Returns whether anything was actually dropped.
        """
        dropped = (
            len(self.generated_sentences)
            + len(self.spoken_chunks)
            + len(self.violations)
        )
        if not dropped and self.llm_ttft is None:
            return False
        self._log.emit(
            "generation_discarded",
            turn=self.turn_index,
            sentences=len(self.generated_sentences),
            chunks=len(self.spoken_chunks),
            violations=len(self.violations),
            regenerated=self.regenerated,
        )
        self.generated_sentences = []
        self.spoken_chunks = []
        self.violations = []
        self.guardrail_total = None
        self.regenerated = False
        # The latency marks describe the reply the buyer heard, and that reply
        # has not started yet. Measured from an unchanged t0, so the buyer's
        # wait still runs from when the model first began.
        self.llm_ttft = None
        self.llm_first_sentence = None
        self.tts_first_audio = None
        return True

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
        if self.regenerated and outcome == "pass":
            # Only after a regeneration, so the extraction cost lands on a rare
            # path rather than on every sentence of every turn - and only on a
            # pass, because a warned sentence is spoken with its figure
            # UNVALIDATED and must not read as a corrected answer.
            try:
                if states_a_figure(raw):
                    self.regenerated_stated_figure = True
            except Exception:
                # Fails towards routing a human: see the failure-direction note
                # on `AmbassadorAgent._backstop_regeneration`.
                logger.warning(
                    "figure detection failed on a regenerated sentence",
                    exc_info=True,
                )
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

    def record_project_confirmation(self, text: str, action: str) -> None:
        """A deterministic project-name confirmation spoken instead of running
        the turn.

        Its text is fixed copy plus an inventory name, so unlike the budget
        confirmation nothing buyer-derived is in it - but it is kept off the
        emitted stream anyway, because the only thing the stream gains from
        the sentence is the project id, which `project_confirmation` already
        carries as an enumerated field.
        """
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit(
            "project_confirmation_spoken", turn=self.turn_index, action=action
        )

    def record_recognition_escalation(self, text: str) -> None:
        """The warm hand-over after three consecutive unusable turns."""
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("recognition_escalation_spoken", turn=self.turn_index)

    def record_contact_line(self, text: str, stage: str) -> None:
        """The one contact request, or the read-back that checks its digits.

        Kept off the emitted stream like the other deterministic lines, and
        here for the sharpest version of that reason: the read-back
        interpolates the buyer's own phone number. `contact_read_back` already
        says a read-back happened, and `stage` says which line was spoken; the
        digits belong only in the in-process record the audit and the
        ambassador view read (docs/10- data handling).

        The line that SETTLES the contact is not recorded here - it is the
        farewell turn, thanks included, and `record_farewell` owns it.
        """
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("contact_line_spoken", turn=self.turn_index, stage=stage)

    def record_farewell(self, text: str) -> None:
        """The authored goodbye that ends the call.

        Recorded like the other deterministic lines, so the audit for the last
        turn says what the buyer actually heard. `completed=True` is corrected
        by `mark_interrupted` if they talked over it - which is also the signal
        that cancels the close.
        """
        self.spoken_chunks.append(SpokenChunk(text=text, completed=True))
        self._log.emit("farewell_spoken", turn=self.turn_index)

    def mark_interrupted(self) -> None:
        """Barge-in: the last chunk handed to TTS may not have finished
        playing. Chunk granularity is the claim (docs/04-)."""
        if self.spoken_chunks:
            last = self.spoken_chunks[-1]
            self.spoken_chunks[-1] = SpokenChunk(text=last.text, completed=False)
        self._log.emit("interrupted", turn=self.turn_index)

    # -- hook 2: tools ----------------------------------------------------

    def record_escalation(self, reason: str) -> bool:
        """Hand the buyer to a human, at most once this turn.

        Returns True when THIS call is the one that handed over.

        The REQUEST is recorded by the caller either way (`record_tool`), because
        which path asked for a human and when is the hook-2 claim. It is the
        NOTIFICATION that must not repeat: the STUB behind it is a CRM write, and
        two writes for one buyer turn is two tasks in an ambassador's queue.

        Two paths genuinely collide, and naming `escalate_to_human` in the
        regeneration instruction (eval F8) made the collision expected rather
        than rare: a retry that calls the tool AND still states an unallowed
        figure gets the composed fallback as well, so the fallback routes while
        the stream is unwinding and the framework executes the model's tool call
        afterwards. Both are real requests; only the first hands anyone over.

        Per TURN, not per session. A buyer who needs a human twice in one call
        has to be handed over twice, or the second ask vanishes - which is why
        the flag lives on the tracker rather than on the agent.
        """
        if self.handed_over:
            self._log.emit("escalation_suppressed", turn=self.turn_index, reason=reason)
            return False
        self.handed_over = True
        self._log.emit("escalation", reason=reason, routed_to="human_ambassador")
        return True

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
            audit_incomplete=audit_incomplete,
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
                None if self.guardrail_total is None else round(self.guardrail_total, 2)
            ),
            tts_first_audio_ms=_ms(self.tts_first_audio),
            total_ms=_ms(total),
            sentences=len(self.generated_sentences),
            violations=len(self.violations),
            regenerated=self.regenerated,
            # The model started before the final transcript arrived, so part of
            # the LLM latency on this turn was spent while the buyer was still
            # being transcribed. The meter needs that to read llm_ttft_ms
            # honestly against its budget row.
            preemptive=self.adopted,
            actions=self.actions,
            reasoning_tokens=self.reasoning_tokens,
            audit_incomplete=audit_incomplete,
        )
        return record
