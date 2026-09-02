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
        tail.wait_for("turn_complete", turn=None, timeout=0.2, label="a turn")
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

        waiter = tail.wait_for("disclosure", turn=None, timeout=3)
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


def test_the_publisher_waits_for_a_subscriber_and_says_when_it_gives_up():
    """`publish_track` returns when the SERVER has the track; the agent
    subscribes afterwards, and frames pushed before that are dropped rather
    than buffered. The first paced run lost its whole first clip that way, so
    the flag exists and its timeout is generous enough to survive a slow join."""
    defaults = vars(bp.build_parser().parse_args(["--room", "r", "--log", "l"]))
    assert defaults["subscribe_timeout"] >= 15
    source = Path(bp.__file__).read_text(encoding="utf-8")
    # The wait is on the publication the framework hands back, not a sleep.
    assert "publication.wait_for_subscription()" in source
    assert "return 1" in source  # a run nobody can hear is a failure, not a warning


def test_attribute_updates_are_counted_so_the_signal_can_be_reported_absent():
    """Whether `lk.agent.state` reaches a remote participant at all is an open
    question. A run reporting zero updates is the evidence."""
    q = bp.AgentQuiescence()
    assert q.attribute_updates == 0


def test_the_measurement_does_not_start_until_the_agent_proves_it_heard_us():
    """Subscription is not proof: a session that logged the agent subscribed
    still lost its entire first clip, and the worker's own transcript sequence
    began at clip two. So the harness offers a throwaway line and waits for the
    agent's `user_turn` before the measurement begins."""

    class FakeMouth:
        def __init__(self) -> None:
            self.said = 0

        async def say(self, frames) -> None:  # noqa: ANN001
            self.said += 1

    async def scenario(tmp: Path, hear_on: int) -> tuple[int | None, int]:
        log = tmp / "run.jsonl"
        log.write_text("", encoding="utf-8")
        tail = bp.EventTail(log)
        mouth = FakeMouth()
        original = mouth.say

        async def say_then_maybe_answer(frames) -> None:  # noqa: ANN001
            await original(frames)
            if mouth.said >= hear_on:
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"event": "user_turn", "turn": 1}) + "\n")

        mouth.say = say_then_maybe_answer  # type: ignore[method-assign]
        turn = await bp.prime(mouth, tail, [], timeout=3.0)  # type: ignore[arg-type]
        return turn, mouth.said

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        turn, attempts = asyncio.run(scenario(tmp, hear_on=1))
        # The index it discovered, not a bare True: clip N is not turn N once
        # the priming line has taken an index of its own.
        assert turn == 1
        assert attempts == 1


def test_priming_retries_and_then_gives_up_rather_than_measuring_deaf():
    """A run the agent cannot hear measures nothing, so it must fail loudly
    instead of producing figures nobody can trust."""

    class DeafMouth:
        def __init__(self) -> None:
            self.said = 0

        async def say(self, frames) -> None:  # noqa: ANN001
            self.said += 1

    import tempfile

    async def scenario(tmp: Path) -> tuple[int | None, int]:
        log = tmp / "run.jsonl"
        log.write_text("", encoding="utf-8")
        mouth = DeafMouth()
        turn = await bp.prime(
            mouth,
            bp.EventTail(log),
            [],
            timeout=2.5,  # type: ignore[arg-type]
        )
        return turn, mouth.said

    with tempfile.TemporaryDirectory() as raw:
        turn, attempts = asyncio.run(scenario(Path(raw)))
        assert turn is None
        assert attempts >= 1


# --- turn-scoped waiting is the default shape, not a patch ----------------
#
# The barge-in trigger waited for "a `tts_first_audio`" and matched the
# PREVIOUS turn's, so the interruption landed on the wrong reply and one
# requested barge-in became two. The adapter had the same shape the day before
# (a speech handle left over from the disclosure reading as a replacement for
# turn 1's). Twice in two days, so the convention is enforced in the signature.


