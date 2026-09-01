"""The synthesiser for one session, and the audio format it asks Fish for.

Selection is config, per ADR-006: nothing here wraps the plugin. This exists as
a factory rather than four keyword arguments inside `entrypoint` because
`entrypoint` needs LiveKit transport, a worker process and real credentials, so
nothing built there is testable - and the one choice below that a test can
protect is a choice that reverts to a slower default the moment somebody drops
the argument.

## output_format: pcm, not the inherited wav (issue #18)

`livekit-plugins-fishaudio` defaults `output_format` to `"wav"` and we never set
it, so every chunk went through the container path. In `livekit-agents`'
`AudioEmitter`, the mime type decides which of two paths runs:

    audio/pcm | audio/raw  ->  AudioByteStream, a plain byte-stream framer
    anything else          ->  codecs.AudioStreamDecoder, plus a decode task
                               spun up on the first chunk

The framework's own comment beside the decoder branch calls WAV fragile across
flush boundaries, because a stateful container parser can be mid-file when a
mid-stream flush arrives - and sentence-level flushing means that happens on
every sentence. `pcm` removes the decoder, the decode task and that whole
fragility class. Issue #18 estimates 5-30ms; the estimate is not the point, the
removed failure mode is.

Two vendor facts this rests on, both pinned by tests rather than trusted:

  sample rate   Fish's default is 24000 for pcm AND for wav
                (`fishaudio.tts._DEFAULT_SAMPLE_RATE`), so this changes no
                resampling behaviour. If a plugin release ever splits them, the
                session would silently resample and the pin fails first.
  s16 mono      `AudioByteStream` frames raw bytes as signed 16-bit at the
                declared rate and channel count. Fish sends s16le mono on the
                pcm path and the plugin declares NUM_CHANNELS = 1.

THE FAILURE MODE HERE IS NOISE, NOT AN EXCEPTION. A raw path handed something
that is not s16 mono produces audible garbage and raises nothing, so no test in
this repository can clear this change. It needs one human listening to one
sentence, and the PR says so rather than implying the suite covers it.
"""

from __future__ import annotations

from livekit.plugins import fishaudio

from .config import Settings

# Raw byte stream rather than the plugin's inherited "wav" container. See the
# module docstring: this is the whole point of the module existing.
OUTPUT_FORMAT = "pcm"

# Fish's own default for both pcm and wav. Asserted against the plugin in
# tests/test_tts_factory.py rather than assumed, because the claim "this changes
# no resampling behaviour" is only true while the two agree.
SAMPLE_RATE = 24000


def build_tts(settings: Settings) -> fishaudio.TTS:
    """Fish Audio s2.1-pro (ADR-014), configured for the voice path.

    `latency_mode="low"` and `output_format` are audio-path tuning and are fixed
    here; the model and the per-language voice are identity and come from
    config.
    """
    return fishaudio.TTS(
        api_key=settings.fish_api_key,
        model=settings.fish_tts_model,
        voice_id=settings.voice_id(settings.language) or fishaudio.tts.DEFAULT_VOICE_ID,
        latency_mode="low",
        output_format=OUTPUT_FORMAT,
    )


def describe(node: fishaudio.TTS) -> dict[str, object]:
    """What to put in the tts_enabled event, without leaking a key.

    `output_format` is on the emitted stream deliberately: it decides whether
    the buyer's audio went through a container decoder, and it is the one thing
    about this session's synthesis that a listener hearing noise would want to
    read back off the log.
    """
    return {
        "provider": node.provider,
        "model": node.model,
        "output_format": node.output_format,
        "sample_rate": node.sample_rate,
        "latency_mode": node.latency_mode,
        # False on the pcm path: the audio reached the room as raw frames rather
        # than through a stateful container parser (issue #18).
        "container_decoded": node.output_format not in ("pcm", "raw"),
    }
