from dataclasses import replace

import pytest
import yaml

from ambassador.figures import languages_covered, load_numerals
from ambassador.guardrails.numeric_claims import check_numeric_claims
from ambassador.guardrails.pipeline import process_sentence
from ambassador.inventory import DATA_DIR
from ambassador.schemas import GuardrailViolation


def test_inventory_figure_passes(allowed):
    assert check_numeric_claims("Skyrise starts at AED 985,000.", allowed) == []


def test_invented_figure_is_caught(allowed):
    violations = check_numeric_claims("It starts at AED 800,000.", allowed)
    assert len(violations) == 1
    assert violations[0].value == 800000.0


def test_invented_figure_caught_in_arabic_digits(allowed):
    assert check_numeric_claims("يبدأ السعر من ٩٨٥٬٠٠٠ درهم", allowed) == []
    violations = check_numeric_claims("يبدأ السعر من ٨٠٠٬٠٠٠ درهم", allowed)
    assert len(violations) == 1
    assert violations[0].value == 800000.0


def test_same_value_in_any_surface_form_passes(allowed):
    # 985,000 is allowed, so every surface form of it must pass (normaliser,
    # not the check, is what gets tuned when this fails)
    for text in ["985k thereabouts", "0.985 million", "985,000"]:
        assert check_numeric_claims(text, allowed) == [], text


def test_wrong_handover_year_is_caught(allowed):
    # 2025 appears in public portals for a project that hands over in 2026 -
    # the evidence exhibit
    violations = check_numeric_claims("Handover is in 2025.", allowed)
    assert [v.value for v in violations] == [2025.0]
    assert check_numeric_claims("Handover is Q4 2026.", allowed) == []


def test_unlisted_percentage_is_caught(allowed):
    assert check_numeric_claims("You pay 20% at booking.", allowed) == []
    violations = check_numeric_claims("You pay 35% at booking.", allowed)
    assert [v.value for v in violations] == [35.0]


def test_conversational_counts_are_exempt(allowed):
    assert (
        check_numeric_claims("It offers 3 bedrooms across 2 towers.", allowed) == []
    )


def test_whitelisted_figures_pass(allowed):
    assert (
        check_numeric_claims(
            "Properties above AED 2,000,000 may qualify; call 80015.", allowed
        )
        == []
    )


def test_crore_confusion_is_caught(allowed):
    # 24 lakh AED (2,400,000) is not in inventory, and neither is 2.4 crore
    # (24,000,000) - but the point is they normalise to DIFFERENT values and
    # each is independently checked
    v1 = check_numeric_claims("that is 24 lakh", allowed)
    v2 = check_numeric_claims("that is 2.4 crore", allowed)
    assert v1[0].value == 2400000.0
    assert v2[0].value == 24000000.0


# A currency token written flush against the digits used to defeat extraction
# entirely, which defeated the validator with it. Both shapes below reached TTS
# unchecked before the lookbehind in figures.py was narrowed to digits, commas
# and decimal points: "AED750,000" extracted nothing at all, and "AED1,985,000"
# restarted after the comma and extracted the allowed "985,000", so a fabricated
# price validated as a real one. These are the product's central claim, so they
# get one test per shape rather than a table.
def test_a_fabricated_price_flush_against_the_currency_is_caught(allowed):
    violations = check_numeric_claims("It starts at AED750,000.", allowed)
    assert [v.value for v in violations] == [750000.0]


def test_a_fabricated_price_is_not_validated_by_an_embedded_allowed_figure(allowed):
    # 985,000 IS allowed and is a substring of this fabricated 1,985,000.
    violations = check_numeric_claims("It starts at AED1,985,000.", allowed)
    assert [v.value for v in violations] == [1985000.0]


def test_a_real_price_flush_against_the_currency_still_passes(allowed):
    assert check_numeric_claims("It starts at AED985,000.", allowed) == []


