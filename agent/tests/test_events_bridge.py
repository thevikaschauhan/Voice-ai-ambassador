"""The one surface that carries the unredacted stream.

Every assertion here is made on BYTES A CLIENT ACTUALLY RECEIVED over a real
loopback socket, not on the state of the object that sent them. This module
exists to move buyer transcripts to another process, so "the queue contains the
right dict" is not the claim under test - what the reader gets is.

Two claims pulled in opposite directions, asserted together, the same way
test_events.py does it:

  fidelity   the demo surface needs the buyer's words, the model's sentence and
             the validator's detail, because a transcript rail and a guardrail
             decision are illegible without them.
  isolation  and adding that surface must not widen the emitted stream by one
             byte, must not reach anything but the process holding the token,
             and must never put the token itself where the data is.
"""

from __future__ import annotations

import asyncio
import json
import stat
from io import StringIO
from pathlib import Path

import pytest

from adapter.events import EventLog
from adapter.events_bridge import (
    HANDSHAKE_ENV,
    PORT_ENV,
    EventsBridge,
    bridge_from_env,
)

UTTERANCE = "My budget is about two million and I am buying from Mumbai"
SENTENCE = "Binghatti Skyrise in Business Bay starts from AED 985,000."


def emitted_lines(stream: StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class Reader:
    """A client of the bridge, speaking the same protocol the Next server will."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls, bridge: EventsBridge, token: str | None) -> "Reader":
        reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
        if token is not None:
            writer.write(token.encode("utf-8") + b"\n")
            await writer.drain()
        return cls(reader, writer)

    async def line(self, timeout: float = 2.0) -> dict:
        raw = await asyncio.wait_for(self._reader.readline(), timeout)
        if not raw:
            raise EOFError("the bridge closed the connection")
        return json.loads(raw)

    async def lines(self, count: int, timeout: float = 2.0) -> list[dict]:
        return [await self.line(timeout) for _ in range(count)]

    async def closed(self, timeout: float = 2.0) -> bool:
        """True when the peer hung up without sending anything."""
        try:
            raw = await asyncio.wait_for(self._reader.read(1), timeout)
        except TimeoutError:
            return False
        return raw == b""

    async def aclose(self) -> None:
        self._writer.close()


@pytest.fixture
def log() -> tuple[EventLog, StringIO]:
    stream = StringIO()
    return EventLog(session_id="sess_test", stream=stream), stream


@pytest.fixture
async def bridge(log, tmp_path):
    event_log, _ = log
    started = EventsBridge(event_log, handshake_path=tmp_path / "bridge.json")
    await started.start()
    yield started
    await started.aclose()


async def test_a_holder_of_the_token_receives_the_buyers_actual_words(bridge, log):
    event_log, stream = log
    reader = await Reader.connect(bridge, bridge.token)

    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    received = await reader.line()

    # The whole reason this surface exists.
    assert received["event"] == "user_turn"
    assert received["text"] == UTTERANCE

    # And the emitted stream is untouched by its existence.
    assert emitted_lines(stream)[-1]["text"] == "[redacted]"
    assert UTTERANCE not in stream.getvalue()
    await reader.aclose()


async def test_the_guardrail_decision_arrives_whole(bridge, log):
    event_log, stream = log
    reader = await Reader.connect(bridge, bridge.token)

    event_log.emit(
        "guardrail",
        turn=1,
        outcome="blocked",
        raw=SENTENCE,
        spoken=None,
        detail="figure 985000.0 is not in the allowed set",
        figures=[{"surface": "AED 985,000", "value": 985000.0, "kind": "amount"}],
    )
    received = await reader.line()

    # A blocked sentence is only legible next to the figure it was blocked for.
    assert received["raw"] == SENTENCE
    assert received["detail"].startswith("figure 985000.0")
    assert received["figures"][0]["surface"] == "AED 985,000"
    # `spoken: null` is a fact about the turn, not content, and stays null.
    assert received["spoken"] is None

    assert SENTENCE not in stream.getvalue()
    await reader.aclose()


async def test_a_wrong_token_gets_nothing_and_no_explanation(bridge, log):
    event_log, _ = log
    reader = await Reader.connect(bridge, "not-the-token")

    assert await reader.closed() is True
    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    with pytest.raises(EOFError):
        await reader.line(timeout=0.5)
    await reader.aclose()


async def test_a_browsers_http_request_is_not_a_handshake(bridge, log):
    """What a drive-by page port-scanning localhost actually sends.

    The token is the boundary, not the loopback binding: a page in the
    presenter's own browser can reach 127.0.0.1. Its first line is a request
    line, so it never gets past the handshake.
    """
    event_log, _ = log
    reader = await Reader.connect(bridge, "GET / HTTP/1.1")

    assert await reader.closed() is True
    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    with pytest.raises(EOFError):
        await reader.line(timeout=0.5)
    await reader.aclose()


async def test_a_surface_connecting_mid_call_replays_the_session(bridge, log):
    """A reload during the demo must not lose the turns already spoken."""
    event_log, _ = log
    event_log.emit("session_start", model="qwen/qwen3.7-flash")
    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    event_log.emit("guardrail", turn=1, outcome="pass", raw=SENTENCE, spoken=SENTENCE)

    reader = await Reader.connect(bridge, bridge.token)
    replayed = await reader.lines(3)

    assert [line["event"] for line in replayed] == [
        "session_start",
        "user_turn",
        "guardrail",
    ]
    assert replayed[1]["text"] == UTTERANCE

    # And it keeps up from there rather than only replaying.
    event_log.emit("turn_complete", turn=1, total_ms=1458.6)
    assert (await reader.line())["event"] == "turn_complete"
    await reader.aclose()


async def test_an_event_during_the_handoff_is_delivered_exactly_once(bridge, log):
    event_log, _ = log
    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    reader = await Reader.connect(bridge, bridge.token)
    event_log.emit("user_turn", turn=2, text="In dirhams.")

    first, second = await reader.lines(2)
    assert (first["turn"], second["turn"]) == (1, 2)
    with pytest.raises(TimeoutError):
        await reader.line(timeout=0.4)
    await reader.aclose()


async def test_it_refuses_to_bind_anything_but_loopback(log):
    event_log, _ = log
    with pytest.raises(ValueError, match="loopback only"):
        EventsBridge(event_log, handshake_path=Path("unused"), host="0.0.0.0")


async def test_the_handshake_file_is_owner_only_and_goes_away(log, tmp_path):
    event_log, stream = log
    path = tmp_path / "handshake" / "bridge.json"
    started = EventsBridge(event_log, handshake_path=path)
    await started.start()

    payload = json.loads(path.read_text())
    assert payload["host"] == "127.0.0.1"
    assert payload["port"] == started.port
    assert payload["token"] == started.token

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"handshake file is {oct(mode)}, not owner-only"

    # The token reaches the consumer here and nowhere else.
    assert started.token not in stream.getvalue()

    await started.aclose()
    assert not path.exists()


async def test_the_token_never_reaches_the_emitted_stream(log, tmp_path):
    """The credential for the unredacted surface must not land in the sink that
    exists because it is redacted."""
    event_log, stream = log
    started = EventsBridge(event_log, handshake_path=tmp_path / "bridge.json")
    await started.start()
    event_log.emit("events_bridge", host="127.0.0.1", port=started.port)
    event_log.emit("user_turn", turn=1, text=UTTERANCE)

    assert started.token not in stream.getvalue()
    await started.aclose()


async def test_a_wedged_reader_never_blocks_an_emit(bridge, log):
    """The voice path is answering a buyer inside 1.2 seconds. A demo surface
    that stopped reading does not get to slow that down."""
    event_log, _ = log
    event_log.emit("user_turn", turn=0, text="first")
    reader = await Reader.connect(bridge, bridge.token)
    assert (await reader.line())["turn"] == 0  # the client is in the fan-out

    # Far more than the client queue bound, and the reader never reads it.
    for turn in range(1, 4000):
        event_log.emit("user_turn", turn=turn, text=UTTERANCE)

    # Nothing raised and nothing awaited on the emit path. What the reader
    # eventually gets is the RECENT traffic plus an in-band count of what it
    # lost: a demo surface wants the turn that just happened, and a gap it is
    # not told about is worse than a gap it is.
    seen: list[dict] = []
    for _ in range(4):
        seen.append(await reader.line())
    assert any(line["event"] == "bridge_backpressure" for line in seen)
    notice = next(line for line in seen if line["event"] == "bridge_backpressure")
    dropped = notice["dropped"]
    assert dropped > 0
    turns = [line["turn"] for line in seen if line["event"] == "user_turn"]
    assert min(turns) > 1000, f"oldest lines should have been dropped, got {turns}"
    await reader.aclose()


async def test_after_close_the_observer_is_gone(log, tmp_path):
    event_log, _ = log
    started = EventsBridge(event_log, handshake_path=tmp_path / "bridge.json")
    await started.start()
    event_log.emit("user_turn", turn=1, text=UTTERANCE)
    assert len(started._backlog) == 1

    await started.aclose()

    # Emitting after teardown is normal - session_end runs late - and must not
    # raise, and must not keep feeding a bridge that is gone.
    event_log.emit("session_end", turns=4)
    assert len(started._backlog) == 1


async def test_it_is_off_unless_the_environment_names_a_handshake(log, monkeypatch):
    event_log, _ = log
    monkeypatch.delenv(HANDSHAKE_ENV, raising=False)
    assert bridge_from_env(event_log) is None

    monkeypatch.setenv(HANDSHAKE_ENV, "/tmp/does-not-need-to-exist-yet.json")
    built = bridge_from_env(event_log)
    assert built is not None
    assert built.token

    monkeypatch.setenv(PORT_ENV, "not-a-port")
    with pytest.raises(ValueError, match=PORT_ENV):
        bridge_from_env(event_log)
