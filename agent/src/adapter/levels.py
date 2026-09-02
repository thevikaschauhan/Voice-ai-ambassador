"""Level-match the shipping voices, so a language change is not a volume change.

## The finding

The three voices in `docs/voice-shortlist.md` are not level-matched, and two of
them are community uploads whose loudness is a property of whatever audio they
were cloned from. Measured across the audition samples synthesised on
2026-09-02 in the exact shipping ids:

    en   speech RMS 2133   peak 22260
    ar   speech RMS 2395   peak 14275
    hi   speech RMS 8299   peak 30491

Hindi is about 3.9x English and peaks within 7% of full scale. Back to back in
front of a client that is a jump in loudness on a language change, and the
headroom on the Hindi voice is thin enough that a louder-than-average sentence
has somewhere unpleasant to go.

## What ~25 seconds of calibration actually buys

Less than the first version of this docstring claimed, and the gap is a fact
about the vendor rather than about the sample size. Fish does not synthesise
the same text at the same level twice. Re-rendering the identical scripts in
the identical voices the same day, measured against the calibration render:

    en   -1.5%
    hi   +4.6%
    ar  +21.3%

So the table is not a description of what a voice WILL measure; it is an
estimate whose error, on the evidence of two renders, can reach a fifth. A
static per-voice gain therefore cannot do better than the voice's own
run-to-run variance, and no amount of extra calibration changes that: averaging
more renders sharpens the estimate and does nothing to the variance in the
render that actually ships.

What it buys in practice: the three voices went from 4.13x apart (12.3 dB) to
1.23x (1.8 dB) on a render this table had never seen. Twelve decibels is a
listener noticing the volume changed; under two is a listener hearing a
different voice. That is the honest claim, and it is not the within-2% figure
the unit tests show - those fix their input, and the vendor is not a fixed
input.

## Attenuation only, and that is the whole safety argument

Every gain here is <= 1.0. Voices are matched DOWN to the quietest rather than
up to a middle, and the variance above is exactly why that is not a stylistic
preference.

The mechanism has to be one whose failure mode, when the estimate is wrong, is
a small loudness error and never distortion. Attenuation cannot clip, for any
utterance, in any voice, however badly this table underestimates: a 21%
surprise costs 21% of loudness accuracy. A gain above 1.0 turns the identical
surprise into 21% of HEADROOM, and on the second render the Hindi voice arrived
at 0.95 of full scale with 5% of it left. Clipping is the one artefact a
listener cannot un-hear, on the one path where no test in this repository can
catch it (#41: the audio path fails by SOUNDING wrong and raising nothing).

The cost is that the demo sits at the quietest voice's level. That is a volume
knob on the listener's side, and every voice still lands between 0.24 and 0.63
of full scale, which is healthy.

## What happens to a voice that is not in the table

Gain 1.0: the frames are passed through untouched, which is exactly today's
behaviour. **That direction is deliberate** - an uncalibrated voice should
sound like it did before this module existed, not like a guess. But silence is
not a good enough answer for a voice we SHIP, so a test asserts that every id
in `config.PROVISIONAL_VOICE_ID_*` has an entry here. The client picks a voice
at the meeting; when that id lands, the suite fails until somebody measures it,
which is the reminder this file cannot give at runtime.

## Recalibrating

`speech_rms()` is the function the table was measured with, so the numbers are
reproducible rather than folklore. Synthesise ~25 seconds in the new voice,
run it through `speech_rms`, add the row. `test_levels.py` re-measures the
audition WAVs and checks the table against them whenever those files are
present - which pins the numbers to the audio they came from, and is a
different claim from "a fresh render will measure this".
"""

from __future__ import annotations

import math
from array import array

