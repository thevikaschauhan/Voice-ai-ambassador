"""Keep a Fish socket pooled across a barge-in, and measure whether it worked
(issue #18).

## The mechanism

`SynthesizeStream._run` holds its websocket inside the framework's
`ConnectionPool.connection()`, whose contract is:

    try:
        yield conn
    except BaseException:
        self.remove(conn)     # closed, not returned
        raise
    else:
        self.put(conn)

A barge-in cancels the `tts_node` task. `CancelledError` derives from
`BaseException`, so the socket is discarded rather than pooled. Discarding it is
CORRECT and deliberately not the thing to change: returning a half-flushed
socket would let the aborted utterance's audio leak onto the next turn and break
the chunk-granular audit claim in docs/04-.

What is wrong is that nothing replaces it. `ConnectionPool.prewarm()` opens a
spare only while `self._prewarm_task is None`, and the framework sets that once,
at activity start (`AgentActivity.start`). It is a weakref that is never cleared,
so after the first prewarm the method is a permanent no-op and the turn after
any barge-in opens a fresh TCP + TLS + WebSocket upgrade to api.fish.audio
inline, before a byte of text can be sent.

## The hook

`reprewarm` is the smallest thing that restores the framework's own behaviour:
clear that one guard and call the framework's `prewarm()` again. Nothing here
opens, owns, closes or hands out a connection - the pool still does all of it,
including the connect timeout, the error handling and the "only if the pool is
empty" check. That is the AGENTS.md rule about not rebuilding what the framework
provides, applied to a one-line defect.

`_prewarm_task` is private and `pyproject.toml` pins the plugin `>=1.7,<2`, so a
patch release could rename it. That is why `reprewarm` returns an outcome
instead of assuming: an `unavailable` on the event stream says the hook stopped
fitting, which turns a silent return to slow barge-in turns into a visible one.

## The measurement

Issue #18 asks for the number before the fix, and the framework's own
`TTSMetrics.acquire_time` cannot give it: `livekit-agents` initialises
`acquire_time`/`connection_reused` to 0.0/False on the stream and only some
plugins fill them in - `livekit-plugins-fishaudio` 1.7.0 does not, so on this
stack they are constants. `ConnectionPool` does keep the same two facts about
its own last `get()`, and `connection_state` reads them.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

logger = logging.getLogger("ambassador.tts_pool")

# The framework's own one-shot guard on `ConnectionPool.prewarm()`. Named once,
# here, because it is the single private attribute this module depends on.
_PREWARM_GUARD: Final = "_prewarm_task"

# "requested", not "warmed": `prewarm()` schedules a background connect and
# swallows its own failures, so the honest claim at this point is that the pool
# was asked. Whether a spare actually landed shows up on the next turn's
# `tts_connection` event, which is the measurement, not the intention.
Outcome = Literal["requested", "unavailable"]


def _pool_of(tts: Any) -> Any | None:
    return getattr(tts, "_pool", None)


def reprewarm(tts: Any) -> Outcome:
    """Ask the TTS's connection pool to open a spare again.

    Never raises and never blocks: the pool's `prewarm()` only creates a task.
    """
    pool = _pool_of(tts)
    if (
        pool is None
        or not hasattr(pool, _PREWARM_GUARD)
        or not callable(getattr(pool, "prewarm", None))
    ):
        logger.warning(
            "cannot re-prewarm the TTS pool: %s has no %s guard to clear, so "
            "post-barge-in turns pay a cold connect",
            type(tts).__name__,
            _PREWARM_GUARD,
        )
        return "unavailable"
    setattr(pool, _PREWARM_GUARD, None)
    try:
        pool.prewarm()
    except RuntimeError:
        # No running loop, so there is nothing to schedule the connect on. Only
        # reachable outside a session; the barge-in path always has one.
        logger.debug("no running loop to re-prewarm the TTS pool on")
        return "unavailable"
    return "requested"


def connection_state(tts: Any) -> dict[str, Any] | None:
    """What the pool did to hand out the socket the current synthesis is using,
    or None when this TTS has no readable pool.

    `connect_ms` is None on a reused socket rather than 0.0. Nothing was
    connected, and events.py's rule is that a missing measurement and a
    zero-latency stage must not look the same - the pool itself writes 0.0 into
    `last_acquire_time` on the reuse path, which is exactly that collision.
    """
    pool = _pool_of(tts)
    if pool is None or not hasattr(pool, "last_connection_reused"):
        return None
    reused = bool(pool.last_connection_reused)
    return {
        "reused": reused,
        "connect_ms": (
            None if reused else round(float(pool.last_acquire_time) * 1000, 1)
        ),
        "pooled": len(getattr(pool, "_connections", ()) or ()),
    }
