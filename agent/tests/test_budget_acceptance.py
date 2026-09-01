"""The whole accumulated string set for issue #25, with spec-derived answers.

One table, every string any of the three review rounds produced, plus god's
worked examples and the cross-product of the mark classes. The design is god's
round-three spec plus its six amendments (`data/currencies.yaml` carries the
precedence); this file is what "conforms to the spec" means, so a reviewer can
check the policy against the table rather than against a narrative.

Each row is `(utterance, expected_value)` where `None` means WITHHELD - the
model answers the buyer instead of the policy taking the turn - and a number
means the policy confirms that figure.

Every expectation below is DERIVED from the precedence, not observed from the
implementation. The derivation is named in the group comment so a wrong row is
an argument about the spec rather than a mystery.
"""

from __future__ import annotations

import pytest

from ambassador.budget import BudgetPolicy, find_budget, load_currency_vocabulary


@pytest.fixture(scope="module")
def vocabulary():
    return load_currency_vocabulary()


# --- the accumulated set, by the mark that decides each row -----------------

NAMING_WINS = [
    # Precedence 1. The buyer names the figure as their budget across a
    # copula, so it is a budget even inside a source frame.
    ("The website says AED 750,000 is my budget", 750_000.0),
    ("The website says AED 750,000 is my budget, and AED 800,000 is the listing price.", 750_000.0),
    ("my budget is 800k", 800_000.0),
    ("my budget is 2 crore", 20_000_000.0),
    ("my budget is AED 2,000,000", 2_000_000.0),
    ("The price is AED 985,000 and my budget is AED 2,000,000.", 2_000_000.0),
    # G6, anaphoric: a pronoun stands in for the figure just mentioned.
    ("the listing says AED 750,000 and that is my budget", 750_000.0),
    ("The listing says AED 750,000, which is my budget, and AED 800,000 is the asking price.", 750_000.0),
]

SOURCE_WITHHOLDS = [
    # Precedence 2, and it beats affordability and ownership by design.
    ("the listing says AED 750,000", None),
    ("it starts from about 3 million", None),
    ("your website says 985,000 dirhams", None),
    ("you said 2 crore on the call", None),
    ("the brochure quotes 3 million", None),
    ("it is priced at 2 million", None),
    ("prices start at AED 750,000", None),
    ("my agent said AED 750,000", None),
    ("The agent told me I could afford 2m.", None),
    ("The listing says AED 750,000 is affordable for me.", None),
    ("The brochure says buyers spend AED 750,000.", None),
    ("The website says AED 750,000 is affordable for most buyers.", None),
    ("The agent said people spend around 3 million here.", None),
    # G1, quotative comma: the comma after a saying verb reports rather than
    # divides, so the source frame still covers the quoted figure.
    ('The agent said, "I can afford 2m."', None),
    # G5, copular price-naming, both orders.
    ("the price is AED 985,000", None),
    ("AED 800,000 is the asking price", None),
    # G5, perception verbs take any subject - you do not see your own budget.
    ("I saw AED 750,000 online", None),
]

QUESTION_WITHHOLDS = [
    # Precedence 4. An auxiliary opens the utterance and marks every segment of
    # its sentence, because the telling words can follow the figure.
    ("Is AED 750,000 the asking price?", None),
    ("Does it cost AED 800,000?", None),
    ("Did you mean AED 900,000?", None),
    ("Will it be AED 750,000?", None),
    ("Is it 750k?", None),
    ("is that 3 million?", None),
]

AFFORDABILITY_WINS = [
    # Precedence 3, above QUESTION: the buyer asking whether their own amount
    # stretches is stating a budget interrogatively.
    ("Is that 800k enough for me?", 800_000.0),
    ("Is that AED 800,000 enough for me?", 800_000.0),
    ("Is this AED 800,000 enough for a studio?", 800_000.0),
    ("Is that 2 crore enough for me?", 20_000_000.0),
    ("Can I afford AED 985,000?", 985_000.0),
]

DIMENSION_WITHHOLDS = [
    # G4, restored between QUESTION and keyword, and scoped to an ambiguous
    # unit surface only.
    ("I need a 2m wide balcony.", None),
    ("The room is 2m wide.", None),
    ("I want a 2m deep terrace", None),
]

