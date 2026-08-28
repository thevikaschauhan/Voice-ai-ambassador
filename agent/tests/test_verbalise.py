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


# The currency token can sit either side of the digits. Observed live: the
# model wrote "985,000 AED" and the verbaliser, which only ever consumed an
# "AED " prefix, spoke "nine hundred and eighty-five thousand dirhams AED".
# The spoken form already names the currency, so exactly one of them survives.
_DIRHAMS = "nine hundred and eighty-five thousand dirhams"
_MILLION = "one point two million dirhams"

CURRENCY_CASES = [
    # (case name, written by the model, what TTS must receive)
    ("prefix", "The starting price is AED 985,000.", f"The starting price is {_DIRHAMS}."),
    ("suffix", "The starting price is 985,000 AED.", f"The starting price is {_DIRHAMS}."),
    ("bare amount", "The starting price is 985,000.", f"The starting price is {_DIRHAMS}."),
    # Casing is not something the model is held to, so neither is the match.
    ("lowercase suffix", "It starts at 985,000 aed.", f"It starts at {_DIRHAMS}."),
    ("lowercase prefix", "It starts at aed 985,000.", f"It starts at {_DIRHAMS}."),
    # The prompt asks for comma separators but does not always get them.
    ("suffix, no separators", "It starts at 985000 AED.", f"It starts at {_DIRHAMS}."),
    ("prefix, no separators", "It starts at AED 985000.", f"It starts at {_DIRHAMS}."),
    ("no separating space", "It starts at 985,000AED.", f"It starts at {_DIRHAMS}."),
    # Defensive: both sides at once must still speak the currency once.
    ("both sides", "It starts at AED 985,000 AED.", f"It starts at {_DIRHAMS}."),
    # Two amounts, one suffixed and one not: each is resolved on its own.
    (
        "two amounts, one suffixed",
        "The studio is 985,000 AED and the one-bed is 1,200,000.",
        f"The studio is {_DIRHAMS} and the one-bed is {_MILLION}.",
    ),
    # A currency token that is not adjacent to the figure is ordinary prose.
    (
        "non-adjacent currency word",
        "AED is the currency and the studio is 985,000.",
        f"AED is the currency and the studio is {_DIRHAMS}.",
    ),
]


@pytest.mark.parametrize(
    "written,expected",
    [pytest.param(written, expected, id=name) for name, written, expected in CURRENCY_CASES],
)
def test_currency_token_is_consumed_on_either_side(forms, written, expected):
    out = verbalise(ValidatedSentence(text=written, language="en"), forms)
    assert out.text == expected


@pytest.mark.parametrize(
    "written",
    [pytest.param(written, id=name) for name, written, _ in CURRENCY_CASES],
)
def test_no_currency_token_survives_next_to_a_spoken_amount(forms, written):
    """The regression in its own terms: never 'dirhams AED', in either order."""
    out = verbalise(ValidatedSentence(text=written, language="en"), forms)
    assert "dirhams AED" not in out.text
    assert "dirhams aed" not in out.text
    assert "AED dirhams" not in out.text


def test_currency_next_to_an_unknown_amount_is_left_alone(forms):
    """No spoken form means no replacement, so nothing is consumed either: the
    digits and the currency both survive for TTS to read."""
    out = verbalise(
        ValidatedSentence(text="A parking bay is 1,234 AED.", language="en"), forms
    )
    assert out.text == "A parking bay is 1,234 AED."


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
