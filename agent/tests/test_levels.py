"""Level matching across the shipping voices.

The audition (2026-09-02) measured the three voices the demo ships and found
Hindi about 3.9x louder than English, peaking within 7% of full scale. These
tests hold the two claims the fix rests on: that the table brings the voices
together, and that it can never do the one thing that would be worse than the
problem, which is amplify.

Nothing here synthesises. The expensive part - measuring real speech in real
voices - was done once and its numbers are the fixture; the last test in this
file re-measures the audio itself whenever those files are on the machine.
"""

from __future__ import annotations

import math
import os
import wave
from array import array
from pathlib import Path
from typing import get_args

import pytest

from adapter.config import (
    PROVISIONAL_VOICE_ID_AR,
    PROVISIONAL_VOICE_ID_EN,
    PROVISIONAL_VOICE_ID_HI,
    load_settings,
)
from adapter.levels import (
    SPEECH_RMS,
    TARGET_RMS,
    apply_gain,
    gain_for,
    speech_rms,
)
from ambassador.schemas import Language

SAMPLE_RATE = 24000

# Where the audition WAVs live if they are on this machine. The samples are
# ~1.2MB each and are audio, which this repository deliberately keeps out of
# git (#53 saved the agent's own ear-check recording to the Desktop for the
# same reason), so the test that reads them self-skips.
SAMPLES = Path(
    os.environ.get(
        "BINGHATTI_VOICE_SAMPLES", Path.home() / "Desktop" / "binghatti-voice-samples"
    )
)


def tone(rms: float, seconds: float = 1.0, hz: float = 200.0) -> bytes:
    """A sine at a known speech-active RMS.

    A constant-amplitude tone has the same energy in every window, so every
    window is active and `speech_rms` returns amplitude/sqrt(2) exactly. That
    makes it a signal whose measured level is arithmetic rather than an
    approximation, which is what a tolerance in these tests should be measuring
    against.
    """
    amplitude = rms * math.sqrt(2)
    n = int(SAMPLE_RATE * seconds)
    samples = array(
        "h",
        (
            round(amplitude * math.sin(2 * math.pi * hz * i / SAMPLE_RATE))
            for i in range(n)
        ),
    )
    return samples.tobytes()


# --- the safety argument ---------------------------------------------------


def test_no_voice_is_ever_amplified():
    """The property the whole design rests on.

    The table is calibrated from ~25 seconds of one script per voice, which is
    a fair estimate of a voice's average level and no kind of bound on its
    peaks. Attenuation turns a bad estimate into a small loudness error;
    amplification turns the same error into clipping, which no test in this
    repository can catch because the audio path fails by sounding wrong and
    raising nothing (#41).
    """
    for voice_id in SPEECH_RMS:
        assert 0.0 < gain_for(voice_id) <= 1.0, voice_id


def test_the_target_is_the_quietest_voice_and_is_derived():
    """Matching to a hand-written target is how one edit to the table leaves a
    voice being amplified. The target is the minimum, so at least one voice is
    always at unity and none is above it."""
    assert TARGET_RMS == min(SPEECH_RMS.values())
    assert any(gain_for(v) == 1.0 for v in SPEECH_RMS)


def test_a_voice_measured_below_the_target_is_still_not_amplified(monkeypatch):
    """The invariant must not depend on somebody keeping the table sorted.

    `gain_for` caps at 1.0 rather than trusting `TARGET_RMS` to be the minimum,
    because the table is edited by hand and a row added below the target would
    otherwise silently switch this module from attenuating to amplifying.
    """
    monkeypatch.setitem(SPEECH_RMS, "quieter-than-target", TARGET_RMS / 4)
    assert gain_for("quieter-than-target") == 1.0


def test_an_unmeasured_voice_is_passed_through_untouched():
    """The failure direction, stated: a voice nobody has measured sounds
    exactly as it did before this module existed, rather than like a guess."""
    assert gain_for("a-voice-nobody-measured") == 1.0
    pcm = tone(4000.0)
    assert apply_gain(pcm, gain_for("a-voice-nobody-measured")) is pcm


def test_unity_returns_the_same_object_rather_than_a_copy():
    """The quietest voice is the one every other voice is matched down to, so
    it is on the hot path at unity for the whole call and must pay nothing."""
    pcm = tone(2000.0)
    assert apply_gain(pcm, 1.0) is pcm


# --- the arithmetic --------------------------------------------------------


def test_the_gains_bring_the_measured_levels_together():
    """The claim, on signals at exactly the measured levels of the three
    voices: after the gain they land on one level.

    On FIXED inputs, which is what makes this arithmetic rather than a
    prediction. The live path does not reach 2%, because the vendor re-renders
    the same text at a different level; `adapter/levels.py` carries the
    measured variance and the ~1.8 dB figure that is true of the product.
    """
    normalised = [
        speech_rms(apply_gain(tone(measured), gain_for(voice_id)), SAMPLE_RATE)
        for voice_id, measured in SPEECH_RMS.items()
    ]
    for level in normalised:
        assert abs(level - TARGET_RMS) / TARGET_RMS < 0.02, normalised
    spread = max(normalised) / min(normalised)
    assert spread < 1.02, normalised


