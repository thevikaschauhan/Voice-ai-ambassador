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
from .vocative import VocativeContext, check_invented_vocative


def run_guardrails(
    raw: str,
    language: Language,
    allowed: AllowedFigures,
    patterns: list[ProhibitedPattern],
    vocatives: VocativeContext,
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
    # docs/03- validator 5. Last of the three because it is the cheapest to
    # repair: a figure violation means the reply was wrong about the property,
    # while this one is the same reply with a name in it that nobody gave.
    invented = check_invented_vocative(
        raw, language, known=vocatives.known, markers=vocatives.markers
    )
    if invented is not None:
        return GuardrailViolation(
            validator="invented_vocative",
            # The word itself, because the regeneration prompt names the
            # violation back to the model and "a name you invented" is not
            # actionable without it. It is the MODEL's word, not the buyer's -
            # nothing the buyer said is quoted here.
            detail=f"addressed the buyer as {invented!r}, which they have not given",
        )
    return ValidatedSentence(text=raw, language=language)


def process_sentence(
    raw: str,
    language: Language,
    allowed: AllowedFigures,
    patterns: list[ProhibitedPattern],
    forms: SpokenForms,
    vocatives: VocativeContext,
) -> SpeakableText | GuardrailViolation:
    """Guardrails first, verbalisation second. The only public producer of
    SpeakableText in the system.

    `vocatives` is REQUIRED rather than defaulted: an empty default would read
    as "no names known" and silently disable validator 5 for any caller that
    forgot it, which is fail-open on a guardrail - the exact shape of the
    `language`-loaded-and-never-read defect `prohibited.py` documents.
    """
    result = run_guardrails(raw, language, allowed, patterns, vocatives)
    if isinstance(result, GuardrailViolation):
        return result
    return verbalise(result, forms)
