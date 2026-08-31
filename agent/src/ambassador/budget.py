"""Buyer-side budget detection and currency policy (ADR-011, docs/04-).

The failure this exists to prevent: a buyer says "do crore ka budget hai" and
the system guesses which currency. INR 2 crore is roughly AED 880k; AED 2
crore is 20 million. Guess wrong and the agent recommends a property off by up
to twenty times, in a warm confident voice, having done nothing a prompt
instruction would have caught.

ADR-007 is the reason this is code: prompt instructions reduce violation rates
without eliminating them, and until now this policy existed only as prompt
constraint 8.

## Why it only became possible now

Detection needs digits in the transcript. The previous recogniser returned
figures as words ("two million"), which `extract_figures` cannot read at all.
Deepgram with `numerals=True` returns "2000000" and "2 crore" (ADR-017), so
there is finally something deterministic to parse. If the recogniser is ever
swapped for one that spells numbers out, this module silently stops seeing
budgets - hence `test_detection_needs_digits_and_says_so`.

## Conversion is refused, not approximated

`data/currencies.yaml` ships no exchange rate and `confirmed: false`. A
made-up rate spoken to a buyer is the same class of error as a made-up price -
a specific, checkable, wrong number said with confidence - so while the rate is
unconfirmed the policy converts nothing. It confirms the currency and routes a
non-AED budget to a human. That is the honest behaviour and it is worth
demonstrating; an operator switches conversion on by setting a real rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .figures import extract_figures, normalise_digits

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

Currency = Literal["AED", "INR"]

# How far either side of a figure a currency word still binds to it. Wide
# enough for "2 crore in rupees" and "rupees 2 crore", tight enough that the
# currency named in the previous clause does not attach to this number.
_BIND_WINDOW = 24

# Multiplier words that make a bare number a budget on their own: nobody says
# "two crore" about a bedroom count. Kept in step with figures._MULTIPLIERS,
# minus the ones too small to imply money.
_BUDGET_UNITS = re.compile(
    r"\b(thousand|million|lakh|lacs|lac|crores|crore|k|m)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ConversionRate:
    inr_per_aed: float | None
    as_of: str | None
    confirmed: bool

    @property
    def usable(self) -> bool:
        """A rate may only drive a spoken figure when someone has vouched for
        it AND it exists. Both, because a confirmed null is a configuration
        mistake, not permission."""
        return self.confirmed and bool(self.inr_per_aed)


@dataclass(frozen=True)
class CurrencyVocabulary:
    """Per language, because a buyer names the currency in the language they
    are speaking. Symbols are language-neutral."""

    words: dict[str, dict[Currency, tuple[str, ...]]]
    symbols: dict[Currency, tuple[str, ...]]
    budget_keywords: dict[str, tuple[str, ...]]
    rate: ConversionRate

    def languages_covered(self) -> frozenset[str]:
        """Languages whose buyer can actually name a currency and be heard."""
        return frozenset(
            language
            for language, by_currency in self.words.items()
            if any(by_currency.values())
        )


@dataclass(frozen=True)
class BudgetMention:
    """One budget figure the buyer stated, and what is unknown about it."""

    surface: str
    value: float
    currency: Currency | None
    # True when the buyer used lakh or crore. Not the same as "currency
    # unstated": those units are overwhelmingly Indian, which makes an
    # unqualified "two crore" MORE likely to be misread as dirhams, not less.
    subcontinental_unit: bool

    @property
    def needs_currency(self) -> bool:
        return self.currency is None


def load_currency_vocabulary(path: Path | None = None) -> CurrencyVocabulary:
    source = path or _DATA_DIR / "currencies.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping, got {type(raw).__name__}."
        )

    words: dict[str, dict[Currency, tuple[str, ...]]] = {}
    for language, by_currency in (raw.get("words") or {}).items():
        words[language] = {
            currency: tuple(
                str(w).lower() for w in (by_currency or {}).get(currency) or []
            )
            for currency in ("AED", "INR")
        }

    symbols = {
        currency: tuple(str(s) for s in (raw.get("symbols") or {}).get(currency) or [])
        for currency in ("AED", "INR")
    }
    keywords = {
        language: tuple(str(k).lower() for k in values or [])
        for language, values in (raw.get("budget_keywords") or {}).items()
    }

    rate_block = raw.get("rate") or {}
    rate = ConversionRate(
        inr_per_aed=rate_block.get("inr_per_aed"),
        as_of=rate_block.get("as_of"),
        confirmed=bool(rate_block.get("confirmed")),
    )
    return CurrencyVocabulary(
        words=words, symbols=symbols, budget_keywords=keywords, rate=rate
    )


def _currency_near(
    text: str, start: int, end: int, vocabulary: CurrencyVocabulary, language: str
) -> Currency | None:
    """The currency named beside this figure, if any.

    Nearest wins, so "rupees, not dirhams, 2 crore" binds to whichever the
    buyer actually put next to the number rather than to whichever the table
    lists first.
    """
    window = text[max(0, start - _BIND_WINDOW) : end + _BIND_WINDOW].lower()
    by_currency = vocabulary.words.get(language, {})
    best: tuple[int, Currency] | None = None
    for currency in ("AED", "INR"):
        candidates = list(by_currency.get(currency, ())) + [
            s.lower() for s in vocabulary.symbols.get(currency, ())
        ]
        for token in candidates:
            if not token:
                continue
            pattern = (
                rf"(?<!\w){re.escape(token)}(?!\w)"
                if token.isalnum()
                else re.escape(token)
            )
            for found in re.finditer(pattern, window):
                # Distance from the figure, which sits at _BIND_WINDOW in the
                # window unless it was clipped at the start of the text.
                offset = min(start, _BIND_WINDOW)
                distance = min(
                    abs(found.start() - offset),
                    abs(found.start() - (offset + end - start)),
                )
                if best is None or distance < best[0]:
                    best = (distance, currency)  # type: ignore[assignment]
    return None if best is None else best[1]


def find_budget(
    utterance: str, vocabulary: CurrencyVocabulary, language: str
) -> BudgetMention | None:
    """The first budget figure in a buyer utterance, or None.

    "Budget-like" means one of three things, because a bare number is usually
    not money: it carries a currency, it carries a lakh/crore/million-style
    multiplier, or a budget keyword sits in the utterance. Without that test
    "three bedrooms" is a budget mention and every turn triggers a
    confirmation.
    """
    text = normalise_digits(utterance)
    lowered = text.lower()
    keywords = vocabulary.budget_keywords.get(language, ())
    keyword_present = any(k and k in lowered for k in keywords)

    for match in sorted(extract_figures(text), key=lambda m: m.start):
        if match.figure.kind != "amount":
            continue
        currency = _currency_near(text, match.start, match.end, vocabulary, language)
        unit = _BUDGET_UNITS.search(match.figure.surface) or _BUDGET_UNITS.search(
            text[match.end : match.end + 12]
        )
        if currency is None and unit is None and not keyword_present:
            continue
        return BudgetMention(
            surface=match.figure.surface,
            value=match.figure.value,
            currency=currency,
            subcontinental_unit=bool(
                unit and unit.group(0).lower().startswith(("lakh", "lac", "crore"))
            ),
        )
    return None


def to_aed(amount: float, currency: Currency, rate: ConversionRate) -> float:
    """Convert to dirhams, or refuse.

    Refusing is the point. The alternative is speaking a figure derived from a
    number nobody has vouched for, which is exactly what the numeric guardrail
    exists to stop the model doing.
    """
    if currency == "AED":
        return amount
    if not rate.usable:
        raise ConversionUnavailable(
            "no confirmed INR/AED rate in data/currencies.yaml, so this budget "
            "cannot be converted. Confirm the currency with the buyer and route "
            "to a human rather than quoting a figure derived from a guess."
        )
    assert rate.inr_per_aed  # narrowed by `usable`
    return amount / rate.inr_per_aed


class ConversionUnavailable(RuntimeError):
    """Raised when a conversion is asked for and no confirmed rate exists."""


# --- the policy state machine ---------------------------------------------
#
# Pure and framework-free: it takes utterances and returns what should happen,
# and the adapter is what actually speaks. Keeping it here means the twenty-
# times error is testable without a room, a socket or a vendor.

Action = Literal[
    "none", "ask_currency", "confirm_amount", "cannot_convert", "give_up"
]

# Three tries, then hand over. A voice bot that makes the buyer repeat
# themselves a fourth time earns lasting resentment (docs/04-).
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Decision:
    action: Action
    mention: BudgetMention | None = None
    # Set once the currency is settled, so the caller can act on the budget.
    currency: Currency | None = None

    @property
    def speaks(self) -> bool:
        return self.action != "none"

    @property
    def hands_over(self) -> bool:
        """Both terminal actions route to a human, and the caller must treat
        them the same way even though the copy differs."""
        return self.action in ("cannot_convert", "give_up")


class BudgetPolicy:
    """One buyer's budget, from first mention to settled currency.

    Deliberately a small state machine rather than a prompt instruction: the
    model never gets the chance to skip the question, because the adapter
    speaks the confirmation INSTEAD of running a turn.
    """

    def __init__(self, vocabulary: CurrencyVocabulary, language: str) -> None:
        self._vocabulary = vocabulary
        self._language = language
        self._mention: BudgetMention | None = None
        self._currency: Currency | None = None
        self._awaiting = False
        self._attempts = 0
        self._settled = False

    @property
    def settled(self) -> bool:
        """True once the budget needs nothing further - confirmed, or given up
        on. A settled policy never speaks again."""
        return self._settled

    @property
    def currency(self) -> Currency | None:
        return self._currency

    def observe(self, utterance: str) -> Decision:
        if self._settled:
            return Decision("none")
        if self._awaiting:
            return self._answer(utterance)
        return self._first_mention(utterance)

    def _first_mention(self, utterance: str) -> Decision:
        mention = find_budget(utterance, self._vocabulary, self._language)
        if mention is None:
            return Decision("none")
        self._mention = mention
        self._awaiting = True
        if mention.needs_currency:
            return Decision("ask_currency", mention)
        # ADR-011 confirms the FIRST mention even when the currency was named:
        # a misheard "two" for "ten" costs as much as a misread currency.
        return Decision("confirm_amount", mention)

    def _answer(self, utterance: str) -> Decision:
        """Read the buyer's reply to the confirmation.

        A currency named anywhere in the reply settles it - the buyer answers
        "dirhams", not in a full sentence - and a reply that names none counts
        as a failed attempt rather than as consent.
        """
        assert self._mention is not None
        currency = _currency_near(
            normalise_digits(utterance), 0, len(utterance), self._vocabulary,
            self._language,
        )
        if currency is None and not self._mention.needs_currency:
            # The question was "have I got that right", not "which currency".
            # Anything that is not a fresh contradiction settles it.
            currency = self._mention.currency

        if currency is None:
            self._attempts += 1
            if self._attempts >= _MAX_ATTEMPTS:
                return self._settle(Decision("give_up"))
            return Decision("ask_currency", self._mention)

        self._currency = currency
        if currency != "AED" and not self._vocabulary.rate.usable:
            # Honest refusal rather than a converted figure nobody vouched for.
            return self._settle(Decision("cannot_convert", self._mention, currency))
        return self._settle(Decision("none", self._mention, currency))

    def _settle(self, decision: Decision) -> Decision:
        self._settled = True
        self._awaiting = False
        return decision
