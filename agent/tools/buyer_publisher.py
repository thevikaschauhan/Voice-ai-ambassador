"""Publish synthetic buyer speech into a LiveKit room, so the latency numbers
can be measured without a human holding a microphone.

## Why this exists

Two budget rows in `docs/04-` can only be measured from real audio, because
their anchors are VAD timestamps inside the framework's recognition loop:

  endpointing        `EOUMetrics.end_of_utterance_delay` - budgeted at
                     200-500ms and never measured (issue #7 item 1)
  barge-in reconnect the step in `tts_first_audio.since_first_sentence_ms` on
                     the turn after an interruption, which is what issue #18's
                     re-prewarm fix is supposed to have removed

Neither exists on a text-mode turn. The obvious alternative - play buyer speech
out of the speakers and let the agent's microphone hear it - was rejected: the
agent would also hear its own output and interrupt itself, contaminating exactly
the numbers under measurement. Publishing as a separate track removes the
acoustic path entirely. The agent subscribes to remote participants and never to
its own published track, so it cannot hear itself at all.

## How it stays honest

  real path        the audio goes through WebRTC, Silero VAD and Deepgram
                   exactly as a caller's would. Nothing is stubbed and no
                   timing is simulated.
  turn sync        the harness reads the agent's OWN event log to know when a
                   turn completed, rather than guessing from a fixed sleep. The
                   audit stream we are measuring is also the clock we pace by,
                   which means a turn that never completes stalls the run
                   visibly instead of silently overlapping the next clip.
  a distinct voice the buyer clips use Fish's own default voice, never the
                   brand voice ids from config (#50). A buyer who sounds like
                   the ambassador makes a barge-in transcript unreadable.

## What it cannot tell you

Synthetic speech ends abruptly. A human trails off, breathes, and leaves a
noisy tail, so the VAD's end-of-speech mark lands differently. The endpointing
number this produces is therefore a BEST CASE, and must be reported as one -
the turn detector's own wait dominates it, but the anchor is cleaner than a
real caller's. Say so wherever the number is filed.

## Running it

Two processes, same room. The agent worker first:

    cd agent
    AMBASSADOR_EVENT_LOG=/tmp/run.jsonl STT_ENABLED=1 \\
      uv run python -m adapter.agent connect --room measure-1

Then the buyer:

    uv run python tools/buyer_publisher.py --room measure-1 --log /tmp/run.jsonl

`--barge-in-at` takes 1-based turn numbers to interrupt: the clip is published
once that turn's `tts_first_audio` appears in the log, so the interruption
always lands inside the agent's speech rather than near it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

import aiohttp
from livekit import api, rtc
from livekit.plugins import fishaudio

# 20ms frames: small enough that pacing error stays well under the measurement's
# resolution, large enough not to spin the event loop pushing them.
FRAME_MS = 20
SAMPLE_RATE = 24000
CHANNELS = 1

# Fish's own default voice, deliberately not `TTS_VOICE_ID_*` from config.
BUYER_VOICE = fishaudio.tts.DEFAULT_VOICE_ID

# Ten buyer turns that exercise the paths the budget cares about: grounded
# lookups, a figure the guardrail must block, an escalation, and a budget
# mention for the confirmation policy. Short, because a long utterance measures
# the speaker rather than the endpointer.
BUYER_TURNS: tuple[str, ...] = (
    "What does a studio at Binghatti Skyrise cost?",
    "And when does it hand over?",
    "Tell me about Burj Binghatti.",
    "What is the down payment on the studio?",
    "My budget is about two million dirhams.",
    "Which areas do you have towers in?",
    "Do you have anything in Jumeirah Village Circle?",
    "Can I speak to a person?",
    "What is the price at Bugatti Residences?",
    "Is the handover date guaranteed?",
)

# Said over the agent mid-sentence. Short and abrupt on purpose: a barge-in is
# an interruption, not a turn.
BARGE_IN_LINE = "Wait, stop."

# Spoken before the measurement starts, and thrown away. Subscription is not
# enough to prove the agent can hear us - see `prime` - so the harness says
# something disposable and waits for the agent's own `user_turn` to prove the
# path works end to end.
PRIMING_LINE = "Hello, can you hear me?"


def _silence() -> rtc.AudioFrame:
    samples = SAMPLE_RATE * FRAME_MS // 1000
    return rtc.AudioFrame(
        data=b"\x00\x00" * samples,
        sample_rate=SAMPLE_RATE,
        num_channels=CHANNELS,
        samples_per_channel=samples,
    )


def buyer_tts(session: aiohttp.ClientSession) -> fishaudio.TTS:
    """Fish, with our own http session.

    The plugin otherwise reaches for the worker's job-scoped session and fails
    outside one - "Attempted to use an http session outside of a job context",
    which is what a script gets. Owning the session here is the documented
    alternative and keeps its lifecycle visible.
    """
    return fishaudio.TTS(
        api_key=os.environ["FISH_API_KEY"],
        model=os.environ.get("FISH_TTS_MODEL", "s2.1-pro"),
        voice_id=BUYER_VOICE,
        # Raw frames, matching what #41 put on the agent's own output path.
        output_format="pcm",
        http_session=session,
    )


async def synthesise(tts: fishaudio.TTS, text: str) -> list[rtc.AudioFrame]:
    """One buyer utterance, as 20ms frames, plus a trailing silence tail.

    The tail is what lets VAD declare end-of-speech: without it the recogniser
    is still waiting when the next clip starts, and every turn reads as one.
    """
    raw = bytearray()
    stream = tts.synthesize(text)
    async for event in stream:
        frame = event.frame
        raw += bytes(frame.data)
    await stream.aclose()

    per_frame = SAMPLE_RATE * FRAME_MS // 1000 * 2  # 16-bit mono
    frames = [
        rtc.AudioFrame(
            data=bytes(chunk.ljust(per_frame, b"\x00")),
            sample_rate=SAMPLE_RATE,
            num_channels=CHANNELS,
            samples_per_channel=per_frame // 2,
        )
        for chunk in (raw[i : i + per_frame] for i in range(0, len(raw), per_frame))
    ]
    # 700ms of silence: comfortably past Silero's min-silence default so the
    # end-of-speech mark is the detector's decision, not our truncation.
    return frames + [_silence() for _ in range(700 // FRAME_MS)]


class AgentRecorder:
    """Write the agent's own output track to a WAV, for an ear check.

    #41 switched Fish to raw pcm and said plainly that no test in the repository
    can clear that change: a raw path handed something that is not s16 mono
    produces noise and raises nothing. The only verification is a human
    listening, and a human cannot listen to a room they were not in - so the
    harness saves what the agent actually sent.

    This is the agent's PUBLISHED track as it arrived over WebRTC, which is the
    honest thing to check: it includes whatever the transport did to it, not
    just what Fish returned.
    """

    def __init__(self, path: Path | None, listener: AgentQuiescence | None = None) -> None:
        self._path = path
        self._listener = listener
        self._writer: wave.Wave_write | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.frames = 0

    def watch(self, room: rtc.Room) -> None:
        @room.on("track_subscribed")
        def _subscribed(track, publication, participant) -> None:  # noqa: ANN001
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            print(f"  recording {participant.identity}'s audio", flush=True)
            self._tasks.append(asyncio.create_task(self._drain(track)))

    async def _drain(self, track) -> None:  # noqa: ANN001
        async for event in rtc.AudioStream(track):
            frame = event.frame
            if self._listener is not None:
                self._listener.observe_frame(frame)
            if self._path is None:
                continue
            if self._writer is None:
                self._writer = wave.open(str(self._path), "wb")
                self._writer.setnchannels(frame.num_channels)
                self._writer.setsampwidth(2)  # rtc.AudioFrame is 16-bit pcm
                self._writer.setframerate(frame.sample_rate)
            self._writer.writeframes(bytes(frame.data))
            self.frames += 1

    def close(self) -> str | None:
        for task in self._tasks:
            task.cancel()
        if self._writer is None:
            return None if self._path is not None else "not requested"
        rate = self._writer.getframerate()
        channels = self._writer.getnchannels()
        self._writer.close()
        return f"{self._path} ({rate} Hz, {channels} channel, {self.frames} frames)"


class AgentQuiescence:
    """Whether the agent is still talking, read off the framework's own signal.

    `room_io` publishes the session's state to the room as the participant
    attribute `lk.agent.state` (`voice/room_io/room_io.py`, and the key is
    `livekit.agents.types.ATTRIBUTE_AGENT_STATE`), so the harness does not have
    to measure frame energy and guess - AGENTS.md's rule about not rebuilding
    what the framework provides applies to the harness too.

    WHY PACING NEEDED THIS. Waiting on `turn_complete` is not waiting for
    silence. A barged-in turn's speech handle resolves the moment it is cut, so
    `turn_complete` fires while the agent is already generating its reply to the
    interruption - and the next clip landed on top of it. The 8-turn session
    asked for barge-ins on turns 3 and 7 and got 3, 4, 7 and 8, with three turns
    producing no sentence at all.

    "listening" alone is not proof either: the framework pauses playout and
    passes through "listening" during a false interruption before resuming
    (`_interrupt_by_audio_activity`). So this requires the state to STAY out of
    speaking/thinking for `min_quiet` seconds rather than trusting one sample.
    """

    # "initializing" counts as busy: the agent has not reached the call yet.
    BUSY = frozenset({"speaking", "thinking", "initializing"})
    ATTRIBUTE = "lk.agent.state"

    # Peak sample above which a 20ms frame counts as speech rather than the
    # silence a published-but-idle track carries. s16, so full scale is 32767.
    SPEECH_PEAK = 500

    def __init__(self) -> None:
        self.state: str | None = None
        self.heard_audio = False
        # Counted rather than assumed: whether `lk.agent.state` reaches a remote
        # participant at all is an open question, and a run that reports zero
        # updates is the evidence for it.
        self.attribute_updates = 0
        self._busy_since = time.monotonic()

    def observe(self, state: str | None) -> None:
        if state is None or state == self.state:
            return
        self.state = state
        if state in self.BUSY:
            self._busy_since = time.monotonic()

    def observe_frame(self, frame: rtc.AudioFrame) -> None:
        """The agent's own audio, which is the signal that actually arrives.

        `lk.agent.state` did not reach the harness in two live runs - it stayed
        `None` through both seeding and attribute events - and the framework's
        `_on_agent_state_changed` cancels its previous `set_attributes` task on
        every state change, so under rapid churn the publish may never land.
        Rather than trust a signal that did not show up, the gate is driven by
        the audio itself: while frames above the noise floor are arriving, the
        agent is talking. The attribute is still read and still counts as busy
        when it says so, so if it starts arriving it only tightens the gate.
        """
        self.heard_audio = True
        # Via bytes: rtc.AudioFrame.data is already a typed memoryview, and
        # memoryview cannot cast between two non-byte formats. A 20ms frame is
        # under a kilobyte, so the copy is free.
        data = memoryview(bytes(frame.data)).cast("h")
        if any(sample > self.SPEECH_PEAK or sample < -self.SPEECH_PEAK for sample in data):
            self._busy_since = time.monotonic()

    def quiet_for(self) -> float:
        """Seconds since the agent was last busy, or 0.0 while it still is."""
        if self.state is not None and self.state in self.BUSY:
            return 0.0
        if self.state is None and not self.heard_audio:
            # Nothing has been heard and nothing has been said: unknown, which
            # must not read as ready.
            return 0.0
        return time.monotonic() - self._busy_since

    def watch(self, room: rtc.Room) -> None:
        @room.on("participant_attributes_changed")
        def _changed(changed, participant) -> None:  # noqa: ANN001
            if self.ATTRIBUTE in changed:
                self.attribute_updates += 1
                self.observe(changed[self.ATTRIBUTE])

        @room.on("participant_connected")
        def _connected(participant) -> None:  # noqa: ANN001
            self.observe(participant.attributes.get(self.ATTRIBUTE))

    def seed(self, room: rtc.Room) -> None:
        """Read the state off whoever is already here.

        The worker joins the room BEFORE the harness does, so
        `participant_connected` never fires for it and the first sample would
        otherwise have to wait for the agent's next state CHANGE. The first run
        with this gate sat at `state: None` through two timeouts for exactly
        that reason.
        """
        for participant in room.remote_participants.values():
            self.observe(participant.attributes.get(self.ATTRIBUTE))

    async def wait_until_quiet(self, *, min_quiet: float, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.quiet_for() >= min_quiet:
                return True
            await asyncio.sleep(0.05)
        print(
            f"  ! agent still busy (state={self.state}, "
            f"audio={'yes' if self.heard_audio else 'none'}) after {timeout:.0f}s; "
            "publishing anyway",
            flush=True,
        )
        return False


async def prime(
    mouth: "Mouth",
    tail: "EventTail",
    frames: list[rtc.AudioFrame],
    *,
    timeout: float,
) -> int | None:
    """Prove the agent can hear us before the measurement starts.

    SUBSCRIPTION IS NOT ENOUGH, measured: a session that logged
    `agent subscribed to the buyer track` still lost its entire first clip -
    the worker's own transcript sequence began at clip TWO, and the first
    `user_turn` arrived 80 seconds later, when clip two was published. So
    something between a subscribed track and the recogniser drops the first
    utterance; the agent's log shows an input stream attached and then detached
    with `source: SOURCE_UNKNOWN` against
    `accepted_sources: ["SOURCE_MICROPHONE"]`, which is the shape of a
    publication whose source resolves after the stream is first attached.

    Whatever the mechanism, it is not fixable from out here - so the harness
    stops guessing and asks. One throwaway line, and the measurement does not
    begin until the agent's own `user_turn` proves the path works end to end.
    The reply to it is discarded; the clips that follow are the measurement.
    Returns the agent turn index the priming line became, so the caller can
    count forward from a real number instead of assuming clip 1 is turn 1.
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        print(f"  priming (attempt {attempt})", flush=True)
        await mouth.say(frames)
        heard = await tail.wait_for(
            "user_turn",
            # The index is what is being discovered - there is no turn to scope
            # to yet, which is why this one is explicitly unscoped.
            turn=None,
            timeout=min(15.0, max(1.0, deadline - time.monotonic())),
            label="the agent to hear the priming line",
        )
        if heard is not None:
            print("  the agent heard us; starting the measurement", flush=True)
            return heard.get("turn")
    return None


