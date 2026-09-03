"""Text mode: the same core, driven as chat, over stdin and stdout.

docs/01- makes this the venue plan B - "if venue audio dies, the same core
demos as text chat" - and the word that carries the claim is SAME. A text mode
that reimplemented the turn would be a third twin of a pipeline that already
has two, and the two it has are kept honest by tests that pin them to each
other. So this composes the existing pieces instead:

  evals.runner.run_turn   the non-streaming twin of a live turn: the
                          deterministic confirmation policy first, then the
                          model, sentences split on the core's boundaries,
                          each one through `process_sentence`, one repair
                          retry, bridge or fallback, escalation.
  adapter.brief           the same post-turn extractor the voice path uses, so
                          the ambassador view is populated by the real thing
                          rather than sitting empty in the fallback.

It emits the SAME event shapes `adapter/events.py` emits, because the demo
surface folds both through one reducer (issue #9). Anything that has to be
different is different because text mode genuinely lacks it - there is no
end-of-utterance to measure and no audio to synthesise - and those fields are
null rather than zero, which is events.py's own rule.

WHY A PROCESS AND NOT A LIBRARY CALL. The web tier is TypeScript and the core
is Python, so something has to cross that line. A subprocess speaking
newline-delimited JSON keeps the whole pipeline - prompts, guardrails,
verbalisation, policies - on the side that owns it, and keeps provider
credentials off the browser, which AGENTS.md requires absolutely. The session
lives for the life of the process because a turn is not independent: the
confirmation coordinator carries call state that cannot be reconstructed from
the transcript.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Final

from ambassador.ambassadors import load_ambassadors
from ambassador.schemas import Language

from .config import load_settings
from evals.backends import BackendError, LiveBackend
from evals.outcome import TurnOutcome
from evals.runner import Harness, run_turn

# The events this module can emit, so a reader does not have to infer the
# contract from the call sites. Every one of them is a name `adapter/events.py`
# already classifies.
EMITTED = (
    "session_start",
    "user_turn",
    "guardrail",
    "regeneration",
    "bridge",
    "fallback",
    "budget_confirmation_spoken",
    "tool_call",
    "escalation",
    "brief",
    "turn_complete",
    "session_error",
)


# Distinguishes "build the real extractor" from "run without one". `None`
# cannot carry both meanings, and a bool sentinel reads as a flag at the call
# site while behaving as an object inside.
_BUILD_EXTRACTOR: Final = object()


class TextSession:
    """One buyer's chat session, with the state a call carries."""

    def __init__(
        self,
        *,
        language: Language = "en",
        backend: Any | None = None,
        harness: Harness | None = None,
        extractor: Any = _BUILD_EXTRACTOR,
    ) -> None:
        # The three seams exist so this is testable without spending money on a
        # model to prove that a blocked sentence renders as a blocked sentence.
        # `main()` injects nothing and gets the real thing.
        needs_settings = backend is None or extractor is _BUILD_EXTRACTOR
        settings = load_settings() if needs_settings else None
        self._language: Language = language
        self._harness = harness or Harness.load()
        self._policies = self._harness.coordinator(language)
        self._prompt = self._harness.prompt(language)
        self._history: list[tuple[str, str]] = []
        self._turn_index = 0
        self._backend = backend or LiveBackend(
            api_key=settings.openrouter_api_key,  # type: ignore[union-attr]
            model=settings.llm_model,  # type: ignore[union-attr]
            base_url=settings.llm_base_url,  # type: ignore[union-attr]
            thinking_disabled=settings.thinking_disabled,  # type: ignore[union-attr]
        )
        self._brief_events: list[dict[str, Any]] = []
        self._brief = (
            _brief_extractor(settings, self._harness, language, self._collect)
            if extractor is _BUILD_EXTRACTOR
            else extractor
        )

    def _collect(self, event: str, **fields: Any) -> None:
        """The brief extractor emits through the same shape `EventLog.emit`
        uses, so its events reach the surface unchanged."""
        self._brief_events.append({"event": event, **fields})

    def start(self) -> list[dict[str, Any]]:
        return [
            {
                "event": "session_start",
                "model": self._backend_model,
                "language": self._language,
                # Text mode runs the ambassador prompt and the enforcing
                # guardrail, and says so rather than leaving the surface to
                # assume: the toggle pair is a property of the session.
                "prompt_mode": "ambassador",
                "guardrail_mode": "enforce",
                "inventory_version": self._harness.prompt_fingerprint(self._language),
                # Same field as the voice path, from the same file. The two
                # transports are pinned to one session contract (#86), and an
                # ambassador who is Jane on a call and unnamed in text would be
                # the kind of divergence that pinning exists to prevent.
                "ambassador_name": load_ambassadors().name_for(self._language),
            }
        ]

    @property
    def _backend_model(self) -> str:
        return getattr(self._backend, "_model", "unknown")

    async def turn(self, text: str) -> list[dict[str, Any]]:
        self._turn_index += 1
        index = self._turn_index
        started = time.perf_counter()
        self._history.append(("user", text))

        events: list[dict[str, Any]] = [
            {"event": "user_turn", "turn": index, "text": text}
        ]

        try:
            outcome = run_turn(
                language=self._language,
                case_id=f"text-turn-{index}",
                turn_index=index - 1,
                buyer=text,
                fixture=None,
                harness=self._harness,
                backend=self._backend,
                policies=self._policies,
                system_prompt=self._prompt,
                history=tuple(self._history),
            )
        except BackendError as exc:
            # A turn never ends in silence, on any transport (AGENTS.md). The
            # composed fallback IS the reply, and it is the line that hands the
            # buyer to a human - so a human is actually notified, exactly as
            # the voice path does it.
            copy = self._harness.fallbacks.fallback[self._language]
            events.append(
                {
                    "event": "fallback",
                    "turn": index,
                    "text": copy,
                    "reason": "backend",
                }
            )
            events.extend(_escalate(index, "the model could not be reached"))
            events.append({"event": "session_error", "turn": index, "error": str(exc)})
            events.append(_turn_complete(index, started, sentences=0, violations=0))
            self._history.append(("assistant", copy))
            return events

        events.extend(_turn_events(index, outcome))

        # What the buyer HEARD is what the model sees next turn, not the raw
        # reply: a blocked sentence never reached them and must not reach the
        # context either, or the next turn is grounded in speech nobody said.
        spoken = " ".join(segment.spoken for segment in outcome.heard)
        if spoken:
            self._history.append(("assistant", spoken))

        if self._brief is not None:
            await self._extract_brief(index)

        events.extend(self._drain_brief_events())
        events.append(
            _turn_complete(
                index,
                started,
                sentences=len(outcome.heard) + len(outcome.blocked),
                violations=len(outcome.blocked),
                regenerated=outcome.regenerated,
                actions=["escalate_to_human"] if outcome.escalated else [],
            )
        )
        return events

    async def _extract_brief(self, index: int) -> None:
        """Awaited here, unlike the voice path.

        On a call the extraction is detached because the buyer is waiting for
        audio; in a chat the reply is already on screen by the time this runs,
        and awaiting it means the ambassador view updates with the turn instead
        of one turn behind. Failures are already swallowed inside the extractor
        and reported as events, so this cannot hang the session on a bad
        response - only on a slow one, which its own timeout bounds.
        """
        assert self._brief is not None
        transcript = [
            {"role": role, "content": content} for role, content in self._history
        ]
        self._brief.schedule(transcript, index)
        await self._brief.drain(timeout=20.0)
        # No `brief` event is appended here: the extractor emits its own
        # through `_collect`, and adding a second one put the same brief on the
        # stream twice. Its failure events - brief_invalid, brief_error,
        # brief_fallback - come through the same channel, so a bad extraction
        # is reported rather than silently absent.

    def _drain_brief_events(self) -> list[dict[str, Any]]:
        drained, self._brief_events = self._brief_events, []
        return drained

    async def aclose(self) -> None:
        close = getattr(self._backend, "close", None)
        if callable(close):
            close()
        if self._brief is not None:
            await self._brief.aclose()


