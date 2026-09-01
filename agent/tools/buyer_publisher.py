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

    async def wait_for(self, predicate, *, timeout: float, label: str) -> dict | None:
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
        await tts.aclose()

    tail = EventTail(Path(args.log))
    room = rtc.Room()
    await room.connect(url, token)
    print(f"joined {args.room} as synthetic-buyer", flush=True)

    source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("buyer", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    mouth = Mouth(source)
    mouth.start()

    try:
        # The agent opens with the AI disclosure, which is uninterruptible by
        # design (docs/04-). Talking over it would measure nothing.
        if not await tail.wait_for(
            lambda r: r.get("event") == "disclosure", timeout=45, label="the disclosure"
        ):
            return 1
        await asyncio.sleep(args.disclosure_seconds)

        barge_at = {int(n) for n in args.barge_in_at.split(",") if n.strip()}
        for number, (text, frames) in enumerate(zip(turns, clips), start=1):
            interrupting = number in barge_at
            print(
                f"[{number}/{len(turns)}]{' BARGE-IN' if interrupting else ''} {text}",
                flush=True,
            )
            await mouth.say(frames)

            if interrupting:
                # Wait for the agent to actually be speaking before cutting in,
                # so the interruption lands inside the speech window rather
                # than in the gap before it.
                if await tail.wait_for(
                    lambda r: r.get("event") == "tts_first_audio",
                    timeout=30,
                    label=f"turn {number} first audio",
                ):
                    await asyncio.sleep(args.barge_in_delay)
                    print("    interrupting", flush=True)
                    await mouth.say(barge)

            await tail.wait_for(
                lambda r: r.get("event") == "turn_complete",
                timeout=60,
                label=f"turn {number} completing",
            )
            await asyncio.sleep(args.gap_seconds)
    finally:
        await mouth.aclose()
        await room.disconnect()
        tail.poll()

    print(f"\nran {len(turns)} turns; {len(tail.seen)} events on the stream", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
