from ambassador.figures import (
    extract_figures,
    find_composed_arithmetic,
    normalise_digits,
    states_a_figure,
)


def _values(text):
    return [(m.figure.kind, m.figure.value) for m in extract_figures(text)]


def test_separator_and_suffix_forms_normalise_to_one_value():
    for text in ["975,000", "975000", "975k", "0.975 million"]:
        assert _values(text) == [("amount", 975000.0)], text


def test_arabic_indic_digits_are_extracted():
    assert normalise_digits("٩٧٥٬٠٠٠") == "975,000"
    assert _values("السعر ٩٧٥٬٠٠٠ درهم") == [("amount", 975000.0)]


def test_devanagari_digits_are_extracted():
    assert _values("९८५०००") == [("amount", 985000.0)]


def test_lakh_and_crore_differ_by_10x():
    (lakh,) = _values("24 lakh")
    (crore,) = _values("2.4 crore")
    assert lakh == ("amount", 2400000.0)
    assert crore == ("amount", 24000000.0)
    assert lakh[1] * 10 == crore[1]


def test_percent_and_year_classification():
    assert _values("20%") == [("percent", 20.0)]
    assert _values("a 20 percent deposit") == [("percent", 20.0)]
    assert _values("handover in 2026") == [("year", 2026)]


def test_small_integers_are_counts():
    assert _values("it has 3 bedrooms and 2 bathrooms") == [
        ("count", 3.0),
        ("count", 2.0),
    ]


def test_quarter_reference_yields_a_checkable_year():
    values = _values("handover is Q4 2026")
    assert ("year", 2026) in values


# --- the issue-#8 adversarial classes ---------------------------------------
#
# Every case below is a reproduced bypass from the issue-#8 review: reachable
# text whose figures were extracted as something other than what the sentence
# claims, so the validator checked the wrong value, the wrong kind, or nothing
# at all. They get one test per class, with the class named, because the class
# is what a future edit will re-open.


def test_a_currency_token_makes_a_small_integer_an_amount():
    # "AED 12" is a price of twelve dirhams. Classified as a conversational
    # count it was exempt, and the documented claim that a small integer
    # "cannot state a price" was disproved by the sentence itself.
    assert _values("It starts at AED 12.") == [("amount", 12.0)]
    assert _values("It offers 12 units.") == [("count", 12.0)]


def test_a_currency_token_makes_a_four_digit_integer_an_amount_not_a_year():
    # 2026 is an allowed handover year, so "AED 2026" validated against the
    # year set and was spoken as a price.
    assert _values("It starts at AED 2026.") == [("amount", 2026.0)]
    assert _values("Handover is in 2026.") == [("year", 2026.0)]


def test_a_currency_token_is_recognised_on_either_side_and_flush():
    for text in ["AED 12", "12 AED", "AED12", "12AED", "12 dirhams", "$12", "12₹"]:
        (kind, value) = _values(text)[0]
        assert (kind, value) == ("amount", 12.0), text


def test_a_currency_token_must_be_a_whole_word():
    # "dh" is a currency token; "dhow" is a boat. A substring match here would
    # turn ordinary prose into prices.
    assert _values("12 dhow trips") == [("count", 12.0)]
    assert _values("berth 12 PAED") == [("count", 12.0)]


def test_a_leading_minus_is_part_of_the_figure():
    # The sign was outside the match, so a negative figure was checked against
    # its allowed positive counterpart and then spoken WITH the sign.
    assert _values("It starts at AED -985,000.") == [("amount", -985000.0)]
    assert _values("The discount is -20%.") == [("percent", -20.0)]
    assert _values("It is −985,000.") == [("amount", -985000.0)]


def test_a_negative_figure_keeps_no_exemption():
    # -5 is not a conversational count and -2026 is not a year: both are
    # checkable claims, and both fall out of the exemptions by value alone.
    assert _values("-5") == [("amount", -5.0)]
    assert _values("-2026") == [("amount", -2026.0)]


def test_a_hyphen_between_two_figures_stays_a_range():
    # The sign must not eat a range mark: "985,000-1,200,000" is two positive
    # figures, and "2026-2027" is two years, not a year and a negative one.
    assert _values("AED 985,000-1,200,000") == [
        ("amount", 985000.0),
        ("amount", 1200000.0),
    ]
    assert _values("between 2026-2027") == [("year", 2026.0), ("year", 2027.0)]


def test_a_leading_decimal_extracts_its_intended_value():
    # ".8 million" matched nothing at all, so 800,000 was never checked.
    assert _values("It starts at AED .8 million.") == [("amount", 800000.0)]


def test_an_exponent_is_one_figure_not_two_counts():
    # "8e5" split into exempt counts 8 and 5 while the sentence said 800,000.
    assert _values("It starts at AED 8e5.") == [("amount", 800000.0)]
    assert _values("8E5") == [("amount", 800000.0)]
    assert _values("8e-2") == [("amount", 0.08)]


