"""Budget detection and the currency policy (ADR-011).

The failure under test is a twenty-times error delivered confidently: "do
crore ka budget hai" is two crore of something, and INR 2 crore is roughly AED
880k while AED 2 crore is 20 million. Until now the policy existed only as
prompt constraint 8, and ADR-007 is explicit that prompt instructions reduce
violation rates without eliminating them.

No framework import, so this runs in core-only mode with the rest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ambassador.budget import (
    BudgetPolicy,
    ConversionRate,
    ConversionUnavailable,
    find_budget,
    load_currency_vocabulary,
    read_reply,
    to_aed,
)

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def vocabulary():
    return load_currency_vocabulary()


def budget(vocabulary, text: str, language: str = "en"):
    return find_budget(text, vocabulary, language)


# --- the ten-times error the units themselves cause -----------------------


def test_lakh_and_crore_differ_by_ten_times(vocabulary):
    """The error this module is named after. 24 lakh is 2,400,000 and 2.4
    crore is 24,000,000, and confusing them is a whole extra digit."""
    assert budget(vocabulary, "Budget is 24 lakh.").value == 2_400_000
    assert budget(vocabulary, "Budget is 2.4 crore.").value == 24_000_000


@pytest.mark.parametrize(
    ("said", "value"),
    [
        ("2 crore", 20_000_000),
        ("1.5 million", 1_500_000),
        ("900k", 900_000),
        ("50 lakh", 5_000_000),
    ],
)
def test_multipliers_canonicalise(vocabulary, said, value):
    assert budget(vocabulary, f"My budget is {said}.").value == value


# --- the twenty-times error the CURRENCY causes ---------------------------


def test_an_unqualified_crore_is_reported_as_needing_a_currency(vocabulary):
    """The whole point. Two crore of what?"""
    mention = budget(vocabulary, "My budget is 2 crore.")
    assert mention.needs_currency
    assert mention.subcontinental_unit


@pytest.mark.parametrize(
    ("said", "currency"),
    [
        ("2 crore rupees", "INR"),
        ("rupees 2 crore", "INR"),
        ("2 crore dirhams", "AED"),
        ("AED 985,000", "AED"),
        ("985,000 aed", "AED"),
        ("₹ 20000000", "INR"),
        ("2 crore INR", "INR"),
    ],
)
def test_a_named_currency_binds_to_the_figure_on_either_side(
    vocabulary, said, currency
):
    assert budget(vocabulary, f"My budget is {said}.").currency == currency


def test_the_nearer_currency_wins(vocabulary):
    """A buyer correcting themselves puts the right word closest to the
    number, and taking the first match in table order would take the wrong
    one."""
    mention = budget(vocabulary, "Not dirhams - my budget is 2 crore rupees.")
    assert mention.currency == "INR"


# --- what is NOT a budget -------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "I need three bedrooms.",
        "We want 2 bathrooms and 3 bedrooms.",
        "Is it on floor 12?",
    ],
)
def test_counts_are_not_budgets(vocabulary, said):
    """Without this, every turn mentioning a bedroom count triggers a
    confirmation and the policy becomes noise the operator turns off."""
    assert budget(vocabulary, said) is None


def test_a_bare_number_counts_only_when_a_budget_keyword_is_near(vocabulary):
    assert budget(vocabulary, "I can spend 900000.") is not None
    assert budget(vocabulary, "900000 is my budget.") is not None
    assert budget(vocabulary, "Unit 900000 in the tower.") is None


def test_a_keyword_marks_only_the_figure_beside_it(vocabulary):
    """Whole-utterance keyword matching was a shipped defect: a budget word
    anywhere turned every later number into a budget."""
    assert (
        budget(vocabulary, "Our budget depends on my wife, who lands on flight 815.")
        is None
    )


def test_weak_keywords_are_not_budget_markers(vocabulary):
    """"Around" and "looking at" qualify positions and floors as easily as
    money; with them in the list, "I'm around floor 15" was a budget of 15."""
    assert budget(vocabulary, "I'm around floor 15.") is None
    assert budget(vocabulary, "We are looking at floor 20.") is None


def test_detection_needs_digits_and_says_so(vocabulary):
    """The recogniser dependency, pinned deliberately.

    Deepgram's `numerals=True` returns "2 crore"; the previous whole-utterance
    recogniser returned "two crore", which `extract_figures` cannot read. If
    the recogniser is ever swapped for one that spells numbers out, budget
    detection silently stops seeing anything, and this test is the note
    explaining why.
    """
    assert budget(vocabulary, "My budget is two crore.") is None
    assert budget(vocabulary, "My budget is 2 crore.") is not None


# --- conversion is refused, not approximated ------------------------------


def test_the_shipped_rate_is_unset_so_nothing_converts(vocabulary):
    """A made-up exchange rate spoken to a buyer is the same class of error as
    a made-up price: a specific, checkable, wrong number said confidently."""
    assert vocabulary.rate.usable is False
    with pytest.raises(ConversionUnavailable, match="route to a human"):
        to_aed(20_000_000, "INR", vocabulary.rate)


def test_dirhams_need_no_rate(vocabulary):
    assert to_aed(985_000, "AED", vocabulary.rate) == 985_000


def test_a_confirmed_rate_converts(vocabulary):
    rate = ConversionRate(inr_per_aed=23.0, as_of="2026-08-31", confirmed=True)
    assert to_aed(23_000_000, "INR", rate) == pytest.approx(1_000_000)


def test_a_confirmed_but_absent_rate_is_still_refused():
    """`confirmed: true` with no number is a configuration mistake, not
    permission to divide by nothing."""
    rate = ConversionRate(inr_per_aed=None, as_of="2026-08-31", confirmed=True)
    assert rate.usable is False
    with pytest.raises(ConversionUnavailable):
        to_aed(1_000, "INR", rate)


def test_an_unconfirmed_rate_is_refused_even_when_a_number_is_present():
    """Someone pasting a rate in without dating or vouching for it must not
    silently switch conversion on."""
    rate = ConversionRate(inr_per_aed=23.0, as_of=None, confirmed=False)
    assert rate.usable is False
    with pytest.raises(ConversionUnavailable):
        to_aed(1_000, "INR", rate)


# --- language coverage ----------------------------------------------------


def test_only_languages_with_authored_words_can_hear_a_currency(vocabulary):
    """An Arabic buyer naming a currency currently reads as unstated, so the
    agent asks. That is the safe direction and no worse than not asking - but
    it must be reported rather than assumed."""
    covered = vocabulary.languages_covered()
    assert "en" in covered
    for language in covered:
        assert vocabulary.words[language]["AED"] or vocabulary.words[language]["INR"]


def test_an_uncovered_language_still_detects_the_figure_and_asks(vocabulary):
    """Losing the currency word must not lose the budget: the amount is still
    found, and it is reported as needing a currency."""
    if "ar" in vocabulary.languages_covered():
        pytest.skip("Arabic currency words have been authored since")
    mention = budget(vocabulary, "2 crore", language="ar")
    assert mention is not None
    assert mention.needs_currency


# --- selection: which figure is THE budget ---------------------------------


def test_the_buyers_budget_beats_a_price_they_are_quoting(vocabulary):
    """"The villa is 5 million dirhams but I only want to spend 2 crore
    rupees" is a 2 crore budget. First-qualifying-figure selection returned
    the villa's price - in the wrong currency - as the buyer's budget."""
    mention = budget(
        vocabulary,
        "The villa is 5 million dirhams but I only want to spend 2 crore rupees.",
    )
    assert mention.value == 20_000_000
    assert mention.currency == "INR"


def test_a_units_own_surface_is_the_only_unit_that_counts(vocabulary):
    """The next figure's multiplier must not qualify this one: "Floor 15, 2
    million" once read as a budget of 15."""
    mention = budget(vocabulary, "Floor 15, 2 million.")
    assert mention.value == 2_000_000


