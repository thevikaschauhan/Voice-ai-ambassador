"""Never call the buyer a name they did not give (docs/03-, validator 5).

On the 08:32Z call the model said, at turn 7:

    "You are welcome, Jim."

The human confirmed they never gave a name. So the ambassador addressed a
buyer by an invented personal detail, in the ambassador's own voice, and
nothing in the pipeline could see it: the numeric validator inspects figures
and the prohibited-pattern validator inspects claims. A name is neither.

It is the same class of failure as an invented price - a fact about the buyer
this system made up - and it is worse in one way, because it sounds like
familiarity. A buyer who is called by the wrong name knows immediately that
nobody is really listening.

THE RULE IS POSITIONAL, NOT A NAME LIST. There is no list of personal names
worth having: the model can invent any word. What is checkable is the SHAPE of
direct address - a capitalised word in a vocative slot, in a sentence that is
addressing the buyer - and the small set of names that are legitimate: the ones
the buyer gave (from the contact capture), the ambassador's own, forms of
address, and the inventory's own vocabulary.

A false positive here costs a regeneration, not a call: the sentence is
refused, the model is asked again with the violation named, and the composed
fallback stands behind that (docs/01-). That is why this can be strict where
the farewell rule could not be.

English only. The vocative pattern is a PATTERN rather than copy, but the
tables stay per-language and ar/hi stay empty until a native reviewer writes
them (AGENTS.md:52) - a machine-guessed Arabic vocative rule would refuse real
Arabic sentences, and a refused sentence is one the buyer never hears.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The live sentence, verbatim. "Jim" is the invented name; it is not the
# buyer's, which is the whole point of the case.
LIVE = "You are welcome, Jim."


def _markers():
    from ambassador.guardrails.vocative import load_vocative_markers

    return load_vocative_markers()


def test_the_live_sentence_is_caught() -> None:
    """Turn 7, verbatim, with no buyer-given name - which is the state every
    call is in until the buyer answers the contact ask."""
    from ambassador.guardrails.vocative import check_invented_vocative

    found = check_invented_vocative(LIVE, "en", known=frozenset(), markers=_markers())
    assert found == "Jim", found


def test_a_name_the_buyer_gave_is_theirs_to_be_called() -> None:
    """The contact capture is the ONLY source of a buyer's name, and once they
    have given it, using it is courtesy rather than invention."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    assert (
        check_invented_vocative(
            "Thank you, Jim.", "en", known=frozenset({"Jim"}), markers=markers
        )
        is None
    )
    # Case and spacing are not the buyer's problem.
    assert (
        check_invented_vocative(
            "Thank you, jim.", "en", known=frozenset({"JIM"}), markers=markers
        )
        is None
    )


def test_a_sentence_that_addresses_nobody_passes() -> None:
    """Plain courtesy, which is what the model should have said."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    for sentence in (
        "You are welcome.",
        "Thank you for your time today.",
        "A studio in Binghatti Skyrise starts at AED 985,000.",
        "",
    ):
        assert (
            check_invented_vocative(sentence, "en", known=frozenset(), markers=markers)
            is None
        ), sentence


def test_the_inventory_vocabulary_is_not_a_personal_name() -> None:
    """The false-positive class that would have made this unshippable.

    A trailing capitalised word after a comma is extremely common in property
    speech - an area, a tower, a city. None of it is a person, and refusing
    those sentences would refuse most of the product.
    """
    from ambassador.guardrails.vocative import check_invented_vocative
    from ambassador.inventory import load_inventory, vocabulary

    markers = _markers()
    known = vocabulary(load_inventory())
    for sentence in (
        "It is in Business Bay, Dubai.",
        "You will love the view, Skyrise.",
        "Thank you for asking about Aquarise.",
        "That tower is Binghatti Circle, in Jumeirah Village Circle.",
    ):
        assert (
            check_invented_vocative(sentence, "en", known=known, markers=markers)
            is None
        ), sentence


def test_the_ambassadors_own_name_is_not_an_invented_one() -> None:
    """ "I am Jane" is the disclosure, not an invented buyer name - and a buyer
    who says the name back gets it repeated to them."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    known = frozenset({"Jane"})
    for sentence in (
        "Jane, the Binghatti ambassador, can take this further.",
        "You are speaking with Jane.",
    ):
        assert (
            check_invented_vocative(sentence, "en", known=known, markers=markers)
            is None
        ), sentence


def test_a_form_of_address_is_not_a_personal_name() -> None:
    """ "Yes, sir." invents nothing. It is not a name, and the card is about
    names."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    for sentence in ("Yes, sir.", "Of course, madam.", "Thank you, Sir."):
        assert (
            check_invented_vocative(sentence, "en", known=frozenset(), markers=markers)
            is None
        ), sentence


def test_a_leading_vocative_is_caught_too() -> None:
    """The other half of the shape. "Jim, the payment plan is..." is the same
    invention with the name at the front."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    assert (
        check_invented_vocative(
            "Jim, the payment plan runs over three years.",
            "en",
            known=frozenset(),
            markers=markers,
        )
        == "Jim"
    )


def test_a_greeting_needs_no_comma_to_be_a_vocative() -> None:
    """ "Hi Jim" and "Thanks Jim" are how it usually arrives."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    for sentence in ("Hi Jim, how can I help?", "Thanks Jim."):
        assert (
            check_invented_vocative(sentence, "en", known=frozenset(), markers=markers)
            == "Jim"
        ), sentence


def test_a_language_with_no_reviewed_pattern_never_fires() -> None:
    """AGENTS.md:52, and empty is SAFE here in the direction that matters: with
    no table the validator does not fire, and the sentence goes through exactly
    as it does today. A guessed Arabic vocative rule would refuse real Arabic
    sentences instead."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    assert markers.detects("en") is True
    for language in ("ar", "hi"):
        assert markers.detects(language) is False
        assert (
            check_invented_vocative(
                "You are welcome, Jim.", language, known=frozenset(), markers=markers
            )
            is None
        )


def test_the_pipeline_refuses_the_sentence_and_names_the_validator() -> None:
    """Through the real guardrail pipeline, which is the only path to TTS."""
    from ambassador.guardrails.pipeline import run_guardrails
    from ambassador.guardrails.vocative import VocativeContext
    from ambassador.schemas import AllowedFigures, GuardrailViolation

    allowed = AllowedFigures(amounts=[], percentages=[], counts=[], years=[])
    context = VocativeContext(known=frozenset(), markers=_markers())

    result = run_guardrails(LIVE, "en", allowed, [], context)
    assert isinstance(result, GuardrailViolation)
    assert result.validator == "invented_vocative"
    # The offending word is in the detail, because the regeneration prompt
    # names the violation back to the model and "a name you invented" is not
    # actionable without it.
    assert "Jim" in result.detail


def test_the_prompt_tells_the_model_not_to_invent_a_name() -> None:
    """The cheap half. The guardrail catches it; the instruction stops most of
    it happening, and costs one line of prompt."""
    from ambassador.prompts import build_ambassador_prompt

    prompt = build_ambassador_prompt(
        "INVENTORY",
        "en",
        system_confirms_budget=True,
        system_confirms_project=True,
    )
    lowered = prompt.lower()
    assert "name" in lowered
    assert "never address the buyer by a name" in lowered, (
        "the instruction has to be explicit; 'be accurate' is not an instruction"
    )
