"""Pure selection from final recogniser metadata, never a guess from script.

Weights are letter counts, so a project name or a short acknowledgement does
not redirect a conversation. The recogniser supplies the language; this rule
only decides whether the evidence warrants changing the reply language.
"""

from collections import Counter
from collections.abc import Sequence
from typing import cast, get_args

from .schemas import Language

_LANGUAGES = frozenset(get_args(Language))


def choose_language(current: Language, segments: Sequence[tuple[str, int]]) -> Language:
    weights: Counter[str] = Counter()
    for code, weight in segments:
        normalised = code.lower().replace("_", "-").split("-")[0]
        normalised = "zh" if normalised == "cmn" else normalised
        weights[normalised] += max(0, weight)
    if not weights:
        return current
    candidate, weight = weights.most_common(1)[0]
    minimum = 4 if candidate in ("zh", "ja") else 8
    if (
        candidate not in _LANGUAGES
        or weight < minimum
        or weight / max(1, weights.total()) < 0.65
    ):
        return current
    return cast(Language, candidate)
