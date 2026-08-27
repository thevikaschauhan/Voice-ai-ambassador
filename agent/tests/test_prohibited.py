from ambassador.guardrails.prohibited import check_prohibited


def test_guarantee_language_is_caught(patterns):
    for text in [
        "This offers a guaranteed 8% rental yield.",
        "Returns are guaranteed by the developer.",
        "It is a risk-free investment.",
        "You can't lose with Business Bay.",
    ]:
        assert check_prohibited(text, patterns), text


def test_advice_and_certainty_are_caught(patterns):
    for text in [
        "You should buy this now.",
        "I recommend you invest in Skyrise.",
        "Prices will rise after handover.",
        "This area is certain to appreciate.",
    ]:
        assert check_prohibited(text, patterns), text


def test_regulatory_overreach_is_caught(patterns):
    assert check_prohibited(
        "Your visa approval is guaranteed at this price.", patterns
    )
    assert check_prohibited("You will get the golden visa.", patterns)


def test_composed_factual_language_passes(patterns):
    for text in [
        "The payment plan asks for 20% at booking.",
        "Handover is planned for Q4 2026.",
        "Many buyers appreciate the Business Bay location.",
        "I can connect you with an ambassador to discuss terms.",
    ]:
        assert check_prohibited(text, patterns) == [], text
