import re
from pathlib import Path

import pytest

from ambassador.schemas import ValidatedSentence
from ambassador.verbalise import load_spoken_forms, verbalise


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

# A written currency token standing beside the spoken one, in either order.
# Word-bounded, so "dirhams aeder" is prose and "dirhams AED." is the bug.
_WRITTEN = r"(?:aed|dhs|dh|dirhams?)"
_DOUBLE_CURRENCY = re.compile(
    rf"\bdirhams?\b[ \t]*\b{_WRITTEN}\b|\b{_WRITTEN}\b[ \t]*\bdirhams?\b",
    re.IGNORECASE,
)

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
    # The written variants the token list covers besides AED. Without them the
    # spoken form's "dirhams" lands next to a written "dirhams".
    ("dhs prefix", "It starts at Dhs 985,000.", f"It starts at {_DIRHAMS}."),
    ("dh suffix", "It starts at 985,000 dh.", f"It starts at {_DIRHAMS}."),
    (
        "the word dirhams",
        "The price is 985,000 dirhams.",
        f"The price is {_DIRHAMS}.",
    ),
    # A token is a token, not the tail of a word. "\bAED\s*$" matched the last
    # three letters of "Sa'aed" - the apostrophe supplied the word boundary -
    # and the replacement ate the name down to "Sa'".
    (
        "the token is a word tail",
        "Sa'aed 985,000 is the price.",
        f"Sa'aed {_DIRHAMS} is the price.",
    ),
    ("the token is a word head", "It is 985,000 aeder.", f"It is {_DIRHAMS} aeder."),
    # The separating whitespace is spaces and tabs, never a line break: an AED
    # that opens the next clause belongs to that clause. "\s*" swallowed it.
    (
        "a line break ends the amount, suffix",
        "It costs 985,000\nAED conversion aside.",
        f"It costs {_DIRHAMS}\nAED conversion aside.",
    ),
    (
        "a line break ends the amount, prefix",
        "It costs AED\n985,000 today.",
        f"It costs AED\n{_DIRHAMS} today.",
    ),
    ("a tab separates", "It starts at 985,000\tAED.", f"It starts at {_DIRHAMS}."),
    # Arabic and Devanagari letters are word characters to Python's default \w,
    # so a token written flush against them had no boundary, survived, and put
    # the double-currency bug back in the two languages this team cannot
    # self-certify. The patterns are ASCII-mode, so an abutting non-Latin letter
    # does not block the match. The expectations look glued because the input is
    # glued: consuming a token that touches its neighbour leaves them touching.
    (
        "arabic script abuts the token, suffix",
        "السعر 985,000 AEDفقط.",
        f"السعر {_DIRHAMS}فقط.",
    ),
    (
        "arabic script abuts the token, prefix",
        "السعرAED 985,000 فقط.",
        f"السعر{_DIRHAMS} فقط.",
    ),
    (
        "devanagari script abuts the token",
        "कीमत 985,000 AEDहै.",
        f"कीमत {_DIRHAMS}है.",
    ),
]


@pytest.mark.parametrize(
    "written,expected",
    [pytest.param(written, expected, id=name) for name, written, expected in CURRENCY_CASES],
)
def test_currency_token_is_consumed_on_either_side(forms, written, expected):
    out = verbalise(ValidatedSentence(text=written, language="en"), forms)
    assert out.text == expected
    # The regression in its own terms, folded in from a test of its own. It
    # cannot fail while the equality above holds, so it is not coverage - it is
    # the tripwire for the one way this suite could go green on a regression:
    # someone pasting bad output into `expected`. Its token list is written out
    # here rather than read from `forms`, deliberately: a tripwire that shares
    # the production list stops being independent of it.
    assert not _DOUBLE_CURRENCY.search(out.text), (
        f"the currency is spoken twice: {out.text!r}"
    )


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


# --- the currency token is per-language data, not a literal in code -------
#
# It used to be the Latin "AED", compiled into this module, while every other
# language-specific speech artefact was data. A spoken form names the currency
# in its own language, so the written token it has to swallow is language
# specific too: the day the ar and hi forms are natively authored, a hard-coded
# Latin token would consume nothing beside them and the double-currency bug
# would come back in exactly those two languages.

_PLACEHOLDER = "AR-SPOKEN-FORM"