def test_a_figure_is_never_extracted_from_inside_another_figure(allowed):
    # The comma is a number-internal character, not a place a new figure starts.
    from ambassador.figures import extract_figures

    surfaces = [m.figure.surface for m in extract_figures("1,985,000 and 2,400,000")]
    assert surfaces == ["1,985,000", "2,400,000"]


# --- the issue-#8 adversarial classes, end to end ---------------------------
#
# The review reproduced every case below through `process_sentence()`, which is
# what the buyer actually hears, and every one returned SpeakableText. Testing
# the validator's return value alone is how 352 tests passed while 11 fabricated
# claims were spoken, so these drive the public pipeline and assert on the type
# it returns.

BYPASSES = [
    # (language, sentence, why it must be blocked)
    ("en", "It starts at AED 12.", "twelve dirhams is not an inventory price"),
    ("en", "It starts at AED 2026.", "an allowed handover year is not a price"),
    ("en", "It starts at AED -985,000.", "negative 985,000 is not in the records"),
    ("en", "The discount is -20%.", "negative twenty per cent is another claim"),
    ("en", "It starts at AED .8 million.", "800,000 was never extracted at all"),
    ("en", "It starts at AED 8e5.", "800,000 split into two exempt counts"),
    ("en", "It starts at AED 8 × 10^5.", "exempt counts composed into 800,000"),
    ("en", "It starts at AED 8-million.", "8,000,000 kept only the exempt 8"),
    ("ar", "الدفعة هي ١٢٪.", "twelve per cent is not an allowed percentage"),
    ("en", "It starts at AED 380 000.", "380,000 validated through 380"),
    ("en", "It starts at AED 80015.", "the hotline number is not a price"),
    ("en", "It starts at AED 380.", "a square footage is not a price"),
]


@pytest.mark.parametrize(("language", "sentence", "why"), BYPASSES)
def test_a_reproduced_bypass_is_now_blocked(
    language, sentence, why, allowed, patterns, forms
):
    result = process_sentence(sentence, language, allowed, patterns, forms)
    assert isinstance(result, GuardrailViolation), f"{sentence!r}: {why}"
    assert result.validator == "numeric_claims"


def test_the_real_sentences_around_those_bypasses_still_pass(allowed, patterns, forms):
    # The fixes are worthless if they block correct replies - a validator that
    # blocks correct output gets switched off by the first engineer who hits it.
    for language, sentence in [
        ("en", "Skyrise starts at AED 985,000."),
        ("en", "It starts at AED985,000."),
        ("en", "You pay 20% at booking."),
        ("en", "Handover is Q4 2026."),
        ("en", "It offers 3 bedrooms across 2 towers."),
        ("en", "Properties above AED 2,000,000 may qualify; call 80015."),
        ("ar", "يبدأ السعر من ٩٨٥٬٠٠٠ درهم"),
    ]:
        result = process_sentence(sentence, language, allowed, patterns, forms)
        assert not isinstance(result, GuardrailViolation), sentence


# --- typed validation: a price is checked against money ---------------------


def test_a_price_no_longer_validates_against_a_square_footage(allowed):
    # 380 is a real allowed figure - a size_sqft_min - so the untyped set said
    # yes to a price of AED 380.
    assert 380.0 in allowed.amounts
    assert 380.0 not in allowed.currency_amounts
    violations = check_numeric_claims("It starts at AED 380.", allowed)
    assert [v.value for v in violations] == [380.0]


def test_a_price_no_longer_validates_against_the_hotline_number(allowed):
    # 80015 is whitelisted with kind: identifier, for the escalation path.
    assert 80015.0 in allowed.amounts
    assert 80015.0 not in allowed.currency_amounts
    violations = check_numeric_claims("It starts at AED 80015.", allowed)
    assert [v.value for v in violations] == [80015.0]