def test_the_lakhs_plural_is_heard(vocabulary):
    mention = budget(vocabulary, "My budget is 24 lakhs.")
    assert mention.value == 2_400_000
    assert mention.subcontinental_unit


# --- negation: a denied currency is not a named one ------------------------


def test_a_negated_currency_does_not_bind_to_the_figure(vocabulary):
    """The docstring example that used to be false: "rupees, not dirhams, 2
    crore" bound to AED because "dirhams" sat nearer the number."""
    mention = budget(vocabulary, "Rupees, not dirhams, 2 crore.")
    assert mention.currency == "INR"


def test_read_reply_hears_denial_and_affirmation(vocabulary):
    reading = read_reply("Not dirhams, rupees.", vocabulary, "en")
    assert reading.affirmed == ("INR",)
    assert reading.denied == ("AED",)


def test_a_comma_separates_a_contradiction_from_the_answer(vocabulary):
    """"No, dirhams" affirms dirhams; "not dirhams" denies them."""
    reading = read_reply("No, dirhams.", vocabulary, "en")
    assert reading.affirmed == ("AED",)
    assert reading.contradicted


def test_uncertainty_reads_as_contradiction_not_consent(vocabulary):
    reading = read_reply("I'm not sure you heard me right.", vocabulary, "en")
    assert reading.contradicted
    assert not reading.affirmed


