"""The two goodbye shapes a real call showed us, in the human's own words.

Lead 0c241ab2, the 08:32Z call. Three consecutive buyer turns, agent "Jane":

  7  "Okay. Uh, Jane, thank you so much. That's it. I don't have any further
      question."          -> the model signed off, and the call stayed open
  8  "Okay. Thank you."   -> the model said "Thank you for calling Binghatti.
                              Goodbye." and the call STILL stayed open
  9  "Goodbye."           -> the authored farewell, at last

Both misses are recorded here verbatim, because paraphrasing the evidence is
how a detector gets tuned against a phrasing nobody actually said. They are
two different failures:

TURN 7 IS A CLOSING THE TABLE ALMOST HAD. A phrase matched ("that's it"), and
the strict rule refused it over three leftovers: the filler "uh", and the
singular "question" against a plural-only phrase. The recogniser transcribed
what the buyer said; the table listed what we imagined they would say.

TURN 8 IS NOT A CLOSING AT ALL, and must not become one - "thank you" mid-call
ends nothing, and `test_farewell.py` keeps that. It is a closing only in the
one context this call produced: the agent has JUST said goodbye, and the buyer
answered it. Ending there is not a guess about the buyer's intent; it is not
making them say goodbye a second time to a service that already said it.

The rule we did NOT take: the model's own "Goodbye." closing the call by
itself. A model that signs off spontaneously would then hang up on a live
buyer, which is the asymmetric failure this whole module is built around. It
takes two readings, as the hybrid does - the difference is that the second
reading may now be a courtesy rather than a phrase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The human's words, verbatim. No PII: no name, number or address in any of
# them, and "Jane" is our own ambassador.
TURN_7 = (
    "Okay. Uh, Jane, thank you so much. That's it. I don't have any further question."
)
TURN_8 = "Okay. Thank you."
TURN_9 = "Goodbye."

NAMES = frozenset({"Jane", "Nora", "Maya"})

# What must still never end a call, kept beside the widening rather than in
# another file: every case below is a live buyer mid-conversation.
STILL_NOT_ENDINGS = [
    "before we say goodbye, what about the payment plan",
    "and before I go, is parking included",
    "that is all I need for now, what about Skyrise",
    "thank you",
    "thanks so much",
    "ok",
    "what does a studio cost",
    "do you have any further questions for me",
]


def test_the_turn_that_stayed_open_is_a_closing() -> None:
    """Turn 7, verbatim. The utterance IS the buyer closing the call."""
    from ambassador.farewell import load_farewells, read_farewell

    reading = read_farewell(TURN_7, load_farewells(), "en", names=NAMES)
    assert reading.closes, (
        f"turn 7 left {reading.unexplained} tokens unexplained; the buyer said "
        "that's it and that they had no further question"
    )


def test_a_singular_question_closes_the_way_the_plural_does() -> None:
    """The recogniser gave us "question". The table listed "questions".

    A closing phrase is a phrase people SAY, and English speakers say both.
    Anchoring on the stem costs nothing here because the phrase carries its own
    negation - "don't have any further" is not a question in either number.
    """
    from ambassador.farewell import is_farewell, load_farewells

    farewells = load_farewells()
    for utterance in (
        "I don't have any further question",
        "I don't have any further questions",
        "no further question",
        "no further questions",
    ):
        assert is_farewell(utterance, farewells, "en"), utterance


def test_a_filler_does_not_cost_a_goodbye() -> None:
    """ "Uh" is not a change of subject, it is a person thinking.

    The recogniser keeps disfluencies, so a table that treats one as an
    unexplained token is a table that refuses real speech.
    """
    from ambassador.farewell import is_farewell, load_farewells

    farewells = load_farewells()
    for utterance in ("uh, goodbye", "um, that's it", "er, that is all thanks"):
        assert is_farewell(utterance, farewells, "en"), utterance


def test_a_courtesy_only_reply_is_reported_as_one() -> None:
    """The fact the adapter needs, and NOT a decision to end the call.

    `closes` stays false for "thank you" - that is the rule that keeps this
    module safe. What the reading gains is the ability to say "this utterance
    was nothing but courtesy", which is only a closing in the one context the
    adapter can see: the agent has just said goodbye.
    """
    from ambassador.farewell import load_farewells, read_farewell

    farewells = load_farewells()
    courteous = read_farewell(TURN_8, farewells, "en", names=NAMES)
    assert courteous.courtesy_only is True
    assert courteous.closes is False, "a courtesy still ends nothing on its own"
    assert courteous.has_phrase is False

    for utterance in ("what does a studio cost", "", "tell me about Skyrise"):
        assert (
            read_farewell(utterance, farewells, "en", names=NAMES).courtesy_only
            is False
        ), utterance

    # A real closing is not "courtesy only" either: it carries a phrase.
    assert read_farewell(TURN_9, farewells, "en").courtesy_only is False


@pytest.mark.parametrize("utterance", STILL_NOT_ENDINGS)
def test_the_widening_did_not_loosen_a_single_dangerous_case(utterance: str) -> None:
    """The half of this card that must not move."""
    from ambassador.farewell import is_farewell, load_farewells

    assert not is_farewell(utterance, load_farewells(), "en", names=NAMES), utterance


def test_the_goodbye_that_already_worked_still_works() -> None:
    """Turn 9, verbatim, and the tail of the same call."""
    from ambassador.farewell import is_farewell, load_farewells

    assert is_farewell(TURN_9, load_farewells(), "en")


async def test_a_thank_you_after_the_agent_said_goodbye_ends_the_call() -> None:
    """Turn 8, verbatim, in the context that makes it a closing.

    The model said goodbye on the previous turn and the call stayed open, so
    the buyer had to say it again. The close is ARMED rather than fired: the
    buyer's turn is not taken back from the model, so they hear the reply they
    are already owed and the call ends on its seal.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import (
        HealthyStream,
        json_lines,
        make_agent,
        preemptive_turn,
    )

    agent, log, buf, _spy = make_agent(
        [
            HealthyStream(["Thank you for calling Binghatti. ", "Goodbye. "]),
            HealthyStream(["You are very welcome. "]),
        ]
    )

    await preemptive_turn(agent, partial="Okay", final=TURN_8)
    assert agent._closing_turn is None, "nothing has said goodbye yet"

    await preemptive_turn(agent, partial="Okay", final=TURN_8)
    await log.aclose()

    reasons = [e["reason"] for e in json_lines(buf) if e["event"] == "call_ended"]
    assert reasons == ["agent_farewell"], (
        "the agent's goodbye is what ended this call; the buyer only agreed"
    )
    # The model's goodbye WAS the goodbye. Saying the authored farewell now
    # would be the third one in two turns.
    assert "farewell_spoken" not in buf.getvalue()


