from ambassador.guardrails.prohibited import (
    check_prohibited,
    languages_covered,
    load_patterns,
)


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
    assert check_prohibited("Your visa approval is guaranteed at this price.", patterns)
    assert check_prohibited("You will get the golden visa.", patterns)


def test_composed_factual_language_passes(patterns):
    for text in [
        "The payment plan asks for 20% at booking.",
        "Handover is planned for Q4 2026.",
        "Many buyers appreciate the Business Bay location.",
        "I can connect you with an ambassador to discuss terms.",
    ]:
        assert check_prohibited(text, patterns) == [], text


# --- what `language` means, and the coverage it does not have ---------------
#
# The field was loaded and never read, which read as if the validator were
# language-aware and merely under-populated. It is neither: every pattern runs
# against every sentence, and `language` records who was competent to write the
# pattern, not when to apply it.


def test_an_english_violation_is_caught_inside_an_arabic_sentence(patterns):
    """The reason patterns are never routed by the sentence's language.

    Arabic-English code-switching is the default Dubai register, not an edge
    case. Filtering to the call's language would silently stop catching this,
    and it is the one form of ar/hi violation currently catchable at all.
    """
    hits = check_prohibited("هذا المشروع يقدم guaranteed returns للمستثمرين.", patterns)
    assert hits, "an English guarantee inside Arabic text must still be caught"
    assert "return_guarantees" in hits[0]


def test_an_english_violation_is_caught_inside_a_hindi_sentence(patterns):
    hits = check_prohibited("यह प्रोजेक्ट risk-free निवेश है।", patterns)
    assert hits
    assert "return_guarantees" in hits[0]


def test_factual_non_english_text_is_not_blocked_by_english_patterns(patterns):
    """The cost of applying everything everywhere, checked rather than assumed.

    Over-blocking is the safe direction, but it is not free: a false positive
    is a sentence the buyer never hears. Scripts differ, so this should be
    clean, and this test is what would notice if a future pattern were written
    loosely enough to match across a script boundary.
    """
    for text in [
        "خطة السداد تطلب ٢٠٪ عند الحجز.",
        "हैंडओवर Q4 2026 के लिए योजनाबद्ध है।",
        "يمكنني توصيلك بأحد مستشارينا.",
    ]:
        assert check_prohibited(text, patterns) == [], text


def test_coverage_reports_the_languages_actually_authored(patterns):
    """A set claim, not a count, and not a snapshot of today's file.

    Naming "English only" here would fail the day a native reviewer delivers
    Arabic patterns. This says the reported set is exactly what the file
    carries, which stays true either way.
    """
    assert languages_covered(patterns) == {p.language for p in patterns}


def test_coverage_is_honest_about_a_language_with_no_patterns(tmp_path):
    """The gap this exists to surface: a language the system offers and the
    guardrail does not cover."""
    source = tmp_path / "prohibited-patterns.yaml"
    source.write_text(
        "- category: return_guarantees\n"
        "  language: en\n"
        "  patterns:\n"
        '    - "\\\\brisk[-\\\\s]?free\\\\b"\n',
        encoding="utf-8",
    )
    covered = languages_covered(load_patterns(source))
    assert "en" in covered
    assert "ar" not in covered