class EventTail:
    """The agent's own event log, read forward. The harness's only clock."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._offset = 0
        self.seen: list[dict] = []

    def poll(self) -> list[dict]:
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as handle:
            handle.seek(self._offset)
            fresh = handle.readlines()
            self._offset = handle.tell()
        new = []
        for line in fresh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:  # a half-written final line
                continue
            new.append(record)
            self.seen.append(record)
        return new

    async def wait_for(
        self,
        event: str,
        *,
        turn: int | None,
        timeout: float,
        label: str | None = None,
    ) -> dict | None:
        """Wait for one named event, ON ONE TURN.

        `turn` is a required keyword with no default, deliberately. Matching an
        event without asking which turn it belongs to is the shape that cost
        this harness a run: the barge-in trigger waited for "a
        `tts_first_audio`" and matched the PREVIOUS turn's, so the interruption
        landed on the wrong reply and one requested barge-in became two. The
        adapter had the same bug the day before, where a speech handle left over
        from the disclosure read as a replacement for turn 1's. Twice in two
        days is a convention, so it is enforced here as a signature rather than
        written down as a rule.

        `turn=None` still means "any turn" and is a legitimate answer - the
        disclosure precedes every turn, and the priming line's turn index is
        the thing being discovered - but it has to be said out loud.
        """
        if turn is None:
            described = label or event
        else:
            described = label or f"{event} on turn {turn}"
        return await self.wait_while(
            lambda record: record.get("event") == event
            and (turn is None or record.get("turn") == turn),
            timeout=timeout,
            label=described,
        )

    async def wait_while(self, predicate, *, timeout: float, label: str) -> dict | None:
        """The raw form, for a condition that is not one event on one turn.

        Prefer `wait_for`. This exists for the genuine exceptions and is named
        so that reaching for it is a visible decision.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for record in self.poll():
                if predicate(record):
                    return record
            await asyncio.sleep(0.05)
        print(f"  ! timed out waiting for {label} after {timeout:.0f}s", flush=True)
        return None


