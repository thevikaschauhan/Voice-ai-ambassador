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
    ConversionRate,
    ConversionUnavailable,
    find_budget,
    load_currency_vocabulary,
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


def test_a_bare_number_counts_only_when_a_budget_keyword_is_present(vocabulary):
    assert budget(vocabulary, "I am looking at 900000.") is not None
    assert budget(vocabulary, "Unit 900000 in the tower.") is None


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