def test_the_hindi_voice_is_the_one_that_moves():
    """Named separately because it is the finding. Roughly a quarter, and the
    other two barely move."""
    assert gain_for(PROVISIONAL_VOICE_ID_HI) == pytest.approx(0.257, abs=0.005)
    assert gain_for(PROVISIONAL_VOICE_ID_EN) == 1.0
    assert gain_for(PROVISIONAL_VOICE_ID_AR) == pytest.approx(0.891, abs=0.005)


def test_attenuation_cannot_push_a_sample_out_of_range():
    """Full-scale input, hardest gain in the table, still int16."""
    loud = array("h", [32767, -32768] * 1000).tobytes()
    out = array("h")
    out.frombytes(apply_gain(loud, gain_for(PROVISIONAL_VOICE_ID_HI)))
    assert max(out) <= 32767 and min(out) >= -32768
    assert max(abs(v) for v in out) < 32767  # it actually got quieter


def test_a_gain_above_one_is_clamped_rather_than_trusted():
    """`apply_gain` is public and the clamp is what makes it safe to call with
    a number that did not come from `gain_for`."""
    loud = array("h", [30000, -30000] * 100).tobytes()
    out = array("h")
    out.frombytes(apply_gain(loud, 0.99))
    assert max(out) <= 32767 and min(out) >= -32768


def test_silence_stays_silence():
    silent = array("h", [0] * 4800).tobytes()
    assert apply_gain(silent, 0.257) == silent
    assert speech_rms(silent, SAMPLE_RATE) == 0.0


def test_measuring_something_shorter_than_a_window_is_an_answer_not_a_raise():
    assert speech_rms(array("h", [1000, -1000]).tobytes(), SAMPLE_RATE) == 0.0
    assert speech_rms(b"", SAMPLE_RATE) == 0.0


def test_the_gaps_between_words_do_not_change_the_measurement():
    """Why `speech_rms` exists rather than a whole-file RMS. The audition clips
    ran 21% to 31% silent, which alone moved a whole-file number by a tenth
    before any voice had spoken - so a comparison on that measure is partly a
    comparison of how much silence each script happened to contain."""
    speech = tone(3000.0, seconds=1.0)
    silence = array("h", [0] * SAMPLE_RATE).tobytes()
    assert speech_rms(speech, SAMPLE_RATE) == pytest.approx(
        speech_rms(speech + silence, SAMPLE_RATE), rel=0.01
    )


# --- the guard that survives the client changing their mind ----------------


def test_every_shipped_voice_is_calibrated():
    """`gain_for` returns 1.0 for an unmeasured voice, which is the right thing
    at runtime and useless as a reminder. The client picks a voice at the
    meeting; when that id lands in config this fails until somebody measures
    it, which is the only place that reminder can live.
    """
    settings = load_settings(Path("/nonexistent/.env"))
    for language in get_args(Language):
        voice_id = settings.voice_id(language)
        assert voice_id in SPEECH_RMS, (
            f"{language} ships voice {voice_id!r} with no entry in "
            "adapter.levels.SPEECH_RMS. Synthesise ~25s in it and measure with "
            "speech_rms(), or it ships at whatever level it happens to have."
        )


# --- the table, checked against the audio it was measured from -------------


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="audition samples not on this machine")
def test_the_table_still_matches_the_audio_it_came_from():
    """The numbers in `SPEECH_RMS` are the output of `speech_rms` run over the
    audition WAVs. Re-running it here is what keeps them reproducible rather
    than folklore, and it is the only test in this file that touches real
    speech in the real voices.

    Set BINGHATTI_VOICE_SAMPLES to point at the directory if it is elsewhere.
    """
    measured: dict[str, float] = {}
    for path in sorted(SAMPLES.glob("*.wav")):
        with wave.open(str(path)) as handle:
            assert handle.getnchannels() == 1 and handle.getsampwidth() == 2
            pcm = handle.readframes(handle.getnframes())
            rate = handle.getframerate()
        # Files are named <language>-<first 8 of the voice id>.wav
        prefix = path.stem.split("-")[-1]
        for voice_id in SPEECH_RMS:
            if voice_id.startswith(prefix):
                measured[voice_id] = speech_rms(pcm, rate)

    assert len(measured) == len(SPEECH_RMS), (
        f"matched {sorted(measured)} against {sorted(SPEECH_RMS)}"
    )
    for voice_id, value in measured.items():
        assert value == pytest.approx(SPEECH_RMS[voice_id], rel=0.01), voice_id

    # The tolerance below is tight because these ARE the files the table was
    # measured from, so this is a reproducibility check on the constants and
    # not a claim about the live path. A fresh render does not land here: Fish
    # does not synthesise the same text at the same level twice, and a second
    # render of these same scripts came in at ar +21.3% and settled at a 1.23x
    # spread rather than 1.02. See adapter/levels.py; that number is the one to
    # quote about the product.
    normalised = {
        voice_id: value * gain_for(voice_id) for voice_id, value in measured.items()
    }
    spread = max(normalised.values()) / min(normalised.values())
    assert spread < 1.02, normalised
    # And the thing a listener would actually complain about is gone.
    before = max(measured.values()) / min(measured.values())
    assert before > 3.0, measured
