"""Exercise final-transcript switching through the actual agent and guard."""

from io import StringIO
from typing import get_args

import pytest

pytest.importorskip("livekit.agents")
from livekit.agents import UserInputTranscribedEvent, llm  # noqa: E402
from adapter.agent import AmbassadorAgent  # noqa: E402
from adapter.events import EventLog  # noqa: E402
from adapter.stt_factory import build_stt  # noqa: E402
from ambassador.contact import ContactPolicy, load_contact_copy  # noqa: E402
from ambassador.prompts import LANGUAGE_NAMES  # noqa: E402
from ambassador.schemas import Language  # noqa: E402
from test_agent import make_settings  # noqa: E402


def make_agent(**settings):
    return AmbassadorAgent(
        settings=make_settings(
            auto_language_switch=True, allow_uncertified_language=True, **settings
        ),
        log=EventLog("language-test", stream=StringIO()),
    )


async def final_turn(agent, language, text="A sufficiently long recognised utterance"):
    agent.note_transcribed_language(
        UserInputTranscribedEvent(language=language, transcript=text, is_final=True)
    )
    ctx = llm.ChatContext()
    message = ctx.add_message(role="user", content=text)
    await agent.on_user_turn_completed(ctx, message)


@pytest.mark.parametrize(
    "language", tuple(language for language in get_args(Language) if language != "en")
)
async def test_final_speech_changes_prompt_and_guard_without_changing_identity(
    language,
):
    agent = make_agent()
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
        await final_turn(agent, language)
        assert agent._guard.language == language
        assert f"Reply in {LANGUAGE_NAMES[language]}" in agent.instructions
        assert "Your name is Jane" in agent.instructions
        assert [
            item for item in agent.chat_ctx.items if item.role != "system"
        ] == prior_messages
        assert agent.brief_extractor is brief
        assert agent._session_voice_id == make_settings().voice_id(language)
        assert voices[-1] == make_settings().voice_id(language)
        # Both success and blocked speech still use the real guard pipeline.
        assert agent._guard.check("Welcome.").outcome == "pass"
        assert (
            agent._guard.check("The price is AED 987654321.").outcome
            == "violation_blocked"
        )
        assert agent._guard.compose("Let me put you through to one of our ambassadors.")
        await final_turn(agent, "en")
        assert agent._guard.language == "en"
        assert agent._budget_policy_runs
    finally:
        await agent.brief_extractor.aclose()


async def test_partials_do_not_switch_and_short_final_does_not_inherit_partial_language():
    agent = make_agent()
    try:
        agent.note_transcribed_language(
            UserInputTranscribedEvent(
                language="fr", transcript="A long partial transcript", is_final=False
            )
        )
        await final_turn(agent, "en", "Yes")
        assert agent._settings.language == "en"
        await final_turn(agent, "fr")
        await final_turn(agent, "en", "Yes")
        assert agent._settings.language == "fr"
    finally:
        await agent.brief_extractor.aclose()


async def test_uncertified_switch_refused_without_explicit_demo_override():
    agent = AmbassadorAgent(
        settings=make_settings(auto_language_switch=True),
        log=EventLog("test", stream=StringIO()),
    )
    try:
        await final_turn(agent, "fr")
        assert agent._settings.language == "en"
    finally:
        await agent.brief_extractor.aclose()


async def test_contact_permission_is_not_inferred_by_switching_language():
    agent = make_agent()
    try:
        contact = ContactPolicy(load_contact_copy(), "en")
        contact.on_farewell(1)
        agent._contact = contact
        agent._contact_awaiting_reply = True
        await final_turn(agent, "fr")
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
