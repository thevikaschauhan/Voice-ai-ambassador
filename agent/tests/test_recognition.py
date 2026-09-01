"""Failed recognitions and the escalation after three (ADR-011, docs/04-).

Two things get pinned here: what counts as a failed recognition (the
deterministic definition, since ADR-011 exists precisely because vendor
confidence is often absent or uncalibrated) and that the count is CONSECUTIVE.
What the buyer hears at the third is asserted in test_agent.py, through
`llm_node`.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from ambassador.schemas import Language
from ambassador.recognition import (
    RecognitionMonitor,
    is_failed_recognition,
    load_noise_words,
)


@pytest.fixture(scope="module")
def noise():
    return load_noise_words()


def monitor(noise, language: str = "en") -> RecognitionMonitor:
    return RecognitionMonitor(noise, language)


# --- what counts as failed ---------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "   ",
        "\n\t ",
        # Punctuation is what a recogniser emits around silence.
        ".",
        "...",
        "?",
        "-- ,",
        # Noise-only turns: breath, a cough, a corridor.
        "uh",
        "um",
        "hmm",
        "Uh, um...",
        "mm mm",
        "er... erm",
    ],
)
def test_an_unusable_turn_is_a_failed_recognition(noise, utterance):
    assert is_failed_recognition(utterance, noise, "en"), repr(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        # Short and unhelpful is still something the buyer said, and is
        # answered rather than counted.
        "what",
        "no",
        "sorry",
        "pardon",
        "yes",
        # One filler in front of a real question must not condemn the turn.
        "um, what is the price",
        "uh my budget is 2 crore",
        # Digits alone are content: this is exactly the transcript the budget
        # policy reads (ADR-017, numerals=True).
        "2 crore",
        "985,000",
    ],
)
def test_a_real_turn_is_not_a_failed_recognition(noise, utterance):
    assert not is_failed_recognition(utterance, noise, "en"), repr(utterance)


def test_the_empty_half_works_in_every_language(noise):
    """`\\w` under re.UNICODE covers Arabic and Devanagari, so an empty turn is
    caught without an authored word list. This is the half of the trigger that
    is live in ar/hi today."""
    for language in get_args(Language):
        assert is_failed_recognition("  ", noise, language)
        assert is_failed_recognition("...", noise, language)


def test_a_language_with_no_noise_list_never_calls_a_turn_garbage(noise):
    """The safe direction: without an authored list we keep answering a buyer
    we cannot hear, which is exactly the behaviour before this existed."""
    assert is_failed_recognition("uh", noise, "en")
    assert not is_failed_recognition("uh", noise, "ar")
    assert "ar" not in noise.languages_covered()
    assert "hi" not in noise.languages_covered()


def test_arabic_and_devanagari_content_is_content(noise):
    assert not is_failed_recognition("مرحبا", noise, "ar")
    assert not is_failed_recognition("नमस्ते", noise, "hi")


def test_a_non_string_noise_word_fails_loudly(tmp_path: Path):
    """The YAML 1.1 boolean trap, the one `currencies.yaml` walked into: bare
    no/yes/on/off load as booleans, and a coerced boolean is a word that can
    never match. Loud at startup beats silent on a call."""
    source = tmp_path / "recognition.yaml"
    source.write_text("noise:\n  en: [uh, no]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not \n?text|not text"):
        load_noise_words(source)


def test_a_quoted_word_loads_fine(tmp_path: Path):
    source = tmp_path / "recognition.yaml"
    source.write_text('noise:\n  en: ["uh", "no"]\n', encoding="utf-8")
    assert load_noise_words(source).words("en") == ("uh", "no")


# --- consecutive, and once ---------------------------------------------------


def test_three_in_a_row_escalate(noise):
    m = monitor(noise)
    assert m.observe("").action == "none"
    assert m.observe("uh").action == "none"
    third = m.observe("   ")
    assert third.action == "escalate"
    assert third.hands_over
    assert third.consecutive == 3
    assert m.handed_over


def test_a_real_turn_resets_the_count(noise):
    """Three failures spread over a good call are three ordinary "could you
    repeat that" moments. Escalating on those makes the policy read as broken,
    which is how it gets switched off."""
    m = monitor(noise)
    m.observe("")
    m.observe("hmm")
    assert m.consecutive == 2
    assert m.observe("what is the payment plan").action == "none"
    assert m.consecutive == 0
    assert m.observe("").action == "none"
    assert m.observe("").action == "none"
    assert not m.handed_over


def test_the_escalation_happens_once_not_on_every_crackle(noise):
    """A human has already been notified. A bot that announces the handover
    again every time the line drops is worse than one that says it once."""
    m = monitor(noise)
    for _ in range(3):
        m.observe("")
    assert m.handed_over
    for _ in range(5):
        decision = m.observe("")
        assert decision.action == "none"
        # Still classified, because the caller's other question - "was this an
        # answer to my open confirmation?" - still has the answer no.
        assert decision.failed


def test_the_caller_can_tell_an_unheard_turn_from_a_silent_policy(noise):
    """`failed` is separate from `action` on purpose: on failures one and two
    the policy says nothing, and the caller still has to know the turn was not
    an answer, or the budget policy counts it as a reply that answered
    nothing."""
    m = monitor(noise)
    first = m.observe("")
    assert first.action == "none" and first.failed
    heard = m.observe("dirhams")
    assert heard.action == "none" and not heard.failed
