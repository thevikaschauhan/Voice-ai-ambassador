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
budgets. Two tests pin the dependency from both ends:
`test_detection_needs_digits_and_says_so` pins that word-form figures are
invisible to `find_budget`, and `test_deepgram_is_built_with_numerals_on`
pins that the STT factory actually requests digits.

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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from .figures import Numerals, default_numerals, extract_figures, normalise_digits

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

Currency = Literal["AED", "INR"]

# The two sides of the Literal, spelled out once. `_other_currency` leans on
# this being exactly two: denying one names the other.
_CURRENCIES: tuple[Currency, Currency] = ("AED", "INR")

# How far either side of a figure a currency word still binds to it. Wide
# enough for "2 crore in rupees" and "rupees 2 crore", tight enough that the
# currency named in the previous clause does not attach to this number.
_BIND_WINDOW = 24

# How far a budget keyword may sit from the figure it marks. Keywords lead in
# speech ("my budget is X", "I want to spend X"), so the window behind the
# figure is wider than the one ahead ("900,000 is my budget"). Whole-utterance
# matching was a shipped defect: with "around" in the list, "I'm around floor
# 15" read as a budget of 15.
_KEYWORD_BEFORE = 30
_KEYWORD_AFTER = 15

# How close a negator must sit in front of a currency word to negate it:
# "not dirhams", "not in dirhams". Punctuation in the gap breaks the bind,
# because "no, dirhams" is an answer that AFFIRMS dirhams while contradicting
# something else, and "not dirhams" is a denial of them.
_NEGATION_GAP = 6
_PUNCTUATION = re.compile(r"[,.;:!?]")

# A currency or keyword never reaches across one of these to a figure in
# another clause: "my budget is 2 crore; I have AED 500,000 saved" must not
# read the deposit's AED onto the crore. Commas and dashes stay bindable -
# "2 crore, in rupees" is one clause.
_CLAUSE_BREAK = re.compile(r"[;.!?]")

# A multiplier this size makes a bare number a budget on its own: nobody says
# "two crore" about a bedroom count, and nobody says "three hundred" about a
# price. Thousand and up.
_MONEY_UNIT_FACTOR = 1000