class Mouth:
    """The single writer on the audio source.

    `rtc.AudioSource.capture_frame` is not safe to call from two tasks at once -
    a silence filler running alongside a clip publisher raised
    `InvalidState - failed to capture frame` on the first clip. So exactly one
    task ever touches the source: it drains queued speech and pushes silence
    whenever the queue is empty, which also keeps the track continuous the way a
    real caller's microphone would.
    """

    def __init__(self, source: rtc.AudioSource) -> None:
        self._source = source
        self._queue: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()
        self._silence = _silence()
        self._stop = asyncio.Event()
        self._pump: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._pump = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._queue.get_nowait()
                queued = True
            except asyncio.QueueEmpty:
                frame, queued = self._silence, False
            try:
                await self._source.capture_frame(frame)
            except Exception as exc:  # the room went away mid-run
                print(f"  ! capture failed: {exc}", flush=True)
                return
            finally:
                if queued:
                    self._queue.task_done()
            await asyncio.sleep(FRAME_MS / 1000)

    async def say(self, frames: list[rtc.AudioFrame]) -> None:
        """Queue one utterance and return when the last frame has gone out."""
        for frame in frames:
            self._queue.put_nowait(frame)
        await self._queue.join()

    async def aclose(self) -> None:
        self._stop.set()
        if self._pump is not None:
            self._pump.cancel()


