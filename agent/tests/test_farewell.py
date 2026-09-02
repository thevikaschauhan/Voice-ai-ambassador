"""Closing-line detection: the matrix, and the asymmetry it is built around.

A missed goodbye leaves the call behaving as it did before this existed. A
false one hangs up on a live buyer. Every case below is chosen for that
asymmetry rather than for coverage.
"""

import sys
from pathlib import Path
from typing import get_args

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ambassador.farewell import is_farewell, load_farewells  # noqa: E402
from ambassador.schemas import Language  # noqa: E402

FAREWELLS = load_farewells()

ENDINGS = [
    "goodbye",
    "Goodbye.",
    "bye",
    "bye bye",
    "ok bye then",
    "thanks so much, goodbye",
    "that is all, thank you",
    "that's all thanks",
    "no more questions thanks",
    "I have to go",
    "I've got to go",
    "we're done",
    "nothing else, thank you very much",
    "have a good day",
]

NOT_ENDINGS = [
    # The case the conservative rule exists for: a farewell word inside a
    # question that has to be answered.
    "before we say goodbye, what about the payment plan",
    "and before I go, is parking included",
    "is that all included in the price",
    "that is all I need for now, what about Skyrise",
    # A courtesy is not an ending. This one would end calls mid-conversation.
    "thank you",
    "thanks so much",
    "ok",
    "that is great, thank you",
    # Ordinary turns.
    "what does a studio cost",
    "tell me about Burj Binghatti",
    "",
    "...",
]


@pytest.mark.parametrize("utterance", ENDINGS)
def test_a_closing_line_is_recognised(utterance):
    assert is_farewell(utterance, FAREWELLS, "en"), utterance


@pytest.mark.parametrize("utterance", NOT_ENDINGS)
def test_what_must_never_end_a_call(utterance):
    assert not is_farewell(utterance, FAREWELLS, "en"), utterance


def test_a_language_with_no_authored_phrases_never_fires():
    """Empty is a supported state, and the SAFE direction: the call ends the
    way it did before this existed. A guessed phrase list is the dangerous
    option, because a wrong entry hangs up on a live buyer."""
    assert not FAREWELLS.detects("ar")
    assert not FAREWELLS.detects("hi")
    for utterance in ENDINGS:
        assert not is_farewell(utterance, FAREWELLS, "ar"), utterance


def test_an_unknown_language_does_not_fire_either():
    assert not is_farewell("goodbye", FAREWELLS, "fr")


def test_every_language_has_a_farewell_to_say():
    """Detection may be off in a language; SPEECH may not be. The duration cap
    can end a call the buyer never said goodbye in, and a call must not end in
    silence."""
    for language in get_args(Language):
        assert FAREWELLS.farewell_speech(language).strip(), language


def test_the_farewell_falls_back_to_english_rather_than_silence():
    assert FAREWELLS.farewell_speech("fr") == FAREWELLS.farewell_speech("en")


def test_the_english_farewell_says_the_two_things_it_is_for():
    speech = FAREWELLS.farewell_speech("en").lower()
    # It thanks them, and it leaves a way back in - a goodbye that closes the
    # door is not what the client asked for.
    assert "thank you" in speech
    assert "ambassador" in speech


def test_an_empty_farewell_is_refused_at_load(tmp_path):
    """Discovered at startup, not when a call tries to end."""
    path = tmp_path / "farewells.yaml"
    path.write_text(
        'phrases:\n  en: ["bye"]\ncourtesies:\n  en: []\nspeech:\n  en: "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="empty"):
        load_farewells(path)


def test_a_missing_english_farewell_is_refused_at_load(tmp_path):
    path = tmp_path / "farewells.yaml"
    path.write_text(
        'phrases:\n  en: ["bye"]\ncourtesies:\n  en: []\nspeech:\n  ar: "x"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="speech.en is required"):
        load_farewells(path)


def test_the_yaml_boolean_trap_is_loud(tmp_path):
    """Bare no/yes/on/off load as booleans, and a coerced boolean is a word
    that can never match - a dead entry that looks like coverage."""
    path = tmp_path / "farewells.yaml"
    path.write_text(
        'phrases:\n  en:\n    - no\ncourtesies:\n  en: []\nspeech:\n  en: "x"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not text"):
        load_farewells(path)


def test_a_longer_phrase_is_consumed_whole(tmp_path):
    """Longest-first matching, or "that is all" leaves "is all" to be judged as
    leftovers and the utterance stops being a farewell."""
    path = tmp_path / "farewells.yaml"
    path.write_text(
        'phrases:\n  en: ["all", "that is all"]\n'
        'courtesies:\n  en: []\nspeech:\n  en: "x"\n',
        encoding="utf-8",
    )
    table = load_farewells(path)
    assert is_farewell("that is all", table, "en")
