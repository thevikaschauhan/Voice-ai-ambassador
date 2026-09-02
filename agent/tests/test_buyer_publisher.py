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


# --- pacing off quiescence, not turn_complete -----------------------------
#
# `turn_complete` is not silence. A barged-in turn's speech handle resolves the
# moment it is cut, so the event fires while the agent is already replying to
# the interruption - and the next clip landed on top of it. The 8-turn session
# asked for barge-ins on turns 3 and 7 and got 3, 4, 7 and 8, with three turns
# producing no sentence at all.
#
# The signal is the framework's own: `room_io` publishes the session state to
# the room as the `lk.agent.state` participant attribute.


def test_the_attribute_is_the_one_the_framework_publishes():
    """Pinned against the framework rather than typed from memory - a renamed
    key would make the harness think the agent is permanently quiet."""
    from livekit.agents.types import ATTRIBUTE_AGENT_STATE

    assert bp.AgentQuiescence.ATTRIBUTE == ATTRIBUTE_AGENT_STATE


def test_an_unseen_agent_is_never_quiet():
    """Before the agent has published any state, silence is unknown - and
    unknown must not read as ready, or the first clip races the disclosure."""
    assert bp.AgentQuiescence().quiet_for() == 0.0


def test_speaking_and_thinking_both_count_as_busy():
    q = bp.AgentQuiescence()
    for state in ("speaking", "thinking", "initializing"):
        q.observe(state)
        assert q.quiet_for() == 0.0, state


def test_quiet_accrues_only_from_the_last_busy_moment():
    q = bp.AgentQuiescence()
    q.observe("speaking")
    q.observe("listening")
    first = q.quiet_for()
    assert first >= 0.0
    # Back to speaking - a false interruption resuming, or the reply to a
    # barge-in - and the clock restarts rather than carrying on.
    q.observe("speaking")
    assert q.quiet_for() == 0.0
    q.observe("idle")
    assert q.quiet_for() < first + 1.0


def test_waiting_returns_true_once_the_agent_has_been_quiet_long_enough():
    q = bp.AgentQuiescence()
    q.observe("listening")
    assert asyncio.run(q.wait_until_quiet(min_quiet=0.0, timeout=1.0)) is True


def test_waiting_gives_up_and_says_so_rather_than_hanging(capsys):
    """A run that stalls silently is indistinguishable from a slow model. It
    publishes anyway, because a stalled harness measures nothing."""
    q = bp.AgentQuiescence()
    q.observe("speaking")
    assert asyncio.run(q.wait_until_quiet(min_quiet=0.5, timeout=0.2)) is False
    assert "still busy (state=speaking" in capsys.readouterr().out


def test_a_barged_in_turn_does_not_release_the_next_clip():
    """The defect, at the level the harness meets it: `turn_complete` has
    arrived and the agent is talking again. Pacing on the event alone would
    publish here; pacing on quiescence does not."""
    q = bp.AgentQuiescence()
    q.observe("speaking")  # the reply
    q.observe("listening")  # barge-in cut it; turn_complete fires about now
    q.observe("speaking")  # ... and the agent is answering the interruption
    assert asyncio.run(q.wait_until_quiet(min_quiet=0.3, timeout=0.2)) is False
    q.observe("listening")
    assert asyncio.run(q.wait_until_quiet(min_quiet=0.0, timeout=1.0)) is True


def test_the_quiet_window_defaults_above_the_false_interruption_pause():
    """A false interruption pauses playout and passes through "listening"
    before resuming, so too small a window reads a pause as the end of a
    reply. 0.7s is the floor this default must clear."""
    defaults = vars(bp.build_parser().parse_args(["--room", "r", "--log", "l"]))
    assert defaults["quiet_seconds"] >= 0.7
    assert defaults["quiet_timeout"] >= 30


def test_the_run_still_takes_the_arguments_the_session_was_driven_with():
    """The flags the measurement session used, so a rename does not silently
    strand the runner script."""
    defaults = vars(bp.build_parser().parse_args(["--room", "r", "--log", "l"]))
    for flag in ("turns", "barge_in_at", "record_agent", "gap_seconds"):
        assert flag in defaults, flag


def test_the_state_is_seeded_from_whoever_is_already_in_the_room():
    """The worker joins before the harness, so `participant_connected` never
    fires for it. Without seeding the first sample waits for the agent's next
    state CHANGE - the first paced run sat at `state: None` through two
    timeouts because of exactly this."""

    class FakeParticipant:
        attributes = {bp.AgentQuiescence.ATTRIBUTE: "listening"}

    class FakeRoom:
        remote_participants = {"agent": FakeParticipant()}

    q = bp.AgentQuiescence()
    assert q.state is None
    q.seed(FakeRoom())  # type: ignore[arg-type]
    assert q.state == "listening"


def test_seeding_an_empty_room_leaves_the_state_unknown():
    class FakeRoom:
        remote_participants: dict[str, object] = {}

    q = bp.AgentQuiescence()
    q.seed(FakeRoom())  # type: ignore[arg-type]
    assert q.state is None
    assert q.quiet_for() == 0.0


def test_audio_above_the_noise_floor_counts_as_talking():
    """The signal that actually arrives. `lk.agent.state` stayed None through
    two live runs, so the gate cannot rest on it alone."""
    q = bp.AgentQuiescence()
    loud = bp.rtc.AudioFrame(
        data=(b"\xff\x7f" * 480),
        sample_rate=24000,
        num_channels=1,
        samples_per_channel=480,
    )
    q.observe_frame(loud)
    assert q.heard_audio is True
    # The clock restarts, so the gate cannot open: what matters is not that the
    # number is exactly zero but that it is below any usable quiet window.
    assert q.quiet_for() < 0.01
    assert asyncio.run(q.wait_until_quiet(min_quiet=0.7, timeout=0.1)) is False


def test_silence_from_a_published_track_is_not_talking():
    """An idle agent still publishes a track. Treating its silence as speech
    would make the gate never open."""
    q = bp.AgentQuiescence()
    q.observe_frame(bp._silence())
    assert q.heard_audio is True
    assert q.quiet_for() > 0.0


def test_the_state_attribute_still_tightens_the_gate_if_it_arrives():
    """It is read and believed when it says busy - a signal that shows up only
    sometimes should still count when it does."""
    q = bp.AgentQuiescence()
    q.observe_frame(bp._silence())
    assert q.quiet_for() > 0.0
    q.observe("thinking")
    assert q.quiet_for() == 0.0
