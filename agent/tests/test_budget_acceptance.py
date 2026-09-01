"""The whole accumulated string set for issue #25, with spec-derived answers.

One table, every string any of the three review rounds produced, plus god's
worked examples and the cross-product of the mark classes. The design is god's
round-three spec plus its six amendments. `_PRECEDENCE` in
`ambassador/budget.py` is the authority; `data/currencies.yaml` restates it for
whoever is reading the word lists. This file is what "conforms to the spec"
means, so a reviewer can check the policy against the table rather than against
a narrative.

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
    ('They said, "our maximum is AED 2m."', None),
    ("prices are affordable from AED 750,000", None),
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
    # Decided by affordability, not by the default: "I can afford" is a
    # first-person affordability shape. It was mislabelled under DEFAULT, which
    # left the table unable to notice a precedence regression on it.
    ("I can afford 750,000", 750_000.0),
    ("would 2 crore be enough", 20_000_000.0),
    ("AED 750,000 is affordable for me", 750_000.0),
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
    ("I said 2 crore", 20_000_000.0),  # saying verbs keep the first-person exemption
    ("I have 2 crore, what is the price?", 20_000_000.0),
    ("I can do 2 million, is that ok?", 2_000_000.0),
]

KEYWORD_WINS = [
    # Precedence 6, and segment-scoped with no distance anywhere. The
    # modifier-laden sentence is the one the old distance-bound gate discarded
    # after the precedence had already decided BUDGET for it.
    ("I can only spend 2 crore", 20_000_000.0),
    ("My budget after several careful financial planning reviews is 750000.", 750_000.0),
    ("up to AED 2,000,000", 2_000_000.0),
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
    + [("keyword", *row) for row in KEYWORD_WINS]
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


# --- the three amendments approved after round three ------------------------
#
# Each of these was a strict xfail naming the clause that produced it and the
# amendment it needed; god approved all three with the scoping constraints
# recorded beside them, so each now asserts plainly and carries the guard that
# keeps the amendment from reaching further than it was granted.


def test_a_possessive_inside_reported_speech_is_not_the_buyer_naming(vocabulary):
    """Amendment 1: naming does not apply inside a quotative-comma segment.

    The guard is that it cannot reach a naming without such a comma, which is
    what keeps naming-beats-source true where it was meant to be.
    """
    assert find_budget('They said, "our maximum is AED 2m."', vocabulary, "en") is None
    for said, expected in (
        ("The website says AED 750,000 is my budget", 750_000.0),
        ("the listing says AED 750,000 and that is my budget", 750_000.0),
        (
            "The listing says AED 750,000, which is my budget, and AED 800,000"
            " is the asking price.",
            750_000.0,
        ),
    ):
        mention = find_budget(said, vocabulary, "en")
        assert mention is not None and mention.value == expected, said


def test_a_price_noun_reaching_across_an_adjective_is_still_a_source(vocabulary):
    """Amendment 2: the copular price shape may cross one adjective and one
    preposition, and ONLY when the price noun is present.

    The guard is the sentence with no price noun in it - "AED 750,000 is
    affordable for me" must stay the buyer's.
    """
    assert (
        find_budget("prices are affordable from AED 750,000", vocabulary, "en")
        is None
    )
    mention = find_budget("AED 750,000 is affordable for me", vocabulary, "en")
    assert mention is not None and mention.value == 750_000.0


def test_an_impersonal_affordability_question_is_still_a_budget(vocabulary):
    """Amendment 3: a bare affordability word counts where the sentence carries
    NO source mark - the over-ask direction, with nobody else to attribute the
    figure to.

    The guard is the sourced sentence, which still withholds.
    """
    mention = find_budget("would 2 crore be enough", vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0
    for said in (
        "The listing says AED 750,000 is affordable.",
        "The website says AED 750,000 is affordable for most buyers.",
    ):
        assert find_budget(said, vocabulary, "en") is None, said


# --- the five conformance slips, each with the guard that bounds its fix -----


@pytest.mark.parametrize(
    "said,expected",
    [
        # P1. The exemption keys on the SUBJECT of the saying verb, so an
        # adverb between subject and verb cannot turn the buyer's restatement
        # into a source.
        ("I clearly said 2 crore.", 20_000_000.0),
        ("We already told you 2 crore.", 20_000_000.0),
        ("I honestly told you 2 crore", 20_000_000.0),
        # Guard: a source noun before the verb IS the subject, whatever
        # pronouns follow it.
        ("The agent told me I could afford 2m.", None),
        ("my agent said AED 750,000", None),
    ],
)
def test_the_exemption_keys_on_the_verbs_subject(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P2. Amendment 2's reach is symmetric - the grant was both orders.
        ("AED 750,000 is an affordable price.", None),
        ("AED 750,000 is a reasonable sale price.", None),
        ("prices are affordable from AED 750,000", None),
        # Guard: keyed to the price noun, so a sentence naming no price stays
        # the buyer's.
        ("AED 750,000 is affordable for me", 750_000.0),
    ],
)
def test_the_price_noun_reach_is_symmetric(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P3. "A sentence with no source mark" means no source on ANY of its
        # figures, not just the one being judged.
        ("Would 2 crore be enough, given AED 750,000 is the sale price?", None),
        # A source in a DIFFERENT sentence does not reach: the amendment reads
        # per sentence, and "?" ends this one. My first draft of this row
        # expected None and was simply wrong about the sentence boundary.
        ("Would 2 crore be enough? The listing says AED 750,000.", 20_000_000.0),
        # Guard: with nobody else in the sentence, the bare shape still counts.
        ("would 2 crore be enough", 20_000_000.0),
        ("Would 2 crore be enough?", 20_000_000.0),
    ],
)
def test_bare_affordability_looks_at_the_whole_sentence(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P4. The quotative exclusion covers the DIRECT naming inside the
        # quote, not an anaphor the buyer adds outside it.
        ('They said, "AED 750,000", and that is my budget.', 750_000.0),
        ('The agent said, "AED 800,000", which is my budget.', 800_000.0),
        # Guard: the possessive INSIDE the quote is still the speaker's.
        ('They said, "our maximum is AED 2m."', None),
    ],
)
def test_the_quotative_exclusion_stops_at_the_quote(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P5. The keyword mark is segment-scoped and nothing distance-bound
        # gates it: the precedence deciding BUDGET has to survive selection.
        ("My budget after several careful financial planning reviews is 750000.", 750_000.0),
        # Guard: a figure with no currency, no money unit and no keyword in its
        # segment is still not a budget candidate at all.
        ("I'm around floor 15", None),
        ("three bedrooms please", None),
        ("The price is AED 985,000 and my budget is AED 2,000,000.", 2_000_000.0),
    ],
)
def test_the_keyword_mark_has_no_distance_gate(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN, god's call, and a consequence of the spec rather than a slip. "
        "Segmentation cuts on EVERY comma by design, so a keyword separated "
        "from its figure by a parenthetical lands in a different segment and "
        "the figure has no mark left to qualify it: 'My budget, after talking "
        "it over with my wife at length, is 750000' is lost. Meredith's P5 "
        "string is the same shape WITHOUT commas and now passes. Not closed by "
        "invention - the amendment I would propose is that a comma-delimited "
        "parenthetical does not divide a claim when the text resumes with a "
        "copula. Found by my own probing, not by review."
    ),
)
def test_a_parenthetical_does_not_lose_the_keyword(vocabulary):
    mention = find_budget(
        "My budget, after talking it over with my wife at length, is 750000.",
        vocabulary,
        "en",
    )
    assert mention is not None and mention.value == 750_000.0


# --- slip round 2: the structures, tested as CLASSES ------------------------
#
# Both earlier slip rounds had the same shape: an approximation stood in where
# the spec names a structure. So these are not more examples of the reviewer's
# strings - they are arbitrary members of each class, on BOTH sides of the
# boundary, which is what an approximation cannot satisfy.


@pytest.mark.parametrize(
    "adverbs",
    ["", "clearly ", "very clearly ", "very clearly and repeatedly ",
     "honestly, truly, and quite deliberately ",
     "after much thought very carefully and repeatedly "],
)
@pytest.mark.parametrize("verb", ["said", "told you"])
def test_any_adverb_stack_leaves_the_buyer_as_the_subject(vocabulary, adverbs, verb):
    """P1, buyer side. An adverb never consumes the subject, however many of
    them there are - a fixed-window scan lost the subject at four tokens."""
    said = f"I {adverbs}{verb} 2 crore."
    mention = find_budget(said, vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0, said


@pytest.mark.parametrize(
    "modifier",
    ["", " from my office", " from my own agency", " who called me yesterday",
     " that I spoke to about my budget",
     " from the office near my building that I visited with my wife"],
)
def test_any_modifier_on_the_subject_keeps_it_the_seller(vocabulary, modifier):
    """P1, seller side. A first-person token inside the subject's own modifier
    does not make the buyer the speaker - a backward scan met it first and
    exempted the seller's statement."""
    said = f"The agent{modifier} said AED 750,000."
    assert find_budget(said, vocabulary, "en") is None, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P3, both figure orders. A copular source in one sentence cannot gate
        # another: the committed guard tested only the order where the
        # questioned figure happened to be global figure 0.
        ("AED 750,000 is the sale price. Would 2 crore be enough?", 20_000_000.0),
        ("Would 2 crore be enough? AED 750,000 is the sale price.", 20_000_000.0),
        ("The listing says AED 750,000. Would 2 crore be enough?", 20_000_000.0),
        ("Would 2 crore be enough? The listing says AED 750,000.", 20_000_000.0),
        # And the same-sentence guard, which must still withhold.
        ("Would 2 crore be enough, given AED 750,000 is the sale price?", None),
        # Reordered, the QUESTION mark does not fire - it needs an
        # utterance-INITIAL auxiliary and this opens with "Given" - so
        # precedence 7 confirms. My first draft of this row expected None and
        # was wrong about which mark applies. Worth knowing that the two orders
        # differ, and that the difference leans to over-asking here, which is
        # the safe side; reported to god as an observation.
        ("Given AED 750,000 is the sale price, would 2 crore be enough?", 20_000_000.0),
    ],
)
def test_a_source_gates_only_its_own_sentence(vocabulary, said, expected):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