def _brief_extractor(
    settings: Any, harness: Harness, language: Language, on_event: Any
):
    """None when nothing is configured to run it, rather than a broken one.

    The surface renders an empty brief panel with copy that says extraction has
    not returned yet, which is true, and is better than a session that fails to
    start because a second model is unavailable.
    """
    from .brief import BriefExtractor

    if not settings.openrouter_api_key:
        return None
    return BriefExtractor(
        api_key=settings.openrouter_api_key,
        model=settings.brief_model,
        base_url=settings.llm_base_url,
        project_ids=[project.id for project in harness.projects],
        language=language,
        on_event=on_event,
        thinking_disabled=settings.thinking_disabled,
    )


def _turn_events(index: int, outcome: TurnOutcome) -> list[dict[str, Any]]:
    """One turn's outcome, as the events the surface already folds."""
    events: list[dict[str, Any]] = []

    for violation in outcome.blocked:
        events.append(
            {
                "event": "guardrail",
                "turn": index,
                "outcome": "blocked",
                "mode": "enforce",
                # The harness does not time the validator, and a made-up
                # duration on the panel built to report real ones would be
                # worse than no number.
                "ms": 0.0,
                "sentence_index": len(events),
                "raw": outcome.model_text or outcome.regenerated_text,
                "spoken": None,
                "validator": violation.validator,
                "detail": violation.detail,
                "figures": [figure.model_dump() for figure in violation.figures],
            }
        )

    if outcome.regenerated:
        events.append(
            {
                "event": "regeneration",
                "turn": index,
                "reason": _first_detail(outcome),
            }
        )

    for position, segment in enumerate(outcome.heard):
        if segment.origin == "model":
            events.append(
                {
                    "event": "guardrail",
                    "turn": index,
                    "outcome": "pass",
                    "mode": "enforce",
                    "ms": 0.0,
                    "sentence_index": len(outcome.blocked) + position,
                    "raw": segment.validated,
                    "spoken": segment.spoken,
                    "validator": None,
                    "detail": None,
                    "figures": None,
                }
            )
        elif segment.origin == "bridge":
            events.append({"event": "bridge", "turn": index, "text": segment.spoken})
        elif segment.origin == "fallback":
            events.append(
                {
                    "event": "fallback",
                    "turn": index,
                    "text": segment.spoken,
                    "reason": "guardrail",
                }
            )
        else:
            events.append(
                {
                    "event": "budget_confirmation_spoken",
                    "turn": index,
                    "action": "confirm",
                    "text": segment.spoken,
                }
            )

    for reason in outcome.escalation_reasons:
        events.extend(_escalate(index, reason))

    return events


