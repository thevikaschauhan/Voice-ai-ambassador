"""Adapter configuration, and the one rule that has no recovery: a secret that
reaches a log cannot be un-logged.

The `session_start` event serialises the whole settings object, so the
redaction is not a nicety - it is the only thing between the demo's stdout and
four live credentials.
"""

from __future__ import annotations

import json

import pytest

from adapter.config import Settings, load_settings, parse_env_file

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

    bare = Settings(**{**settings.redacted(), "openrouter_api_key": "", "fish_api_key": ""})  # type: ignore[arg-type]
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
