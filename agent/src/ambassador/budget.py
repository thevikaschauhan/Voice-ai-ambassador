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
from typing import Any, Final, Literal

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


# Units that are money in the numeral table and something else in ordinary
# property speech. "m" is millions and metres, and a folded "2m" is both "two
# million" and "two metres". A dimension word therefore withholds THIS unit and
# no other: suppressing every amount near a room was a regression, because "I
# have AED 800,000 for a room" is a budget and a room does not make a currency
# ambiguous.
_AMBIGUOUS_UNITS = frozenset({"m"})


def _ambiguous_unit(surface: str) -> bool:
    """Does this figure's own surface carry a unit that is money in the numeral
    table and a measurement in ordinary property speech?

    Only such a surface can be withheld by a dimension word (G4). A
    plain-currency figure is unambiguous whatever else the segment mentions:
    "I have AED 800,000 for a room" is a budget, and a room does not make a
    currency ambiguous.
    """
    found = _budget_units().search(surface)
    return bool(found) and found.group(1).lower() in _AMBIGUOUS_UNITS


# Attribution, ownership and dimensions are decided by SEGMENT, not by character
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
    # Whose figure is it (issue #25, god's round-three design). Closed lists,
    # fixed precedence, every ambiguity falling to over-asking. See
    # data/currencies.yaml for the precedence itself.
    #
    # A SOURCE mark is a noun AND a verb together, never a bare noun: "after
    # checking your website" is the buyer describing what they did.
    source_nouns: dict[str, tuple[str, ...]]
    # Saying verbs keep a first-person exemption ("I said 2 crore" is the buyer);
    # perception verbs take any subject (you do not see your own budget).
    saying_verbs: dict[str, tuple[str, ...]]
    perception_verbs: dict[str, tuple[str, ...]]
    pricing_verbs: dict[str, tuple[str, ...]]
    # "the price is X" / "X is the asking price" - a source shape in both orders.
    price_nouns: dict[str, tuple[str, ...]]
    # The buyer naming a figure as their budget across a copula, either order.
    naming_terms: dict[str, tuple[str, ...]]
    copulas: dict[str, tuple[str, ...]]
    # "that/which/it is my budget" - a pronoun standing in for the figure.
    anaphora: dict[str, tuple[str, ...]]
    # A first-person anchor plus a money term. Deliberately WEAKER than a
    # source frame: reported speech is full of first-person pronouns.
    # Pronouns that can HEAD a clause, so they can be a saying verb's subject.
    subject_pronouns: dict[str, tuple[str, ...]]
    connective_adverbs: dict[str, tuple[str, ...]]
    first_person: dict[str, tuple[str, ...]]
    money_terms: dict[str, tuple[str, ...]]
    # A first-person affordability shape, not a bare word.
    affordability_shapes: dict[str, tuple[str, ...]]
    # Counts only in a sentence with no source mark (approved amendment 3).
    bare_affordability: dict[str, tuple[str, ...]]
    # An auxiliary opening the utterance, which marks every segment of its
    # sentence - the telling words can follow the figure.
    question_openers: dict[str, tuple[str, ...]]
    # Figures joined by nothing but one of these fuse and share one fate.
    range_connectors: dict[str, tuple[str, ...]]
    # A range may carry one of these between its figures: "start at X and go up
    # to Y" is still one range.
    range_verbs: dict[str, tuple[str, ...]]
    # A measurement rather than a sum, and only for an ambiguous unit surface.
    dimensions: dict[str, tuple[str, ...]]
    conjunctions: dict[str, tuple[str, ...]]
    rate: ConversionRate

    def languages_covered(self) -> frozenset[str]:
        """Languages whose buyer can actually name a currency and be heard."""
        return frozenset(
            language
            for language, by_currency in self.words.items()
            if any(by_currency.values())
        )


