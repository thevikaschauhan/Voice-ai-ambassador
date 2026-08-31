import 'server-only'

import { createConnection } from 'node:net'
import type { Socket } from 'node:net'
import { BridgeUnavailable, readHandshake } from '@/lib/bridge/handshake'

/**
 * The client half of the agent's line protocol.
 *
 * The protocol, as frozen in `events_bridge.py`: connect, send the token and a
 * newline, then read newline-delimited JSON until the socket closes. Nothing is
 * ever sent after the token - the stream is read-only by design, and writing to
 * it would be writing to a server that stopped listening.
 *
 * This runs on the Next server and nowhere else. It is the only code that sees
 * the token; the route handler above it forwards events and not credentials.
 */

const CONNECT_TIMEOUT_MS = 2_000

/** A line longer than this is not one of our events; refuse rather than buffer. */
const MAX_LINE_BYTES = 1_048_576

export interface BridgeStream {
  lines: AsyncIterableIterator<string>
  close: () => void
}

export async function openBridge(signal?: AbortSignal): Promise<BridgeStream> {
  const { host, port, token } = await readHandshake()
  const socket = await connect(host, port)
  socket.write(token + '\n')

  const close = () => {
    socket.destroy()
  }
  signal?.addEventListener('abort', close, { once: true })

  return { lines: readLines(socket), close }
}

function connect(host: string, port: number): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host, port })
    socket.setTimeout(CONNECT_TIMEOUT_MS)
    socket.once('connect', () => {
      // The timeout guarded the handshake only. Leaving it armed would drop a
      // live call during any quiet stretch between turns.
      socket.setTimeout(0)
      resolve(socket)
    })
    socket.once('timeout', () => {
      socket.destroy()
      reject(new BridgeUnavailable(`timed out connecting to ${host}:${port}`))
    })
    socket.once('error', (error) => {
      reject(new BridgeUnavailable(`cannot reach ${host}:${port}: ${error.message}`))
    })
  })
}

/**
 * Newline-delimited framing over a byte stream.
 *
 * A socket does not deliver messages, it delivers bytes: one read can carry
 * half an event or three of them, so the tail has to be carried across reads.
 * Reassembling on the boundary is the whole job here, and getting it wrong
 * shows up as a JSON parse error on a turn that was perfectly fine.
 */
async function* readLines(socket: Socket): AsyncIterableIterator<string> {
  let tail = ''
  for await (const chunk of socket) {
    tail += (chunk as Buffer).toString('utf-8')
    if (tail.length > MAX_LINE_BYTES) {
      socket.destroy()
      throw new BridgeUnavailable('bridge sent an oversized line')
    }
    let newline = tail.indexOf('\n')
    while (newline !== -1) {
      const line = tail.slice(0, newline).trim()
      tail = tail.slice(newline + 1)
      if (line !== '') yield line
      newline = tail.indexOf('\n')
    }
  }
  // A final line with no trailing newline is a truncated write, not an event.
  // Dropping it is correct: half a JSON object is not a partial turn, it is
  // nothing.
}