def budget_unit_pattern(numerals: Numerals) -> re.Pattern[str]:
    """The money-sized multipliers, matched against a FIGURE'S OWN SURFACE.

    Derived from the same table the extractor uses, not hand-kept beside it.
    The list here used to be a literal "kept in step with data/numerals.yaml",
    and two of its ten tokens could never match anything: `extract_figures`
    folds an adjacent multiplier INTO the surface, so a k/m budget arrives as
    "800k", and `\bk\b` cannot match there because there is no word boundary
    between "0" and "k". A k/m budget was therefore only ever detected when a
    budget keyword happened to sit nearby - "around 800k" returned nothing.
    That is the regex-that-looks-live-and-never-fires trap, and deriving the
    pattern is what stops a multiplier added to the data file being silently
    unreachable here.

    Hence the boundary: a unit may sit directly against the digits ("800k") or
    stand as its own word ("3 million").

    Longest alternatives first so the match is deterministic rather than
    dependent on the data file's key order. It is NOT load-bearing for
    correctness - "3 lakhs" matches either way, because a shorter alternative
    that fails its own trailing boundary backtracks into the longer one - and
    the earlier version of this comment claimed otherwise. A mutation that
    removed the sort left the suite green, which is how the claim was caught.
    """
    words = sorted(
        (
            word
            for word, factor in numerals.multipliers.items()
            if factor >= _MONEY_UNIT_FACTOR
        ),
        key=len,
        reverse=True,
    )
    if not words:
        raise ValueError(
            "no money-sized multipliers in the numeral table, so no figure can "
            "ever read as a budget on its unit alone. Check data/numerals.yaml."
        )
    alternatives = "|".join(re.escape(word) for word in words)
    return re.compile(rf"(?:(?<=\d)|\b)({alternatives})\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def _budget_units() -> re.Pattern[str]:
    """The shipped pattern, compiled once. `budget_unit_pattern` is the part
    that decides anything and takes its vocabulary explicitly, the same split
    `figures.py` uses."""
    return budget_unit_pattern(default_numerals())


# Attribution, ownership and dimensions are bound by CLAUSE, not by character
# distance. The first attempt ran a distance contest - a source word within 14
# characters, a budget keyword within 30 - and a natural modifier defeats it:
# "My budget after checking your website is AED 750,000" puts "website" 8
# characters from the figure and "budget" 36, so the source word won and a
# stated budget went unconfirmed. Who owns a figure is not a question about
# spacing, and both numbers are gone.


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
    # "not", "don't": a currency these precede is being denied, not named.
    negators: dict[str, tuple[str, ...]]
    # "no", "wrong": the buyer is contradicting what was read back to them.
    contradictions: dict[str, tuple[str, ...]]
    # "yes", "correct": explicit agreement with a read-back. Consent is never
    # inferred from the absence of an objection - "can you repeat that?" is
    # not a yes.
    affirmations: dict[str, tuple[str, ...]]
    # "the listing says", "priced at": the figures after these in the same
    # clause came from somewhere else, or are prices rather than budgets.
    attributions: dict[str, tuple[str, ...]]
    # "my budget", "I can spend", "enough for me": the buyer saying a figure is
    # THEIRS. Outranks attribution in the same clause, at any distance.
    ownership: dict[str, tuple[str, ...]]
    # "wide", "ceiling", "balcony": the figure measures something. "m" is a
    # money multiplier in the numeral table and the metre abbreviation in this
    # domain, and a folded unit is enough to make a figure budget-like.
    dimensions: dict[str, tuple[str, ...]]
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
    # The digit-normalised utterance the mention was extracted from. The
    # confirmation's echo is validated against THIS text, never against a
    # later turn's transcript: a re-ask happens precisely because the buyer's
    # reply did not repeat the number, so checking the echo against the reply
    # guaranteed failure and (before it was caught in review) silently
    # disabled the policy on the exact path it existed for.
    utterance: str

    @property
    def needs_currency(self) -> bool:
        return self.currency is None


def _word_lists(raw: Any, key: str) -> dict[str, tuple[str, ...]]:
    lists: dict[str, tuple[str, ...]] = {}
    for language, values in (raw.get(key) or {}).items():
        words: list[str] = []
        for word in values or []:
            if not isinstance(word, str):
                # YAML 1.1 reads bare no/yes/on/off as booleans. Coercing the
                # boolean to text would leave the word silently unmatchable -
                # "no" is exactly the kind of word these lists hold.
                raise ValueError(
                    f"currencies.yaml: {key}.{language} contains {word!r}, "
                    "not text. Quote the word in the data file."
                )
            words.append(word.lower())
        lists[language] = tuple(words)
    return lists


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
            for currency in _CURRENCIES
        }

    symbols = {
        currency: tuple(str(s) for s in (raw.get("symbols") or {}).get(currency) or [])
        for currency in _CURRENCIES
    }

    rate_block = raw.get("rate") or {}
    rate = ConversionRate(
        inr_per_aed=rate_block.get("inr_per_aed"),
        as_of=rate_block.get("as_of"),
        confirmed=bool(rate_block.get("confirmed")),
    )
    return CurrencyVocabulary(
        words=words,
        symbols=symbols,
        budget_keywords=_word_lists(raw, "budget_keywords"),
        negators=_word_lists(raw, "negators"),
        contradictions=_word_lists(raw, "contradictions"),
        affirmations=_word_lists(raw, "affirmations"),
        attributions=_word_lists(raw, "attributions"),
        ownership=_word_lists(raw, "ownership"),
        dimensions=_word_lists(raw, "dimensions"),
        rate=rate,
    )


# --- reading currency words out of a transcript -----------------------------


@dataclass(frozen=True)
class _CurrencyHit:
    currency: Currency
    start: int
    end: int
    negated: bool


def _token_pattern(token: str) -> str:
    return (
        rf"(?<!\w){re.escape(token)}(?!\w)" if token.isalnum() else re.escape(token)
    )


def _negator_spans(lowered: str, vocabulary: CurrencyVocabulary, language: str):
    spans: list[tuple[int, int]] = []
    for token in vocabulary.negators.get(language, ()):
        if not token:
            continue
        for found in re.finditer(_token_pattern(token), lowered):
            spans.append((found.start(), found.end()))
    return spans


