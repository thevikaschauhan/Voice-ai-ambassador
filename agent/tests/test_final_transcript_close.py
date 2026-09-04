"""How often is the goodbye seen ONLY in the final transcript? (P2, jim)

The card asks for a measurement before a behaviour change, and it is right to:
`_note_closing_from_final` arms the close without consulting `ContactPolicy`,
so a call whose goodbye appears only there ends with `contact_status=not_asked`
- but nobody knows how many calls that is, because the stream cannot tell the
two paths apart. `call_ended` reads `buyer_farewell` either way.

So this is the instrument, not the fix. One event, two enumerated facts:

  farewell_from_final   the close was armed from the FINAL transcript, which
                        means the deterministic seam never saw the closing
                        (it read the partial), and the model is already
                        answering - the turn cannot be taken back without the
                        double-goodbye that path exists to avoid
  contact_owed          whether a contact ask was still owed at that moment,
                        which is what the miss actually costs

With those two, the decision the card asks for becomes arithmetic on real
traffic rather than an argument: if the path is rare, or rarely owes an ask, it
stays ask-free and is documented as such; if it is common, the ask moves to the
next turn boundary and pays for the complexity.

The other half of a measurement is that it must not double-count. A closing the
deterministic seam already read is NOT this path, and the guard for that is
here too - otherwise the number answers a different question than the one asked.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _agent(language: str = "en", replies: int = 4, contact: bool = True):
    """An agent with the contact policy wired, and its event buffer."""
    from test_agent import HealthyStream, SpyLLM, make_settings

    from adapter.agent import AmbassadorAgent, build_contact_policy
    from adapter.events import EventLog

    settings = make_settings(language=language)
    buffer = StringIO()
    log = EventLog("sess_final", stream=buffer, verbose=False)
    agent = AmbassadorAgent(
        settings=settings,
        log=log,
        contact=build_contact_policy(settings, log) if contact else None,
    )
    agent._llm = SpyLLM([HealthyStream(["A studio is AED 985,000. "])] * replies)
    return agent, log, buffer


async def test_a_closing_seen_only_in_the_final_transcript_says_so() -> None:
    """The event that makes the path countable.

    The partial carries no closing and the final does, which is the shape the
    framework produces when it judges the two equivalent and never calls
    `llm_node` again. Today that arms the close in silence.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import json_lines, preemptive_turn

    agent, log, buffer = _agent()
    await preemptive_turn(agent, partial="Okay that is", final="Okay, that is all.")
    await log.aclose()

    events = [e for e in json_lines(buffer) if e["event"] == "farewell_from_final"]
    assert len(events) == 1, "the path that armed the close has to be visible"
    assert events[0]["turn"] == 1
    assert events[0]["reason"] == "buyer_farewell"
    # The words themselves stay off this stream, like every other farewell event.
    assert "that is all" not in buffer.getvalue()


async def test_the_event_says_whether_a_contact_ask_was_owed() -> None:
    """What the miss costs, on the same line as the miss.

    A path that is common and never owes an ask needs no fix at all; a path
    that is rare and always owes one is a different decision. The count alone
    cannot tell those apart, so the event carries both.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import json_lines, preemptive_turn

    agent, log, buffer = _agent()
    await preemptive_turn(agent, partial="Okay that is", final="Okay, that is all.")
    await log.aclose()

    events = [e for e in json_lines(buffer) if e["event"] == "farewell_from_final"]
    assert events[0]["contact_owed"] is True, (
        "the ask was owed and this path skipped it - that is the cost the card "
        "is asking us to size"
    )


async def test_no_ask_owed_is_reported_as_no_ask_owed() -> None:
    """The other value, and the one that could make this a no-op.

    A call with no contact policy at all - the eval harness, and any language
    with no reviewed ask - loses nothing on this path, and the measurement has
    to say so rather than counting every closing as a cost.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import json_lines, preemptive_turn

    agent, log, buffer = _agent(contact=False)
    await preemptive_turn(agent, partial="Okay that is", final="Okay, that is all.")
    await log.aclose()

    events = [e for e in json_lines(buffer) if e["event"] == "farewell_from_final"]
    assert len(events) == 1
    assert events[0]["contact_owed"] is False


