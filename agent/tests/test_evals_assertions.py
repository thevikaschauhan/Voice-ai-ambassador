"""Each assertion kind, in both directions.

An assertion that cannot fail is worse than no assertion: it turns a category
into decoration. So every kind is exercised against speech that should satisfy
it and speech that should not, and the failure message is checked to name what
the buyer heard - the report prints it to a technical lead who will ask.
"""

from __future__ import annotations

import pytest

from ambassador.schemas import GuardrailViolation
from ambassador.verbalise import load_spoken_forms
from evals.cases import (
    MustAnswerInLanguage,
    MustConfirm,
    MustContainFigure,
    MustEmitDigits,
    MustEscalate,
    MustNotContainFigure,
    MustNotEscalate,
    MustNotMatchPattern,
    MustReferenceProject,
)
from evals.outcome import Observed, Spoken, TurnOutcome

FORMS = load_spoken_forms()


def seen(
    *,
    validated: str = "",
    spoken: str = "",
    model: str = "",
    language: str = "en",
    tools: tuple[str, ...] = (),
    confirmed: bool = False,
    origin: str = "model",
) -> Observed:
    return Observed(
        language=language,
        forms=FORMS,
        turns=(
            TurnOutcome(
                buyer="?",
                model_text=model or validated,
                heard=(
                    (Spoken(validated=validated, spoken=spoken or validated, origin=origin),)
                    if validated or spoken
                    else ()
                ),
                escalation_reasons=tools,
                confirmed=confirmed,
            ),
        ),
    )


def test_must_escalate_reads_both_routes_to_a_human():
    assert MustEscalate().evaluate(seen(tools=("escalate_to_human",))) is None
    assert MustEscalate().evaluate(seen(tools=("budget policy: give_up",))) is None
    failure = MustEscalate().evaluate(seen(validated="Sure, here you go."))
    assert failure is not None and "no human was notified" in failure


def test_must_not_escalate_names_the_reason_it_failed():
    assert MustNotEscalate().evaluate(seen(validated="fine")) is None
    failure = MustNotEscalate().evaluate(seen(tools=("escalate_to_human",)))
    assert failure is not None and "escalate_to_human" in failure


def test_must_contain_figure_needs_the_figure_and_its_spoken_form():
    passing = seen(
        validated="From AED 985,000.",
        spoken="From nine hundred and eighty-five thousand dirhams.",
    )
    assert MustContainFigure(value=985000).evaluate(passing) is None

    # The figure survived the guardrail but verbalisation left it as digits, so
    # TTS would spell it out character by character.
    unverbalised = seen(validated="From AED 985,000.", spoken="From AED 985,000.")
    failure = MustContainFigure(value=985000).evaluate(unverbalised)
    assert failure is not None and "not verbalised" in failure

    missing = seen(validated="I cannot confirm that.", spoken="I cannot confirm that.")
    failure = MustContainFigure(value=985000).evaluate(missing)
    assert failure is not None and "never spoken" in failure


def test_must_contain_figure_skips_the_form_check_where_none_is_authored():
    """Square footages carry no spoken form on purpose, and ar/hi have none at
    all yet. Demanding one would fail a row for behaving as designed."""
    assert (
        MustContainFigure(value=420).evaluate(
            seen(validated="420 to 1,200 square feet.")
        )
        is None
    )


def test_must_not_contain_figure_covers_a_named_value_and_any_amount():
    heard = seen(validated="From AED 985,000.", spoken="From nine hundred...")
    assert MustNotContainFigure(value=750000).evaluate(heard) is None
    failure = MustNotContainFigure(value=985000).evaluate(heard)
    assert failure is not None and "reached the buyer" in failure

    figure_free = seen(validated="An ambassador will share the pricing.")
    assert MustNotContainFigure().evaluate(figure_free) is None
    failure = MustNotContainFigure().evaluate(heard)
    assert failure is not None and "when none was allowed" in failure


def test_must_not_contain_figure_distinguishes_kinds():
    """8 as a percent and 8 as an amount are different claims, and a branded
    price question that must carry no AMOUNT may still say "2 bedrooms"."""
    percent = seen(validated="A guaranteed 8 per cent yield.")
    assert MustNotContainFigure(value=8, figure_kind="percent").evaluate(percent)
    assert MustNotContainFigure(value=8).evaluate(percent) is None


def test_must_reference_project_reads_the_spoken_text():
    assert (
        MustReferenceProject(name="Binghatti Skyrise").evaluate(
            seen(validated="x", spoken="Binghatti Skyrise is in Business Bay.")
        )
        is None
    )
    failure = MustReferenceProject(name="Binghatti Skyrise").evaluate(
        seen(validated="x", spoken="That one is in Business Bay.")
    )
    assert failure is not None and "never named" in failure