def _currency_hits(
    lowered: str, vocabulary: CurrencyVocabulary, language: str
) -> list[_CurrencyHit]:
    """Every currency word or symbol in the text, with whether it is negated.

    A currency word is negated when a negator ends just before it with no
    punctuation in between: "not dirhams" and "not in dirhams" deny dirhams,
    "no, dirhams" does not - the comma makes "no" a contradiction of something
    else and "dirhams" the answer.
    """
    negators = _negator_spans(lowered, vocabulary, language)
    by_currency = vocabulary.words.get(language, {})
    hits: list[_CurrencyHit] = []
    for currency in _CURRENCIES:
        candidates = list(by_currency.get(currency, ())) + [
            s.lower() for s in vocabulary.symbols.get(currency, ())
        ]
        for token in candidates:
            if not token:
                continue
            for found in re.finditer(_token_pattern(token), lowered):
                negated = any(
                    0 <= found.start() - neg_end <= _NEGATION_GAP
                    and not _PUNCTUATION.search(lowered[neg_end : found.start()])
                    for _, neg_end in negators
                )
                hits.append(
                    _CurrencyHit(currency, found.start(), found.end(), negated)
                )
    return hits


@dataclass(frozen=True)
class ReplyReading:
    """What a reply to a confirmation actually said about it.

    `affirmed` are currencies named without a negator in front; `denied` are
    currencies every occurrence of which was negated; `contradicted` means the
    buyer pushed back on what was read to them - a contradiction word, or a
    negator that is not denying a currency ("no", "that's not right", "I'm
    not sure"). Uncertainty counts as contradiction on purpose: the one thing
    a doubted read-back must never be treated as is consent. `agreed` means an
    explicit agreement word was said; consent is never inferred from silence.
    """

    affirmed: tuple[Currency, ...]
    denied: tuple[Currency, ...]
    contradicted: bool
    agreed: bool


def read_reply(
    utterance: str, vocabulary: CurrencyVocabulary, language: str
) -> ReplyReading:
    lowered = normalise_digits(utterance).lower()
    hits = _currency_hits(lowered, vocabulary, language)

    affirmed = tuple(
        c for c in _CURRENCIES if any(h.currency == c and not h.negated for h in hits)
    )
    denied = tuple(
        c
        for c in _CURRENCIES
        if c not in affirmed and any(h.currency == c and h.negated for h in hits)
    )

    # A negator that is busy denying a currency is not a contradiction; one
    # that is not ("I'm not sure") is. The check is by span: which currency
    # hits sit within the gap of this negator.
    contradicted = False
    for neg_start, neg_end in _negator_spans(lowered, vocabulary, language):
        binds = any(
            h.negated and 0 <= h.start - neg_end <= _NEGATION_GAP for h in hits
        )
        if not binds:
            contradicted = True
            break
    if not contradicted:
        for token in vocabulary.contradictions.get(language, ()):
            if not token:
                continue
            for found in re.finditer(_token_pattern(token), lowered):
                binds = any(
                    h.negated
                    and 0 <= h.start - found.end() <= _NEGATION_GAP
                    and not _PUNCTUATION.search(lowered[found.end() : h.start])
                    for h in hits
                )
                if not binds:
                    contradicted = True
                    break
            if contradicted:
                break

    agreed = any(
        token and re.search(_token_pattern(token), lowered)
        for token in vocabulary.affirmations.get(language, ())
    )
    return ReplyReading(
        affirmed=affirmed, denied=denied, contradicted=contradicted, agreed=agreed
    )


def _other_currency(currency: Currency) -> Currency:
    return "INR" if currency == "AED" else "AED"


# --- finding the budget in an utterance --------------------------------------