async def test_a_thank_you_with_no_goodbye_behind_it_keeps_the_call_open() -> None:
    """The case that keeps the new rule from being "thank you ends calls".

    Same utterance, same words, no goodbye anywhere before it: the call goes
    on. This is the guard, and it is the reason the rule lives in the adapter
    where the context is, rather than in the table.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import HealthyStream, json_lines, make_agent, preemptive_turn

    agent, log, buf, _spy = make_agent(
        [
            HealthyStream(["A studio is AED 985,000. "]),
            HealthyStream(["Of course, anything else? "]),
        ]
    )

    await preemptive_turn(agent, partial="What does", final="What does a studio cost?")
    await preemptive_turn(agent, partial="Okay", final=TURN_8)
    await log.aclose()

    assert agent._closing_turn is None
    assert [e for e in json_lines(buf) if e["event"] == "call_ended"] == []


async def test_a_question_after_the_agents_goodbye_is_still_a_question() -> None:
    """The buyer is not finished just because the model tried to finish.

    A sign-off followed by a real question must not end the call, and the
    sign-off must not sit there waiting to end the NEXT courtesy either - two
    turns later, "thank you" is an ordinary thank you again.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import HealthyStream, json_lines, make_agent, preemptive_turn

    agent, log, buf, _spy = make_agent(
        [
            HealthyStream(["Feel free to reach out. ", "Goodbye. "]),
            HealthyStream(["A studio is AED 985,000. "]),
            HealthyStream(["Anything else? "]),
        ]
    )

    await preemptive_turn(agent, partial="Okay", final="Okay.")
    await preemptive_turn(agent, partial="What does", final="What does a studio cost?")
    assert agent._closing_turn is None, "a question is not an acknowledgement"

    await preemptive_turn(agent, partial="Okay", final=TURN_8)
    await log.aclose()

    assert [e for e in json_lines(buf) if e["event"] == "call_ended"] == [], (
        "the goodbye was two turns ago and the conversation moved on"
    )