# The precedence, as data. First match wins, per figure, and the boolean is
# whether that mark makes the figure the buyer's budget. Written out so the
# order is readable in one place rather than implied by a chain of ifs - three
# earlier designs hid their precedence inside the control flow and every review
# round turned on it.
_PRECEDENCE: Final = (
    ("naming", True),  # "my budget is X" / "X is my budget"
    ("source", False),  # "the listing says X" - beats affordability
    ("affordability", True),  # "is X enough for me?"
    ("question", False),  # "Does it cost X?"
    ("dimension", False),  # "a 2m wide balcony" - ambiguous unit only
    ("keyword", True),  # a plain budget keyword
    # OWNERSHIP is deliberately NOT a step here. It ranks below everything that
    # withholds, and its outcome equals the default, so it could never change
    # an answer - verified by deleting it and running the whole accumulated
    # string set: not one of the fifty distinguished it. The MARK is still
    # computed, because range fusion is cancelled by it (G3).
)


@dataclass(frozen=True)
class _Marks:
    """Which marks apply to one figure. Segment-level except where noted."""

    naming: bool
    source: bool
    affordability: bool  # sentence-level
    question: bool  # sentence-level
    dimension: bool
    # Not in the precedence - see _PRECEDENCE. Kept because fusion-cancel reads
    # it: a figure the buyer claims is not part of a quoted range.
    ownership: bool
    keyword: bool

    @property
    def withheld(self) -> bool:
        """The precedence, applied. Default is BUDGET: an ambiguity the closed
        lists cannot settle becomes one extra question, never a silent guess.
        """
        for mark, is_budget in _PRECEDENCE:
            if getattr(self, mark):
                return not is_budget
        return False


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
        source_nouns=_word_lists(raw, "source_nouns"),
        saying_verbs=_word_lists(raw, "saying_verbs"),
        perception_verbs=_word_lists(raw, "perception_verbs"),
        pricing_verbs=_word_lists(raw, "pricing_verbs"),
        price_nouns=_word_lists(raw, "price_nouns"),
        naming_terms=_word_lists(raw, "naming_terms"),
        copulas=_word_lists(raw, "copulas"),
        anaphora=_word_lists(raw, "anaphora"),
        subject_pronouns=_word_lists(raw, "subject_pronouns"),
        connective_adverbs=_word_lists(raw, "connective_adverbs"),
        first_person=_word_lists(raw, "first_person"),
        money_terms=_word_lists(raw, "money_terms"),
        affordability_shapes=_word_lists(raw, "affordability_shapes"),
        bare_affordability=_word_lists(raw, "bare_affordability"),
        question_openers=_word_lists(raw, "question_openers"),
        range_connectors=_word_lists(raw, "range_connectors"),
        range_verbs=_word_lists(raw, "range_verbs"),
        dimensions=_word_lists(raw, "dimensions"),
        conjunctions=_word_lists(raw, "conjunctions"),
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
    return rf"(?<!\w){re.escape(token)}(?!\w)" if token.isalnum() else re.escape(token)


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
                hits.append(_CurrencyHit(currency, found.start(), found.end(), negated))
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
        binds = any(h.negated and 0 <= h.start - neg_end <= _NEGATION_GAP for h in hits)
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

    # --- whose figure is it (issue #25, god's round-three design) -----------
    #
    # Segment on sentence punctuation, EVERY comma and EVERY conjunction, then
    # fuse a quoted range back together; mark each segment from closed lists;
    # apply one fixed precedence per figure. Nothing here measures a distance
    # or binds a marker to a figure, because three designs built that way each
    # shipped adjacent regressions.
    def says(where: str, words: tuple[str, ...]) -> bool:
        return any(word and re.search(_token_pattern(word), where) for word in words)

    def word_list(name: str) -> tuple[str, ...]:
        return getattr(vocabulary, name).get(language, ())

    def sentence_bounds() -> list[tuple[int, int]]:
        cuts = [0]
        for found in _CLAUSE_BREAK.finditer(lowered):
            cuts.append(found.end())
        cuts.append(len(lowered))
        return [
            (a, b) for a, b in zip(cuts, cuts[1:], strict=False) if lowered[a:b].strip()
        ] or [(0, len(lowered))]

    def segment_bounds() -> list[tuple[int, int]]:
        """Sentence punctuation, every comma, every conjunction - then fusion.

        Cutting on every comma and conjunction is what makes the marks
        conclusive: a segment is a single short claim. It also cuts quoted
        ranges in half, which is what RANGE FUSION below puts back.
        """
        cuts = {0, len(lowered)}
        for found in _CLAUSE_BREAK.finditer(lowered):
            cuts.add(found.end())
        # G1, QUOTATIVE COMMA: a comma straight after a saying verb reports
        # what follows rather than starting a new claim, so it does not split.
        # "The agent said, I can afford 2m" is one claim by the agent. STT
        # transcripts mostly lack the comma anyway; this closes the text form.
        saying = word_list("saying_verbs")
        for found in re.finditer(r",", lowered):
            before = lowered[: found.start()].rstrip().rstrip('"\u201c\u2018')
            if any(
                word and re.search(rf"(?<!\w){re.escape(word)}$", before)
                for word in saying
            ):
                continue
            cuts.add(found.end())
        for word in word_list("conjunctions"):
            if not word:
                continue
            for found in re.finditer(_token_pattern(word), lowered):
                cuts.add(found.start())

        bounds = [(a, b) for a, b in zip(sorted(cuts), sorted(cuts)[1:], strict=False)]

        # RANGE FUSION. Two figures joined by NOTHING but a connector, a
        # currency token and punctuation are one quoted range sharing one fate:
        # "says AED 750,000, and AED 800,000" is a single claim. "750k works,
        # and I have 800k" is two, because words intervene.
        connectors = word_list("range_connectors")
        currency_tokens = [
            token.lower()
            for tokens in vocabulary.words.get(language, {}).values()
            for token in tokens
        ] + [
            symbol.lower()
            for symbols in vocabulary.symbols.values()
            for symbol in symbols
        ]

        # G2: a range may carry a verb between its figures - "start at X and go
        # up to Y" is still one range - so the range verbs are strippable too.
        strippable = sorted(
            connectors + word_list("range_verbs") + tuple(currency_tokens),
            key=len,
            reverse=True,
        )

        def only_a_connector(gap: str) -> bool:
            rest = gap
            for token in strippable:
                if token:
                    rest = re.sub(_token_pattern(token), " ", rest)
            return not re.search(r"[^\W_]", rest)

        def buyer_claims_the_second(right_end: int) -> bool:
            """G3, FUSION-CANCEL. The gap between two figures can look like a
            range while the second figure is plainly the buyer's: "the listing
            says 750k and 800k works for me". So a naming, affordability or
            ownership mark after the second figure cancels the fusion."""
            tail = lowered[right_end:]
            stop = next((s for s, _ in spans if s >= right_end), len(lowered))
            tail = lowered[right_end:stop] if stop > right_end else tail
            return (
                says(tail, word_list("affordability_shapes"))
                or says(tail, word_list("naming_terms"))
                or (
                    says(tail, word_list("first_person"))
                    and says(tail, word_list("money_terms"))
                )
            )

        for left, right in zip(spans, spans[1:], strict=False):
            if not only_a_connector(lowered[left[1] : right[0]]):
                continue
            if buyer_claims_the_second(right[1]):
                continue
            merged = []
            for a, b in bounds:
                if a <= left[0] and b > left[0]:
                    start_a = a
                elif a <= right[0] < b:
                    merged.append((start_a, b))
                    continue
                elif left[0] < a and b <= right[0]:
                    continue
                else:
                    merged.append((a, b))
            bounds = sorted(set(merged)) or bounds
        return bounds

    sentences = sentence_bounds()
    segments = segment_bounds()

    def enclosing(bounds: list[tuple[int, int]], position: int) -> tuple[int, int]:
        for a, b in bounds:
            if a <= position < b:
                return a, b
        return bounds[-1]

    def is_question(sentence: str) -> bool:
        """An auxiliary or copula opening the utterance marks the WHOLE
        sentence, because the telling words can follow the figure: "Is AED
        750,000 the asking price?"."""
        opening = sentence.strip()
        return any(
            word and (opening.startswith(word + " ") or opening.startswith(word + "'"))
            for word in word_list("question_openers")
        )

    def clause_start(verb_at: int) -> int:
        """Where the clause owning the saying verb at `verb_at` begins.

        A clause, not a segment. Segments cut on every comma and conjunction so
        that MARKS are conclusive, but a subject can sit on the far side of such
        a cut - "I very clearly AND repeatedly said 2 crore" puts the adverbs'
        own conjunction between the subject and its verb.

        So a conjunction heads a coordinated CLAUSE **unless** everything
        between it and the verb is affirmatively SHARED-SUBJECT material, which
        in English is adverbial: a connective adverb, an uncapitalised -ly
        adverb, or another conjunction joining two of them. Nothing at all
        between them is a coordinated verb phrase, which is the same subject.

        The test is inverted on purpose, and it took three rounds to get here.
        Nouns are an OPEN class, so every version that tried to RECOGNISE the
        new subject met one it did not know: first a noun outside the roster,
        then a bare proper name, which takes no determiner either. Adverbs
        between a conjunction and its verb are a closed class, so the question
        that CAN be answered is the negative one. The asymmetry settles which
        way the default falls: a missed adverb withholds a buyer's figure and
        the buyer is asked again, while a missed noun confirms a seller's figure
        as the buyer's budget. Unknown material therefore means NEW SUBJECT.
        """
        start = enclosing(sentences, verb_at)[0]
        adverbs = set(word_list("connective_adverbs"))
        joiners = {word for word in word_list("conjunctions") if word}

        def shared_subject_filler(token: str) -> bool:
            lower = token.lower()
            if lower in adverbs or lower in joiners:
                return True
            # The -ly suffix is honoured only on an uncapitalised token.
            # "Kelly" and "repeatedly" are morphologically identical, and
            # reading a name as an adverb hands the seller's clause the buyer's
            # subject. Capitalisation is weak evidence, so it is consulted only
            # in the direction where being wrong is safe: an unrecognised
            # adverb withholds a figure, it never confirms one.
            return lower.endswith("ly") and token == lower

        for word in joiners:
            for found in re.finditer(_token_pattern(word), lowered):
                if not start <= found.start() < verb_at:
                    continue
                between = re.findall(r"[^\W_]+", text[found.end() : verb_at])
                if between and not all(map(shared_subject_filler, between)):
                    start = max(start, found.end())
        return start

    def has_source(start: int, end: int, i: int) -> bool:
        """Three closed shapes, never a bare noun (G5).

        1. A noun and a SAYING verb - with a first-person exemption, because
           "I said 2 crore" is the buyer restating their own budget and
           withholding that is the expensive direction.
        2. Any subject and a PERCEPTION or PRICING verb: you do not SEE your own
           budget, and "it costs X" is the seller's number whoever says it.
        3. A price noun and a copula either side of the figure, so both "the
           price is X" and "X is the asking price" are sourced.
        """
        region = lowered[start:end]
        if says(region, word_list("perception_verbs")) or says(
            region, word_list("pricing_verbs")
        ):
            return True

        # The first-person exemption is about WHO IS SAYING, so it keys on the
        # subject of the saying verb rather than on any first-person token in
        # the segment. "I said 2 crore" and "I told you 2 crore" are the buyer
        # restating their own budget; "they said our maximum is 2m" is reported
        # speech whose possessive belongs to the speaker, not to the buyer.
        # Reading the exemption as "any first person anywhere" let a source
        # noun like "you" defeat it in "I told you 2 crore".
        def subject_is_the_buyer(before: str) -> bool:
            """Is the SUBJECT of this saying verb the buyer?

            The subject is the clause-initial nominal, found left to right: the
            first token that is either a pronoun able to head a clause or a
            source noun. Everything before it is a determiner, everything after
            it belongs to a modifier, and neither can change who is speaking.

            There is no token count anywhere, and that is the point. A reverse
            scan over a fixed window is a distance rule wearing a subject's
            clothes, and it failed at both boundaries: "The agent from my
            office said" met the possessive inside the agent's own modifier and
            exempted the seller, while "I very clearly and repeatedly said"
            pushed the real subject out of the window and lost the buyer's
            budget.
            """
            subjects = word_list("subject_pronouns")
            nominals = set(subjects) | set(word_list("source_nouns"))
            for token in re.findall(r"[^\W_]+", before):
                if token in nominals:
                    return token in subjects
            return False

        for verb in word_list("saying_verbs"):
            if not verb:
                continue
            for found in re.finditer(_token_pattern(verb), lowered):
                if not start <= found.start() < end:
                    continue
                # The subject is read from the CLAUSE start, not from this
                # segment's, because a segment cut can fall between a subject
                # and its verb.
                verb_at = found.start()
                if subject_is_the_buyer(lowered[clause_start(verb_at) : verb_at]):
                    continue
                return True
        return copular(i, word_list("price_nouns"), reach=2)

    def copular(
        i: int,
        terms: tuple[str, ...],
        *,
        reach: int = 0,
        within: tuple[int, int] | None = None,
    ) -> bool:
        """A term and a copula on one side of this figure or the other.

        "my budget is X" and "X is my budget" are the same claim, and so are
        "the price is X" and "X is the asking price", which is why one helper
        serves both the naming mark and the copular half of the source mark.
        """
        region_start, region_end = within or (0, len(lowered))
        figure_start, figure_end = spans[i]
        if not region_start <= figure_start < region_end:
            # The figure is not in the region being asked about, so nothing
            # inside that region can be a copular claim about it. Unbounded
            # slices here are how a price in one sentence gated another.
            return False
        # A currency token may sit between the copula and the figure - "the
        # price is AED 985,000" - and it belongs to the figure, not to the gap.
        money = "|".join(
            re.escape(token)
            for token in sorted(
                {
                    t.lower()
                    for tokens in vocabulary.words.get(language, {}).values()
                    for t in tokens
                }
                | {
                    s.lower()
                    for symbols in vocabulary.symbols.values()
                    for s in symbols
                },
                key=len,
                reverse=True,
            )
            if token
        )
        # `reach` is approved amendment 2: the PRICE-noun shape may cross one
        # adjective and one preposition - "prices are affordable FROM AED
        # 750,000" - and nothing else may, so naming cannot borrow it. Keyed to
        # the price noun being present, it cannot touch "AED 750,000 is
        # affordable for me", which names no price at all.
        crossing = rf"(?:\w+\s+){{0,{reach}}}" if reach else ""
        gap = rf"\s*{crossing}(?:(?:{money})\s*)?$" if money else rf"\s*{crossing}$"
        for term in terms:
            if not term:
                continue
            for copula in word_list("copulas"):
                if not copula:
                    continue
                before = rf"(?<!\w){re.escape(term)}\s+{re.escape(copula)}{gap}"
                after = (
                    rf"^\s*{re.escape(copula)}\s+{crossing}"
                    rf"(?:the|a|an)?\s*{re.escape(term)}(?!\w)"
                )
                if re.search(before, lowered[region_start:figure_start]) or re.match(
                    after, lowered[figure_end:region_end]
                ):
                    return True
        return False

    def reported_regions() -> list[tuple[int, int]]:
        """Every stretch of the utterance that is somebody else's words.

        Two shapes: text between a pair of quote marks, and - when the speech
        is unquoted - everything from a saying verb's reporting comma to the end
        of its sentence. Computed ONCE and consulted through `naming_allowed`
        below, which is the whole point: the gate was previously applied on one
        naming path and not the other, so the anaphoric form walked round it.
        """
        regions: list[tuple[int, int]] = []
        opens: list[int] = []
        for found in re.finditer(r'["\u201c\u201d]', lowered):
            if opens:
                regions.append((opens.pop(), found.end()))
            else:
                opens.append(found.start())
        quoted = list(regions)
        for word in word_list("saying_verbs"):
            if not word:
                continue
            for found in re.finditer(rf"(?<!\w){re.escape(word)}\s*,", lowered):
                _, sentence_end = enclosing(sentences, found.start())
                if any(
                    found.end() <= open_at and close_at <= sentence_end
                    for open_at, close_at in quoted
                ):
                    # Quote marks delimit this speech, so they are the extent of
                    # it. Adding comma-to-end-of-sentence on top would swallow
                    # what the buyer says AFTER the closing quote: 'They said,
                    # "AED 750,000", and that is my budget' names 750,000.
                    continue
                regions.append((found.end(), sentence_end))
        return regions

    reported = reported_regions()

    def naming_allowed(position: int) -> bool:
        """THE quotative gate. Every naming path asks this, and nothing else
        decides it.

        A naming phrase inside reported speech belongs to the speaker being
        quoted, not to the buyer: `They said, "our maximum is AED 2m."` and
        `They said, "AED 750,000, which is my budget."` are both the speaker.
        A naming the buyer adds OUTSIDE the quotation still counts, which is
        why the gate is keyed to the position of the naming phrase rather than
        to the figure's segment.
        """
        return not any(start <= position < end for start, end in reported)

    def has_naming(i: int) -> bool:
        """The buyer naming this figure as their budget.

        Direct - "my budget is X" / "X is my budget" - or ANAPHORIC (G6), where
        a pronoun stands in for the figure just mentioned: "750,000, which is
        my budget". The pronoun refers to the figure in its own segment or the
        one immediately before it, so both are checked.
        """
        # Both paths below go through `naming_allowed`, keyed to where the
        # naming PHRASE sits. The direct form sits against the figure.
        if naming_allowed(spans[i][0]) and copular(i, word_list("naming_terms")):
            return True
        naming = word_list("naming_terms")
        anaphors = word_list("anaphora")
        copulas = word_list("copulas")
        shapes = [
            rf"(?<!\w){re.escape(a)}\s+{re.escape(c)}\s+{re.escape(n)}(?!\w)"
            for a in anaphors
            for c in copulas
            for n in naming
            if a and c and n
        ]
        # The anaphor has to follow this figure, and no later figure may sit
        # between them - otherwise it is referring to that one instead.
        after_here = spans[i][1]
        next_figure = next((s for s, _ in spans if s > after_here), len(lowered))
        window = lowered[after_here:next_figure]
        for shape in shapes:
            found = re.search(shape, window)
            if found and naming_allowed(after_here + found.start()):
                return True
        return False

    def sentence_has_source(sent_start: int, sent_end: int) -> bool:
        """Does ANY figure in this sentence carry a source mark?

        Amendment 3 grants a bare affordability word only in a sentence with no
        source mark, and `has_source` is figure-relative for its copular half -
        so asking it about one figure missed a price attached to another.
        "Would 2 crore be enough, given AED 750,000 is the sale price?" carries
        a source, on the second figure.
        """
        return any(
            has_source(sent_start, sent_end, j)
            for j, (figure_start, _) in enumerate(spans)
            if sent_start <= figure_start < sent_end
        )

    def marks_for(i: int) -> _Marks:
        seg_start, seg_end = enclosing(segments, spans[i][0])
        segment = lowered[seg_start:seg_end]
        sent_start, sent_end = enclosing(sentences, spans[i][0])
        sentence = lowered[sent_start:sent_end]
        return _Marks(
            naming=has_naming(i),
            source=has_source(seg_start, seg_end, i),
            affordability=(
                says(sentence, word_list("affordability_shapes"))
                or (
                    says(sentence, word_list("bare_affordability"))
                    and not sentence_has_source(sent_start, sent_end)
                )
            ),
            question=is_question(sentence),
            dimension=(
                says(segment, word_list("dimensions"))
                and _ambiguous_unit(candidates[i].figure.surface)
            ),
            ownership=(
                says(segment, word_list("first_person"))
                and says(segment, word_list("money_terms"))
            ),
            keyword=keyword_in_segment(i),
        )

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

    def keyword_in_segment(i: int) -> bool:
        """Is a budget keyword in this figure's own segment?

        Segment-scoped, with no distance anywhere - the design says so in as
        many words. The old distance-bound `marked` set survived here as the
        gate in front of selection, so a keyword could decide BUDGET in the
        precedence while the same figure was discarded before it could be
        chosen: "My budget after several careful financial planning reviews is
        750000" was lost to the keyword reach.
        """
        start, end = enclosing(segments, spans[i][0])
        return says(lowered[start:end], word_list("budget_keywords"))

    fallback: BudgetMention | None = None
    for i, match in enumerate(candidates):
        held = currency_of.get(i)
        currency = None if held is None else held[1]
        unit = _budget_units().search(match.figure.surface)
        if marks_for(i).withheld:
            # Not the buyer's figure to confirm. The precedence that decided
            # this is `_Marks.withheld`, and it is deliberately the only place
            # the question is answered.
            continue
        if currency is None and unit is None and not keyword_in_segment(i):
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
        if keyword_in_segment(i):
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
