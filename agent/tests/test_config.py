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

import pytest

from adapter.config import Settings, _is_credential, load_settings, parse_env_file

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