@pytest.mark.parametrize(
    "said,expected",
    [
        # P4, the DIRECT naming path inside the quote.
        ('They said, "our maximum is AED 2m."', None),
        ('The agent said, "my budget is AED 750,000."', None),
        # P4, the ANAPHORIC naming path inside the quote - the path that used to
        # walk round the gate entirely.
        ('They said, "AED 750,000, which is my budget."', None),
        ('The agent said, "AED 800,000, and that is my budget."', None),
        # Both paths still name from OUTSIDE the quotation.
        ('They said, "AED 750,000", and that is my budget.', 750_000.0),
        ('The agent said, "AED 800,000", which is my budget.', 800_000.0),
        # Unquoted reported speech, where the reporting comma is the extent.
        ("The agent said, I can afford 2m.", None),
        # And no reported speech at all: naming still beats source.
        ("The website says AED 750,000 is my budget", 750_000.0),
    ],
)
def test_both_naming_paths_consult_the_one_quotative_gate(
    vocabulary, said, expected
):
    mention = find_budget(said, vocabulary, "en")
    assert (None if mention is None else mention.value) == expected, said


def test_the_quotative_gate_is_a_single_choke_point(vocabulary):
    """The structural claim, asserted structurally rather than by example.

    Both naming paths must go through `naming_allowed`. If a future third path
    is added that does not, this fails - which is the property the last round's
    per-path patch did not have.
    """
    import inspect

    from ambassador import budget

    source = inspect.getsource(budget.find_budget)
    body = source[source.index("def has_naming("):]
    body = body[: body.index("\n    def ", 1)] if "\n    def " in body[1:] else body
    # Every `return True` inside has_naming is a naming decision, and each must
    # be guarded by the gate.
    decisions = [
        line for line in body.splitlines() if line.strip() == "return True"
    ]
    assert decisions, "has_naming no longer returns a positive decision"
    assert body.count("naming_allowed") >= len(decisions), (
        "a naming path does not consult naming_allowed: the quotative gate has "
        f"{body.count('naming_allowed')} consultations for {len(decisions)} "
        "positive decisions"
    )


