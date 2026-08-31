"""Issue #18: the Fish socket a barge-in discards is never replaced.

These run against the REAL `livekit.agents.utils.ConnectionPool` rather than a
stand-in for it, because the defect is entirely in that class's behaviour and a
fake would only re-assert what the fake was written to do. The connections
themselves are fakes - a socket is whatever `connect_cb` returns - so the whole
mechanism is exercised with no network and no Fish account.

The order matters. The first test is the measurement the issue asks for before
any fix: it shows the pool going empty on cancellation and staying empty. The
second shows the hook restoring it. If a future `livekit-agents` patch release
fixes this upstream, the first test is what fails, and the hook can be deleted.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents.utils import ConnectionPool  # noqa: E402

from adapter.tts_pool import connection_state, reprewarm  # noqa: E402


class FakeSocket:
    def __init__(self, index: int) -> None:
        self.index = index
        self.closed = False


class FakeTTS:
    """Shaped like the Fish plugin where this module touches it: one `_pool`,
    built the way `fishaudio.TTS.__init__` builds it."""

    def __init__(self) -> None:
        self.opened: list[FakeSocket] = []
        self._pool: ConnectionPool[FakeSocket] = ConnectionPool(
            connect_cb=self._connect,
            close_cb=self._close,
            max_session_duration=300,
            mark_refreshed_on_get=True,
        )

    async def _connect(self, timeout: float) -> FakeSocket:
        sock = FakeSocket(len(self.opened))
        self.opened.append(sock)
        return sock

    async def _close(self, sock: FakeSocket) -> None:
        sock.closed = True


async def until(predicate: Any, *, timeout: float = 2.0) -> None:
    """Wait for a background pool task to land, without sleeping blind."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the pool")
        await asyncio.sleep(0.005)


async def start_warm() -> FakeTTS:
    """A TTS in the state `AgentActivity.start` leaves it in: prewarmed once,
    one spare socket in the pool, ready for the first utterance."""
    tts = FakeTTS()
    tts._pool.prewarm()
    await until(lambda: len(tts.opened) == 1)
    return tts


async def barge_in(tts: FakeTTS) -> None:
    """One synthesis holding the pooled socket, cancelled mid-stream.

    This is `SynthesizeStream._run` and nothing else: the socket is held inside
    `pool.connection()` while audio streams, and a barge-in cancels the task.
    """
    holding = asyncio.Event()

    async def synthesise() -> None:
        async with tts._pool.connection(timeout=1.0):
            holding.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(synthesise())
    await holding.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- the measurement, before any fix -------------------------------------


async def test_a_barge_in_discards_the_socket_and_prewarm_cannot_replace_it():
    """The mechanism, end to end, against the framework's own pool.

    `connection()` unwinds through `except BaseException: self.remove(conn)`,
    and `CancelledError` is a BaseException, so the socket is closed rather than
    returned. That half is deliberate. The defect is the next two assertions:
    `prewarm()` is a permanent no-op once the framework has called it, so
    nothing refills the pool, and the utterance after the barge-in opens a fresh
    TCP + TLS + WebSocket handshake to Fish before it can send a byte of text.
    """
    tts = await start_warm()
    await barge_in(tts)

    # The framework's own retry at prewarming, which is all a caller can do
    # without reaching into the pool.
    tts._pool.prewarm()
    await asyncio.sleep(0.05)
    assert len(tts.opened) == 1, "prewarm() refilled the pool; the hook is obsolete"

    # So the next turn connects inline, on the buyer's clock.
    await tts._pool.get(timeout=1.0)
    assert tts._pool.last_connection_reused is False
    assert len(tts.opened) == 2


async def test_a_clean_turn_keeps_its_socket_pooled():
    """The control. Without it, "the pool is empty" proves nothing about
    barge-in: it could be true of every turn."""
    tts = await start_warm()
    async with tts._pool.connection(timeout=1.0):
        pass

    await tts._pool.get(timeout=1.0)
    assert tts._pool.last_connection_reused is True
    assert len(tts.opened) == 1


# --- the fix --------------------------------------------------------------


async def test_re_prewarming_after_a_barge_in_keeps_the_next_turn_pooled():
    tts = await start_warm()
    await barge_in(tts)

    assert reprewarm(tts) == "requested"
    # The connect happens here, in the silence while the buyer is still
    # talking, rather than inline on the reply.
    await until(lambda: len(tts.opened) == 2)

    await tts._pool.get(timeout=1.0)
    assert tts._pool.last_connection_reused is True
    assert len(tts.opened) == 2, "the post-barge-in turn still paid a connect"


async def test_re_prewarming_twice_over_survives_a_second_barge_in():
    """One barge-in is the demo; a tech lead interrupting twice is the scenario
    the issue is actually about. A hook that only works once would pass the
    test above and still fail on stage."""
    tts = await start_warm()
    for _ in range(3):
        await barge_in(tts)
        assert reprewarm(tts) == "requested"
        await asyncio.sleep(0.05)

    await tts._pool.get(timeout=1.0)
    assert tts._pool.last_connection_reused is True
    assert len(tts.opened) == 4  # the original plus one spare per interruption


async def test_re_prewarming_a_pool_that_never_lost_anything_opens_nothing():
    """`prewarm()` keeps its own "only if empty" precondition, so the hook
    cannot leak sockets on a path that did not lose one."""
    tts = await start_warm()

    assert reprewarm(tts) == "requested"
    await asyncio.sleep(0.05)
    assert len(tts.opened) == 1


# --- the hook reaches into a private attribute, so it says when it stops fitting


async def test_a_tts_whose_pool_no_longer_has_the_guard_reports_unavailable():
    """`_prewarm_task` is private and the plugin is pinned `>=1.7,<2`. A patch
    release that renames it must degrade to the slow-but-correct behaviour
    visibly, on the event stream, rather than silently."""

    class Renamed:
        _pool = type("Pool", (), {"prewarm": lambda self: None})()

    assert reprewarm(Renamed()) == "unavailable"
    assert reprewarm(object()) == "unavailable"


# --- the measurement the human reads off a live call ----------------------


async def test_the_connection_state_says_which_socket_the_turn_got():
    tts = await start_warm()

    await tts._pool.get(timeout=1.0)
    assert connection_state(tts) == {"reused": True, "connect_ms": None, "pooled": 1}


async def test_a_cold_connect_reports_the_handshake_it_cost():
    """The `reused: False` case, which is the whole point: on a barge-in turn
    this is the number the latency meter was missing."""
    tts = await start_warm()
    await barge_in(tts)

    await tts._pool.get(timeout=1.0)
    state = connection_state(tts)
    assert state is not None
    assert state["reused"] is False
    # A measured handshake, not a None-shaped hole: the fake connects instantly,
    # so the assertion is that a number was taken at all.
    assert isinstance(state["connect_ms"], float)
    assert state["connect_ms"] >= 0.0


async def test_a_reused_socket_reports_no_connect_rather_than_a_zero_one():
    """The pool writes `last_acquire_time = 0.0` on the reuse path. Passing that
    through would put a 0.0ms connect on the meter for a connect that never
    happened - events.py's own None-vs-zero rule, one layer down."""
    tts = await start_warm()
    await tts._pool.get(timeout=1.0)

    assert tts._pool.last_acquire_time == 0.0
    state = connection_state(tts)
    assert state is not None
    assert state["connect_ms"] is None


def test_a_tts_with_no_readable_pool_reports_nothing_at_all():
    assert connection_state(object()) is None
