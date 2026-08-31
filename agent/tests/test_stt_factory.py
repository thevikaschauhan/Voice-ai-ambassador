"""The recogniser choice is a latency decision, not a vendor preference.

Whole-utterance transcription lands entirely after endpoint; streaming charges
only the tail. The budget in docs/04- was written for the second and the
project shipped the first, so which one a session builds is worth pinning.
"""

from __future__ import annotations

import pytest

pytest.importorskip("livekit.agents")

from adapter.stt_factory import BRAND_KEYTERMS, build_stt, describe  # noqa: E402
from test_agent import make_settings  # noqa: E402


def test_stt_off_builds_nothing():
    assert build_stt(make_settings(stt_enabled=False)) is None


def test_openrouter_builds_the_whole_utterance_node():
    node = build_stt(make_settings(stt_enabled=True, stt_provider="openrouter"))
    assert describe(node)["charges_after_endpoint"] == "whole utterance"


def test_deepgram_builds_a_streaming_node():
    pytest.importorskip("livekit.plugins.deepgram")
    node = build_stt(
        make_settings(
            stt_enabled=True, stt_provider="deepgram", deepgram_api_key="fake-key"
        )
    )
    described = describe(node)
    assert described["streaming"] is True
    assert described["charges_after_endpoint"] == "tail only"


def test_an_unknown_provider_refuses_rather_than_falling_back():
    # A session that quietly runs the slow path is worse than one that will not
    # start: the only place it would show is the latency meter.
    with pytest.raises(ValueError, match="unknown STT_PROVIDER"):
        build_stt(make_settings(stt_enabled=True, stt_provider="whisper.cpp"))


def test_the_client_brand_is_boosted():
    # "Binghatti" came back as "Bint Jbeil" from the whole-utterance path, which
    # had no way to bias it. Mispronouncing or mishearing the client's name in
    # their own boardroom is the one unrecoverable demo failure.
    assert "Binghatti" in BRAND_KEYTERMS


def test_describe_never_carries_a_key():
    node = build_stt(make_settings(stt_enabled=True, stt_provider="openrouter"))
    assert "fake" not in repr(describe(node)).lower()
    assert not any("key" in str(k).lower() for k in describe(node))


def test_deepgram_is_built_with_numerals_on(monkeypatch):
    """ADR-011's blindness pin, at the right end. Budget detection needs
    digits in the transcript; a recogniser configured to spell numbers out
    silently blinds the whole confirmation policy. test_budget pins that
    word-form figures are invisible; this pins that the session actually
    asks Deepgram for digits."""
    deepgram = pytest.importorskip("livekit.plugins.deepgram")

    captured: dict = {}

    def recorder(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepgram, "STT", recorder)
    build_stt(
        make_settings(
            stt_enabled=True, stt_provider="deepgram", deepgram_api_key="fake-key"
        )
    )
    assert captured["numerals"] is True