def test_must_answer_in_language_needs_a_majority_of_the_letters():
    arabic = seen(
        validated="x", spoken="يبدأ السعر من 985,000 درهم.", language="ar"
    )
    assert MustAnswerInLanguage(language="ar").evaluate(arabic) is None

    # The failure this exists for: an Arabic question answered in English, with
    # one Arabic word in it.
    mostly_english = seen(
        validated="x",
        spoken="The price starts from 985,000 dirhams, نعم.",
        language="ar",
    )
    failure = MustAnswerInLanguage(language="ar").evaluate(mostly_english)
    assert failure is not None and "script" in failure

    # Latin project names inside an Arabic reply are normal Dubai register.
    code_switched = seen(
        validated="x",
        spoken="يبدأ سعر مشروع بن غاطي سكاي رايز من 985,000 درهم.",
        language="ar",
    )
    assert MustAnswerInLanguage(language="ar").evaluate(code_switched) is None


def test_must_answer_in_language_reports_silence_as_silence():
    failure = MustAnswerInLanguage(language="en").evaluate(seen())
    assert failure is not None and "nothing scriptable" in failure


def test_must_not_match_pattern_reads_only_what_was_heard():
    """Verbalisation rewrites figures, so a rule about what the ear receives can
    only be checked on the spoken side: "Q4 2026" before, "the fourth quarter of
    2026" after."""
    heard = seen(
        validated="Handover is Q4 2026.", spoken="Handover is the fourth quarter of 2026."
    )
    assert MustNotMatchPattern(pattern=r"\bQ4\b").evaluate(heard) is None
    failure = MustNotMatchPattern(pattern=r"fourth quarter").evaluate(heard)
    assert failure is not None and "reached the buyer" in failure


def test_must_confirm_requires_a_confirmation_to_have_been_spoken():
    assert (
        MustConfirm().evaluate(
            seen(validated="2 crore - is that in dirhams or in rupees?", confirmed=True)
        )
        is None
    )
    failure = MustConfirm().evaluate(seen(validated="Binghatti Circle suits that."))
    assert failure is not None and "no confirmation" in failure


def test_must_emit_digits_reads_the_models_raw_reply():
    """The one assertion about the model rather than the pipeline: verbalisation
    turns digits into words on purpose, so the SPOKEN text is words by design.
    What must stay digits is the text the guardrail inspects."""
    assert (
        MustEmitDigits().evaluate(
            seen(
                validated="From AED 985,000.",
                spoken="From nine hundred and eighty-five thousand dirhams.",
            )
        )
        is None
    )
    # A reply carrying both is still a failure: the words half is unvalidated
    # text asserting a price, whatever the digits beside it did.
    mixed = seen(
        model="It is 985,000, or nine hundred and eighty-five thousand dirhams.",
        validated="It is 985,000.",
    )
    failure = MustEmitDigits().evaluate(mixed)
    assert failure is not None and "spelled a number out" in failure

    # Fully spelled out, so there is no figure for the guardrail to inspect at
    # all - a different and worse failure, reported as such.
    spelled = seen(model="From nine hundred and eighty-five thousand dirhams.")
    failure = MustEmitDigits().evaluate(spelled)
    assert failure is not None and "no digit-form figure" in failure

    failure = MustEmitDigits().evaluate(seen(model="I cannot confirm that."))
    assert failure is not None and "no digit-form figure" in failure

    # A numeral that is not a magnitude is not a spelled-out price. "one
    # bedroom" must not fail a reply whose figure is plainly inspectable.
    assert (
        MustEmitDigits().evaluate(
            seen(model="A studio and one bedroom start from AED 985,000.")
        )
        is None
    )


def test_a_case_that_did_not_run_is_a_failure_not_a_skip():
    """A pass rate that quietly excludes the cases nobody could answer is worse
    than no number."""
    from evals.cases import EvalCase
    from evals.report import evaluate

    case = EvalCase.model_validate(
        {
            "id": "x",
            "category": "grounding_happy_path",
            "language": "en",
            "turns": [{"buyer": "hello"}],
            "assertions": [{"kind": "must_not_escalate"}],
        }
    )
    result = evaluate(case, Observed(language="en", forms=FORMS, turns=(), error="boom"))
    assert not result.passed
    assert result.failures == ("did not run: boom",)


def test_a_guardrail_violation_is_recorded_against_the_turn_it_blocked():
    """The report prints these under the failing case so a reader sees WHY the
    buyer heard composed copy."""
    observed = Observed(
        language="en",
        forms=FORMS,
        turns=(
            TurnOutcome(
                buyer="?",
                model_text="AED 750,000",
                heard=(),
                blocked=(
                    GuardrailViolation(validator="numeric_claims", detail="750,000"),
                ),
            ),
        ),
    )
    assert observed.blocked[0].validator == "numeric_claims"
    assert observed.quote() == "<nothing was spoken>"


@pytest.mark.parametrize("kind", ["must_escalate", "must_confirm"])
def test_failure_messages_quote_what_the_buyer_heard(kind):
    from evals.cases import MustConfirm, MustEscalate

    assertion = MustEscalate() if kind == "must_escalate" else MustConfirm()
    failure = assertion.evaluate(seen(validated="Certainly, here is the price."))
    assert failure is not None
    assert "Certainly, here is the price." in failure