# Speech-active RMS per voice id, measured 2026-09-02 from the audition samples
# (~25s each, the recorded eval fixtures, synthesised through the shipped
# pipeline). Keyed by voice id rather than by language: the level belongs to the
# voice, and the languages are only how we currently reach them.
# Re-measured on a second render of the same scripts (2026-09-02, main at
# 7624c65): en 2100.9, ar 2905.7, hi 8677.0. Those are not corrections to the
# numbers below - they are what the same voices measured on a different day,
# and the spread between them is the reason this table is an estimate. See the
# module docstring.
SPEECH_RMS: dict[str, float] = {
    "536d3a5e000945adb7038665781a4aca": 2133.2,  # en, "Ethan", Fish Official
    "10c5c2a37a284a81bb0cf3c53955d795": 2395.1,  # ar, Gulf-accented, community
    "6209a5682085409fa935f901f0bce950": 8299.3,  # hi, "neel", community
}

# The quietest shipped voice. Derived, never typed: matching to a hand-written
# target is how one edit to the table above leaves a voice being amplified.
TARGET_RMS: float = min(SPEECH_RMS.values())

# Below this a window is silence between words rather than speech. Relative to
# the loudest window in the clip, so it does not assume a recording level - the
# thing being measured is exactly what varies between these voices.
_ACTIVE_FRACTION = 0.10

# The window the level is measured over. 20ms because that is the frame the
# voice path already carries, so a measurement and a runtime frame are the same
# unit of audio.
_WINDOW_MS = 20

_INT16_MIN = -32768
_INT16_MAX = 32767


def gain_for(voice_id: str) -> float:
    """The attenuation to apply to this voice, in [0, 1].

    1.0 for a voice with no measurement, which passes audio through unchanged.
    """
    measured = SPEECH_RMS.get(voice_id)
    if not measured:
        return 1.0
    # min() rather than a bare ratio: if a voice is ever measured QUIETER than
    # the target, the ratio exceeds 1 and this module would start amplifying,
    # which is the one thing its safety argument rests on not doing. Today
    # TARGET_RMS is the minimum so this cannot trigger; it is here because the
    # table is edited by hand and the invariant must not depend on that.
    return min(1.0, TARGET_RMS / measured)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale signed 16-bit little-endian mono PCM.

    Returns the input object untouched at unity, so the quietest voice - the
    one every other voice is matched to - pays nothing at all, not even a copy.

    Clamping is belt and braces. With `gain_for` capping at 1.0 no sample can
    leave the int16 range, but this function is public and the clamp is what
    makes it safe to call with a number that did not come from there.
    """
    if gain >= 1.0:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    for i, value in enumerate(samples):
        # round(), not int(): truncation toward zero shrinks every sample by up
        # to one LSB, which is a quiet, systematic amplitude loss on top of the
        # attenuation actually asked for. Rounding is unbiased and costs
        # nothing at 24 kHz.
        scaled = round(value * gain)
        samples[i] = _INT16_MAX if scaled > _INT16_MAX else max(_INT16_MIN, scaled)
    return samples.tobytes()


def speech_rms(pcm: bytes, sample_rate: int) -> float:
    """RMS over the windows that carry speech, ignoring the gaps between words.

    Whole-file RMS is the wrong measure for comparing voices, because it also
    measures how much silence the clip happens to contain: the audition samples
    ran 21% to 31% silent, which alone moved the numbers by a tenth before any
    voice spoke. Averaging only the active windows compares delivery.

    Returns 0.0 for empty or wholly silent audio rather than raising - the
    caller is measuring, and "no speech in this clip" is an answer.
    """
    samples = array("h")
    samples.frombytes(pcm)
    window = sample_rate * _WINDOW_MS // 1000
    if window <= 0 or len(samples) < window:
        return 0.0
    windows = [
        math.sqrt(sum(v * v for v in samples[i : i + window]) / window)
        for i in range(0, len(samples) - window + 1, window)
    ]
    loudest = max(windows)
    if loudest == 0.0:
        return 0.0
    active = [w for w in windows if w >= loudest * _ACTIVE_FRACTION]
    return math.sqrt(sum(w * w for w in active) / len(active))
