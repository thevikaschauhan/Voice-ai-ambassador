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