def test_another_turns_event_of_the_same_name_is_not_a_match(tmp_path):
    """The regression, exactly. Scoped to turn 3, a turn-2 event must not
    satisfy the wait."""
    log = tmp_path / "run.jsonl"
    log.write_text(
        json.dumps({"event": "tts_first_audio", "turn": 2}) + "\n", encoding="utf-8"
    )
    tail = bp.EventTail(log)

    assert asyncio.run(tail.wait_for("tts_first_audio", turn=3, timeout=0.2)) is None
    # And the same event IS a match for the turn it belongs to.
    log.write_text(
        json.dumps({"event": "tts_first_audio", "turn": 3}) + "\n", encoding="utf-8"
    )
    found = asyncio.run(
        bp.EventTail(log).wait_for("tts_first_audio", turn=3, timeout=1)
    )
    assert found is not None
    assert found["turn"] == 3


def test_the_turn_cannot_be_left_out(tmp_path):
    """No default, deliberately: an unscoped match has to be typed as
    `turn=None` rather than reached by omission. This is the convention
    delivered as code."""
    tail = bp.EventTail(tmp_path / "run.jsonl")
    with pytest.raises(TypeError):
        asyncio.run(tail.wait_for("turn_complete", timeout=0.1))  # type: ignore[call-arg]


def test_the_raw_predicate_form_still_exists_under_its_own_name(tmp_path):
    """Genuine exceptions keep a route, named so that taking it is visible."""
    log = tmp_path / "run.jsonl"
    log.write_text(json.dumps({"event": "brief", "extra": 1}) + "\n", encoding="utf-8")
    found = asyncio.run(
        bp.EventTail(log).wait_while(
            lambda r: "extra" in r, timeout=1, label="anything with extra"
        )
    )
    assert found is not None


def test_the_wrong_turn_never_leaks_into_the_label(tmp_path, capsys):
    """The timeout message names the turn it wanted, so a phase shift is
    readable in the run output rather than inferred afterwards."""
    tail = bp.EventTail(tmp_path / "run.jsonl")
    asyncio.run(tail.wait_for("turn_complete", turn=7, timeout=0.15))
    assert "turn_complete on turn 7" in capsys.readouterr().out


# --- a scoped wait finds an event that has already been consumed ----------
#
# `EventTail` reads forward only, so a wait for an event that has already gone
# past used to time out on a line that arrived. In the verified pacing run the
# barged-in turn's seal cost 60 seconds that way: it was consumed while waiting
# for the barge-in's own turn, and then waited for on its own account.


def write_events(path: Path, *records: dict) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_a_scoped_wait_finds_a_seal_another_wait_already_consumed(tmp_path):
    """The exact 60-second timeout from the run, as a unit test."""
    log = tmp_path / "run.jsonl"
    write_events(
        log,
        {"event": "turn_complete", "turn": 3},
        {"event": "user_turn", "turn": 4},
        {"event": "turn_complete", "turn": 4},
    )
    tail = bp.EventTail(log)

    # The barge-in's own turn is looked up first, consuming turn 3's seal on
    # the way past.
    barged = asyncio.run(tail.wait_for("user_turn", turn=None, timeout=1))
    assert barged is not None and barged["turn"] == 4

    # And now the barged-in turn's seal, asked for after the fact.
    sealed = asyncio.run(tail.wait_for("turn_complete", turn=3, timeout=1))
    assert sealed is not None
    assert sealed["turn"] == 3


def test_an_unscoped_wait_stays_forward_only(tmp_path):
    """The trap that makes the naive version of this fix wrong. "Any turn"
    means the NEXT one - the clip-to-turn lookup depends on it - so history is
    deliberately not searched, or every clip would resolve to the first
    `user_turn` of the run."""
    log = tmp_path / "run.jsonl"
    write_events(log, {"event": "user_turn", "turn": 1})
    tail = bp.EventTail(log)

    first = asyncio.run(tail.wait_for("user_turn", turn=None, timeout=1))
    assert first is not None and first["turn"] == 1

    # Nothing new has arrived, so there is no next one.
    assert asyncio.run(tail.wait_for("user_turn", turn=None, timeout=0.2)) is None


def test_the_raw_form_stays_forward_only_too(tmp_path):
    """An arbitrary predicate's intent is unknown, so satisfying it from
    history could answer "the next X" with an X from a minute ago."""
    log = tmp_path / "run.jsonl"
    write_events(log, {"event": "brief", "extra": 1})
    tail = bp.EventTail(log)

    assert (
        asyncio.run(tail.wait_while(lambda r: "extra" in r, timeout=1, label="x"))
        is not None
    )
    assert (
        asyncio.run(tail.wait_while(lambda r: "extra" in r, timeout=0.2, label="x"))
        is None
    )


