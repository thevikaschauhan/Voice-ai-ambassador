"""The measurement harness's own moving parts.

Nothing here talks to Fish or a room - the live run is the point of the tool and
cannot be a unit test. What can be tested is everything that would silently
corrupt a measurement: frame geometry (wrong sample width and the recogniser
hears noise, wrong tail and every turn reads as one), and the event tail that
paces the run (a parser that quietly returns nothing turns every wait into a
timeout and every gap into a fixed sleep).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import buyer_publisher as bp  # noqa: E402


def test_a_silence_frame_is_the_declared_geometry():
    """The raw path assumes signed 16-bit mono at the declared rate (#41). A
    frame whose byte count disagrees with `samples_per_channel` is noise, and
    noise raises nothing."""
    frame = bp._silence()
    assert frame.sample_rate == bp.SAMPLE_RATE == 24000
    assert frame.num_channels == bp.CHANNELS == 1
    assert frame.samples_per_channel == bp.SAMPLE_RATE * bp.FRAME_MS // 1000
    assert len(bytes(frame.data)) == frame.samples_per_channel * 2
    assert set(bytes(frame.data)) == {0}


def test_the_buyer_never_borrows_the_brand_voice(monkeypatch):
    """A buyer who sounds like the ambassador makes a barge-in transcript
    unreadable, and #50 gave the agent real per-language voice ids that are
    sitting right there in the environment to be picked up by accident."""
    from livekit.plugins import fishaudio

    monkeypatch.setenv("FISH_API_KEY", "not-a-real-credential")
    monkeypatch.setenv("TTS_VOICE_ID_EN", "the-brand-voice")

    tts = bp.buyer_tts(session=None)  # type: ignore[arg-type]
    assert tts.voice_id == fishaudio.tts.DEFAULT_VOICE_ID
    assert tts.voice_id != "the-brand-voice"
    # And on the same raw path #41 put the agent's own output on, so the buyer
    # audio cannot be the thing that differs.
    assert tts.output_format == "pcm"


def test_the_turns_are_short_enough_to_measure_the_endpointer():
    """A long utterance measures the speaker, not the detector, and the run has
    to stay inside the authorised spend."""
    assert len(bp.BUYER_TURNS) == 10
    for line in bp.BUYER_TURNS:
        assert len(line.split()) <= 10, line
    assert len(bp.BARGE_IN_LINE.split()) <= 4


def test_the_event_tail_reads_forward_and_never_re_reads(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_text('{"event": "session_start"}\n', encoding="utf-8")
    tail = bp.EventTail(log)

    assert [r["event"] for r in tail.poll()] == ["session_start"]
    assert tail.poll() == []  # already consumed

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "endpointing", "endpoint_ms": 412.0}) + "\n")
    assert [r["event"] for r in tail.poll()] == ["endpointing"]
    assert len(tail.seen) == 2


def test_a_half_written_line_is_skipped_rather_than_crashing(tmp_path):
    """The agent writes this file while the harness reads it, so a torn final
    line is normal. Crashing there would end a run mid-measurement."""
    log = tmp_path / "run.jsonl"
    log.write_text('{"event": "user_turn"}\n{"event": "endpo', encoding="utf-8")
    tail = bp.EventTail(log)
    assert [r["event"] for r in tail.poll()] == ["user_turn"]


def test_a_missing_log_is_not_an_error(tmp_path):
    """The harness starts before the worker has written anything."""
    assert bp.EventTail(tmp_path / "absent.jsonl").poll() == []


def test_waiting_for_an_event_that_never_arrives_times_out_and_says_so(tmp_path):
    """A silent hang in a live run is indistinguishable from a slow model. The
    wait has to give up and name what it wanted."""
    log = tmp_path / "run.jsonl"
    log.write_text('{"event": "session_start"}\n', encoding="utf-8")
    tail = bp.EventTail(log)

    found = asyncio.run(
        tail.wait_for(
            lambda r: r.get("event") == "turn_complete", timeout=0.2, label="a turn"
        )
    )
    assert found is None


def test_waiting_finds_an_event_appended_after_the_wait_began(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    tail = bp.EventTail(log)

    async def scenario():
        async def append():
            await asyncio.sleep(0.05)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "disclosure"}) + "\n")

        waiter = tail.wait_for(
            lambda r: r.get("event") == "disclosure", timeout=3, label="disclosure"
        )
        found, _ = await asyncio.gather(waiter, append())
        return found

    assert asyncio.run(scenario()) is not None