def _table(language: str, tokens: str, spoken: str = _PLACEHOLDER) -> str:
    return (
        f"{language}:\n"
        f"  currency_tokens: {tokens}\n"
        "  forms:\n"
        f'    - {{ kind: amount, value: 985000, spoken: "{spoken}" }}\n'
    )


def test_the_shipped_table_gives_currency_tokens_to_english_only(forms):
    """ar and hi carry an empty list under a VERIFY: marker in the data file.

    Empty is correct while their `forms` lists are empty too - nothing is
    replaced, so nothing needs consuming. It stops being correct the moment a
    native spoken form lands without its tokens, which is what the marker is
    there to catch.
    """
    assert set(forms.currency) == {"en"}


def test_a_language_consumes_the_tokens_its_own_table_names(tmp_path: Path):
    path = tmp_path / "spoken-forms.yaml"
    path.write_text(_table("ar", "[AED]"), encoding="utf-8")

    out = verbalise(
        ValidatedSentence(text="السعر 985,000 AED.", language="ar"),
        load_spoken_forms(path),
    )
    assert out.text == f"السعر {_PLACEHOLDER}."


def test_a_non_latin_token_is_consumed_too(tmp_path: Path):
    """The mechanism day 3 will need, probed on a temp file.

    The native spoken forms will name the currency in Arabic and the written
    token beside them will be an Arabic word, so the loaded list cannot be
    Latin-only. This proves it is not. It does NOT author anything:
    data/spoken-forms.yaml keeps its empty ar list and its VERIFY: marker, and
    only a native speaker resolves that.
    """
    path = tmp_path / "spoken-forms.yaml"
    path.write_text(_table("ar", '["درهم"]'), encoding="utf-8")

    out = verbalise(
        ValidatedSentence(text="السعر 985,000 درهم.", language="ar"),
        load_spoken_forms(path),
    )
    assert out.text == f"السعر {_PLACEHOLDER}."


def test_a_language_with_no_tokens_consumes_nothing(tmp_path: Path):
    """The regression this move exists to prevent, in the direction that bites.

    English's tokens must not leak into another language's replacement. With
    the token list held in code, the Latin AED beside an Arabic spoken form was
    consumed by a rule nobody had reviewed for Arabic; with it held in data, an
    empty list means the written token survives and is audible in a rehearsal.
    """
    path = tmp_path / "spoken-forms.yaml"
    path.write_text(_table("ar", "[]"), encoding="utf-8")

    out = verbalise(
        ValidatedSentence(text="السعر 985,000 AED.", language="ar"),
        load_spoken_forms(path),
    )
    assert out.text == f"السعر {_PLACEHOLDER} AED."


def test_case_variants_of_one_token_collapse(tmp_path: Path):
    """The match is case-insensitive, so Dhs and DHS are one alternative."""
    path = tmp_path / "spoken-forms.yaml"
    path.write_text(_table("en", "[Dhs, DHS]", "SPOKEN"), encoding="utf-8")
    forms = load_spoken_forms(path)

    assert forms.currency["en"].before.pattern.count("Dhs") == 1
    for written in ("985,000 dhs.", "985,000 DHS.", "Dhs 985,000."):
        out = verbalise(ValidatedSentence(text=written, language="en"), forms)
        assert out.text.rstrip(".") == "SPOKEN"


def test_an_empty_file_loads_to_an_empty_table(tmp_path: Path):
    path = tmp_path / "spoken-forms.yaml"
    path.write_text("", encoding="utf-8")
    forms = load_spoken_forms(path)

    assert forms.by_value == {} and forms.by_surface == {} and forms.currency == {}
    out = verbalise(ValidatedSentence(text="It is 985,000.", language="en"), forms)
    assert out.text == "It is 985,000."


def test_a_language_block_may_be_empty(tmp_path: Path):
    path = tmp_path / "spoken-forms.yaml"
    path.write_text("ar:\nhi:\n", encoding="utf-8")
    forms = load_spoken_forms(path)

    assert forms.currency == {}


def test_the_old_flat_list_shape_fails_with_a_message(tmp_path: Path):
    """The file used to map a language straight to its list of forms.

    Left unchecked, a file still in that shape reaches `.get` on a list and
    raises an AttributeError from inside the loader.
    """
    path = tmp_path / "spoken-forms.yaml"
    path.write_text(
        'en:\n  - { kind: amount, value: 985000, spoken: "x" }\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="'en' must map to 'currency_tokens'"):
        load_spoken_forms(path)
