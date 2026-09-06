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

    allowed = AllowedFigures(
        amounts=frozenset(), percents=frozenset(), years=frozenset()
    )
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


# --- what makes a name the buyer's -----------------------------------------
#
# The validator is only as good as the set it trusts, and the contact capture
# is the one component in the system that learns a buyer's name. These cases
# are the seam between them: without it the guardrail would refuse the buyer
# their own name for the rest of the call.


def test_a_name_the_contact_capture_learned_is_not_an_invention() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy
    from ambassador.guardrails.vocative import (
        check_invented_vocative,
        load_vocative_context,
    )

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(4)
    policy.observe_reply("Jim, jim@example.com", 5)

    assert policy.state.name == "Jim"
    assert policy.names_given == frozenset({"Jim"})

    context = load_vocative_context(policy.names_given)
    assert (
        check_invented_vocative(
            "Thank you, Jim.", "en", known=context.known, markers=context.markers
        )
        is None
    )


def test_a_name_given_beside_a_number_counts_before_the_read_back_settles() -> None:
    """The read-back is about the DIGITS. Treating the name as unknown until
    they are confirmed would refuse the buyer their own name for a turn, on the
    one turn where the agent is most likely to use it."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(4)
    outcome = policy.observe_reply("Jim, 0501234567", 5)

    assert outcome.settled is False
    assert policy.state.name is None
    assert policy.names_given == frozenset({"Jim"})


def test_a_call_that_learned_nothing_trusts_only_our_own_words() -> None:
    """The 08:32Z state. Nothing was asked and nothing was given, so every
    capitalised word in a vocative slot but ours is an invention."""
    from ambassador.ambassadors import load_ambassadors
    from ambassador.guardrails.vocative import load_vocative_context
    from ambassador.inventory import load_inventory, vocabulary

    context = load_vocative_context()
    assert context.known == vocabulary(load_inventory()) | frozenset(
        name for name in load_ambassadors().names.values() if name
    )
    assert "Jim" not in context.known


def test_adding_no_names_returns_the_same_context() -> None:
    """Every sentence of every call before the buyer gives anything takes this
    path, so it allocates nothing."""
    from ambassador.guardrails.vocative import load_vocative_context

    context = load_vocative_context()
    assert context.with_names(frozenset()) is context
    widened = context.with_names(frozenset({"Jim"}))
    assert widened is not context
    assert "Jim" in widened.known
    assert widened.markers is context.markers


def test_a_trailing_vocative_needs_only_a_word_for_you() -> None:
    """The third shape, and the one with no greeting in front of it. "your" is
    what says this sentence is spoken to somebody; without it the same trailing
    capitalised word would be a place, which is why "It is in Business Bay,
    Dubai." still passes."""
    from ambassador.guardrails.vocative import check_invented_vocative

    markers = _markers()
    assert (
        check_invented_vocative(
            "That is your best option, Jim.",
            "en",
            known=frozenset(),
            markers=markers,
        )
        == "Jim"
    )
    # The same sentence with the name the buyer actually gave.
    assert (
        check_invented_vocative(
            "That is your best option, Jim.",
            "en",
            known=frozenset({"Jim"}),
            markers=markers,
        )
        is None
    )


def test_a_table_file_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    """Loud at startup rather than a validator that silently never fires. An
    empty table is a decision this file records; an unreadable one is not."""
    from ambassador.guardrails.vocative import load_vocative_markers

    source = tmp_path / "vocatives.yaml"
    source.write_text("- hi\n- hello\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_vocative_markers(source)


def test_a_bare_no_that_yaml_read_as_a_boolean_is_refused(tmp_path: Path) -> None:
    """The trap `currencies.yaml`, `recognition.yaml` and `farewells.yaml` all
    walked into: YAML 1.1 loads bare no/yes as booleans, and a coerced boolean
    is a word that can never match - so the table quietly loses an entry."""
    from ambassador.guardrails.vocative import load_vocative_markers

    source = tmp_path / "vocatives.yaml"
    source.write_text("address_words:\n  en:\n    - no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Quote the entry"):
        load_vocative_markers(source)


def test_a_sentence_adverb_is_not_a_person() -> None:
    """Both of these came out of the eval corpus, not out of my head.

    Running the validator over every recorded model reply in `evals/cases`
    flagged two sentences in 102, and both were this shape: English puts an
    adverb in exactly the slot a leading vocative uses. Without the stop-list
    the guardrail refuses real replies, which is how a validator gets switched
    off by the first engineer who hits it.
    """
    from ambassador.guardrails.vocative import (
        check_invented_vocative,
        load_vocative_context,
    )

    context = load_vocative_context()
    for sentence in (
        "Actually, the booking amount for Binghatti Aquarise is 20 percent.",
        "Noted, and Binghatti Emerald starts from AED 500,000.",
        "However, the handover is in 2026.",
        "Unfortunately, that project is sold out.",
    ):
        assert (
            check_invented_vocative(
                sentence, "en", known=context.known, markers=context.markers
            )
            is None
        ), sentence


def test_no_recorded_model_reply_in_the_eval_corpus_is_refused() -> None:
    """The measurement itself, kept as a test.

    A false positive here costs a regeneration rather than a call, but the
    corpus is the only evidence we have about what this model actually says,
    and a rule nobody measured against it is a rule nobody has tested. If a new
    fixture trips this, the answer is usually a word for `sentence_openers` -
    not a weaker rule.
    """
    import yaml

    from ambassador.guardrails.vocative import (
        check_invented_vocative,
        load_vocative_context,
    )
    from ambassador.sentences import split_sentences

    context = load_vocative_context()
    cases = Path(__file__).resolve().parents[1] / "evals" / "cases"
    refused: list[tuple[str, str, str]] = []
    seen = 0
    for path in sorted(cases.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            language = case.get("language", "en")
            for turn in case.get("turns") or []:
                text = (turn.get("model") or {}).get("text") or ""
                if not text:
                    continue
                complete, remainder = split_sentences(text)
                for sentence in [*complete, *([remainder] if remainder else [])]:
                    seen += 1
                    found = check_invented_vocative(
                        sentence,
                        language,
                        known=context.known,
                        markers=context.markers,
                    )
                    if found is not None:
                        refused.append((case["id"], found, sentence))

    assert seen > 50, f"the corpus went missing: {seen} sentences read"
    assert not refused, refused
