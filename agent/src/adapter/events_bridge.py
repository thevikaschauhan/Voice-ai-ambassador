"""The full-fidelity event stream, for the demo surface only (issue #9).

Everything else that leaves this process is redacted. `events.py` emits no free
text at all to stdout or the file sink - no buyer utterance, no model sentence,
no validator detail - because those sinks are durable or scraped and free text
carries buyer-derived content by more routes than the obvious one (docs/03-,
validator 4). That is the right default and it is not changing.

But the demo surface needs exactly what that stream withholds: the transcript
rail shows what the buyer said, the ambassador view shows the brief, and the
guardrail decisions are only legible next to the sentence they objected to. So
there has to be one surface that carries the unredacted view, and the whole
design question is how to make that one surface unreachable by anything except
the process we intend.

TWO BOUNDS, AND NEITHER IS SUFFICIENT ALONE.

  1. Bound to 127.0.0.1. Full-fidelity buyer data never leaves the machine,
     which is the ADR-012/013 posture: in-memory, nothing durable, nothing off
     the host.
  2. A per-session random token, required as the first line of every
     connection.

The second exists because the first is not a boundary against a page running in
the same browser. Any web page the presenter has open can reach 127.0.0.1 - it
is the browser's own loopback, not ours - so a drive-by page can port-scan
localhost and speak to whatever answers. Localhost binding stops the network;
the token stops the browser. Neither stops both.

The token is held by the Next server and never by the browser. The browser only
ever talks same-origin to Next, so the credential lives on the same side of the
wire as the data it protects, and a page that finds this port has nothing to
send it.

WHY A LINE PROTOCOL AND NOT HTTP. The only client is a trusted server-side
process, so HTTP semantics buy nothing here and cost a parser: headers, methods,
chunked encoding, and a shape a browser can produce by accident. What is left is
`readline()` for the token and `write()` for each event, which is a surface
small enough to read in one sitting - and this module carries buyer data, so
that is the property worth optimising for. A browser's `fetch` cannot pass the
handshake either way: its first line is a request line, not the token.

READ-ONLY. After the token line the server never reads from the socket again.
There is no command channel here, and the mode toggles are deliberately not
folded in: `GUARDRAIL_MODE` and `PROMPT_MODE` are read at session start, so a
control channel is a different question with a different threat model, and
adding one would make this a surface that can change the agent rather than a
surface that can watch it.

NEVER BLOCKS THE VOICE PATH. `EventLog.emit` runs on the hot path - two of its
calls are the TTFT and TTS-first-audio marks - so the observer this module
installs only ever does a non-blocking put onto a bounded queue. A slow or
wedged reader loses its oldest lines and is told how many, exactly as the stdout
writer does; it never applies backpressure to a turn in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
from collections import deque
from pathlib import Path
from typing import Any, Final

from adapter.events import EventLog

logger = logging.getLogger("ambassador.events_bridge")

# Presence of this path is what turns the bridge on. Deliberately the same
# variable that tells the consumer how to connect: a bridge nobody was told
# about is a listening socket for no reason, so there is no way to enable one.
HANDSHAKE_ENV: Final = "AMBASSADOR_BRIDGE_HANDSHAKE"

# Optional fixed port. The default is 0 - an ephemeral port - because the
# handshake file carries the real one and a fixed port is one more thing a
# scanning page can guess.
PORT_ENV: Final = "AMBASSADOR_BRIDGE_PORT"

LOOPBACK: Final = frozenset({"127.0.0.1", "::1", "localhost"})

# Enough for a demo's whole session, bounded so a long call cannot grow without
# limit. A surface connecting late replays this and catches up.
_BACKLOG_MAX: Final = 4096

# Per-client. Same discipline as the stdout writer: oldest lines lose, and the
# count is reported rather than the drop being silent.
_CLIENT_QUEUE_MAX: Final = 1024

# One demo surface, plus room for a reload that has not been reaped yet. A
# bound at all, so a client that reconnects in a loop cannot exhaust the
# process.
_MAX_CLIENTS: Final = 4

# The token line must arrive promptly. A connection that opens and says nothing
# is either broken or probing; either way it does not get to hold a slot.
_HANDSHAKE_TIMEOUT: Final = 5.0

# How long teardown waits for a client to flush what it has already been
# handed. Short, because a wedged reader must not hold up the process exit;
# long enough that a healthy one sees `session_end`, which is the event that
# tells the surface the call is over rather than the socket having dropped.
_DRAIN_TIMEOUT: Final = 2.0

# The token line is ~43 characters. This is the reader's buffer bound, so a
# client that sends megabytes without a newline is disconnected rather than
# buffered.
_READ_LIMIT: Final = 4096

_STOP: Final = object()


class EventsBridge:
    """Serves this session's unredacted event records on loopback.

    Lifecycle mirrors `EventLog`: created beside it, started before the first
    event so the backlog is complete, closed in `shutdown_session`.
    """

    def __init__(
        self,
        log: EventLog,
        *,
        handshake_path: Path,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
    ) -> None:
        if host not in LOOPBACK:
            # Not a configuration mistake to tolerate. Binding this anywhere
            # else publishes buyer transcripts to the network, and the token
            # was never meant to be the only thing standing in the way.
            raise ValueError(
                f"the events bridge binds loopback only, refusing host {host!r}"
            )
        self._log = log
        self._host = host
        self._port = port
        self._handshake_path = handshake_path
        self._token = token or secrets.token_urlsafe(32)
        self._server: asyncio.Server | None = None
        self._backlog: deque[str] = deque(maxlen=_BACKLOG_MAX)
        self._clients: set[_Client] = set()
        self._remove_observer: Any = None
        self._closing = False

    @property
    def token(self) -> str:
        return self._token

    @property
    def port(self) -> int:
        """The bound port, valid after `start()`."""
        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
            limit=_READ_LIMIT,
        )
        self._remove_observer = self._log.add_observer(self._on_event)
        self._write_handshake()
        logger.info("events bridge listening on %s:%d", self._host, self.port)
        return self.port

    async def aclose(self) -> None:
        """Stop observing, let the readers finish, then close.

        The drain is not politeness. `shutdown_session` emits `session_end` and
        then closes this, so tearing the sockets down without waiting loses the
        one event that distinguishes "the call ended" from "the connection
        dropped" - and those mean different things on screen.
        """
        self._closing = True
        if self._remove_observer is not None:
            self._remove_observer()
            self._remove_observer = None
        self._delete_handshake()

        clients = list(self._clients)
        for client in clients:
            client.stop()
        if clients:
            # A wedged reader does not get to hold up process exit; it just
            # does not get its tail.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(_DRAIN_TIMEOUT):
                    await asyncio.gather(*(client.drained() for client in clients))
        self._clients.clear()

        if self._server is not None:
            self._server.close()
            # `wait_closed()` waits for every connection handler, so it is
            # bounded here: process exit must not depend on a peer that stopped
            # reading.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(_DRAIN_TIMEOUT):
                    await self._server.wait_closed()
            self._server = None

    # -- the handshake file -----------------------------------------------

    def _write_handshake(self) -> None:
        """Hand the port and token to the local consumer, and to nobody else.

        Created 0600 before anything is written to it, so the token is never
        briefly world-readable. The directory is the caller's choice and is not
        widened here.
        """
        payload = json.dumps(
            {"host": self._host, "port": self.port, "token": self._token}
        )
        self._handshake_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._handshake_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)

    def _delete_handshake(self) -> None:
        with contextlib.suppress(OSError):
            self._handshake_path.unlink()

    # -- the observer -----------------------------------------------------

    def _on_event(self, record: dict[str, Any]) -> None:
        """Called from `EventLog.emit`, on the voice path. Never blocks."""
        line = json.dumps(record, ensure_ascii=False, default=str)
        self._backlog.append(line)
        for client in self._clients:
            client.offer(line)

    # -- connections ------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._closing or len(self._clients) >= _MAX_CLIENTS:
            await _close(writer)
            return

        if not await self._authenticate(reader):
            # No message and no distinguishing delay: a caller that guessed
            # wrong learns only that the connection closed.
            await _close(writer)
            return

        if self._closing:
            # Re-checked AFTER the handshake, not only before it. A connection
            # that authenticated while teardown was running would otherwise
            # join a set that has already been drained and cleared, so nothing
            # would ever stop its pump - and `Server.wait_closed()` waits for
            # every handler, so one such client hangs the whole teardown.
            await _close(writer)
            return

        client = _Client(writer)
        # The backlog is taken before the client joins the fan-out, so an event
        # emitted during this handoff is delivered once, not twice or never.
        for line in list(self._backlog):
            client.offer(line)
        self._clients.add(client)
        try:
            await client.pump()
        finally:
            self._clients.discard(client)
            await _close(writer)

    async def _authenticate(self, reader: asyncio.StreamReader) -> bool:
        try:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                line = await reader.readline()
        except (TimeoutError, asyncio.IncompleteReadError, ValueError, OSError):
            # ValueError covers the reader's own limit: a client that sends
            # more than _READ_LIMIT without a newline is not authenticating.
            return False
        # compare_digest, not ==, so a wrong token cannot be narrowed down one
        # character at a time by timing the reply.
        return hmac.compare_digest(line.decode("utf-8", "replace").strip(), self._token)


class _Client:
    """One connected reader, with its own bounded queue.

    Bounded because the alternative is that a stalled demo surface holds memory
    proportional to how long it stalls, on a process whose other job is
    answering a buyer inside 1.2 seconds.
    """

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX)
        self._dropped = 0
        self._done = asyncio.Event()

    def offer(self, line: str) -> None:
        """Non-blocking by contract: this runs on the voice path."""
        while True:
            try:
                self._queue.put_nowait(line)
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - drained under us
                    return
                self._dropped += 1

    def stop(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_STOP)

    async def drained(self) -> None:
        await self._done.wait()

    async def pump(self) -> None:
        try:
            await self._pump()
        finally:
            self._done.set()

    async def _pump(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            if self._dropped:
                dropped, self._dropped = self._dropped, 0
                # In band, so a gap in the stream is never silent: the surface
                # can say it fell behind rather than quietly missing turns.
                item = (
                    json.dumps(
                        {
                            "event": "bridge_backpressure",
                            "dropped": dropped,
                            "queue_max": _CLIENT_QUEUE_MAX,
                        }
                    )
                    + "\n"
                    + item
                )
            try:
                self._writer.write(item.encode("utf-8") + b"\n")
                await self._writer.drain()
            except (ConnectionError, OSError):
                return


async def _close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, OSError):
        writer.close()
        await writer.wait_closed()


def bridge_from_env(log: EventLog) -> EventsBridge | None:
    """The bridge the environment asked for, or None.

    Off unless `AMBASSADOR_BRIDGE_HANDSHAKE` names a path. A listening socket
    carrying buyer transcripts is not something to have on by default, and the
    variable that enables it is the same one that tells the consumer where to
    find it, so an enabled bridge always has a reader that was told about it.
    """
    path = os.environ.get(HANDSHAKE_ENV, "").strip()
    if not path:
        return None
    port = 0
    raw_port = os.environ.get(PORT_ENV, "").strip()
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError(f"{PORT_ENV} must be an integer, got {raw_port!r}") from exc
    return EventsBridge(log, handshake_path=Path(path), port=port)