def test_a_scoped_wait_still_finds_an_event_that_arrives_later(tmp_path):
    """Looking backwards must not stop it looking forwards."""
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    tail = bp.EventTail(log)

    async def scenario():
        async def append():
            await asyncio.sleep(0.05)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "turn_complete", "turn": 9}) + "\n")

        found, _ = await asyncio.gather(
            tail.wait_for("turn_complete", turn=9, timeout=3), append()
        )
        return found

    assert asyncio.run(scenario())["turn"] == 9


def test_a_scoped_wait_ignores_the_same_event_on_another_turn_in_history(tmp_path):
    """Looking backwards must not loosen the scoping #60 introduced."""
    log = tmp_path / "run.jsonl"
    write_events(log, {"event": "turn_complete", "turn": 2})
    tail = bp.EventTail(log)

    assert asyncio.run(tail.wait_for("turn_complete", turn=5, timeout=0.2)) is None


def test_a_scoped_wait_that_never_arrives_still_names_the_turn(tmp_path, capsys):
    tail = bp.EventTail(tmp_path / "run.jsonl")
    assert asyncio.run(tail.wait_for("turn_complete", turn=4, timeout=0.15)) is None
    assert "turn_complete on turn 4" in capsys.readouterr().out


# --- the barge-in trigger reads the audio, not the log --------------------
#
# Hosted, the event log arrives p50 6.3s and p90 85.6s after the event it
# describes (measured over 162 records), so a barge-in fired 0.6s after a
# `tts_first_audio` that arrived six seconds late lands on the FOLLOWING turn.
# One hosted run asked for two interruptions and got four. The agent's own
# track cannot lag its own audio.


def _loud() -> "bp.rtc.AudioFrame":
    return bp.rtc.AudioFrame(
        data=(b"\xff\x7f" * 480),
        sample_rate=24000,
        num_channels=1,
        samples_per_channel=480,
    )


def test_nothing_is_interrupted_before_the_agent_speaks():
    q = bp.AgentQuiescence()
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is False


def test_the_onset_is_the_moment_audio_starts():
    q = bp.AgentQuiescence()
    q.observe_frame(_loud())
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is True


def test_silence_from_an_idle_track_is_not_an_onset():
    """An idle agent still publishes a track. Treating its silence as the start
    of a reply would fire the barge-in into the gap before one."""
    q = bp.AgentQuiescence()
    q.observe_frame(bp._silence())
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is False


def test_arming_forgets_the_previous_reply():
    """The off-by-one-turn error, one layer down: without arming, the first
    read matches the tail of the PREVIOUS reply and the interruption fires
    before this turn's speech has begun."""
    q = bp.AgentQuiescence()
    q.observe_frame(_loud())
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is True

    q.arm_onset()
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is False

    q.observe_frame(_loud())
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is True


def test_the_onset_survives_the_speech_continuing():
    """`_busy_since` moves forward on every loud frame, which is why it cannot
    answer 'when did this reply START' - the onset has to be kept separately."""
    q = bp.AgentQuiescence()
    q.arm_onset()
    q.observe_frame(_loud())
    first = q._speaking_since
    q.observe_frame(_loud())
    q.observe_frame(_loud())
    assert q._speaking_since == first


def test_an_onset_still_reads_as_busy_for_the_pacing_gate():
    """One signal, two jobs, and they must not disagree: the frame that opens
    the barge-in window is also the frame that keeps the next clip waiting."""
    q = bp.AgentQuiescence()
    q.arm_onset()
    q.observe_frame(_loud())
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is True
    assert q.quiet_for() < 0.01


def test_the_trigger_names_what_it_waited_for_when_it_times_out(capsys):
    """A silent skip would look like a barge-in that was asked for and landed."""
    q = bp.AgentQuiescence()
    assert asyncio.run(q.wait_until_speaking(timeout=0.05)) is False
    assert "never started speaking" in capsys.readouterr().out