def test_a_quantity_or_an_identifier_still_passes_without_a_currency_token(allowed):
    # The kind split narrows what a PRICE may validate against; it does not
    # stop the agent stating a size or reading the hotline out.
    assert check_numeric_claims("Units run from 380 square feet.", allowed) == []
    assert check_numeric_claims("Call 80015 and ask for Dana.", allowed) == []


def test_an_empty_currency_set_blocks_rather_than_admits(allowed):
    # The safe direction, asserted: an under-populated currency set can only
    # block sentences, never speak an unverified figure.
    starved = replace(allowed, currency_amounts=frozenset())
    assert check_numeric_claims("It starts at AED 985,000.", starved)


# --- composed arithmetic ----------------------------------------------------


def test_composed_arithmetic_is_a_violation_without_being_computed(allowed):
    violations = check_numeric_claims("It starts at AED 8 × 10^5.", allowed)
    surfaces = [v.surface for v in violations]
    assert "8 × 10^5" in surfaces
    assert 800000.0 not in [v.value for v in violations]


# --- the localised-word gap, and the mechanism that closes it ---------------


def _numerals_with(tmp_path, language, **lists):
    """The shipped file with one language's word lists filled in.

    This is how the Arabic and Hindi mechanism is proved without anyone here
    authoring Arabic or Hindi (AGENTS.md: never write copy for a language you
    do not speak). The words below are the review report's own examples, used
    as TEST DATA against the extractor, and they are deliberately not written
    into data/numerals.yaml - that file's `ar` and `hi` lists stay `VERIFY:`
    until a native speaker fills them.
    """
    raw = yaml.safe_load(
        (DATA_DIR / "numerals.yaml").read_text(encoding="utf-8")
    )
    raw["languages"][language].update(lists)
    path = tmp_path / "numerals.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return load_numerals(path)


def test_a_localised_multiplier_word_is_a_disclosed_gap_today(allowed):
    # NOT a test that the behaviour is right - a test that the gap is exactly
    # where docs/03-guardrails.md says it is, and no wider. The magnitude lives
    # in a word nobody here may author, so the leading digit stays an exempt
    # count and the sentence is spoken. Delete this test when the VERIFY: data
    # lands; the two below must then be the whole story.
    assert "ar" not in languages_covered()
    assert "hi" not in languages_covered()
    assert check_numeric_claims("يبدأ السعر من ٨ مليون درهم.", allowed) == []
    assert check_numeric_claims("कीमत ८ करोड़ दिरहम है।", allowed) == []


def test_the_arabic_multiplier_mechanism_blocks_the_claim_once_data_exists(
    allowed, tmp_path
):
    numerals = _numerals_with(
        tmp_path, "ar", multipliers={"مليون": 1000000}, currency_words=["درهم"]
    )
    assert "ar" in languages_covered(numerals)
    violations = check_numeric_claims(
        "يبدأ السعر من ٨ مليون درهم.", allowed, numerals
    )
    assert [v.value for v in violations] == [8000000.0]


def test_the_hindi_multiplier_mechanism_blocks_the_claim_once_data_exists(
    allowed, tmp_path
):
    numerals = _numerals_with(
        tmp_path, "hi", multipliers={"करोड़": 10000000}, currency_words=["दिरहम"]
    )
    assert "hi" in languages_covered(numerals)
    violations = check_numeric_claims("कीमत ८ करोड़ दिरहम है।", allowed, numerals)
    assert [v.value for v in violations] == [80000000.0]


def test_a_native_currency_word_alone_kills_the_exemption_once_data_exists(
    allowed, tmp_path
):
    # The currency half of the same gap: with "درهم" known, a bare ٨ beside it
    # is money to be checked rather than a count of eight.
    numerals = _numerals_with(tmp_path, "ar", currency_words=["درهم"])
    violations = check_numeric_claims("السعر ٨ درهم.", allowed, numerals)
    assert [v.value for v in violations] == [8.0]
