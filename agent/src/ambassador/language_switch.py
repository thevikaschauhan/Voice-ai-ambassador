"""Pure selection from final recogniser metadata, never a guess from script.

The recogniser supplies the language; this rule only decides whether the
evidence warrants changing the reply language for the rest of the call. That
decision is not cosmetic: the response language selects which prohibited
patterns apply, which farewell phrases the buyer can close the call with, and
which disclosure copy is certified (ADR-010, ADR-019). So the bias is towards
the INCUMBENT, and a challenger has to earn the change twice.

Three barriers, and they fail apart:

  weight   letters, so a short acknowledgement carries little evidence and a
           2/3 share of the turn is required. Chinese and Japanese need fewer
           letters because they write fewer of them for the same content.
  terms    brand and project names are removed BEFORE anything is counted, so
           saying the client's own product name is not evidence of a language
           at all. `Binghatti Skyhall` was 16 letters of German once the
           recogniser guessed German, and that was enough to flip a call.
           Passed in rather than imported: the list lives in the adapter
           beside the recogniser's keyterms, and a core module may not import
           the adapter (ADR-002).
  turns    a challenger must lead on two CONSECUTIVE calls of this function.
           Counted per INVOCATION and never per segment - one buyer turn can
           carry several finals, so a per-segment streak would let a single
           talkative utterance satisfy the requirement by itself.

`SwitchProgress` is returned rather than kept here, so the whole rule stays a
function of its arguments and a test can replay a conversation from a list of
turns.

NOTE FOR THE CALLER: `segments` carries the raw TRANSCRIPT, because the
exclusion has to happen before the letters are counted. That makes the
caller's segment buffer hold buyer speech rather than integers, so it must
never be handed to an event emitter - free text on the emitted stream is
redacted by validator 4 precisely because it is not supposed to be there
(docs/03-).
"""

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import cast, get_args

from .schemas import Language

_LANGUAGES = frozenset(get_args(Language))

# Letters needed before a challenger is considered at all. Chinese and
# Japanese write far fewer characters for the same content, so holding them to
# the same count would mean they could only ever switch on a speech.
_MINIMUM_LETTERS = 8
_MINIMUM_LETTERS_BY_LANGUAGE = {"zh": 4, "ja": 4}

# Share of the turn's letters the challenger must hold. Two thirds rather than
# a bare majority: a buyer who code-switches one phrase into their own
# language has not changed language, and a 51% rule reads that as a change.
_REQUIRED_SHARE = 0.65

# Consecutive invocations - turns - a challenger must lead before it takes
# over. The card asked for two; the unit is the point (see `turns` above).
_REQUIRED_TURNS = 2


@dataclass(frozen=True)
class SwitchProgress:
    """How close a challenger is to taking over. The caller holds it.

    An empty value means no challenger is pending, which is also the state a
    completed switch leaves behind.
    """

    candidate: Language | None = None
    turns: int = 0


def _base_code(code: str) -> str:
    """`pt-BR` to `pt`, `cmn` to `zh`.

    The adapter hands over an already-normalised base code, so on a real call
    this is a no-op. It stays because the eval harness and the unit tests feed
    raw recogniser codes, and normalising in one place is cheaper than knowing
    which callers did it.
    """
    normalised = code.lower().replace("_", "-").split("-")[0]
    return "zh" if normalised == "cmn" else normalised


def _without_terms(text: str, terms: Collection[str]) -> str:
    """Remove brand and project names, case-insensitively.

    Longest first, so a multi-word name is removed as a phrase before any word
    inside it can be removed on its own - otherwise `Bugatti Residences` loses
    `Bugatti` and leaves `Residences` behind as evidence.
    """
    for term in sorted((term for term in terms if term), key=len, reverse=True):
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    return text


def _letters_by_language(
    segments: Sequence[tuple[str, str]], terms: Collection[str]
) -> Counter[str]:
    weights: Counter[str] = Counter()
    for code, transcript in segments:
        weights[_base_code(code)] += sum(
            character.isalpha() for character in _without_terms(transcript, terms)
        )
    return weights


def choose_language(
    current: Language,
    segments: Sequence[tuple[str, str]],
    *,
    progress: SwitchProgress = SwitchProgress(),
    excluded_terms: Collection[str] = (),
    required_turns: int = _REQUIRED_TURNS,
) -> tuple[Language, SwitchProgress]:
    """The reply language for the next turn, and the progress to carry forward.

    Call once per buyer turn with that turn's segments. Returns `current`
    unchanged unless a single challenger has now led for `required_turns`
    consecutive calls.
    """
    weights = _letters_by_language(segments, excluded_terms)
    total = weights.total()
    if not total:
        return current, SwitchProgress()

    candidate, weight = weights.most_common(1)[0]
    minimum = _MINIMUM_LETTERS_BY_LANGUAGE.get(candidate, _MINIMUM_LETTERS)
    if (
        candidate == current
        or candidate not in _LANGUAGES
        or weight < minimum
        or weight / total < _REQUIRED_SHARE
    ):
        # The incumbent held this turn, so a pending challenger starts over.
        # Consecutive is the requirement; accumulating single turns over a long
        # call is how a buyer who says one foreign sentence now and then would
        # otherwise switch the call without ever meaning to.
        return current, SwitchProgress()

    turns = progress.turns + 1 if progress.candidate == candidate else 1
    if turns < required_turns:
        return current, SwitchProgress(candidate=cast(Language, candidate), turns=turns)
    return cast(Language, candidate), SwitchProgress()
