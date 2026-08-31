"""Confirmation copy for the budget policy (ADR-011).

Fixed copy with one slot, loaded from data/confirmations.yaml - the same rule
as the disclosure and the fallbacks (ADR-013): copy the model composes can
vary, and a confirmation that varies is not a deterministic policy.

## Why this copy does not go through the guardrail

`SentenceGuard.compose()` routes composed speech through `process_sentence`,
which is right for the bridge and the fallback and wrong here: the slot holds
the buyer's own budget, which is by construction NOT an inventory figure, so
every confirmation would be blocked as a fabricated amount.

The numeric guardrail exists to stop the MODEL asserting a figure it invented.
Reading a buyer's own number back to them to check it is the opposite
operation, and blocking it would remove the one mechanism that catches a
misheard number.

The bypass is bounded rather than trusted. `compose()` refuses unless the
echoed text is a literal substring of what the buyer actually said, so nothing
that did not come out of the transcript can reach TTS through this path, and
no model output passes through this module at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)

# Every key the policy can ask for. A language missing any of them cannot run
# the policy at all, so partial authoring is treated as none.
_KEYS: Final = ("ask_currency", "confirm_amount", "cannot_convert", "give_up")


@dataclass(frozen=True)
class ConfirmationCopy:
    by_language: dict[Language, dict[str, str]]

    def languages_covered(self) -> frozenset[Language]:
        return frozenset(
            language
            for language, copy in self.by_language.items()
            if all(copy.get(key) for key in _KEYS)
        )

    def covers(self, language: Language) -> bool:
        return language in self.languages_covered()

    def line(self, language: Language, key: str) -> str:
        return self.by_language.get(language, {}).get(key, "")


def load_confirmations(path: Path | None = None) -> ConfirmationCopy:
    source = path or _DATA_DIR / "confirmations.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping of language to copy, "
            f"got {type(raw).__name__}."
        )
    by_language: dict[Language, dict[str, str]] = {}
    for language in _LANGUAGES:
        block = raw.get(language) or {}
        if not isinstance(block, dict):
            raise ValueError(
                f"{source.name}: {language!r} must map each of "
                f"{', '.join(_KEYS)} to text, got {type(block).__name__}."
            )
        copy: dict[str, str] = {}
        for key in _KEYS:
            value = block.get(key) or ""
            if not isinstance(value, str):
                raise ValueError(
                    f"{source.name}: {language!r}.{key} is a "
                    f"{type(value).__name__}, not text. It is spoken verbatim."
                )
            copy[key] = value.strip()
        by_language[language] = copy
    return ConfirmationCopy(by_language=by_language)


class EchoNotInTranscript(ValueError):
    """The slot value did not come from what the buyer said.

    Raised rather than spoken: this path skips the numeric guardrail, and the
    substring check is the whole reason that is safe.
    """


def compose(template: str, *, echoed: str, said: str) -> str:
    """Fill the one slot, refusing anything the buyer did not actually say.

    `said` must be the digit-normalised transcript, because `echoed` is a
    surface taken from normalised text - an Arabic-Indic figure would not match
    the raw utterance.
    """
    if not echoed or echoed not in said:
        raise EchoNotInTranscript(
            f"refusing to speak {echoed!r}: it is not a literal part of the "
            "buyer's utterance, and this copy bypasses the numeric guardrail "
            "precisely because it only ever echoes the transcript."
        )
    return template.format(amount=echoed)