def _escalate(index: int, reason: str) -> list[dict[str, Any]]:
    """Both halves, the way the voice path does it: the tool call is what the
    audit sees, the escalation is what actually notified somebody."""
    return [
        {
            "event": "tool_call",
            "turn": index,
            "tool": "escalate_to_human",
            "args": {"reason": reason},
            "at_ms": None,
            "audio_already_played": False,
        },
        {"event": "escalation", "reason": reason, "routed_to": "human_ambassador"},
    ]


def _first_detail(outcome: TurnOutcome) -> str:
    return outcome.blocked[0].detail if outcome.blocked else "regenerated"


def _turn_complete(
    index: int,
    started: float,
    *,
    sentences: int,
    violations: int,
    regenerated: bool = False,
    actions: list[str] | None = None,
) -> dict[str, Any]:
    """Only `total_ms` is measured here, and the rest say so.

    A typed turn has no end-of-utterance, no recogniser and no synthesis, so
    those stages are absent rather than fast. events.py's rule is that a
    missing measurement and a zero-latency stage must not look the same on the
    meter, and text mode is where that rule earns itself.
    """
    return {
        "event": "turn_complete",
        "turn": index,
        "endpoint_ms": None,
        "stt_ms": None,
        "llm_ttft_ms": None,
        "llm_first_sentence_ms": None,
        "guardrail_ms": None,
        "tts_first_audio_ms": None,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "sentences": sentences,
        "violations": violations,
        "regenerated": regenerated,
        "actions": actions or [],
        "reasoning_tokens": None,
        "audit_incomplete": False,
    }


# -- the process interface --------------------------------------------------


async def _serve(stdin: Any, stdout: Any) -> None:
    """One request per line in, one response per line out.

    Requests: {"text": "..."} and nothing else is required. The response is
    {"events": [...]}, or {"error": "..."} for a request this could not read -
    never a traceback, because the other side of this pipe renders to a buyer.
    """
    session: TextSession | None = None
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                text = str(request["text"]).strip()
                if not text:
                    raise ValueError("text is required")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                _write(stdout, {"error": f"bad request: {exc}"})
                continue

            # ONE line in, ONE line out. The session's opening events ride
            # along with the first turn rather than arriving as an extra line:
            # a protocol where some requests answer twice makes the caller
            # track which request it is on, and the caller got that wrong the
            # first time by sending a second turn to collect the second line.
            opening: list[dict[str, Any]] = []
            if session is None:
                session = TextSession()
                opening = session.start()

            _write(stdout, {"events": opening + await session.turn(text)})
    finally:
        if session is not None:
            await session.aclose()


def _write(stdout: Any, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    stdout.flush()


def main() -> None:
    asyncio.run(_serve(sys.stdin, sys.stdout))


if __name__ == "__main__":
    main()
