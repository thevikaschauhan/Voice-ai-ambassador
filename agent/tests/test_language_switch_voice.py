"""Exercise final-transcript switching through the actual agent and guard."""

import json
from inspect import signature
from io import StringIO

import pytest

pytest.importorskip("livekit.agents")
from livekit.agents import UserInputTranscribedEvent, llm  # noqa: E402
from adapter.agent import AmbassadorAgent  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from adapter.stt_factory import build_stt  # noqa: E402
from ambassador.ambassadors import load_ambassadors  # noqa: E402
from ambassador.contact import ContactPolicy, load_contact_copy  # noqa: E402
from ambassador.language_switch import choose_language  # noqa: E402
from ambassador.prompts import LANGUAGE_NAMES  # noqa: E402
from test_agent import make_settings  # noqa: E402


def make_agent(**settings):
    return AmbassadorAgent(
        settings=make_settings(
            auto_language_switch=True, allow_uncertified_language=True, **settings
        ),
        log=EventLog("language-test", stream=StringIO()),
    )


# One turn can no longer switch: the rule needs a challenger to lead for more
# than one. Taken from the rule's own default so these tests cannot drift into
# asserting a switch that never happens.
REQUIRED_TURNS: int = signature(choose_language).parameters["required_turns"].default


async def switch_over(agent, language, text="A sufficiently long recognised utterance"):
    """Drive the whole streak the rule requires before it will act."""
    for _ in range(REQUIRED_TURNS):
        await final_turn(agent, language, text)


async def final_turn(agent, language, text="A sufficiently long recognised utterance"):
    agent.note_transcribed_language(
        UserInputTranscribedEvent(language=language, transcript=text, is_final=True)
    )
    ctx = llm.ChatContext()
    message = ctx.add_message(role="user", content=text)
    await agent.on_user_turn_completed(ctx, message)


@pytest.mark.parametrize("language", ("ar", "hi"))
async def test_final_speech_changes_prompt_and_guard_without_changing_identity(
    language,
):
    """A switch swaps the speech profile and keeps the identity.

    Driven INTO English, because a target with no authored farewell phrases is
    now refused and English is the only language that has them - every other
    target is covered by the refusal test in test_language_switch_fixes.py.
    The opening languages here are the two non-English ones that HAVE an
    ambassador name, which is what makes the identity assertion mean something:
    a call that opens as Nora is still Nora after switching to English. The
    name is a fact about the call, not about the language being spoken.
    """
    agent = make_agent(language=language)
    try:
        voices: list[str] = []
        agent.set_tts_voice_updater(voices.append)
        history = llm.ChatContext()
        history.add_message(role="user", content="I am looking for a studio.")
        await agent.update_chat_ctx(history)
        prior_messages = [
            item for item in agent.chat_ctx.items if item.role != "system"
        ]
        brief = agent.brief_extractor
        await switch_over(agent, "en")
        assert agent._guard.language == "en"
        assert f"Reply in {LANGUAGE_NAMES['en']}" in agent.instructions
        assert f"Your name is {load_ambassadors().name_for(language)}" in (
            agent.instructions
        )
        assert [
            item for item in agent.chat_ctx.items if item.role != "system"
        ] == prior_messages
        assert agent.brief_extractor is brief
        assert agent._session_voice_id == make_settings().voice_id("en")
        assert voices[-1] == make_settings().voice_id("en")
        # Both success and blocked speech still use the real guard pipeline.
        assert agent._guard.check("Welcome.").outcome == "pass"
        assert (
            agent._guard.check("The price is AED 987654321.").outcome
            == "violation_blocked"
        )
        assert agent._guard.compose("Let me put you through to one of our ambassadors.")
        assert agent._budget_policy_runs
        # And it does NOT switch back: the opening language has no authored
        # closing phrases, so going there would leave a call the buyer cannot
        # end. Refused, and the refusal is on the stream.
        await switch_over(agent, language, "Ein ausreichend langer Satz hier bitte")
        assert agent._settings.language == "en"
    finally:
        await agent.brief_extractor.aclose()


async def test_partials_do_not_switch_and_short_final_does_not_inherit_partial_language():
    agent = make_agent(language="fr")
    try:
        agent.note_transcribed_language(
            UserInputTranscribedEvent(
                language="en", transcript="A long partial transcript", is_final=False
            )
        )
        # A short final cannot inherit the partial's language, and the partial
        # cannot carry the switch on its own.
        await final_turn(agent, "en", "Yes")
        assert agent._settings.language == "fr"
        # A full streak of finals does switch.
        await switch_over(agent, "en")
        assert agent._settings.language == "en"
    finally:
        await agent.brief_extractor.aclose()


async def test_uncertified_switch_refused_without_explicit_demo_override():
    """And refused for THAT reason, which the outcome alone cannot show.

    Two independent barriers now stand in front of every non-English target -
    no certified disclosure, and no authored closing phrases - so
    `settings.language` staying 'en' proves only that one of them held. This
    test kept passing with the certification check deleted, because the
    farewell gate caught French as well. The reason on the event is the only
    thing that distinguishes them, so that is what it asserts. The full streak
    is driven for the same reason: one turn would not reach a gate at all.
    """
    buf = StringIO()
    log = EventLog("test", stream=buf)
    agent = AmbassadorAgent(
        settings=make_settings(auto_language_switch=True),
        log=log,
    )
    try:
        await switch_over(agent, "fr")
        assert agent._settings.language == "en"
    finally:
        await agent.brief_extractor.aclose()
    await log.aclose()
    skipped = [
        json.loads(line)
        for line in buf.getvalue().splitlines()
        if line.strip()
        and json.loads(line)["event"] == "response_language_switch_skipped"
    ]
    assert [record["reason"] for record in skipped] == ["uncertified_language"]


async def test_contact_permission_is_not_inferred_by_switching_language():
    agent = make_agent(language="fr")
    try:
        # In English because the ask has to have HAPPENED for cancelling it to
        # mean anything, and contact copy exists for English.
        contact = ContactPolicy(load_contact_copy(), "en")
        contact.on_farewell(1)
        agent._contact = contact
        agent._contact_awaiting_reply = True
        await switch_over(agent, "en")
        assert contact.state.status == "unconfirmed"
        assert not contact.state.confirmed
        assert not contact.state.contact_permission
        await final_turn(agent, "en")
        assert not contact.owes_request()
    finally:
        await agent.brief_extractor.aclose()


def test_full_language_switching_refuses_a_fixed_language_recogniser():
    with pytest.raises(ValueError, match="requires STT_PROVIDER=soniox"):
        build_stt(make_settings(stt_enabled=True, auto_language_switch=True))


def test_soniox_uses_language_identification_without_translation(monkeypatch):
    from livekit.plugins import soniox

    captured = {}
    monkeypatch.setattr(soniox, "STT", lambda **kwargs: captured.update(kwargs))
    build_stt(
        make_settings(
            stt_enabled=True, stt_provider="soniox", auto_language_switch=True
        )
    )
    params = captured["params"]
    assert set(params.language_hints) == set(LANGUAGE_NAMES)
    assert params.enable_language_identification
    assert params.translation is None


def test_soniox_credential_is_required_only_when_selected():
    assert (
        "SONIOX_API_KEY"
        in make_settings(stt_enabled=True, stt_provider="soniox").missing_for_voice()
    )
    assert (
        "SONIOX_API_KEY"
        not in make_settings(
            stt_enabled=False, stt_provider="soniox"
        ).missing_for_voice()
    )