# --- the policy, driven the way a buyer drives it ---------------------------
#
# The review that blocked the first version of this policy found eight defects
# behind 26 green tests, and named the cause: everything asserted on Decision
# objects for single turns, and the three-turn conversation the feature is
# about was never exercised. These tests walk the state machine through whole
# exchanges and assert what each reply does to it.


def policy(vocabulary) -> BudgetPolicy:
    return BudgetPolicy(vocabulary, "en")


def test_a_reply_that_answers_nothing_is_asked_again_not_skipped(vocabulary):
    """The fail-open defect. The re-ask must carry the ORIGINAL mention, and
    that mention must carry the transcript it came from, or the adapter's
    echo check refuses its own question and the model answers unconfirmed."""
    p = policy(vocabulary)
    first = p.observe("My budget is 2 crore.")
    assert first.action == "ask_currency"

    again = p.observe("I did not catch that, can you repeat?")
    assert again.action == "ask_currency"
    assert again.mention.surface == "2 crore"
    # The invariant the adapter's compose() enforces, checkable right here:
    assert again.mention.surface in again.mention.utterance
    assert not p.settled


def test_no_to_a_read_back_is_never_consent(vocabulary):
    """The one mechanism that exists to catch a misheard number must not
    record an explicit rejection as agreement."""
    p = policy(vocabulary)
    assert p.observe("My budget is 5 million dirhams.").action == "confirm_amount"

    d = p.observe("No, that's wrong.")
    assert d.action == "ask_amount"
    assert not p.settled
    assert p.currency is None