DEFAULT_OVER_ASKS = [
    # Precedence 7. Nothing conclusive, so the policy asks - ADR-011's safe
    # direction. Note the dimension rows above do NOT extend to these: a
    # currency or a non-ambiguous unit is not made ambiguous by a room.
    ("I have AED 800,000 for a room.", 800_000.0),
    ("I have 800k for a wide balcony.", 800_000.0),
    ("985,000 dirhams for a high floor", 985_000.0),
    ("around 800k", 800_000.0),
    ("2 crore", 20_000_000.0),
    ("we are looking at around 985,000 dirhams", 985_000.0),
    ("I can afford 750,000", 750_000.0),
    ("I can only spend 2 crore", 20_000_000.0),
    ("I said 2 crore", 20_000_000.0),  # saying verbs keep the first-person exemption
    ("I have 2 crore, what is the price?", 20_000_000.0),
    ("I can do 2 million, is that ok?", 2_000_000.0),
]

RANGE_FUSION = [
    # The fusion exception: one source frame covers a coordinated range, so no
    # member escapes on its own.
    ("The listing says AED 750,000 or AED 800,000.", None),
    ("The listing says AED 750,000, and AED 800,000.", None),
    ("The listing says AED 750,000, and AED 800,000, or AED 900,000.", None),
    ("You quoted AED 750,000 to AED 900,000.", None),
    ("The listing says AED 750,000, AED 800,000 or AED 900,000.", None),
    # G2, a range may carry a verb between its figures.
    ("Prices start at AED 750,000 and go up to AED 900,000.", None),
    ("The website says units start at AED 750,000, and go up to AED 900,000.", None),
]

FUSION_CANCELLED = [
    # G3. The gap between the figures looks like a range, but a buyer mark
    # after the second figure says it is not one.
    ("The listing says AED 750,000 and AED 800,000 works for me.", 800_000.0),
    ("The listing says AED 750,000 and AED 800,000 is within my limit.", 800_000.0),
    ("The listing says AED 750,000 and I have AED 800,000 available.", 800_000.0),
]

CONTRAST = [
    # A conjunction ends the segment, so the buyer's own claim stands alone.
    ("The listing says AED 750,000, but I have AED 800,000 available.", 800_000.0),
    ("The listing says AED 750,000 but AED 800,000 works for me.", 800_000.0),
    ("The price is AED 985,000, however I only have 2 crore.", 20_000_000.0),
    ("It starts at 3 million though I was thinking 800k.", 800_000.0),
    ("The price is too high, I can do 2 million.", 2_000_000.0),
    ("the listing says 3 million, although my budget is 2 crore", 20_000_000.0),
]

NOT_MONEY = [
    # Unchanged by any of this: a figure with no currency, no money unit and no
    # budget keyword is not a budget candidate at all.
    ("three bedrooms please", None),
    ("what floor is it on", None),
    ("I'm around floor 15", None),
    ("what is the payment plan", None),
    ("when does it hand over", None),
]

ACCEPTANCE = (
    [("naming", *row) for row in NAMING_WINS]
    + [("source", *row) for row in SOURCE_WITHHOLDS]
    + [("question", *row) for row in QUESTION_WITHHOLDS]
    + [("affordability", *row) for row in AFFORDABILITY_WINS]
    + [("dimension", *row) for row in DIMENSION_WITHHOLDS]
    + [("default", *row) for row in DEFAULT_OVER_ASKS]
    + [("fusion", *row) for row in RANGE_FUSION]
    + [("fusion-cancel", *row) for row in FUSION_CANCELLED]
    + [("contrast", *row) for row in CONTRAST]
    + [("not-money", *row) for row in NOT_MONEY]
)


@pytest.mark.parametrize(
    "mark,said,expected", ACCEPTANCE, ids=[f"{m}:{s[:44]}" for m, s, _ in ACCEPTANCE]
)
def test_the_accumulated_string_set(vocabulary, mark, said, expected):
    mention = find_budget(said, vocabulary, "en")
    actual = None if mention is None else mention.value
    assert actual == expected, f"[{mark}] {said!r}"