def find_budget(
    utterance: str, vocabulary: CurrencyVocabulary, language: str
) -> BudgetMention | None:
    """The budget figure in a buyer utterance, or None.

    "Budget-like" means one of three things, because a bare number is usually
    not money: it carries a currency, it carries a lakh/crore/million-style
    multiplier IN ITS OWN SURFACE, or a budget keyword belongs to it. The unit
    must come from the figure's own surface because `extract_figures` already
    folds an adjacent multiplier in - a unit found by looking further ahead
    belongs to the NEXT figure, which is how "Floor 15, 2 million" once read
    as a budget of 15.

    Currency words and budget keywords are OWNED by their nearest figure, and
    never reach across a clause break. Both rules exist because window-only
    matching shipped defects: "my budget is 2 crore; I have AED 500,000 saved"
    read the deposit's AED onto the crore, and in "the price is AED 985,000
    and my budget is AED 2,000,000" the word "budget" fell inside the price's
    window too and the price won.

    When several figures qualify, the one owning a budget keyword wins over
    the ones that merely carry a currency or a unit: in "the villa is 5
    million dirhams but I only want to spend 2 crore rupees", the budget is
    what the buyer wants to spend, not the price they are quoting back.
    """
    text = normalise_digits(utterance)
    lowered = text.lower()
    candidates = [
        m
        for m in sorted(extract_figures(text), key=lambda m: m.start)
        if m.figure.kind == "amount"
    ]
    if not candidates:
        return None
    spans = [(m.start, m.end) for m in candidates]

    def owner(
        start: int, end: int, *, prefer_following: bool = False
    ) -> tuple[int, int]:
        """(index, gap) of the figure this token belongs to.

        `prefer_following` breaks exact ties toward the figure the token
        precedes: budget keywords lead their figure in speech ("my budget is
        X"), and in "the price is AED 985,000 and my budget is AED 2,000,000"
        the word "budget" sits at the same gap from both figures - the tie
        must go to the buyer's number, not the quoted price.
        """
        best_i, best_d, best_follows = 0, -1, False
        for i, (s, e) in enumerate(spans):
            if end <= s:
                gap, follows = s - end, True
            elif start >= e:
                gap, follows = start - e, False
            else:
                gap, follows = 0, False
            if (
                best_d < 0
                or gap < best_d
                or (gap == best_d and prefer_following and follows and not best_follows)
            ):
                best_i, best_d, best_follows = i, gap, follows
        return best_i, best_d

    def crosses_clause(start: int, end: int, i: int) -> bool:
        s, e = spans[i]
        return bool(_CLAUSE_BREAK.search(lowered[min(end, e) : max(start, s)]))

    currency_of: dict[int, tuple[int, Currency]] = {}
    for hit in _currency_hits(lowered, vocabulary, language):
        if hit.negated:
            continue
        i, distance = owner(hit.start, hit.end)
        if distance > _BIND_WINDOW or crosses_clause(hit.start, hit.end, i):
            continue
        held = currency_of.get(i)
        if held is None or distance < held[0]:
            currency_of[i] = (distance, hit.currency)

    marked: set[int] = set()
    for keyword in vocabulary.budget_keywords.get(language, ()):
        if not keyword:
            continue
        for found in re.finditer(_token_pattern(keyword), lowered):
            i, distance = owner(found.start(), found.end(), prefer_following=True)
            # Keywords lead in speech ("my budget is X"), so the reach behind
            # a figure is wider than the reach ahead of it.
            reach = _KEYWORD_BEFORE if found.end() <= spans[i][0] else _KEYWORD_AFTER
            if distance <= reach and not crosses_clause(found.start(), found.end(), i):
                marked.add(i)

    def in_clause(start: int, end: int, i: int) -> bool:
        return not crosses_clause(start, end, i)

    def bound(
        words: tuple[str, ...], *, whole_clause: bool = False, leads: bool = False
    ) -> set[int]:
        """The figures these words claim.

        `whole_clause` claims every figure AFTER the word in its clause rather
        than only the nearest one, which is what a quoted range needs: "The
        listing says AED 750,000 or AED 800,000" is quoting both prices, and
        binding one token to one figure let the second walk straight past.

        `leads` claims the figure the word INTRODUCES - the nearest one that
        follows it in the clause, falling back to a preceding figure only when
        none does. Nearest-with-a-tie-break is not enough for a phrase: the
        single word "budget" sits at an exact tie between the two figures of
        "The price is AED 985,000 and my budget is AED 2,000,000" and the
        tie-break sends it forwards, but the PHRASE "my budget" starts four
        characters earlier and reaches backwards to the quoted price instead.
        A phrase that names an owner names what comes after it.
        """
        claimed: set[int] = set()
        for word in words:
            if not word:
                continue
            for found in re.finditer(_token_pattern(word), lowered):
                if whole_clause:
                    claimed.update(
                        i
                        for i, (figure_start, _) in enumerate(spans)
                        if figure_start >= found.end()
                        and in_clause(found.start(), found.end(), i)
                    )
                    continue
                if leads:
                    following = [
                        i
                        for i, (figure_start, _) in enumerate(spans)
                        if figure_start >= found.end()
                        and in_clause(found.start(), found.end(), i)
                    ]
                    if following:
                        claimed.add(following[0])
                        continue
                i, _ = owner(found.start(), found.end(), prefer_following=True)
                if in_clause(found.start(), found.end(), i):
                    claimed.add(i)
        return claimed

    # Quoted, not offered: attributed to a listing, to our own website, or to
    # us on an earlier call - or framed as a price rather than as what the
    # buyer will spend. Forward-only, so a source word cannot reach back over a
    # figure the buyer stated before mentioning any source.
    attributed = bound(vocabulary.attributions.get(language, ()), whole_clause=True)
    # The buyer claiming a figure as their own. Overrides both withholding
    # rules, at any distance inside the clause.
    owned = bound(vocabulary.ownership.get(language, ()), leads=True)
    # A measurement rather than a sum.
    measured = bound(vocabulary.dimensions.get(language, ()))

    fallback: BudgetMention | None = None
    for i, match in enumerate(candidates):
        held = currency_of.get(i)
        currency = None if held is None else held[1]
        unit = _budget_units().search(match.figure.surface)
        if (i in attributed or i in measured) and i not in owned:
            # Withheld: quoted from somewhere else, or measuring something.
            #
            # Only OWNERSHIP overrides this, never a plain budget keyword, and
            # the difference is a quoted range: "up to" is a budget keyword and
            # it appears in "Prices start at AED 750,000 and go up to AED
            # 900,000", where it belongs to the range rather than to the buyer.
            # A generic keyword says which figure is the budget once we know
            # one of them is; only the buyer saying "my budget" or "I can
            # spend" says that a quoted figure is theirs after all - and
            # withholding a real budget is the expensive direction, so that
            # list is generous.
            continue
        if currency is None and unit is None and i not in marked:
            continue
        mention = BudgetMention(
            surface=match.figure.surface,
            value=match.figure.value,
            currency=currency,
            subcontinental_unit=bool(
                unit and unit.group(0).lower().startswith(("lakh", "lac", "crore"))
            ),
            utterance=text,
        )
        if i in marked:
            return mention
        if fallback is None:
            fallback = mention
    return fallback


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
    "none", "ask_currency", "confirm_amount", "ask_amount", "cannot_convert", "give_up"
]