# --- slip round 3: a coordinated clause, whatever heads its subject ----------
#
# `clause_start` used to accept a coordinated clause only when the token
# IMMEDIATELY after the conjunction was a nominal, so a determiner, possessive
# or adjective stack hid the boundary; the subject scan then reached back into
# the EARLIER clause, found the buyer's pronoun and exempted a seller. Both
# sides of that boundary are classes, so both are tested as classes.


@pytest.mark.parametrize(
    "subject",
    [
        "the agent",  # determiner
        "my agent",  # possessive
        "their agent",  # third-person possessive
        "the pushy agent",  # adjective
        "the very pushy senior agent",  # adjective stack
        "the broker",  # a noun this file does not list
        "a colleague of mine",  # unlisted noun behind an article
        "their sales rep",  # unlisted noun behind a possessive
    ],
)
@pytest.mark.parametrize("conjunction", ["but", "and"])
def test_a_coordinated_clause_moves_the_subject_whatever_heads_it(
    vocabulary, subject, conjunction
):
    """The seller side: the buyer's earlier pronoun must not reach across.

    The unlisted nouns are the point of the determiner half. Where the new
    subject is a noun the vocabulary does not know, the subject test finds no
    buyer and the figure stays a source, which is the safe direction.
    """
    said = f"I like it, {conjunction} {subject} said AED 750,000."
    assert find_budget(said, vocabulary, "en") is None, said