def test_a_rejected_read_back_recovers_to_a_settled_budget(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    p.observe("No, that's wrong.")
    d = p.observe("It is 6 million dirhams.")
    assert d.action == "confirm_amount"
    assert d.mention.value == 6_000_000

    d = p.observe("Yes, that's right.")
    assert d.action == "none"
    assert d.currency == "AED"
    assert p.settled


def test_a_restated_budget_replaces_the_stale_one(vocabulary):
    """"Sorry, I meant 5 million dirhams" is about 5 million; settling the
    misheard 2 crore against it was a shipped defect."""
    p = policy(vocabulary)
    assert p.observe("My budget is 2 crore.").action == "ask_currency"

    d = p.observe("Sorry, I meant 5 million dirhams.")
    assert d.action == "confirm_amount"
    assert d.mention.value == 5_000_000
    assert d.mention.currency == "AED"

    d = p.observe("Correct.")
    assert p.settled and d.currency == "AED"


def test_a_correction_with_a_new_amount_restarts_the_confirmation(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    d = p.observe("No! 10 million.")
    # The new amount named no currency, so carrying the old one over would be
    # a guess; the policy asks instead.
    assert d.action == "ask_currency"
    assert d.mention.value == 10_000_000
    assert not p.settled


def test_denying_one_currency_names_the_other(vocabulary):
    """There are exactly two, so "not dirhams" is an answer. It settles INR,
    which without a confirmed rate goes to a human rather than converting."""
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    d = p.observe("Not dirhams.")
    assert d.action == "cannot_convert"
    assert d.currency == "INR"
    assert d.hands_over
    assert p.settled


def test_a_named_currency_settles_and_dirhams_need_no_handover(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    d = p.observe("Dirhams.")
    assert d.action == "none"
    assert d.currency == "AED"
    assert not d.hands_over
    assert p.settled


def test_three_unanswered_attempts_hand_over(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    assert p.observe("What?").action == "ask_currency"
    assert p.observe("Sorry?").action == "ask_currency"
    d = p.observe("The weather is lovely today.")
    assert d.action == "give_up"
    assert d.hands_over
    assert p.settled


def test_uncertainty_about_a_read_back_reopens_the_amount(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    d = p.observe("I'm not sure you heard me right.")
    assert d.action == "ask_amount"
    assert not p.settled


def test_a_repeat_without_a_currency_is_not_an_answer(vocabulary):
    """Echoing "2 crore" back at "dirhams or rupees?" answers nothing."""
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    d = p.observe("2 crore.")
    assert d.action == "ask_currency"
    assert not p.settled


def test_a_repeat_that_adds_the_currency_settles(vocabulary):
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    d = p.observe("2 crore rupees.")
    assert d.currency == "INR"
    assert p.settled


def test_abandon_settles_without_consent_and_the_policy_stays_silent(vocabulary):
    """The adapter calls this when a confirmation cannot be composed, AFTER
    routing the buyer to a human: the failure direction is closed, and a
    closed policy must never reopen and loop."""
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    p.abandon()
    assert p.settled
    assert p.observe("My budget is 2 crore.").action == "none"


# --- the second independent review's findings, pinned -----------------------
#
# Five classes reproduced through production code while 341 tests were green.
# Each test below is one of those reproductions, verbatim.


def test_a_contradiction_beats_a_currency_named_in_the_same_reply(vocabulary):
    """"No, dirhams" rejects the read-back; the dirhams is grammar, not
    consent. The currency used to win because it was read first."""
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    d = p.observe("No, dirhams.")
    assert d.action == "ask_amount"
    assert not p.settled


def test_uncertainty_about_a_currency_never_settles_it(vocabulary):
    """"I'm not sure about rupees" must not settle INR and hand over."""
    p = policy(vocabulary)
    p.observe("My budget is 2 crore.")
    d = p.observe("I'm not sure about rupees.")
    assert d.action == "ask_currency"
    assert not p.settled
    assert p.currency is None


def test_a_doubted_currency_out_of_negation_reach_still_reads_as_doubt(vocabulary):
    """"I don't think it was dirhams": the negator sits too far from the
    currency to deny it, but it is still a contradiction, never consent."""
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    d = p.observe("I don't think it was dirhams.")
    assert d.action == "ask_amount"
    assert not p.settled


def test_a_signal_free_reply_to_a_read_back_is_not_consent(vocabulary):
    """"Can you repeat that?" carries no signal at all. Consent must be said
    - it settled as agreement, which confuses a non-answer with a yes."""
    p = policy(vocabulary)
    p.observe("My budget is 5 million dirhams.")
    d = p.observe("Can you repeat that?")
    assert d.action == "confirm_amount"
    assert not p.settled

    d = p.observe("Yes, that's right.")
    assert p.settled and d.currency == "AED"


def test_a_currency_does_not_reach_across_a_clause_to_another_figure(vocabulary):
    """The deposit's AED must not resolve the crore budget: that recreates
    the 20x error the whole policy exists to prevent."""
    mention = budget(
        vocabulary, "My budget is 2 crore; I have AED 500,000 saved for the deposit."
    )
    assert mention.surface == "2 crore"
    assert mention.needs_currency


def test_a_keyword_tie_goes_to_the_figure_it_precedes(vocabulary):
    """"The price is AED 985,000 and my budget is AED 2,000,000": the word
    "budget" sits at the same gap from both figures, and the tie must go to
    the buyer's number, not the quoted price."""
    mention = budget(
        vocabulary, "The price is AED 985,000 and my budget is AED 2,000,000."
    )
    assert mention.value == 2_000_000


def test_a_currency_in_the_next_sentence_does_not_bind(vocabulary):
    """Nearest-figure ownership alone cannot save this one: the dirhams is
    the crore's nearest figure AND inside the distance window, and only the
    sentence boundary says it belongs to different talk."""
    mention = budget(
        vocabulary, "My budget is 2 crore. My salary is paid in dirhams."
    )
    assert mention.surface == "2 crore"
    assert mention.needs_currency


def test_a_keyword_in_the_previous_sentence_does_not_mark(vocabulary):
    mention = budget(vocabulary, "I went over our budget. Floor 15 is fine.")
    assert mention is None


# --- issue #25: figures the buyer quotes, and units that never fired -------
#
# Both defects were re-verified by execution on fdaf1d6 before anything here
# was written, with the exact strings from the issue's status comment. The
# direction of the F3 fix is narrow on purpose: ADR-011's
# first-mention-always-confirmed stance is deliberate and over-asking stays the
# safe direction, so only a figure the buyer ATTRIBUTES to us or to a listing
# is withheld - never one they merely stated plainly.


@pytest.mark.parametrize(
    "said",
    [
        # The two repro strings from the status comment, verbatim.
        "the listing says AED 750,000",
        "it starts from about 3 million",
        # Ordinary variants of the same two shapes, so the fix is the class and
        # not the examples.
        "your website says 985,000 dirhams",
        "you said 2 crore on the call",
        "I saw AED 750,000 online",
        "the brochure quotes 3 million",
        "the price is AED 985,000",
        "it is priced at 2 million",
        "prices start at AED 750,000",
    ],
)
def test_a_figure_the_buyer_attributes_elsewhere_is_not_their_budget(
    vocabulary, said
):
    """F3. A quoted price took the turn from the model, so the buyer's actual
    question went unanswered - and once confirmed it settled the policy, which
    suppressed every real budget mention for the rest of the session."""
    assert budget(vocabulary, said) is None, said


@pytest.mark.parametrize(
    "said,value",
    [
        # Plainly stated, no attribution: still confirmed on the first
        # mention, which is ADR-011's deliberate stance.
        ("we are looking at around 985,000 dirhams", 985_000.0),
        ("my budget is 750,000 dirhams", 750_000.0),
        ("2 crore", 20_000_000.0),
        ("about 3 million", 3_000_000.0),
        # A budget keyword outranks attribution, so the buyer's own number
        # still wins even in the same breath as a quoted one.
        ("the listing says AED 750,000 but my budget is 2 million", 2_000_000.0),
        ("you said 3 million, but I can only spend 2 crore", 20_000_000.0),
    ],
)
def test_the_fix_does_not_cost_us_a_real_budget(vocabulary, said, value):
    """The dangerous direction. A missed budget is an unconfirmed twenty-times
    risk, where an extra read-back costs one question."""
    mention = budget(vocabulary, said)
    assert mention is not None, said
    assert mention.value == value


def test_attribution_does_not_reach_across_a_clause_break(vocabulary):
    """The same ownership rule the currency and keyword binds already use: the
    quoted price's framing belongs to the quoted price."""
    mention = budget(vocabulary, "The price is AED 985,000. My budget is 2 crore.")
    assert mention is not None
    assert mention.value == 20_000_000.0


def test_a_quoted_price_does_not_suppress_a_later_real_budget(vocabulary):
    """The consequence that made F3 worth fixing rather than tolerating.

    A confirmed quoted price settles the policy, and a settled policy never
    speaks again - so the buyer's real budget, stated two turns later, was
    never confirmed and never questioned.
    """
    policy = BudgetPolicy(vocabulary, "en")
    assert not policy.observe("the listing says AED 750,000").speaks
    assert not policy.settled

    decision = policy.observe("my budget is 2 crore")
    assert decision.action == "ask_currency"
    assert decision.mention is not None
    assert decision.mention.value == 20_000_000.0


@pytest.mark.parametrize(
    "said,value",
    [
        # The third repro line from the status comment: no keyword near it, so
        # before the fix the mention was never built at all.
        ("around 800k", 800_000.0),
        ("800k", 800_000.0),
        ("2m", 2_000_000.0),
        ("I was thinking 1.5m", 1_500_000.0),
        # And the keyword-marked case that always worked, so the fix is
        # additive rather than a swap.
        ("my budget is 800k", 800_000.0),
    ],
)
def test_a_folded_unit_makes_a_figure_budget_like(vocabulary, said, value):
    """F4. `extract_figures` folds the multiplier into the surface ("800k"),
    and `\\bk\\b` cannot match inside it - there is no word boundary between
    "0" and "k". The unit test therefore has to allow a unit sitting directly
    against the digits."""
    mention = budget(vocabulary, said)
    assert mention is not None, said
    assert mention.value == value


def test_every_money_sized_unit_in_the_data_file_is_reachable():
    """The trap class AGENTS.md documents: a regex that looks live and never
    fires. The unit list was hand-kept "in step with" data/numerals.yaml, and
    two of its ten tokens could not match anything the extractor produces.

    Deriving the pattern from the same table the extractor uses means a
    multiplier added to the data file cannot be silently unreachable here, and
    this test fails if the derivation is ever replaced by a literal list again.
    """
    from ambassador.budget import _budget_units
    from ambassador.figures import default_numerals

    pattern = _budget_units()
    money = {
        word: factor
        for word, factor in default_numerals().multipliers.items()
        if factor >= 1000
    }
    assert money, "no money-sized multipliers in the data file at all"
    unreachable = [
        word
        for word in money
        # Both shapes the extractor can produce: folded against the digits
        # ("800k") and spaced ("3 million").
        if not (pattern.search(f"800{word}") and pattern.search(f"3 {word}"))
    ]
    assert not unreachable, (
        "these multipliers are in data/numerals.yaml and cannot make a figure "
        f"budget-like: {unreachable}"
    )


def test_units_below_money_size_are_still_not_budget_markers(vocabulary):
    """"Two hundred" is a count, not a sum. The threshold is what keeps a
    bedroom count out, and dropping it would make every small number a
    budget."""
    from ambassador.budget import _budget_units

    assert not _budget_units().search("3 hundred")
    assert not _budget_units().search("200")


@pytest.mark.parametrize(
    "said",
    [
        # The same F3 class arriving with no attribution word at all: the buyer
        # is ASKING what the price is. Found by the eval-runner's own guardrail
        # fixtures, which use this shape as a vehicle and broke the moment
        # "750k" became budget-like - the tests were right and the reading was
        # wrong.
        "Is it 750k?",
        "is that 3 million?",
        "is this 2 crore?",
        "Is the price 985,000 dirhams?",
    ],
)
def test_a_question_about_a_figure_is_not_an_offer_of_one(vocabulary, said):
    assert budget(vocabulary, said) is None, said


@pytest.mark.parametrize(
    "said,value",
    [
        # Attribution binds FORWARD only, and these are why. A marker sitting
        # just after the buyer's own number would otherwise withhold a real
        # budget, which is the expensive direction.
        ("I can do 2 million, is that ok?", 2_000_000.0),
        ("I have 2 crore, what is the price?", 20_000_000.0),
        ("my budget is 800k, is that enough?", 800_000.0),
    ],
)
def test_a_marker_after_the_figure_does_not_withhold_it(vocabulary, said, value):
    mention = budget(vocabulary, said)
    assert mention is not None, said
    assert mention.value == value


def test_a_quoted_figure_the_buyer_adopts_is_still_their_budget(vocabulary):
    """The boundary of the F3 rule, and the regression it would otherwise have
    introduced: this returns 750,000 both before and after the change.

    "The listing says AED 750,000 and that is my budget" is attribution and
    adoption in one sentence. The keyword's own marking window is too narrow to
    reach back this far - that is a separate, pre-existing calibration - so the
    override uses a wider reach, which is safe because it can only ever RESTORE
    a mention, never invent one on the wrong figure.
    """
    mention = budget(
        vocabulary, "the listing says AED 750,000 and that is my budget"
    )
    assert mention is not None
    assert mention.value == 750_000.0


def test_the_attribution_window_does_not_reach_the_next_clause(vocabulary):
    """The window is 14 because the measured attributed phrasings all bind
    within 10, and the nearest phrasing that must NOT bind sits at 23. A wider
    window swallows the buyer's own number later in the sentence."""
    mention = budget(vocabulary, "the price is too high, I can do 2 million")
    assert mention is not None
    assert mention.value == 2_000_000.0


def test_only_the_clause_break_stops_this_attribution(vocabulary):
    """Same rule the currency and keyword binds already use, and here it is the
    ONLY defence: "price" sits 7 characters from "800k", well inside the
    window, and belongs to the sentence before it."""
    mention = budget(vocabulary, "The price is high. 800k is mine.")
    assert mention is not None
    assert mention.value == 800_000.0


def test_a_unit_too_small_to_be_money_never_marks_a_budget():
    """The threshold guards a data file that does not exercise it today: every
    multiplier shipped is thousand-sized or larger, so only an explicit table
    can prove the rule. `budget_unit_pattern` takes its vocabulary as an
    argument precisely so this is testable without editing data/numerals.yaml.
    """
    from dataclasses import replace

    from ambassador.budget import budget_unit_pattern
    from ambassador.figures import default_numerals

    numerals = replace(
        default_numerals(),
        multipliers={"hundred": 100.0, "dozen": 12.0, "thousand": 1000.0},
    )
    pattern = budget_unit_pattern(numerals)
    assert pattern.search("3 thousand")
    assert not pattern.search("3 hundred"), "a count is not a sum"
    assert not pattern.search("2 dozen")


# --- PR #44 review: four reproduced findings -------------------------------
#
# Meredith's exact strings, each written before its fix. Two of these are
# regressions the first attempt INTRODUCED - it withheld real budgets - which
# is the direction the attribution rule was designed to protect.


@pytest.mark.parametrize(
    "said",
    [
        # One attribution token owned only its NEAREST figure, so the second
        # price in a quoted list or range walked straight past it.
        "The listing says AED 750,000 or AED 800,000.",
        "You quoted AED 750,000 to AED 900,000.",
        "Prices start at AED 750,000 and go up to AED 900,000.",
        # Three figures, in case two was the accident.
        "The listing says AED 750,000, AED 800,000 or AED 900,000.",
    ],
)
def test_a_quoted_range_is_attributed_whole(vocabulary, said):
    """Finding 1. Attribution is a property of the CLAUSE, not of one figure:
    a listing that quotes two prices is quoting both of them.

    Note the third string: "up to" is a budget keyword, and letting a generic
    keyword rescue a figure inside a quoted range is what made the old
    character-distance contest wrong.
    """
    assert budget(vocabulary, said) is None, said


@pytest.mark.parametrize(
    "said,value",
    [
        # Finding 2. A natural modifier puts "my budget" outside a 30-character
        # window while a source word sits inside 14. Both of these are
        # confirmed on the base commit and were lost by the first attempt.
        ("My budget after checking your website is AED 750,000.", 750_000.0),
        ("My price range after checking the listing is AED 750,000.", 750_000.0),
        # Finding 3. A generic interrogative opener cannot tell a question
        # about the seller's price from a question about whether the buyer's
        # own amount is enough. These state a budget interrogatively.
        ("Is that AED 800,000 enough for me?", 800_000.0),
        ("Is this AED 800,000 enough for a studio?", 800_000.0),
        ("Is that 2 crore enough for me?", 20_000_000.0),
    ],
)
def test_the_buyer_s_own_budget_survives_a_source_word(vocabulary, said, value):
    """Findings 2 and 3, which are one mistake: withholding is decided by
    distance and by a generic opener rather than by who owns the figure.

    A missed budget is an unconfirmed twenty-times risk. First-person
    ownership - "my budget", "I can spend", "enough for me" - outranks any
    source word in the same clause, at any distance.
    """
    mention = budget(vocabulary, said)
    assert mention is not None, said
    assert mention.value == value


@pytest.mark.parametrize(
    "said",
    [
        # Finding 4. "m" is the metre abbreviation in the exact domain this
        # agent sells in, and F4 made a folded unit sufficient on its own - so
        # a balcony took the deterministic budget turn.
        "I need a 2m wide balcony.",
        "The room is 2m wide.",
        "Is the ceiling 3m high?",
        "I want a 2m deep terrace",
    ],
)
def test_a_physical_measurement_is_not_a_budget(vocabulary, said):
    assert budget(vocabulary, said) is None, said


@pytest.mark.parametrize(
    "said,value",
    [
        # And the F4 headline still holds: a folded unit with no dimension
        # word about it is money.
        ("around 800k", 800_000.0),
        ("around 2m", 2_000_000.0),
        ("my budget is 2m", 2_000_000.0),
        ("2m dirhams", 2_000_000.0),
    ],
)
def test_a_folded_unit_is_still_money_without_a_dimension(vocabulary, said, value):
    mention = budget(vocabulary, said)
    assert mention is not None, said
    assert mention.value == value