@pytest.mark.parametrize(
    "said,expected", [(s, e) for _, s, e in ACCEPTANCE], ids=[s[:48] for _, s, _ in ACCEPTANCE]
)
def test_the_policy_speaks_exactly_when_a_figure_is_confirmed(
    vocabulary, said, expected
):
    """The same table at the seam that matters. A withheld figure must leave the
    turn to the model, and a confirmed one must take it - `find_budget`
    returning None is only half the claim."""
    decision = BudgetPolicy(vocabulary, "en").observe(said)
    assert decision.speaks is (expected is not None), said


# --- the composition seams god named ---------------------------------------


@pytest.mark.parametrize(
    "said,expected",
    [
        # quotative comma x fusion: the comma after "says" does not divide, and
        # the range inside the reported speech still fuses.
        ('The agent said, "AED 750,000 or AED 800,000."', None),
        ('The agent said, "prices run from AED 750,000 to AED 900,000."', None),
        # quotative comma x fusion-cancel: reported range, then the buyer.
        ('The agent said, AED 750,000 and AED 800,000 works for me.', 800_000.0),
    ],
)
def test_quotative_comma_composes_with_fusion(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # anaphora x fusion-cancel: "which is my budget" names the first figure
        # while the second is sourced.
        ("The listing says AED 750,000, which is my budget, and AED 800,000.", 750_000.0),
        # anaphora after a fused range names the LAST member, and the range
        # cancels because the buyer claims it.
        ("The listing says AED 750,000 or AED 800,000, which is my budget.", 800_000.0),
    ],
)
def test_anaphora_composes_with_fusion_cancel(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # perception-source x first-person: the exemption is for SAYING verbs
        # only, so "I saw" is sourced and "I said" is not.
        ("I saw AED 750,000 online", None),
        ("I read AED 800,000 on the website", None),
        ("I found 2 crore listed", None),
        ("I said 2 crore", 20_000_000.0),
        ("I told you 2 crore", 20_000_000.0),
    ],
)
def test_perception_source_composes_with_the_first_person_exemption(
    vocabulary, said, expected
):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


# --- the one row the amended spec does not settle --------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN, and god's call rather than mine. The spec ranks NAMING above "
        "SOURCE deliberately, which is what makes 'The website says AED "
        "750,000 is my budget' confirm 750,000. The same precedence makes a "
        "possessive inside REPORTED speech name a budget, and here the 'our' "
        "belongs to the speaker being quoted, not to the buyer. The review "
        "requires withheld; the spec as written says budget. Left failing "
        "rather than closed by invention - the proposed one-clause amendment "
        "is that NAMING does not apply inside a quotative-comma segment. "
        "strict, so it fails loudly the moment the behaviour changes either "
        "way."
    ),
)
def test_a_possessive_inside_reported_speech_is_not_the_buyer_naming(vocabulary):
    assert find_budget('They said, "our maximum is AED 2m."', vocabulary, "en") is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN, god's call. G5's copular price shape wants the copula next to "
        "the figure, and here an adjective and a preposition sit between them: "
        "'prices ARE AFFORDABLE FROM AED 750,000'. It is a listing claim and "
        "should be withheld. Not closed by invention - the proposed amendment "
        "is to let the price-noun copular shape reach across an adjective and "
        "a preposition. Found by my own corpus, not by review."
    ),
)
def test_a_price_noun_reaching_across_an_adjective_is_still_a_source(vocabulary):
    assert find_budget("prices are affordable from AED 750,000", vocabulary, "en") is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN, god's call. AFFORDABILITY is specified as a FIRST-PERSON shape, "
        "so 'would 2 crore be enough' carries no first-person token and QUESTION "
        "withholds it at step 4 - a lost budget, which is the expensive "
        "direction. The buyer is stating a budget interrogatively exactly as in "
        "'Is that 800k enough for me?'. Proposed amendment: allow a bare "
        "affordability shape ('be enough', 'enough?') when the sentence carries "
        "no other speaker. Found by my own corpus, not by review."
    ),
)
def test_an_impersonal_affordability_question_is_still_a_budget(vocabulary):
    mention = find_budget("would 2 crore be enough", vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0
