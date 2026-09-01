"""Issue #18's remainder: the audio format the session asks Fish for.

None of this can tell you the audio SOUNDS right. A raw path handed something
that is not s16 mono produces noise and raises nothing, so the ear check is a
human step and the PR says so. What these tests can do is hold the two things
that would silently undo the change: the argument going missing, and the vendor
assumption it rests on drifting underneath it.
"""

from __future__ import annotations

from typing import Any

import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents.tts import AudioEmitter  # noqa: E402
from livekit.agents.utils import aio  # noqa: E402
from livekit.plugins import fishaudio  # noqa: E402

from adapter.tts_factory import (  # noqa: E402
    OUTPUT_FORMAT,
    SAMPLE_RATE,
    build_tts,
    describe,
)

from test_agent import make_settings  # noqa: E402


def test_the_session_asks_fish_for_raw_pcm():
    """The change itself. Dropping `output_format` reverts to the plugin's
    inherited "wav" and nothing else in the suite would notice."""
    assert build_tts(make_settings()).output_format == "pcm"


async def test_the_pcm_mime_type_takes_the_framework_off_the_decoder_path():
    """The reason pcm is worth asking for. `AudioEmitter` branches on the mime
    type: audio/pcm and audio/raw go through `AudioByteStream`, and everything
    else builds a `codecs.AudioStreamDecoder` plus a decode task - which the
    framework's own comment there calls fragile across flush boundaries, and
    sentence-level flushing crosses one on every sentence.

    Asserted against the framework rather than described in a comment, because
    the branch is the entire justification and it is in someone else's code.
    """

    async def is_raw(mime: str) -> bool:
        emitter = AudioEmitter(label="test", dst_ch=aio.Chan())
        emitter.initialize(
            request_id="req",
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            mime_type=mime,
            stream=True,
        )
        try:
            return emitter._is_raw_pcm
        finally:
            await aio.gracefully_cancel(emitter._main_atask)

    # The plugin builds the mime type as f"audio/{output_format}", so this is
    # the string our choice actually produces.
    assert await is_raw(f"audio/{OUTPUT_FORMAT}")
    assert not await is_raw("audio/wav")


def test_pcm_and_wav_share_fishs_default_sample_rate():
    """"This changes no resampling behaviour" is only true while the plugin's
    two defaults agree. If a release ever splits them, the session would quietly
    resample every frame and this is the test that says so first."""
    defaults = fishaudio.tts._DEFAULT_SAMPLE_RATE
    assert defaults[OUTPUT_FORMAT] == defaults["wav"] == SAMPLE_RATE
    assert build_tts(make_settings()).sample_rate == SAMPLE_RATE


def test_fish_is_still_asked_for_one_channel():
    """`AudioByteStream` frames raw bytes at the declared channel count, so mono
    is half of the s16-mono assumption the raw path makes."""
    assert fishaudio.tts.NUM_CHANNELS == 1


def test_the_low_latency_mode_and_the_configured_voice_survive():
    """The factory replaced a constructor call in `entrypoint`; a move that
    quietly dropped one of its other arguments would cost latency or speak in
    the wrong voice."""
    tts = build_tts(make_settings(tts_voice_id_en="voice-en-fixture"))
    assert tts.latency_mode == "low"
    assert tts.voice_id == "voice-en-fixture"


def test_an_unset_voice_falls_back_to_the_plugin_default():
    tts = build_tts(make_settings(tts_voice_id_en=""))
    assert tts.voice_id == fishaudio.tts.DEFAULT_VOICE_ID


def test_the_session_start_event_says_which_audio_path_ran():
    """A listener hearing noise needs to read the format back off the log, not
    infer it from the commit history."""
    described: dict[str, Any] = describe(build_tts(make_settings()))
    assert described["output_format"] == "pcm"
    assert described["container_decoded"] is False
    assert described["sample_rate"] == SAMPLE_RATE
    assert described["provider"] == "FishAudio"


def test_the_described_event_carries_no_credential():
    key = make_settings().fish_api_key
    assert key
    assert key not in repr(describe(build_tts(make_settings())))
