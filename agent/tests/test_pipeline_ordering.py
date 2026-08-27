"""The load-bearing tests (AGENTS.md invariant 4). Do not delete.

process_sentence() is the only public path to SpeakableText, guardrails run
on digits BEFORE verbalisation destroys them, and nothing framework-shaped
may leak into the core.
"""

from pathlib import Path

from ambassador.guardrails.pipeline import process_sentence
from ambassador.schemas import GuardrailViolation, SpeakableText


def test_allowed_sentence_becomes_speakable(allowed, patterns, forms):
    result = process_sentence(
        "Skyrise starts at AED 985,000.", "en", allowed, patterns, forms
    )
    assert isinstance(result, SpeakableText)
    assert "nine hundred and eighty-five thousand dirhams" in result.text
    assert "985" not in result.text  # verbalisation ran, after validation


def test_invented_figure_never_becomes_speakable(allowed, patterns, forms):
    result = process_sentence(
        "It starts at AED 800,000.", "en", allowed, patterns, forms
    )
    assert isinstance(result, GuardrailViolation)
    assert result.validator == "numeric_claims"
    # Ordering proof: the validator saw DIGITS. Had verbalisation run first,
    # there would be no digits left to extract and this surface could not
    # have been recorded.
    assert result.figures[0].surface == "800,000"


def test_prohibited_language_never_becomes_speakable(allowed, patterns, forms):
    result = process_sentence(
        "This is a risk-free investment.", "en", allowed, patterns, forms
    )
    assert isinstance(result, GuardrailViolation)
    assert result.validator == "prohibited_language"


def test_guarantee_holds_in_arabic_digits(allowed, patterns, forms):
    # The numeric guardrail is language-agnostic: Arabic-Indic digits are
    # extracted and checked identically
    result = process_sentence(
        "يبدأ السعر من ٨٠٠٬٠٠٠ درهم", "ar", allowed, patterns, forms
    )
    assert isinstance(result, GuardrailViolation)
    assert result.figures[0].value == 800000.0


def test_core_has_no_framework_imports():
    # ADR-002: the core stays headless. The LiveKit adapter lives in
    # src/adapter, never here.
    core = Path(__file__).resolve().parents[1] / "src" / "ambassador"
    offenders = [
        path
        for path in core.rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
        and ("livekit" in line or "pipecat" in line)
    ]
    assert offenders == []