async def run(args: argparse.Namespace) -> int:
    url = os.environ["LIVEKIT_URL"]
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("synthetic-buyer")
        .with_name("Synthetic buyer")
        .with_grants(api.VideoGrants(room_join=True, room=args.room))
        .to_jwt()
    )

    turns = list(BUYER_TURNS[: args.turns])
    print(f"synthesising {len(turns)} buyer clips + the barge-in line", flush=True)
    async with aiohttp.ClientSession() as http:
        tts = buyer_tts(http)
        clips = [await synthesise(tts, text) for text in turns]
        barge = await synthesise(tts, BARGE_IN_LINE)
        primer = await synthesise(tts, PRIMING_LINE)
        await tts.aclose()

    tail = EventTail(Path(args.log))
    room = rtc.Room()
    quiescence = AgentQuiescence()
    quiescence.watch(room)
    # One subscription, two jobs: the WAV when it was asked for, and the audio
    # the pacing gate runs on either way. Registered before connect, or the
    # agent's track can be subscribed before the handler exists.
    recorder = AgentRecorder(
        Path(args.record_agent) if args.record_agent else None, quiescence
    )
    recorder.watch(room)
    await room.connect(url, token)
    quiescence.seed(room)
    print(
        f"joined {args.room} as synthetic-buyer; agent state {quiescence.state!r}",
        flush=True,
    )

    source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("buyer", source)
    publication = await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    mouth = Mouth(source)
    mouth.start()

    # NOBODY IS LISTENING YET. `publish_track` returns as soon as the server has
    # the track; the agent subscribes afterwards, and frames pushed before that
    # are dropped rather than buffered for a late subscriber. The first paced run
    # lost its whole first clip this way - the agent's first user turn arrived 70
    # seconds later and matched clip TWO, so every clip after it landed one turn
    # out of phase, inside a live reply, and all four turns registered an
    # interruption where one was asked for. `wait_for_subscription` is the
    # framework's own answer; the blind sleep this replaced only hid the race.
    try:
        await asyncio.wait_for(
            publication.wait_for_subscription(), timeout=args.subscribe_timeout
        )
        print("  agent subscribed to the buyer track", flush=True)
    except TimeoutError:
        print(
            f"  ! nobody subscribed to the buyer track in "
            f"{args.subscribe_timeout:.0f}s - the agent will not hear this run",
            flush=True,
        )
        return 1

    try:
        # The agent opens with the AI disclosure, which is uninterruptible by
        # design (docs/04-). Talking over it would measure nothing.
        if not await tail.wait_for(
            "disclosure",
            # The disclosure precedes every turn, so there is nothing to scope.
            turn=None,
            timeout=45,
            label="the disclosure",
        ):
            return 1
        # The disclosure is uninterruptible by design (docs/04-), so wait for
        # it to finish rather than sleeping a guessed number of seconds.
        if not await quiescence.wait_until_quiet(
            min_quiet=args.quiet_seconds, timeout=args.disclosure_seconds
        ):
            await asyncio.sleep(args.disclosure_seconds)

        primed_turn = await prime(mouth, tail, primer, timeout=args.prime_timeout)
        if primed_turn is None:
            print(
                "  ! the agent never heard the priming line - aborting rather "
                "than measuring a run it cannot hear",
                flush=True,
            )
            return 1
        # The priming line is a real turn and the agent is replying to it. The
        # first clip was published straight over that reply and interrupted it,
        # which is half of why one requested barge-in became two: the handshake
        # proves audibility, it does not end the turn it created.
        await tail.wait_for(
            "turn_complete", turn=primed_turn, timeout=60, label="the priming turn"
        )
        await quiescence.wait_until_quiet(
            min_quiet=args.quiet_seconds, timeout=args.quiet_timeout
        )

        barge_at = {int(n) for n in args.barge_in_at.split(",") if n.strip()}
        for number, (text, frames) in enumerate(zip(turns, clips), start=1):
            interrupting = number in barge_at
            print(
                f"[{number}/{len(turns)}]{' BARGE-IN' if interrupting else ''} {text}",
                flush=True,
            )
            await mouth.say(frames)

            # Which agent turn this clip became. Read, never assumed: the
            # priming line takes one index and a lost clip takes none, so clip
            # N is not turn N and every later wait has to be scoped to the real
            # number.
            opened = await tail.wait_for(
                "user_turn",
                turn=None,
                timeout=45,
                label=f"the agent to hear clip {number}",
            )
            if opened is None:
                print(
                    f"  ! clip {number} was never heard - the rest of this run "
                    "would be a turn out of phase, so stopping here",
                    flush=True,
                )
                return 1
            agent_turn = opened.get("turn")
            print(f"    clip {number} is agent turn {agent_turn}", flush=True)

            if interrupting:
                # Wait for THIS turn to be speaking before cutting in, so the
                # interruption lands inside its speech window. Matching any
                # `tts_first_audio` put the barge-in on the previous turn's
                # reply and produced a second, unasked interruption.
                if await tail.wait_for(
                    "tts_first_audio", turn=agent_turn, timeout=30
                ):
                    await asyncio.sleep(args.barge_in_delay)
                    print("    interrupting", flush=True)
                    await mouth.say(barge)
                    # The interruption is itself a buyer utterance and becomes
                    # its own turn. Consume it, or the next clip's lookup would
                    # read this one's index as its own.
                    barged = await tail.wait_for(
                        "user_turn",
                        turn=None,
                        timeout=45,
                        label="the barge-in to register",
                    )
                    if barged is not None:
                        await tail.wait_for(
                            "turn_complete",
                            turn=barged.get("turn"),
                            timeout=60,
                        )

            await tail.wait_for("turn_complete", turn=agent_turn, timeout=60)
            # The turn is accounted for, but the agent may still be talking -
            # after a barge-in it is replying to the interruption. Publishing
            # now is what turned two requested barge-ins into four.
            await quiescence.wait_until_quiet(
                min_quiet=args.quiet_seconds, timeout=args.quiet_timeout
            )
            await asyncio.sleep(args.gap_seconds)
    finally:
        await mouth.aclose()
        await room.disconnect()
        tail.poll()
        written = recorder.close()
        print(f"agent audio: {written or 'nothing recorded'}", flush=True)
        print(
            f"lk.agent.state seen: {quiescence.attribute_updates} update(s), "
            f"final {quiescence.state!r}",
            flush=True,
        )

    print(f"\nran {len(turns)} turns; {len(tail.seen)} events on the stream", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True)
    parser.add_argument("--log", required=True, help="the agent's AMBASSADOR_EVENT_LOG")
    parser.add_argument("--turns", type=int, default=len(BUYER_TURNS))
    parser.add_argument(
        "--barge-in-at",
        default="",
        help="1-based turn numbers to interrupt, e.g. 3,6",
    )
    parser.add_argument("--barge-in-delay", type=float, default=0.6)
    parser.add_argument("--gap-seconds", type=float, default=1.0)
    parser.add_argument("--disclosure-seconds", type=float, default=8.0)
    parser.add_argument(
        "--quiet-seconds",
        type=float,
        default=1.0,
        help=(
            "how long the agent must stay out of speaking/thinking before the "
            "next clip. Below ~0.7s a false interruption's pause through "
            "'listening' can read as silence"
        ),
    )
    parser.add_argument("--quiet-timeout", type=float, default=45.0)
    parser.add_argument(
        "--prime-timeout",
        type=float,
        default=60.0,
        help=(
            "how long to keep offering a throwaway line until the agent proves "
            "it can hear the buyer track. Subscription alone does not prove it"
        ),
    )
    parser.add_argument(
        "--subscribe-timeout",
        type=float,
        default=30.0,
        help=(
            "how long to wait for the agent to subscribe to the buyer track "
            "before giving up - a run it cannot hear measures nothing"
        ),
    )
    parser.add_argument(
        "--record-agent",
        default="",
        help="write the agent's own audio to this WAV, for #41's ear check",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
