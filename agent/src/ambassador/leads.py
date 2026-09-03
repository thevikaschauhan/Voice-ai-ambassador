"""The interest score: all of it computed here, none of it by the model.

ADR-020 draws the line this module enforces. The session-analysis model returns
summary text, boolean signals and the turn indexes supporting each one. It
never returns a score, and it never does arithmetic - which is invariant 2 of
this system ("the model never does arithmetic") applied to a second surface.
The reason is the same one and it is sharper here: a price a model invents can
be checked against `data/inventory.json`, and a score a model invents can be
checked against nothing at all. It would simply be a number on an admin screen
that looks like a measurement.

So the model's output is evidence, and evidence is CHECKED before it counts.
Every cited turn index must exist in the snapshot that was saved, and every
named project must resolve through the inventory loader. An index nobody can
resolve is indistinguishable from a real one once it is a number on a screen,
and the admin surface shows exactly those turns as the reason a lead scored
what it did.

## Nothing is clamped silently

docs/10- is explicit that invalid input fails rather than degrading. That rule
does real work: a truncated call whose timestamps run backwards would otherwise
score as a very short one, which is the confusion `ended_cleanly` exists to
prevent. The only two caps here are the ones the rubric documents - two
questions and five minutes - and both are stated in the breakdown rather than
applied invisibly.

## The version is the point of the version

Weights change; historic scores are never recomputed. A total is therefore only
interpretable beside `score_version`, so the loader refuses a rubric without
one and every score carries the version that produced it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from .schemas import (
    ContactCapture,
    InterestScore,
    LeadAnalysisDraft,
    ScoreItem,
    ScoreSignal,
)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Derived from the Literal rather than restated, the rule every loader here
# follows: an eighth signal added to the contract must not silently stop being
# demanded of the rubric.
_SIGNALS: Final[tuple[ScoreSignal, ...]] = get_args(ScoreSignal)

# Fixed by docs/10- so a total is comparable across rubric versions. A rubric
# out of 50 would make a historic and a current lead read the same and mean
# different things.
_REQUIRED_MAXIMUM: Final = 100

# The two counted signals. Their caps are in points, not events, because that
# is the unit the breakdown reports and the unit the rubric bounds.
_POINTS_PER_QUESTION: Final = 5
_MAX_QUESTIONS: Final = 2
_SECONDS_PER_LENGTH_POINT: Final = 60


@dataclass(frozen=True)
class Rubric:
    """A versioned weight per signal. Frozen because a rubric that can be
    edited after loading is a rubric whose version means nothing."""

    version: str
    maximum: int
    weights: dict[ScoreSignal, int]


@dataclass(frozen=True)
class ScoringInputs:
    """Everything the score is computed from, model-supplied and not.

    The split matters: `draft` is what a model produced and every other field
    is a fact it never saw. Passing them together as one frozen value keeps a
    caller from scoring a draft against somebody else's snapshot.
    """

    draft: LeadAnalysisDraft
    contact: ContactCapture
    started_at: datetime
    ended_at: datetime
    buyer_turn_indexes: list[int]
    project_ids_in_inventory: list[str]


def load_rubric(path: Path | None = None) -> Rubric:
    """Parse the rubric, or refuse in front of whoever edited it.

    Every check here is a way the file can be wrong while still looking
    correct, which is why none of them is a warning.
    """
    source = path or _DATA_DIR / "interest-score.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the rubric must be a mapping, got "
            f"{type(document).__name__}."
        )

    version = document.get("version")
    if not isinstance(version, str) or not version.strip():
        # Without it a saved score cannot be interpreted after the next weight
        # change, which is the whole reason weights are versioned.
        raise ValueError(
            f"{source.name}: no 'version'. Historic scores keep the rubric that "
            "produced them, so a rubric without a version makes every score it "
            "writes uninterpretable later."
        )

    maximum = document.get("maximum")
    if maximum != _REQUIRED_MAXIMUM:
        raise ValueError(
            f"{source.name}: 'maximum' must be {_REQUIRED_MAXIMUM}, got "
            f"{maximum!r}. It is fixed so a score means the same thing across "
            "rubric versions."
        )

    raw_weights = document.get("weights")
    if not isinstance(raw_weights, dict):
        raise ValueError(
            f"{source.name}: 'weights' must be a mapping of signal to points, "
            f"got {type(raw_weights).__name__}."
        )

    unknown = sorted(set(raw_weights) - set(_SIGNALS))
    if unknown:
        raise ValueError(
            f"{source.name}: weights name signals the scorer does not compute: "
            f"{', '.join(unknown)}. Points nobody can earn silently change what "
            "a total out of 100 means."
        )
    missing = [signal for signal in _SIGNALS if signal not in raw_weights]
    if missing:
        raise ValueError(
            f"{source.name}: no weight for {', '.join(missing)}. All "
            f"{len(_SIGNALS)} signals are required exactly once; a dropped one "
            "lowers every future score for a reason no reader could see."
        )

    weights: dict[ScoreSignal, int] = {}
    for signal in _SIGNALS:
        value = raw_weights[signal]
        # `bool` is an int in Python and would score `True` as one point.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"{source.name}: the weight for {signal!r} is {value!r}. Points "
                "are whole and non-negative: a fraction makes totals "
                "irreproducible across versions, and a negative would let a "
                "signal subtract, which no rubric describes."
            )
        weights[signal] = value

    total = sum(weights.values())
    if total != maximum:
        raise ValueError(
            f"{source.name}: the weights sum to {total}, not the declared "
            f"maximum of {maximum}. The bound is what makes 'out of {maximum}' "
            "true rather than aspirational."
        )
    return Rubric(version=version, maximum=maximum, weights=weights)


def _check_evidence(
    signal: str, cited: list[int], buyer_turn_indexes: set[int]
) -> None:
    """Refuse an index that is not a turn in the saved snapshot.

    Fails rather than dropping the index, because a signal quietly scoring on
    half its evidence is worse than one that does not score: the admin sees the
    points and a shorter list of turns, with nothing saying why.
    """
    unknown = sorted(set(cited) - buyer_turn_indexes)
    if unknown:
        raise ValueError(
            f"{signal}: turn "
            f"{', '.join(str(index) for index in unknown)} "
            "is not a buyer turn in this lead. Evidence has to point at the "
            "transcript that was saved."
        )


def _boolean_item(
    signal: ScoreSignal, evidence, rubric: Rubric, buyer_turns: set[int]
) -> ScoreItem:
    _check_evidence(signal, evidence.turn_indexes, buyer_turns)
    return ScoreItem(
        signal=signal,
        observed=evidence.observed,
        raw_value=evidence.observed,
        points_awarded=rubric.weights[signal] if evidence.observed else 0,
        max_points=rubric.weights[signal],
        evidence_turn_indexes=list(evidence.turn_indexes),
    )


def score_interest(inputs: ScoringInputs, rubric: Rubric) -> InterestScore:
    """The total and its per-signal explanation.

    Every signal appears in the breakdown even at zero, because the breakdown
    IS the explanation: a signal missing from it reads as "not part of the
    rubric" rather than "scored nothing".
    """
    draft = inputs.draft
    buyer_turns = set(inputs.buyer_turn_indexes)

    if inputs.ended_at < inputs.started_at:
        raise ValueError(
            "ended_at is before started_at, so this call has no duration to "
            "score. Refused rather than scored as zero, which would make a "
            "broken clock look like a short call."
        )

    unknown_projects = sorted(
        set(draft.project_ids) - set(inputs.project_ids_in_inventory)
    )
    if unknown_projects:
        raise ValueError(
            f"project_named cites {', '.join(unknown_projects)}, which does not "
            "resolve to an inventory project. A name the model recognised and "
            "inventory does not must not earn points for being named."
        )

    items = [
        _boolean_item("budget_stated", draft.budget_stated, rubric, buyer_turns),
        _boolean_item("project_named", draft.project_named, rubric, buyer_turns),
        _boolean_item("timeline_stated", draft.timeline_stated, rubric, buyer_turns),
    ]

    # Contact needs a reachable value AND permission. A name with no number is
    # not a shared contact, and a number retained without permission must never
    # earn points for having been retained.
    shared = (
        inputs.contact.status == "captured"
        and inputs.contact.has_reachable_value
        and inputs.contact.contact_permission
    )
    items.append(
        ScoreItem(
            signal="contact_shared",
            observed=shared,
            raw_value=shared,
            points_awarded=rubric.weights["contact_shared"] if shared else 0,
            max_points=rubric.weights["contact_shared"],
            evidence_turn_indexes=(
                [inputs.contact.source_turn_index]
                if shared and inputs.contact.source_turn_index is not None
                else []
            ),
        )
    )

    items.append(
        _boolean_item(
            "viewing_or_human_requested",
            draft.viewing_or_human_requested,
            rubric,
            buyer_turns,
        )
    )

    # DISTINCT turns: a model listing the same index twice must not buy the cap
    # with one question.
    _check_evidence("questions_asked", draft.question_turn_indexes, buyer_turns)
    questions = sorted(set(draft.question_turn_indexes))
    counted = min(len(questions), _MAX_QUESTIONS)
    items.append(
        ScoreItem(
            signal="questions_asked",
            observed=bool(questions),
            raw_value=len(questions),
            points_awarded=min(
                counted * _POINTS_PER_QUESTION, rubric.weights["questions_asked"]
            ),
            max_points=rubric.weights["questions_asked"],
            evidence_turn_indexes=questions,
        )
    )

    # COMPLETE minutes only, from the timestamps rather than from anything the
    # model said about how long the call felt.
    seconds = (inputs.ended_at - inputs.started_at).total_seconds()
    minutes = math.floor(seconds / _SECONDS_PER_LENGTH_POINT)
    items.append(
        ScoreItem(
            signal="call_length",
            observed=minutes > 0,
            raw_value=minutes,
            points_awarded=min(minutes, rubric.weights["call_length"]),
            max_points=rubric.weights["call_length"],
        )
    )

    ordered = {item.signal: item for item in items}
    breakdown = [ordered[signal] for signal in _SIGNALS]
    return InterestScore(
        total=sum(item.points_awarded for item in breakdown),
        score_version=rubric.version,
        breakdown=breakdown,
    )
