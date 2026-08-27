import pytest

from ambassador.schemas import ValidatedSentence
from ambassador.verbalise import verbalise


def test_known_amount_speaks_from_the_table(forms):
    out = verbalise(
        ValidatedSentence(text="The starting price is AED 985,000.", language="en"),
        forms,
    )
    assert out.text == (
        "The starting price is nine hundred and eighty-five thousand dirhams."
    )
    assert "AED" not in out.text  # the currency word moved into the spoken form


def test_quarter_is_spoken_not_spelled(forms):
    out = verbalise(
        ValidatedSentence(text="Handover is Q4 2026.", language="en"), forms
    )
    assert out.text == "Handover is the fourth quarter of 2026."


def test_percent_speaks_per_locale(forms):
    out = verbalise(
        ValidatedSentence(text="You pay 20% at booking.", language="en"), forms
    )
    assert out.text == "You pay twenty per cent at booking."


def test_unknown_value_falls_back_to_digits(forms):
    out = verbalise(
        ValidatedSentence(text="The tower has 1,234 windows.", language="en"), forms
    )
    assert "1,234" in out.text


def test_language_without_a_table_keeps_digits(forms):
    # Arabic table is empty until natively authored (VERIFY: day 3);
    # digits are normalised to western form and left for TTS
    out = verbalise(
        ValidatedSentence(text="السعر ٩٨٥٬٠٠٠ درهم", language="ar"), forms
    )
    assert "985,000" in out.text


def test_verbalise_rejects_unvalidated_text(forms):
    # The ordering guarantee: text that has not passed guardrails cannot
    # be verbalised
    with pytest.raises(TypeError, match="ValidatedSentence"):
        verbalise("It starts at AED 800,000.", forms)