# The questions the policy can be waiting on an answer to.
_Question = Literal["ask_currency", "confirm_amount", "ask_amount"]

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
        """Both terminal actions route to a human. The caller must actually
        notify one - the same escalation the escalate_to_human tool performs -
        not merely speak the copy; saying "let me put you through" with nobody
        notified is the exact anti-pattern that tool's docstring names."""
        return self.action in ("cannot_convert", "give_up")


class BudgetPolicy:
    """One buyer's budget, from first mention to settled currency.

    Deliberately a small state machine rather than a prompt instruction: the
    model never gets the chance to skip the question, because the adapter
    speaks the confirmation INSTEAD of running a turn.

    While a question is open, every reply is read for five things, in order:
    a restated budget (which replaces the stale mention and restarts the
    confirmation - "sorry, I meant 5 million dirhams" is about 5 million, not
    about whatever was misheard first), a contradiction ("no", "that's
    wrong", "I'm not sure") - read BEFORE any currency in the same reply, so
    "no, dirhams" rejects the read-back rather than settling it - then a
    currency named without negation, a currency denied ("not dirhams" names
    the other one, because there are exactly two), and finally explicit
    agreement ("yes", "correct") to a read-back. A reply carrying none of
    those is a failed attempt, never consent: "can you repeat that?" is not
    a yes, and three of them hand the buyer to a human.
    """

    def __init__(self, vocabulary: CurrencyVocabulary, language: str) -> None:
        self._vocabulary = vocabulary
        self._language = language
        self._mention: BudgetMention | None = None
        self._currency: Currency | None = None
        self._asked: _Question | None = None
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

    @property
    def pending(self) -> Decision | None:
        """The question this policy is waiting on an answer to, if any.

        Read-only and non-mutating, and that is the whole point: the caller
        re-speaks it on a turn nobody could hear (recognition.py), and a turn
        nobody could hear must not consume one of the buyer's three attempts.
        Routing such a turn through `observe` instead would count it as a
        reply that answered nothing, which is exactly what it was not.
        """
        if self._asked is None:
            return None
        return Decision(self._asked, self._mention)

    def answers(self, utterance: str) -> bool:
        """Does this reply say anything about the question that is open?

        Pure and non-mutating. The coordinator asks before letting a policy
        read a turn, because a reply that says nothing about THIS question may
        well be an answer to another one - and reading it here would spend an
        attempt the buyer never used. See ambassador/confirmation.py.

        A reply counts when it restates a budget, names or denies a currency,
        pushes back, or agrees. Deliberately the same five signals `_answer`
        acts on, and no others: a predicate that is more generous than the
        reader it guards would hand the reader turns it has nothing to do with.
        """
        if self._settled or self._asked is None:
            return False
        said = normalise_digits(utterance)
        if find_budget(said, self._vocabulary, self._language) is not None:
            return True
        reading = read_reply(said, self._vocabulary, self._language)
        return bool(
            reading.affirmed or reading.denied or reading.contradicted or reading.agreed
        )

    def observe(self, utterance: str) -> Decision:
        if self._settled:
            return Decision("none")
        said = normalise_digits(utterance)
        if self._asked is not None:
            return self._answer(said)
        return self._first_mention(said)

    def abandon(self) -> None:
        """Give up on the policy without consent: the caller could not speak
        the confirmation (broken copy, refused echo) and has routed the buyer
        to a human instead. Settling here is what makes that path fail CLOSED:
        the alternative - leaving the policy open and letting the model take
        the turn - is the fail-open defect this rework removed."""
        self._settled = True
        self._asked = None

    def _first_mention(self, said: str) -> Decision:
        mention = find_budget(said, self._vocabulary, self._language)
        if mention is None:
            return Decision("none")
        return self._confirm(mention)

    def _confirm(self, mention: BudgetMention) -> Decision:
        """Open (or reopen) the confirmation for this mention.

        ADR-011 confirms the FIRST mention even when the currency was named:
        a misheard "two" for "ten" costs as much as a misread currency.
        """
        self._mention = mention
        if mention.needs_currency:
            self._asked = "ask_currency"
            return Decision("ask_currency", mention)
        self._asked = "confirm_amount"
        return Decision("confirm_amount", mention)

    def _answer(self, said: str) -> Decision:
        assert self._mention is not None and self._asked is not None
        fresh = find_budget(said, self._vocabulary, self._language)

        if self._asked == "ask_amount":
            # "What is the budget, then?" - any figure in the reply is the
            # answer, and it starts a fresh confirmation.
            if fresh is not None:
                self._attempts = 0
                return self._confirm(fresh)
            return self._failed_attempt()

        if fresh is not None and fresh.value != self._mention.value:
            # A restated budget replaces the stale one. Settling the old
            # amount against a reply that corrected it was a shipped defect.
            self._attempts = 0
            return self._confirm(fresh)

        reading = read_reply(said, self._vocabulary, self._language)
        if reading.contradicted:
            # Contradiction is read FIRST, before any currency in the same
            # reply. "No, dirhams" and "I'm not sure about rupees" name a
            # currency grammatically while rejecting or doubting the
            # read-back, and letting the currency win recorded rejection as
            # consent - a shipped defect twice over. A rejected read-back
            # reopens the amount; a doubted currency question is asked again.
            if self._asked == "confirm_amount":
                return self._failed_attempt(reopen="ask_amount")
            return self._failed_attempt()

        if len(reading.affirmed) == 1:
            return self._settle_currency(reading.affirmed[0])
        if not reading.affirmed and len(reading.denied) == 1:
            # Two currencies exist, so denying one names the other.
            return self._settle_currency(_other_currency(reading.denied[0]))

        if self._asked == "confirm_amount" and reading.agreed:
            # Consent must be said, never inferred: "can you repeat that?"
            # carries no signal at all and used to settle as agreement.
            assert self._mention.currency is not None
            return self._settle_currency(self._mention.currency)

        return self._failed_attempt()

    def _failed_attempt(self, reopen: _Question | None = None) -> Decision:
        self._attempts += 1
        if self._attempts >= _MAX_ATTEMPTS:
            return self._settle(Decision("give_up"))
        if reopen is not None:
            self._asked = reopen
        assert self._asked is not None
        return Decision(self._asked, self._mention)

    def _settle_currency(self, currency: Currency) -> Decision:
        self._currency = currency
        if currency != "AED" and not self._vocabulary.rate.usable:
            # Honest refusal rather than a converted figure nobody vouched for.
            return self._settle(Decision("cannot_convert", self._mention, currency))
        return self._settle(Decision("none", self._mention, currency))

    def _settle(self, decision: Decision) -> Decision:
        self._settled = True
        self._asked = None
        return decision
