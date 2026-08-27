from ambassador.figures import extract_figures, normalise_digits


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
