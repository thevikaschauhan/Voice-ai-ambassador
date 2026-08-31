"""What the buyer actually heard, and the questions an assertion may ask of it.

The repo learned this the hard way (AGENTS.md, 2026-08-31): a suite that
asserts on the objects a state machine returned can be green while every
sentence the buyer hears is wrong. So the eval harness records SPEECH - the
segments that reached the point of synthesis, in the order they were spoken,
across every turn of the case - and every assertion is answered from that.

Two texts are kept per segment and they are not interchangeable:

  `validated`  the digit-form text that passed `run_guardrails`. Figure
               assertions read this, because a figure is a figure only while it
               is still digits: after verbalisation "985,000" is the word
               "thousand" and `extract_figures` cannot see it.
  `spoken`     what `verbalise()` produced, which is what TTS is handed. The
               spoken-form and language assertions read this, because it is the
               only text that reflects what a caller's ear receives.

A segment's `origin` records who composed it. Composed copy - the bridge, the
fallback, the budget confirmation - is speech the buyer heard just as much as a
model sentence is, so it belongs in this record; the confirmation deliberately
does not pass through verbalisation (see adapter/confirmations.py) and carries
the same string in both fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ambassador.figures import extract_figures
from ambassador.schemas import ExtractedFigure, FigureKind, GuardrailViolation, Language
from ambassador.verbalise import SpokenForms

SegmentOrigin = Literal["model", "bridge", "fallback", "confirmation"]


@dataclass(frozen=True)
class Spoken:
    validated: str
    spoken: str
    origin: SegmentOrigin


@dataclass(frozen=True)
class TurnOutcome:
    buyer: str
    # The model's unvalidated FIRST reply, empty when the deterministic policy
    # took the turn and the model was never called. First, not last, because
    # `must_emit_digits` is about the text the guardrail was handed - and a
    # spelled-out price is never blocked, so the first attempt is the one that
    # would have reached the buyer.
    model_text: str
    heard: tuple[Spoken, ...]
    blocked: tuple[GuardrailViolation, ...] = ()
    # The one regeneration the recovery policy allows, when it was spent. Kept
    # for the report: "blocked, told why, blocked again" is a different story
    # from "blocked once", and a reader of the failing row needs to see both
    # attempts to tell them apart.
    regenerated_text: str = ""
    escalation_reasons: tuple[str, ...] = ()
    confirmed: bool = False
    regenerated: bool = False

    @property
    def escalated(self) -> bool:
        return bool(self.escalation_reasons)


@dataclass(frozen=True)
class Observed:
    """One finished case, as the buyer experienced it.

    `escalated` is true when a human was actually notified, by either route:
    the model calling `escalate_to_human`, or the deterministic budget policy
    handing over. Both are the same event from the buyer's side, and the policy
    docstring is explicit that a handover must notify someone - "let me put you
    through" with nobody notified is the anti-pattern the tool names.
    """

    language: Language
    forms: SpokenForms
    turns: tuple[TurnOutcome, ...]
    # Non-empty when the case could not be run at all (a missing fixture, a
    # live call that failed). A case that did not run is a FAILURE, never a
    # skip: the alternative is a report whose pass rate quietly excludes the
    # cases nobody could answer.
    error: str = ""
    _figures: list[ExtractedFigure] = field(default_factory=list, repr=False)

    @property
    def heard(self) -> tuple[Spoken, ...]:
        return tuple(segment for turn in self.turns for segment in turn.heard)

    @property
    def spoken_text(self) -> str:
        return " ".join(segment.spoken for segment in self.heard)

    @property
    def validated_text(self) -> str:
        return " ".join(segment.validated for segment in self.heard)

    @property
    def model_text(self) -> str:
        return " ".join(turn.model_text for turn in self.turns if turn.model_text)

    @property
    def blocked(self) -> tuple[GuardrailViolation, ...]:
        return tuple(v for turn in self.turns for v in turn.blocked)

    @property
    def escalated(self) -> bool:
        return any(turn.escalated for turn in self.turns)

    @property
    def escalation_reasons(self) -> tuple[str, ...]:
        return tuple(r for turn in self.turns for r in turn.escalation_reasons)

    @property
    def confirmed(self) -> bool:
        return any(turn.confirmed for turn in self.turns)

    @property
    def regenerated(self) -> bool:
        return any(turn.regenerated for turn in self.turns)

    @property
    def figures(self) -> list[ExtractedFigure]:
        if not self._figures:
            self._figures.extend(m.figure for m in extract_figures(self.validated_text))
        return self._figures

    def stated(self, value: float, kind: FigureKind = "amount") -> bool:
        return any(f.value == value and f.kind == kind for f in self.figures)

    def spoken_form(self, value: float, kind: FigureKind = "amount") -> str | None:
        return self.forms.by_value.get((self.language, kind, float(value)))

    def quote(self, limit: int = 220) -> str:
        """The spoken text, trimmed, for a failure message. Empty speech is
        reported as such rather than as an empty string: a turn that ended in
        silence is its own defect (AGENTS.md)."""
        text = self.spoken_text.strip()
        if not text:
            return "<nothing was spoken>"
        return repr(text if len(text) <= limit else text[: limit - 1] + "…")
