"""Failed recognitions, counted (ADR-011, docs/04-).

The fourth of ADR-011's triggers: *three consecutive failed recognitions
escalate warmly*. It is a different thing from the budget policy's three
attempts, which count replies that answered the question wrongly. This counts
turns nobody could hear at all.

## What a failed recognition IS, deterministically

docs/04- names the two shapes an unusable turn arrives in, and neither needs a
vendor confidence score - ADR-011 exists precisely because streaming
confidence is often absent or uncalibrated:

1. **Empty.** The transcript carries no letter and no digit: nothing at all,
   whitespace, or punctuation the recogniser emitted around silence.
2. **Garbage.** Every token in it is a noise token - "uh", "hmm", "mm" - the
   sound a recogniser returns when it heard breath, a cough or a corridor.
   The word lists live in `data/recognition.yaml`, per language, because what
   a filler sounds like is a fact about a language.

Everything else is a recognition, however unhelpful: "what?" and "no" are
things the buyer said and are answered, not counted.

The empty half is language-neutral and works in all three languages today.
The garbage half needs an authored list, and a language without one simply
never classifies a turn as garbage - the safe direction, and no worse than the
behaviour before this existed.

## Consecutive, and once

The counter resets on any real turn: three failures spread over a good call
are three ordinary "sorry, could you repeat that" moments, and escalating on
them would make the policy read as broken. Three in a row is a call that is
not working.

At three the policy hands over and then stops. It does not re-escalate on the
fourth failure: a human has already been notified, and a bot that announces
the handover again every time the line crackles is worse than one that says
it once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .figures import normalise_digits

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Any letter or digit in any script. Arabic and Devanagari included: `\w`
# under re.UNICODE covers them, which is what makes the empty test work in
# every language without an authored list.
_CONTENT = re.compile(r"[^\W_]", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

# Three, from ADR-011 and docs/04-. A voice bot that makes the buyer repeat
# themselves a fourth time earns lasting resentment.
_MAX_CONSECUTIVE = 3


@dataclass(frozen=True)
class NoiseWords:
    """Per language, the tokens that mean the recogniser heard no speech."""

    by_language: dict[str, tuple[str, ...]]

    def languages_covered(self) -> frozenset[str]:
        return frozenset(
            language for language, words in self.by_language.items() if words
        )

    def words(self, language: str) -> tuple[str, ...]:
        return self.by_language.get(language, ())


def load_noise_words(path: Path | None = None) -> NoiseWords:
    source = path or _DATA_DIR / "recognition.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping, got {type(raw).__name__}."
        )
    by_language: dict[str, tuple[str, ...]] = {}
    for language, values in (raw.get("noise") or {}).items():
        words: list[str] = []
        for word in values or []:
            if not isinstance(word, str):
                # The YAML 1.1 boolean trap, the same one `currencies.yaml`
                # walked into: bare no/yes/on/off load as booleans and a
                # coerced boolean is a word that can never match. Loud here
                # beats silent on a call.
                raise ValueError(
                    f"{source.name}: noise.{language} contains {word!r}, not "
                    "text. Quote the word in the data file."
                )
            words.append(word.lower())
        by_language[language] = tuple(words)
    return NoiseWords(by_language=by_language)


def is_failed_recognition(utterance: str, noise: NoiseWords, language: str) -> bool:
    """Did this turn carry anything the agent can answer?"""
    text = normalise_digits(utterance)
    if not _CONTENT.search(text):
        return True
    words = noise.words(language)
    if not words:
        return False
    tokens = _TOKEN.findall(text.lower())
    return all(token in words for token in tokens)


Action = Literal["none", "escalate"]


@dataclass(frozen=True)
class RecognitionDecision:
    action: Action
    # Whether THIS turn was unusable. The caller needs it even when the action
    # is "none": a turn nobody heard is not an answer to an open confirmation,
    # and must not consume one of the buyer's attempts at it.
    failed: bool
    consecutive: int

    @property
    def speaks(self) -> bool:
        return self.action != "none"

    @property
    def hands_over(self) -> bool:
        return self.action == "escalate"


class RecognitionMonitor:
    """Counts consecutive unusable turns and hands over at the third."""

    def __init__(
        self, noise: NoiseWords, language: str, *, limit: int = _MAX_CONSECUTIVE
    ) -> None:
        self._noise = noise
        self._language = language
        self._limit = limit
        self._consecutive = 0
        self._handed_over = False

    @property
    def consecutive(self) -> int:
        return self._consecutive

    @property
    def handed_over(self) -> bool:
        return self._handed_over

    def observe(self, utterance: str) -> RecognitionDecision:
        failed = is_failed_recognition(utterance, self._noise, self._language)
        if not failed:
            self._consecutive = 0
            return RecognitionDecision("none", failed=False, consecutive=0)
        self._consecutive += 1
        if self._handed_over or self._consecutive < self._limit:
            # Still classified after the handover, because the caller's other
            # question is "was this an answer?" and the answer stays no.
            return RecognitionDecision(
                "none", failed=True, consecutive=self._consecutive
            )
        self._handed_over = True
        return RecognitionDecision(
            "escalate", failed=True, consecutive=self._consecutive
        )
