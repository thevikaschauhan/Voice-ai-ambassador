// @vitest-environment node
import { chmod, mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { BridgeUnavailable, readHandshake } from '@/lib/bridge/handshake'

/**
 * The handshake carries the credential for the unredacted stream, so every
 * check here is about refusing to use one that has been tampered with.
 *
 * Real files with real permissions, because the mode check is the whole point
 * of two of these and a mocked `stat` would assert nothing about it.
 */

let dir: string
const original = process.env.AMBASSADOR_BRIDGE_HANDSHAKE

async function write(contents: unknown, mode = 0o600): Promise<string> {
  const path = join(dir, 'bridge.json')
  await writeFile(path, typeof contents === 'string' ? contents : JSON.stringify(contents))
  await chmod(path, mode)
  process.env.AMBASSADOR_BRIDGE_HANDSHAKE = path
  return path
}

const GOOD = { host: '127.0.0.1', port: 54321, token: 'a'.repeat(43) }

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'handshake-'))
})

afterEach(() => {
  if (original === undefined) delete process.env.AMBASSADOR_BRIDGE_HANDSHAKE
  else process.env.AMBASSADOR_BRIDGE_HANDSHAKE = original
})

describe('the agent handshake', () => {
  it('reads a well-formed one written by the agent', async () => {
    await write(GOOD)
    await expect(readHandshake()).resolves.toEqual(GOOD)
  })

  it('refuses one that is readable by other users on the box', async () => {
    // The agent writes 0600. Anything wider means something else rewrote it,
    // and the token in it protects buyer transcripts.
    await write(GOOD, 0o644)
    await expect(readHandshake()).rejects.toThrow(/mode 644, expected 600/)
  })

  it('refuses to dial anywhere but loopback', async () => {
    // The agent refuses to BIND anything else; this refuses to CONNECT
    // anywhere else, so a rewritten handshake cannot aim the token off-box.
    await write({ ...GOOD, host: '10.0.0.5' })
    await expect(readHandshake()).rejects.toThrow(/not loopback/)
  })

  it('refuses a token too short to be the agent’s', async () => {
    await write({ ...GOOD, token: 'short' })
    await expect(readHandshake()).rejects.toThrow(/too short/)
  })

  it('refuses a port that is not one', async () => {
    await write({ ...GOOD, port: 99999 })
    await expect(readHandshake()).rejects.toThrow(/is not a port/)
  })

  it('treats a missing file as “no call in progress”, not a crash', async () => {
    // The agent deletes this on teardown, so absence is the normal state
    // between calls.
    process.env.AMBASSADOR_BRIDGE_HANDSHAKE = join(dir, 'never-written.json')
    await expect(readHandshake()).rejects.toBeInstanceOf(BridgeUnavailable)
    await expect(readHandshake()).rejects.toThrow(/the agent is not running/)
  })

  it('reports the bridge off when nothing configured it', async () => {
    delete process.env.AMBASSADOR_BRIDGE_HANDSHAKE
    await expect(readHandshake()).rejects.toThrow(/is not set/)
  })
})