def test_a_hyphen_joined_multiplier_extracts_its_intended_value():
    # "8-million" kept only the exempt 8.
    assert _values("It starts at AED 8-million.") == [("amount", 8000000.0)]


def test_unicode_group_separators_extract_to_one_value():
    # The AED1,985,000 class again, one separator later: U+202F split the
    # surface into an allowed square footage of 380 and an exempt 000, so a
    # fabricated 380,000 validated through a smaller figure that really exists.
    for separator in (" ", " ", " "):
        assert _values(f"It starts at AED 380{separator}000.") == [
            ("amount", 380000.0)
        ], repr(separator)


def test_an_ordinary_space_is_not_a_group_separator():
    # Making it one would fuse unrelated figures: this must stay two counts.
    assert _values("3 bedrooms and 2 towers") == [("count", 3.0), ("count", 2.0)]


def test_a_no_break_space_is_not_swallowed_into_a_surface():
    # A separator only joins DIGITS. Otherwise the span would cover the space
    # and verbalisation would replace it along with the figure.
    (match,) = extract_figures("3 bedrooms")
    assert match.figure.surface == "3"


def test_the_arabic_percent_sign_is_a_percentage():
    # U+066A is language-neutral punctuation, so it needs no native reviewer:
    # without it "١٢٪" was an exempt count of twelve.
    assert _values("الدفعة هي ١٢٪.") == [
        ("percent", 12.0)
    ]
    for symbol in ("%", "٪", "％", "﹪"):
        assert _values(f"12{symbol}") == [("percent", 12.0)], repr(symbol)


def test_composed_arithmetic_is_reported_and_never_computed():
    # "8 × 10^5" is three individually exempt integers that together state
    # 800,000. The run is reported as one unverifiable surface; the value on it
    # is the leading operand, not the product, because nothing here multiplies.
    (composed,) = find_composed_arithmetic("It starts at AED 8 × 10^5.")
    assert composed.composed is True
    assert composed.figure.surface == "8 × 10^5"
    assert composed.figure.value == 8.0
    assert composed.figure.value != 800000.0


def test_composed_arithmetic_covers_a_lone_operator_and_a_superscript():
    assert [m.figure.surface for m in find_composed_arithmetic("AED 8*5")] == ["8*5"]
    assert [m.figure.surface for m in find_composed_arithmetic("AED 10⁵")] == [
        "10⁵"
    ]
    assert [m.figure.surface for m in find_composed_arithmetic("AED 8 · 5")] == [
        "8 · 5"
    ]


def test_an_operator_joined_to_no_figure_is_not_composed_arithmetic():
    assert find_composed_arithmetic("see the note marked * below") == []
    assert find_composed_arithmetic("* and 5 apart") == []
    assert find_composed_arithmetic("no digits here ×") == []


def test_latin_x_is_deliberately_not_an_arithmetic_operator():
    # It is the ordinary dimension separator, so blocking it would cost real
    # sentences rather than nothing. The caret in "8 x 10^5" still catches that
    # shape.
    assert find_composed_arithmetic("units are 2 x 3 metres") == []
    assert [m.figure.surface for m in find_composed_arithmetic("AED 8 x 10^5")] == [
        "10^5"
    ]


# --- states_a_figure: does this sentence assert a figure at all? ----------
#
# The regeneration backstop's whole input (issue #33). A regenerated reply that
# states no figure has refused, and a refusal promises a colleague; a reply that
# states one corrected itself. Getting the count exemption wrong here either
# pages an ambassador on every "there are 2 layouts" or lets a refusal through
# unrouted.


def test_a_sentence_with_no_digits_states_no_figure():
    assert not states_a_figure("I do not have that project in our current listings.")
    assert not states_a_figure("")


def test_an_amount_is_a_figure():
    assert states_a_figure("Binghatti Skyrise starts from AED 985,000.")


def test_a_year_is_a_figure():
    assert states_a_figure("Handover is Q4 2026.")


def test_a_percent_is_a_figure():
    assert states_a_figure("The down payment is 20%.")


def test_a_conversational_count_is_not_a_figure():
    """The documented 0-12 exemption. "There are 2 layouts" answers nothing
    about money, so it must not read as a corrected figure."""
    assert not states_a_figure("There are 2 layouts and 3 towers.")


def test_a_currency_token_makes_even_a_small_integer_a_figure():
    """`_classify` voids the count exemption beside a currency token, so the
    sentence that priced a studio in dirhams cannot hide behind it."""
    assert states_a_figure("It starts at AED 12.")


def test_arabic_indic_digits_state_a_figure_too():
    """The predicate runs on the normalising extractor, not on ASCII digits, or
    the backstop would read every Arabic reply as a refusal."""
    assert states_a_figure("يبدأ سعر المشروع من ٩٨٥٬٠٠٠ درهم.")
