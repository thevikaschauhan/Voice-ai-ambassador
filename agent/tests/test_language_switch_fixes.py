"""The review fixes for 3ab4928's mid-call language switch.

One test per finding on task-review-3ab4928-language-switch. The findings are
named in comments so a future reader can get from a failure back to the reason
the behaviour is wanted, rather than guessing from the assertion.

These drive the real hook, the real guard and the real event log, because every
one of these findings was about what the ADAPTER does on a turn - a fake turn
path would have passed for the broken code too.
"""

import json
from io import StringIO
from typing import get_args

import pytest

pytest.importorskip("livekit.agents")
from livekit.agents import UserInputTranscribedEvent, llm  # noqa: E402
from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID  # noqa: E402

from adapter.agent import AmbassadorAgent  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from ambassador.prompts import LANGUAGE_NAMES  # noqa: E402
from ambassador.schemas import Language  # noqa: E402
from test_agent import make_settings  # noqa: E402

# Long enough to clear choose_language's minimum, and English enough to win its
# share test, so these turns switch for the reason under test and not by luck.
ENGLISH_UTTERANCE = "Tell me about the payment plan for this apartment please"

NON_ENGLISH = tuple(language for language in get_args(Language) if language != "en")


def make_agent(**settings):
    """An agent mid-call, with the switch enabled and the event stream captured."""
    buf = StringIO()
    agent = AmbassadorAgent(
        settings=make_settings(
            **{
                "auto_language_switch": True,
                "allow_uncertified_language": True,
                **settings,
            }
        ),
        log=EventLog("lsfix", stream=buf),
    )
    return agent, buf


def events(buf, name):
    records = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    return [record for record in records if record["event"] == name]


async def switching_turn(agent, language, text=ENGLISH_UTTERANCE):
    """One buyer turn whose FINAL transcript the recogniser reports as `language`.

    Returns the turn context the framework would generate from, which is the
    object the hook receives and the one P1b is about.
    """
    agent.note_transcribed_language(
        UserInputTranscribedEvent(language=language, transcript=text, is_final=True)
    )
    turn_ctx = llm.ChatContext()
    message = turn_ctx.add_message(role="user", content=text)
    await agent.on_user_turn_completed(turn_ctx, message)
    return turn_ctx


def instructions_in(turn_ctx):
    index = turn_ctx.index_by_id(INSTRUCTIONS_MESSAGE_ID)
    return None if index is None else turn_ctx.items[index].content[0]


# -- P1b: the switch turn spoke the OLD language in the NEW voice -------------


async def test_the_turn_that_triggers_the_switch_generates_under_the_new_language():
    """P1b. The voice changed on this turn, so the prompt has to as well.

    The framework copies the chat context BEFORE calling the hook and generates
    from that copy, so writing only to the agent's own context left this turn
    being generated under the previous language while it was already being
    spoken in the new language's voice.
    """
    agent, _ = make_agent(language="hi")
    try:
        turn_ctx = await switching_turn(agent, "en")
        assert agent._settings.language == "en"
        assert f"Reply in {LANGUAGE_NAMES['en']}" in instructions_in(turn_ctx)
    finally:
        await agent.brief_extractor.aclose()


async def test_a_turn_that_does_not_switch_leaves_the_turn_context_untouched():
    """The other half of P1b: editing the turn context invalidates the
    framework's preemptive generation, so a turn that does not switch must not
    touch it or every turn would pay for the feature."""
    agent, _ = make_agent()
    try:
        turn_ctx = await switching_turn(agent, "en")
        assert instructions_in(turn_ctx) is None
    finally:
        await agent.brief_extractor.aclose()


# -- P1d: a switch cancelled a pending hang-up -------------------------------


async def test_a_switch_does_not_cancel_a_pending_sign_off():
    """P1d. The model said goodbye on the previous turn; the buyer answering in
    another language must not un-say it. A sign-off is not a language-bound
    read-back, unlike the confirmation questions that DO reset."""
    agent, _ = make_agent(language="hi")
    try:
        agent._signed_off_turn = 1
        await switching_turn(agent, "en")
        assert agent._settings.language == "en"
        assert agent._signed_off_turn == 1
    finally:
        await agent.brief_extractor.aclose()


# -- P0: after a switch the buyer could not end the call ---------------------


@pytest.mark.parametrize("language", NON_ENGLISH)
async def test_a_target_with_no_authored_farewell_phrases_is_refused(language):
    """P0. Goodbye detection is keyed on the response language, and only English
    has authored closing phrases, so switching away from English left a call
    the buyer could not end. Refused rather than degraded: the switch is the
    optional feature, being able to hang up is not."""
    agent, buf = make_agent()
    try:
        await switching_turn(agent, language, text="Ein ausreichend langer Satz hier")
        assert agent._settings.language == "en"
        assert agent._farewell_detects
        assert [
            record["reason"] for record in events(buf, "response_language_switch_skipped")
        ] == ["no_farewell_coverage"]
    finally:
        await agent.brief_extractor.aclose()


# -- P1e: a raise inside the switch dropped the turn AND its record ----------


async def test_a_failure_inside_the_switch_keeps_the_turn_and_the_old_language(
    monkeypatch,
):
    """P1e. The framework swallows an exception from this hook and returns,
    which skips the reply - and because the switch runs before the tracker is
    started, the turn went missing from the record too, with nothing on the
    durable stream to say why."""
    agent, buf = make_agent(language="hi")
    try:

        def unbuildable(**kwargs):
            raise RuntimeError("a speech profile that cannot be built")

        monkeypatch.setattr(agent, "_guard_factory", unbuildable)
        await switching_turn(agent, "en")
        assert agent._settings.language == "hi"
        assert agent.tracker is not None
        assert agent.tracker.buyer_utterance == ENGLISH_UTTERANCE
        assert [
            record["error"] for record in events(buf, "response_language_switch_failed")
        ] == ["RuntimeError"]
    finally:
        await agent.brief_extractor.aclose()


# -- P2: file I/O and one skip event per turn, for the whole call -------------


async def test_a_refused_switch_neither_re_reads_the_yaml_nor_repeats_itself(
    monkeypatch,
):
    """P2. The certification check read disclosures.yaml from disk inside the
    turn path, and emitted a skip event on EVERY turn, for a buyer who was
    simply speaking another language."""
    agent, buf = make_agent(allow_uncertified_language=False)
    try:
        import adapter.agent as agent_module

        def refuse_to_read(*args, **kwargs):
            raise AssertionError("disclosures re-read inside the turn path")

        monkeypatch.setattr(agent_module, "load_disclosures", refuse_to_read)
        await switching_turn(agent, "de", text="Ein ausreichend langer Satz hier")
        await switching_turn(agent, "de", text="Noch ein ausreichend langer Satz")
        assert agent._settings.language == "en"
        assert [
            record["reason"] for record in events(buf, "response_language_switch_skipped")
        ] == ["uncertified_language"]
    finally:
        await agent.brief_extractor.aclose()


# -- P2: a bilingual call was unauditable per turn ---------------------------


async def test_the_turn_record_carries_the_language_the_turn_was_spoken_in():
    """P2. The call-level record reports the language at hang-up, so a call that
    switched could not say which language any given turn was answered in."""
    agent, _ = make_agent(language="hi")
    try:
        await switching_turn(agent, "en")
        assert agent.tracker is not None
        assert agent.tracker.finish(audit_incomplete=False).language == "en"
    finally:
        await agent.brief_extractor.aclose()
