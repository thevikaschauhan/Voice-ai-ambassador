"""The ordered sentence pipeline: guardrails, then verbalisation, then (in the
adapter) synthesis.

This module is the answer to "how do you stop it speaking a price it made up".
process_sentence() is the ONLY public path to SpeakableText. Guardrails
inspect digits; verbalisation destroys digits; audio cannot be retracted -
so the order here is load-bearing and enforced by the types:
run_guardrails() is the only producer of ValidatedSentence, and verbalise()
accepts nothing else. Do not add another path, do not weaken the types
(AGENTS.md invariant 4).
"""

from ..schemas import (
    AllowedFigures,
    GuardrailViolation,
    Language,
    SpeakableText,
    ValidatedSentence,
)
from ..verbalise import SpokenForms, verbalise
from .numeric_claims import check_numeric_claims
from .prohibited import ProhibitedPattern, check_prohibited


def run_guardrails(
    raw: str,
    language: Language,
    allowed: AllowedFigures,
    patterns: list[ProhibitedPattern],
) -> ValidatedSentence | GuardrailViolation:
    numeric_violations = check_numeric_claims(raw, allowed)
    if numeric_violations:
        return GuardrailViolation(
            validator="numeric_claims",
            detail=(
                "figure(s) not in the allowed set: "
                + ", ".join(f"{f.surface} ({f.kind})" for f in numeric_violations)
            ),
            figures=numeric_violations,
        )
    prohibited_hits = check_prohibited(raw, patterns, language)
    if prohibited_hits:
        return GuardrailViolation(
            validator="prohibited_language",
            detail="; ".join(prohibited_hits),
        )
    return ValidatedSentence(text=raw, language=language)


def process_sentence(
    raw: str,
    language: Language,
    allowed: AllowedFigures,
    patterns: list[ProhibitedPattern],
    forms: SpokenForms,
) -> SpeakableText | GuardrailViolation:
    """Guardrails first, verbalisation second. The only public producer of
    SpeakableText in the system."""
    result = run_guardrails(raw, language, allowed, patterns)
    if isinstance(result, GuardrailViolation):
        return result
    return verbalise(result, forms)
