"""Confirmation copy for the budget policy (ADR-011).

Fixed copy with one slot, loaded from data/confirmations.yaml - the same rule
as the disclosure and the fallbacks (ADR-013): copy the model composes can
vary, and a confirmation that varies is not a deterministic policy.

## Why this copy does not go through the guardrail pipeline

`SentenceGuard.compose()` routes composed speech through `process_sentence`,
which is guardrails and then verbalisation, and both halves are wrong here.

The guardrail half would block SOME confirmations - a buyer budget that is
not also an inventory figure reads as fabricated - but not reliably: a budget
that happens to coincide with an inventory price composes fine, so the
guardrail is not a dependable gate either way. It exists to stop the MODEL
asserting a figure it invented; reading a buyer's own number back to check it
is the opposite operation.

The verbalisation half is the decisive reason. `verbalise()` rewrites
"985,000" into a spoken form that names dirhams, asserting a currency the
buyer never stated - on the exact turn whose purpose is to ASK which currency
they meant. The echo must stay the buyer's transcript surface, verbatim: the
substring bound below is the safety mechanism, and a read-back that
paraphrases is not a read-back. The surfaces are short amounts ("2 crore",
"985,000"), and plain digits are the same fallback ADR-009 already accepts
as TTS-readable for anything outside the spoken-forms table.

The bypass is bounded rather than trusted. `compose()` refuses unless the
echoed text is a literal substring of the utterance the mention was extracted
from, so nothing that did not come out of the buyer's transcript can reach
TTS through this path, and no model output passes through this module at all.

## Failure direction

Every error this module raises must be handled by FAILING CLOSED: speak the
give-up line and route the buyer to a human. Returning the turn to the model
"because the confirmation could not be spoken" is the fail-open defect the
review caught - the safety mechanism switching itself off on exactly the path
it exists to protect. `give_up` is therefore validated at load time to carry
no slot, so the terminal line every failure falls back to can always compose.
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
_KEYS: Final = (
    "ask_currency",
    "confirm_amount",
    "ask_amount",
    "cannot_convert",
    "give_up",
)


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
        if "{" in copy["give_up"]:
            # Any brace, not just a well-formed {amount}: give_up is spoken
            # VERBATIM on the failure path, without composing, so a malformed
            # slot here would reach TTS braces and all.
            raise ValueError(
                f"{source.name}: {language!r}.give_up must not carry a format "
                "slot of any kind. It is the terminal line every composition "
                "failure falls back to, spoken verbatim, so it must always be "
                "speakable with nothing to fill."
            )
        by_language[language] = copy
    return ConfirmationCopy(by_language=by_language)


class EchoNotInTranscript(ValueError):
    """The slot value did not come from what the buyer said.

    Raised rather than spoken: this path skips the guardrail pipeline, and
    the substring check is the whole reason that is safe. The caller must
    fail closed (hand over), never fall through to the model.
    """


class UnspeakableConfirmation(RuntimeError):
    """The template itself could not be filled - a bad slot name like
    `{ammount}`, or copy that filled to nothing. A defect in the data file,
    not in the buyer's reply; the caller must fail closed (hand over)."""


def compose(template: str, *, echoed: str, said: str) -> str:
    """Fill the one slot, refusing anything the buyer did not actually say.

    `said` must be the digit-normalised utterance the mention was EXTRACTED
    FROM (`BudgetMention.utterance`), not the current turn's transcript - a
    re-ask happens precisely because the current turn did not repeat the
    number, so checking against it can only fail.
    """
    if not echoed or echoed not in said:
        raise EchoNotInTranscript(
            f"refusing to speak {echoed!r}: it is not a literal part of the "
            "buyer's utterance, and this copy bypasses the guardrail pipeline "
            "precisely because it only ever echoes the transcript."
        )
    try:
        text = template.format(amount=echoed)
    except Exception as exc:
        # Deliberately every exception, converted to one the caller fails
        # CLOSED on. str.format raises more than the obvious ValueError and
        # KeyError: "{amount.foo}" is an AttributeError and "{amount[x]}" a
        # TypeError, and the first version of this catch let both escape the
        # voice path entirely - a silent turn, then a fall-through to the
        # model on the same-turn retry.
        raise UnspeakableConfirmation(
            f"confirmation template {template!r} could not be filled: {exc}"
        ) from exc
    if not text.strip():
        raise UnspeakableConfirmation(
            "confirmation template composed to nothing; an empty yield would "
            "be a silent turn."
        )
    return text
