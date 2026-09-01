"""Adapter configuration, and the one rule that has no recovery: a secret that
reaches a log cannot be un-logged.

The `session_start` event serialises the whole settings object, so the
redaction is not a nicety - it is the only thing between the demo's stdout and
every live credential the process holds.

That last phrase used to name a number. It said "four live credentials", and
it was four when it was written; the redaction test enumerated those same four
and stayed green while a fifth, DEEPGRAM_API_KEY, printed in full. Both the
sentence and the test are now written against whatever the dataclass actually
carries, because a count is a claim that rots without failing.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import get_args

import pytest

from ambassador.schemas import Language

from adapter.config import (
    PROVISIONAL_VOICE_ID_AR,
    PROVISIONAL_VOICE_ID_EN,
    PROVISIONAL_VOICE_ID_HI,
    Settings,
    _is_credential,
    load_settings,
    missing_credentials_error,
    parse_env_file,
)

REAL_LOOKING_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                f"OPENROUTER_API_KEY={REAL_LOOKING_KEY}",
                "FISH_API_KEY=fish-secret-value",
                "LIVEKIT_API_SECRET=lk-secret-value",
                "LLM_MODEL=qwen/qwen3.7-flash",
                "FISH_TTS_MODEL=s2.1-pro        # inline comment is stripped",
                "GUARDRAIL_MODE=warn",
                "PROMPT_MODE=naive",
                "TTS_VOICE_ID_EN=voice-en-1",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_parses_comments_blanks_and_inline_comments(env_file):
    values = parse_env_file(env_file)
    assert values["FISH_TTS_MODEL"] == "s2.1-pro"
    assert values["LLM_MODEL"] == "qwen/qwen3.7-flash"
    assert "# a comment" not in values


def test_process_environment_overrides_the_file(env_file, monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    settings = load_settings(env_file)
    assert settings.guardrail_mode == "enforce"  # process wins
    assert settings.prompt_mode == "naive"  # file value still applies


def test_missing_file_is_not_an_error(tmp_path):
    assert parse_env_file(tmp_path / "absent.env") == {}


def test_no_secret_value_survives_repr_or_redaction(env_file):
    settings = load_settings(env_file)
    secrets = [REAL_LOOKING_KEY, "fish-secret-value", "lk-secret-value"]

    rendered = repr(settings)
    dumped = json.dumps(settings.redacted())

    for secret in secrets:
        assert secret not in rendered
        assert secret not in dumped

    # Presence is still reportable - the operator needs to know a key is set.
    assert settings.redacted()["openrouter_api_key"] == "<set>"
    assert settings.redacted()["livekit_api_key"] == "<unset>"
    # Non-secret configuration stays legible.
    assert settings.redacted()["fish_tts_model"] == "s2.1-pro"
    assert settings.redacted()["tts_voice_id_en"] == "voice-en-1"


def test_missing_voice_credentials_are_named_not_echoed(env_file, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    settings = load_settings(env_file)
    # An empty process value falls back to the file, which has the key set.
    assert settings.missing_for_voice() == []

    bare = Settings(
        **{**settings.redacted(), "openrouter_api_key": "", "fish_api_key": ""}
    )  # type: ignore[arg-type]
    assert bare.missing_for_voice() == ["OPENROUTER_API_KEY", "FISH_API_KEY"]


def test_thinking_is_off_unless_explicitly_turned_on(env_file, monkeypatch):
    assert load_settings(env_file).thinking_disabled is True

    monkeypatch.setenv("LLM_THINKING", "on")
    assert load_settings(env_file).thinking_disabled is False

    # Anything ambiguous stays safe: guessing wrong costs seconds before first
    # audio (ADR-016).
    monkeypatch.setenv("LLM_THINKING", "maybe")
    assert load_settings(env_file).thinking_disabled is True


def test_invalid_mode_fails_loudly_at_startup(env_file, monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "off")
    with pytest.raises(ValueError, match="GUARDRAIL_MODE"):
        load_settings(env_file)

    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    monkeypatch.setenv("PROMPT_MODE", "helpful")
    with pytest.raises(ValueError, match="PROMPT_MODE"):
        load_settings(env_file)


def test_stt_is_off_by_default_so_the_agent_runs_without_audio_credit(env_file):
    assert load_settings(env_file).stt_enabled is False


# --- credential classification -------------------------------------------
#
# The tests above enumerate; these derive. Both are wanted: the enumerated one
# proves the specific credentials in the fixture are handled, and these prove
# the handling extends to credentials nobody has thought of yet.


def test_every_credential_field_is_redacted_whatever_it_is_called(env_file):
    """Canaries built from the dataclass, so a new credential needs no edit here.

    Each credential-bearing field gets a distinct, unmistakable value; none of
    them may appear in the two renderings that reach a human.
    """
    base = load_settings(env_file).redacted()
    credentials = [f.name for f in fields(Settings) if _is_credential(f.name)]
    assert credentials, "no field was classified as a credential, so this is vacuous"

    canaries = {name: f"CANARY-{name}-must-not-print" for name in credentials}
    settings = Settings(**{**base, **canaries})  # type: ignore[arg-type]

    rendered = repr(settings)
    dumped = json.dumps(settings.redacted())
    for name, canary in canaries.items():
        assert canary not in rendered, name
        assert canary not in dumped, name
        assert settings.redacted()[name] == "<set>", name


def test_the_credential_rule_covers_the_names_this_system_actually_uses():
    """The set claim, stated as membership rather than as a count.

    `deepgram_api_key` is named explicitly because it is the one that regressed.
    """
    classified = {f.name for f in fields(Settings) if _is_credential(f.name)}
    assert {
        "livekit_api_key",
        "livekit_api_secret",
        "openrouter_api_key",
        "fish_api_key",
        "deepgram_api_key",
    } <= classified


# Deliberately looser than `_is_credential`: it matches a credential word
# ANYWHERE in the name, so it also flags run-together spellings like
# `livekit_apikey` that the real rule skips to keep plain configuration
# readable. The looseness is the point - this is a build-time tripwire, not
# the masking rule.
_CREDENTIAL_SUBSTRINGS = ("key", "secret", "token", "password", "credential")


def test_no_credential_looking_field_escapes_classification():
    """The gap `_is_credential` leaves on purpose, closed at build time.

    `_is_credential` matches whole underscore-separated parts so that `monkey`
    is not a `key`. That means a field named `livekit_apikey` or `authtoken`
    would slip through and print in full. Rather than loosen the masking rule
    and make real configuration unreadable in the operator's own log, this
    fails the suite the moment such a field is added, and the fix is to rename
    it to the `*_api_key` convention the rest of the settings already use.
    """
    suspicious = [
        field.name
        for field in fields(Settings)
        if any(word in field.name.lower() for word in _CREDENTIAL_SUBSTRINGS)
    ]
    assert suspicious, "no field looks credential-bearing, so this is vacuous"

    unclassified = [name for name in suspicious if not _is_credential(name)]
    assert not unclassified, (
        "these fields read as credentials but are not masked, so their values "
        f"reach repr() and the event stream: {unclassified}. Rename them to the "
        "underscore-separated convention (…_api_key, …_api_secret) that "
        "_is_credential recognises."
    )


def test_plausible_future_credential_names_are_caught_and_plain_config_is_not():
    # Caught: the shapes a future vendor field is likely to take.
    for name in (
        "twilio_auth_token",
        "webhook_signing_secret",
        "db_password",
        # Plurals. The first version of the rule compared whole parts
        # against singular words only and let every one of these through.
        "fish_api_keys",
        "auth_tokens",
        "service_credentials",
        "signing_secrets",
    ):
        assert _is_credential(name), name
    # Not caught, and must not be: redacting these would make the operator's
    # own configuration unreadable in the very log they check it from.
    for name in ("llm_model", "tts_voice_id_en", "stt_provider", "monkey_patch"):
        assert not _is_credential(name), name


def test_the_recogniser_credential_is_demanded_only_when_that_recogniser_runs(
    env_file,
):
    """Conditional on the selected provider, so the preflight is neither
    silent about Deepgram nor wrong about OpenRouter."""
    base = load_settings(env_file).redacted()

    def settings(**overrides):
        return Settings(**{**base, **overrides})  # type: ignore[arg-type]

    deepgram_live = settings(
        stt_enabled=True, stt_provider="deepgram", deepgram_api_key=""
    )
    assert "DEEPGRAM_API_KEY" in deepgram_live.missing_for_voice()

    # Same missing key, but nothing will construct the recogniser.
    assert (
        "DEEPGRAM_API_KEY"
        not in settings(
            stt_enabled=False, stt_provider="deepgram", deepgram_api_key=""
        ).missing_for_voice()
    )
    assert (
        "DEEPGRAM_API_KEY"
        not in settings(
            stt_enabled=True, stt_provider="openrouter", deepgram_api_key=""
        ).missing_for_voice()
    )

    # Set, so not reported. STT_PROVIDER is matched case-insensitively.
    assert (
        settings(
            stt_enabled=True, stt_provider="Deepgram", deepgram_api_key="dg-set"
        ).missing_for_voice()
        == []
    )


# --- ADR-017: the shipped example must select the recogniser the ADR chose ---
#
# ADR-017 (docs/01-) says Deepgram nova-3 IS the recogniser and the
# whole-utterance OpenRouter path "stays selectable and tested, but is not the
# default". Code said otherwise: `STT_PROVIDER` defaulted to "openrouter" and
# agent/.env.example shipped it, so an operator who copied the example ran the
# retired recogniser - p50 1081ms after endpoint against 258-327ms, "Binghatti"
# heard as "Bint Jbeil", and figures arriving as words that ADR-011's
# deterministic confirmation cannot parse.

EXAMPLE_ENV = Path(__file__).resolve().parents[1] / ".env.example"


def test_the_shipped_example_selects_the_adr_017_recogniser():
    """The operator path, end to end: copy .env.example, fill the keys, run."""
    assert EXAMPLE_ENV.exists()
    assert load_settings(EXAMPLE_ENV).stt_provider == "deepgram"


def test_the_default_with_no_env_file_at_all_is_the_adr_017_recogniser():
    """A checkout with no .env - CI, a fresh clone, a container with only real
    env vars set - must not fall back to the retired path either."""
    assert load_settings(Path("/nonexistent/.env")).stt_provider == "deepgram"


def test_the_example_still_documents_the_key_the_default_now_needs():
    """Flipping the default without the key beside it turns a working example
    into one that cannot start."""
    text = EXAMPLE_ENV.read_text(encoding="utf-8")
    assert "DEEPGRAM_API_KEY=" in text
    assert "DEEPGRAM_MODEL=nova-3" in text


def test_the_retired_path_is_still_selectable(env_file):
    """ADR-017 keeps it selectable and tested. A default change that removed the
    option would be a different decision from the one the ADR made."""
    base = load_settings(env_file).redacted()
    settings = Settings(  # type: ignore[arg-type]
        **{**base, "stt_provider": "openrouter", "stt_enabled": True}
    )
    assert settings.stt_provider == "openrouter"
    assert "DEEPGRAM_API_KEY" not in settings.missing_for_voice()


# --- the startup failure a keyless checkout gets --------------------------
#
# Flipping the default moves the cost of a missing key from "the demo is
# quietly four times slower" to "the agent will not start", which is the right
# trade only if the refusal explains itself. A bare list of variable names is
# diagnosable only by whoever already knows why each is needed.


def test_the_example_path_refuses_rather_than_falling_back_silently(env_file):
    """The behaviour change, at the level an operator meets it: copy the
    example, switch STT on, forget the Deepgram key. Before, this ran the
    retired recogniser and said nothing."""
    base = load_settings(EXAMPLE_ENV).redacted()
    settings = Settings(  # type: ignore[arg-type]
        **{
            **base,
            "stt_enabled": True,
            "openrouter_api_key": "k",
            "fish_api_key": "k",
            "deepgram_api_key": "",
        }
    )
    assert settings.stt_provider == "deepgram"
    assert settings.missing_for_voice() == ["DEEPGRAM_API_KEY"]


def test_the_failure_names_the_variable_and_every_way_out():
    message = missing_credentials_error(["DEEPGRAM_API_KEY"])
    assert "DEEPGRAM_API_KEY" in message
    # What it is for, so the refusal is not just an obstacle.
    assert "ADR-017" in message
    # The three ways out, including the two that need no new account.
    assert "console.deepgram.com" in message
    assert "STT_PROVIDER=openrouter" in message
    assert "STT_ENABLED=" in message
    assert "agent/.env.example" in message


def test_a_credential_with_no_remedy_is_still_named_plainly():
    """Most missing keys need no essay. Only the one whose requirement CHANGED
    carries a remedy, or the message becomes a wall nobody reads."""
    message = missing_credentials_error(["OPENROUTER_API_KEY", "FISH_API_KEY"])
    assert "OPENROUTER_API_KEY, FISH_API_KEY" in message
    assert "ADR-017" not in message
    assert len(message.splitlines()) == 2


def test_the_failure_message_echoes_no_value(env_file):
    """`missing_for_voice` reports by name only and this must not undo that: the
    message is printed by whatever supervisor restarted the worker."""
    settings = load_settings(env_file)
    message = missing_credentials_error(settings.missing_for_voice())
    for value in (
        settings.openrouter_api_key,
        settings.fish_api_key,
        settings.deepgram_api_key,
    ):
        if value:
            assert value not in message


# --- provisional voice ids ------------------------------------------------
#
# TTS_VOICE_ID_EN/_AR/_HI were empty, so every run fell through to
# `fishaudio.tts.DEFAULT_VOICE_ID` - one English voice nobody selected, used
# for Arabic and Hindi as well. They now default to the top register match in
# each language's shortlist (docs/voice-shortlist.md), marked PROVISIONAL: the
# client chooses at the meeting, and these only stop the demo choosing by
# accident in the meantime.


def test_every_shipped_language_has_a_provisional_voice_without_any_env():
    """A fresh clone, CI, a container with no .env: no language may fall
    through to Fish's own default voice, and no two languages may share an id -
    sharing one is the bug this replaces, wearing a different hat."""
    settings = load_settings(Path("/nonexistent/.env"))
    ids = {language: settings.voice_id(language) for language in get_args(Language)}
    assert all(ids.values()), ids
    assert len(set(ids.values())) == len(ids), ids


def test_the_shipped_example_and_the_code_default_agree():
    """The drift this repository has already had once, one variable deeper.

    `parse_env_file` records a bare `TTS_VOICE_ID_EN=` as an empty STRING, and
    an empty string is a value - `_resolve` returns it and the code default
    never runs. So an example left blank would silently switch the default off
    for every operator who copied it, which is exactly how ADR-017's recogniser
    default came to disagree with the shipped example. Both surfaces carry the
    ids, and this is what notices when only one of them is updated.
    """
    assert EXAMPLE_ENV.exists()
    settings = load_settings(EXAMPLE_ENV)
    assert settings.voice_id("en") == PROVISIONAL_VOICE_ID_EN
    assert settings.voice_id("ar") == PROVISIONAL_VOICE_ID_AR
    assert settings.voice_id("hi") == PROVISIONAL_VOICE_ID_HI


def test_a_blank_entry_in_a_local_env_still_wins_over_the_default(tmp_path):
    """Kept deliberately, and the reason this file has to repeat the ids rather
    than rely on the code. An operator who blanks the line in their own
    `agent/.env` is asking for Fish's default voice back, and gets it. That is
    the same mechanism that would have made a blank `.env.example` a silent
    regression, so the behaviour is pinned rather than assumed."""
    path = tmp_path / ".env"
    path.write_text("TTS_VOICE_ID_EN=\n", encoding="utf-8")
    assert load_settings(path).voice_id("en") == ""


def test_a_local_env_overrides_the_provisional_pick(tmp_path):
    """How a candidate gets auditioned without committing to it."""
    path = tmp_path / ".env"
    path.write_text("TTS_VOICE_ID_AR=some-other-voice\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.voice_id("ar") == "some-other-voice"
    assert settings.voice_id("en") == PROVISIONAL_VOICE_ID_EN


def test_the_provisional_ids_are_the_ones_the_shortlist_names():
    """The doc and the code are one decision written twice, so they are checked
    against each other. A voice id is 32 hex characters and unreadable; nobody
    reviewing a diff would catch a transposed one, and the failure mode is a
    demo speaking in a voice nobody picked."""
    shortlist = (
        Path(__file__).resolve().parents[2] / "docs" / "voice-shortlist.md"
    ).read_text(encoding="utf-8")
    for voice_id in (
        PROVISIONAL_VOICE_ID_EN,
        PROVISIONAL_VOICE_ID_AR,
        PROVISIONAL_VOICE_ID_HI,
    ):
        assert f"`{voice_id}`" in shortlist, voice_id
