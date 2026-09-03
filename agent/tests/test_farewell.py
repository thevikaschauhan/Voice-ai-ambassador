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

from ambassador.farewell import (  # noqa: E402
    contains_closing_phrase,
    is_farewell,
    load_farewells,
    read_farewell,
)
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


# --- what the hosted call actually said -----------------------------------
#
# A real client said goodbye, the model answered with a goodbye of its own, and
# the call stayed open. The utterance is redacted in the log, so the tables were
# widened against the SHAPES a real goodbye takes - the ambassador's name and
# the tails people trail - and `read_farewell` now reports how close a miss came
# so the next widening comes from evidence rather than guesses.

NAMES = frozenset({"Jane", "Nora", "Maya"})

REAL_GOODBYES = [
    "Thanks Jane, that is all for today, goodbye",
    "goodbye Jane",
    "ok thanks Jane, take care",
    "that is all, thank you so much for your help today",
    "thanks for your time, bye now",
    "no more questions, have a good day",
    "that's it for now, thanks",
]


@pytest.mark.parametrize("utterance", REAL_GOODBYES)
def test_the_shapes_a_real_goodbye_takes(utterance):
    assert is_farewell(utterance, FAREWELLS, "en", names=NAMES), utterance


@pytest.mark.parametrize("utterance", NOT_ENDINGS)
def test_widening_did_not_loosen_the_dangerous_cases(utterance):
    """The whole point of the rule is that it cannot hang up on a live buyer.
    Widening the courtesies must not buy a goodbye at that cost."""
    assert not is_farewell(utterance, FAREWELLS, "en", names=NAMES), utterance


def test_the_ambassadors_name_is_a_courtesy_only_when_it_is_passed():
    """The name lives in data/ambassadors.yaml, not in farewells.yaml: one name
    in two files is a name that can disagree with itself."""
    assert not is_farewell("goodbye Jane", FAREWELLS, "en")
    assert is_farewell("goodbye Jane", FAREWELLS, "en", names=NAMES)


def test_a_near_miss_reports_its_shape_and_not_its_words():
    """What `farewell_candidate` carries. The buyer's words are theirs; a count
    and a boolean are what tuning actually needs."""
    reading = read_farewell(
        "Thanks Jane that was really helpful, goodbye", FAREWELLS, "en", names=NAMES
    )
    assert reading.closes is False
    assert reading.has_phrase is True
    assert reading.unexplained == 1
    assert reading.named_ambassador is True


def test_a_question_around_a_goodbye_is_a_wide_miss_not_a_near_one():
    reading = read_farewell(
        "before we say goodbye, what about the payment plan", FAREWELLS, "en"
    )
    assert reading.closes is False
    assert reading.has_phrase is True
    assert reading.unexplained > 3


def test_an_ordinary_turn_carries_no_phrase_at_all():
    reading = read_farewell("what does a studio cost", FAREWELLS, "en")
    assert reading.has_phrase is False
    assert reading.unexplained == 0


def test_the_agents_own_goodbye_is_read_loosely():
    """The second signal in the hybrid. The model writes prose, so the strict
    rule would never match it - and it does not have to: the buyer's turn has
    already had to carry a closing phrase before this is consulted."""
    assert contains_closing_phrase(
        "Thank you for your time today. Goodbye.", FAREWELLS, "en"
    )
    assert not contains_closing_phrase(
        "A studio at Skyrise is AED 985,000.", FAREWELLS, "en"
    )


# --- the two the human actually said --------------------------------------
#
# Hosted call, turns 13 and 15: `farewell_candidate` fired with unexplained=1
# and unexplained=3, both `named_ambassador=false`, both then interrupted, so
# the hybrid never reached the seal and the call ran on until a third, cleaner
# goodbye. Asked what they said, the client gave two shapes:
#
#   (A) "that's it from my end, thank you so much"
#   (B) "I don't have any further question(s)"
#
# Neither reproduces 1 or 3 against the tables at fa78c90 - (A) reads 2 and (B)
# carries no closing phrase AT ALL, so it could not have produced a candidate
# event. The recogniser's words were not the remembered words. What the two
# shapes do establish is the two holes, and those are what these cases pin.

SHAPE_A = "that's it from my end, thank you so much"
SHAPE_B = "I don't have any further questions"


def test_a_closing_that_says_where_it_comes_from():
    """(A). "that's it" has been a phrase since #95, so the miss is the tail:
    "from my end" is how people mark a closing as their own."""
    assert is_farewell(SHAPE_A, FAREWELLS, "en", names=NAMES)


def test_the_same_tail_in_its_other_spellings():
    for utterance in (
        "that's it from my side, thanks",
        "that is all from my end, thank you",
        "that's it on my end",
    ):
        assert is_farewell(utterance, FAREWELLS, "en", names=NAMES), utterance


def test_running_out_of_questions_is_a_closing():
    """(B), and the more important half: it carries no closing phrase at all
    today, so it is invisible - no close, and no `farewell_candidate` either.
    A shape the telemetry cannot see is a shape nobody can tune."""
    assert is_farewell(SHAPE_B, FAREWELLS, "en", names=NAMES)


def test_the_other_ways_of_running_out_of_questions():
    for utterance in (
        "no further questions, thanks",
        "no further questions from my end",
        "that answers everything, thank you",
        "I have no more questions",
    ):
        assert is_farewell(utterance, FAREWELLS, "en", names=NAMES), utterance


def test_asking_about_questions_is_still_a_question():
    """The reason "further questions" is only safe as part of a longer phrase:
    a buyer who asks whether they may come back later has not closed."""
    for utterance in (
        "can I ask any further questions later",
        "who do I send further questions to",
    ):
        assert not is_farewell(utterance, FAREWELLS, "en", names=NAMES), utterance
