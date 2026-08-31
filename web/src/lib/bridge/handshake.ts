import 'server-only'

import { readFile } from 'node:fs/promises'
import type { Stats } from 'node:fs'
import { stat } from 'node:fs/promises'

/**
 * Where the agent left the details of its unredacted stream.
 *
 * The agent writes this file 0600 at session start and deletes it on teardown
 * (`agent/src/adapter/events_bridge.py`). It carries the port and the token,
 * and it is the only place the token exists outside the agent's memory - so
 * everything that touches it stays on this side of the wire. Nothing in
 * `handshake` is ever returned to a client component or serialised into a
 * page: see `app/api/session/stream/route.ts`, which reads it, connects, and
 * forwards only the events.
 */

export interface Handshake {
  host: string
  port: number
  token: string
}

const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost'])

export class BridgeUnavailable extends Error {}

/**
 * The path the agent was told to write, or null when the bridge is off.
 *
 * Same variable on both sides on purpose: the agent only listens when this is
 * set, so an unset variable here means there is nothing to connect to rather
 * than a misconfiguration to report.
 */
export function handshakePath(): string | null {
  const path = process.env.AMBASSADOR_BRIDGE_HANDSHAKE?.trim()
  return path ? path : null
}

export async function readHandshake(): Promise<Handshake> {
  const path = handshakePath()
  if (path === null) {
    throw new BridgeUnavailable('AMBASSADOR_BRIDGE_HANDSHAKE is not set')
  }

  let info: Stats
  try {
    info = await stat(path)
  } catch {
    // The agent deletes this on teardown, so absence is the normal "no call in
    // progress" state and not an error worth a stack trace.
    throw new BridgeUnavailable(`no handshake at ${path}; the agent is not running`)
  }

  // The agent creates it 0600. Anything wider means something else rewrote it,
  // and the token in it protects buyer transcripts - so this refuses rather
  // than reading a credential that other users on the box could also read.
  const mode = info.mode & 0o777
  if (mode & 0o077) {
    throw new BridgeUnavailable(
      `handshake at ${path} is mode ${mode.toString(8)}, expected 600`,
    )
  }

  const parsed: unknown = JSON.parse(await readFile(path, 'utf-8'))
  if (typeof parsed !== 'object' || parsed === null) {
    throw new BridgeUnavailable('handshake is not an object')
  }
  const { host, port, token } = parsed as Partial<Handshake>
  if (typeof host !== 'string' || !LOOPBACK.has(host)) {
    // The agent refuses to bind anything but loopback; this refuses to dial
    // anything else, so a rewritten handshake cannot point the token at a
    // remote host.
    throw new BridgeUnavailable(`handshake host ${String(host)} is not loopback`)
  }
  if (typeof port !== 'number' || !Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new BridgeUnavailable(`handshake port ${String(port)} is not a port`)
  }
  if (typeof token !== 'string' || token.length < 16) {
    throw new BridgeUnavailable('handshake token is missing or too short')
  }
  return { host, port, token }
}