@pytest.mark.parametrize(
    "subject", ["I", "we"]
)
@pytest.mark.parametrize("conjunction", ["but", "and"])
def test_a_coordinated_clause_can_also_hand_the_turn_to_the_buyer(
    vocabulary, subject, conjunction
):
    """The buyer side of the same boundary, which must still be found."""
    said = f"The agent was pushy, {conjunction} {subject} said 2 crore."
    mention = find_budget(said, vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0, said


@pytest.mark.parametrize(
    "adverbs",
    [
        "repeatedly",  # -ly morphology
        "firmly",
        "quite deliberately",  # closed-list degree adverb plus -ly
        "very carefully",
        "then",  # closed-list connective adverb alone
        "again",
        "then again",
        "repeatedly and firmly",  # a conjunction joining two adverbs
    ],
)
def test_coordinated_adverbs_are_not_a_clause_boundary(vocabulary, adverbs):
    """The guard: no new subject opens, so the buyer's own subject still rules.

    This is the string the boundary must NOT move, and under the inverted test
    it is the ONLY thing that holds the boundary back, so it carries both
    adverbial markers: the -ly suffix and the closed list.
    """
    said = f"I clearly and {adverbs} said 2 crore."
    mention = find_budget(said, vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0, said


@pytest.mark.parametrize(
    "said",
    [
        "The agent called me and said AED 750,000.",
        "The agent read the listing and said AED 750,000.",
        "The broker rang and said AED 750,000.",
    ],
)
def test_a_shared_subject_keeps_the_earlier_subject(vocabulary, said):
    """The other guard: nothing opens between the conjunction and the verb.

    A bare shared subject is the SAME subject, so reaching back for it is
    correct. If the boundary moved here, the seller's own clause would lose its
    subject and the price would be confirmed as a budget.
    """
    assert find_budget(said, vocabulary, "en") is None, said


def test_the_clause_boundary_uses_no_token_count(vocabulary):
    """The structural claim: distance never re-enters through this door.

    Both slip rounds were caused by an approximation of a structure, and in
    both the approximation was a count. `clause_start` must stay countless.
    """
    import inspect

    from ambassador import budget

    source = inspect.getsource(budget.find_budget)
    body = source[source.index("def clause_start(") :]
    body = body[: body.index("\n    def ", 1)]
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    code = code.split('"""')[0] + code.split('"""')[-1]
    for smell in ("[-", "[:", "reversed(", "_TOKENS", "range("):
        assert smell not in code, (
            f"clause_start has grown a positional rule ({smell!r}); the "
            "boundary is a structure, not a distance"
        )


# --- slip round 4: the discriminator is inverted -----------------------------
#
# Rounds 2 and 3 both asked "is there a recognised subject here", and a noun is
# an OPEN class, so each round met one it did not know: a noun outside the
# roster, then a bare proper name, which takes no determiner either. The test is
# now the negative one, over the closed class: a conjunction heads a new clause
# UNLESS everything between it and the verb is affirmatively adverbial.


@pytest.mark.parametrize(
    "subject",
    [
        "Alice",  # bare proper name - takes no determiner at all
        "Ahmed",
        "Kelly",  # a proper name carrying the -ly suffix
        "Sally",
        "colleague",  # bare common noun, no determiner
        "rep",
        "agent",  # STT-style determiner drop on a noun we DO list
        "then Alice",  # adverb and noun mixed - not ALL adverbial
        "then the broker",
        "the agent",  # the round-3 forms must still hold
        "my agent",
        "the very pushy senior agent",
        "a colleague of mine",
        "their sales rep",
    ],
)
@pytest.mark.parametrize("conjunction", ["but", "and"])
def test_anything_not_wholly_adverbial_opens_a_new_clause(
    vocabulary, subject, conjunction
):
    """The seller side: unknown material means a NEW SUBJECT.

    A bare proper name is the case that broke both previous rounds. Nothing here
    needs to be recognised as a subject: it is enough that it is not adverbial.
    """
    said = f"I like it, {conjunction} {subject} said AED 750,000."
    assert find_budget(said, vocabulary, "en") is None, said


@pytest.mark.parametrize(
    "adverbs", ["then", "again", "also", "just", "clearly", "repeatedly"]
)
@pytest.mark.parametrize("conjunction", ["but", "and"])
def test_a_wholly_adverbial_filler_keeps_the_buyers_subject(
    vocabulary, adverbs, conjunction
):
    """The buyer side of the inverted test, in both markers.

    If this direction ever fails the inversion has swallowed the buyer, which is
    the cost side of defaulting unknown material to a new subject.
    """
    said = f"I looked at it {conjunction} {adverbs} said 2 crore."
    mention = find_budget(said, vocabulary, "en")
    assert mention is not None and mention.value == 20_000_000.0, said


def test_a_capitalised_ly_token_is_a_name_not_an_adverb(vocabulary):
    """Why the -ly marker is honoured only on an uncapitalised token.

    "Kelly" and "repeatedly" are morphologically identical. Reading the name as
    an adverb hands the seller's clause the buyer's subject and confirms the
    seller's figure, which is the unsafe direction, so capitalisation is
    consulted - and only here, where being wrong about it withholds.
    """
    assert find_budget("I like it, but Kelly said AED 750,000.", vocabulary, "en") is None
    buyer = find_budget("I like it, but quickly said 2 crore.", vocabulary, "en")
    assert buyer is not None and buyer.value == 20_000_000.0


@pytest.mark.parametrize(
    "filler",
    [
        "after much thought very carefully",  # a prepositional phrase
        "thereupon",  # an adverb this file does not list
        "REPEATEDLY",  # all-caps transcription of a real adverb
    ],
)
def test_residual_unlisted_non_ly_filler_withholds(vocabulary, filler):
    """Disclosed residual (d), and the price of the inverted default.

    Material that IS shared-subject but is neither -ly nor listed reads as a new
    subject, so the buyer's own figure is withheld. That is the safe direction -
    the confirmation flow asks again - and it is the direction the asymmetry
    chooses deliberately: the alternative default confirms a seller's price as
    the buyer's budget. Pinned so that widening the adverb list is a visible
    change rather than a drift.
    """
    said = f"I clearly and {filler} said 2 crore."
    assert find_budget(said, vocabulary, "en") is None, said


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RESIDUAL (e), god's call, and the cost of the adjudicated -ly "
        "refinement rather than a slip. Casing is the ONLY marker separating "
        "'kelly' from 'repeatedly', so an -ly proper name that reaches us "
        "uncapitalised reads as an adverb, the earlier clause's buyer subject "
        "leaks across the conjunction and the seller's figure confirms. It is "
        "the unsafe direction, and doubly conditioned: it needs an -ly name "
        "AND a casing failure in transcription, where ADR-017's default "
        "(Deepgram smart formatting) capitalises proper nouns. There is no "
        "structural close - distrusting uncapitalised -ly would gut every "
        "adverb stack, including the round-2 must-pass 'I very clearly and "
        "repeatedly said 2 crore'. Not closed by invention; disclosed."
    ),
)
def test_residual_a_lowercase_ly_name_reads_as_an_adverb(vocabulary):
    """The one input the capitalisation refinement cannot separate."""
    assert find_budget("I like it, but kelly said AED 750,000.", vocabulary, "en") is None
