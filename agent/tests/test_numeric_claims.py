from ambassador.guardrails.numeric_claims import check_numeric_claims


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