async def test_the_courtesy_only_close_on_this_path_is_counted_too() -> None:
    """The second way this seam arms a close, and it must not be invisible.

    A courtesy-only reply after the model signed off closes the call here as
    well, with `agent_farewell`. Counting only the phrase shape would size the
    problem too small.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import HealthyStream, SpyLLM, json_lines, preemptive_turn

    agent, log, buffer = _agent()
    agent._llm = SpyLLM(
        [
            HealthyStream(["Thank you for calling Binghatti. ", "Goodbye. "]),
            HealthyStream(["You are very welcome. "]),
        ]
    )

    await preemptive_turn(agent, partial="Okay", final="Okay.")
    # "Fine" is not a courtesy token, so the partial is NOT courtesy-only and
    # the deterministic seam declines the turn: this seam is the one that sees
    # the acknowledgement, which is the case being counted.
    await preemptive_turn(agent, partial="Fine", final="Okay. Thank you.")
    await log.aclose()

    events = [e for e in json_lines(buffer) if e["event"] == "farewell_from_final"]
    assert [e["reason"] for e in events] == ["agent_farewell"]


async def test_a_closing_the_deterministic_seam_read_is_not_counted_here() -> None:
    """The guard that keeps the measurement answering its own question.

    When the partial already carried the closing, the deterministic seam took
    the turn and spoke the authored farewell - a different path with a
    different cost, because the ask CAN be made there and is. Counting it here
    would inflate the number the decision rests on.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import json_lines, preemptive_turn

    agent, log, buffer = _agent()
    await preemptive_turn(agent, partial="Goodbye", final="Goodbye.")
    await log.aclose()

    stream = list(json_lines(buffer))
    assert [e for e in stream if e["event"] == "farewell_from_final"] == []
    # And the ask WAS made on that path, which is the contrast the number is
    # meant to expose.
    assert [e for e in stream if e["event"] == "contact_asked"]


async def test_a_goodbye_in_the_final_does_not_hang_up_on_the_ask_it_triggered() -> (
    None
):
    """The defect this instrument found on its first run, and it is live.

    The buyer says "Goodbye." The deterministic seam reads the PARTIAL, the
    contact ask takes the turn, and the close is deliberately NOT armed - the
    call has to stay open for the answer. Then the final transcript arrives
    with the same goodbye in it, this seam reads it as a closing, and arms the
    close on the very turn the ask was spoken on. The buyer is asked for their
    number and hung up on before they can give it.

    Reachable on every voice call that ends politely, because the ask ALWAYS
    takes the turn the goodbye arrived on. It is the interaction between #132
    and this seam rather than a fault in either alone, which is why it showed
    up as soon as one test drove both.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

    from test_agent import json_lines, preemptive_turn

    agent, log, buffer = _agent()
    await preemptive_turn(agent, partial="Goodbye", final="Goodbye.")

    # `_closing` rather than `_closing_turn`: the turn field is CLEARED when a
    # close fires, so asserting it is None would pass in the broken case too.
    assert agent._closing is False, (
        "the contact ask took this turn; arming the close here cuts it off"
    )
    stream = list(json_lines(buffer))
    assert [e for e in stream if e["event"] == "contact_asked"], "the ask happened"
    assert [e for e in stream if e["event"] == "call_ended"] == []

    # And the exchange still finishes: the reply settles it and the farewell
    # takes that turn, exactly as it does when no final transcript intervenes.
    await preemptive_turn(agent, partial="No thanks", final="No thanks.")
    await log.aclose()
    assert agent._contact.state.status == "declined"
    # `_closing`, again: this turn runs to its SEAL, so the close does not stay
    # armed - it fires, and `_closing_turn` is cleared on the way out.
    assert agent._closing is True
    assert [e["reason"] for e in json_lines(buffer) if e["event"] == "call_ended"] == [
        "buyer_farewell"
    ]
