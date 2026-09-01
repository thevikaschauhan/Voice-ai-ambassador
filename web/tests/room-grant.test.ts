// @vitest-environment node
import { TokenVerifier } from 'livekit-server-sdk'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The grant IS the security boundary, so it is decoded and inspected rather
 * than trusted to have been written correctly.
 *
 * `listRooms` is stubbed because these are about what the token permits, not
 * about reaching LiveKit. Everything else - signing, claims, expiry - is the
 * real SDK.
 */

const KEY = 'APItestkey'
const SECRET = 'a-secret-long-enough-for-hs256-signing-and-then-some'

const listRooms = vi.fn()
vi.mock('livekit-server-sdk', async (importOriginal) => {
  const actual = await importOriginal<typeof import('livekit-server-sdk')>()
  return {
    ...actual,
    RoomServiceClient: class {
      listRooms = listRooms
    },
  }
})

async function claims() {
  const { mintViewerGrant } = await import('@/lib/livekit/room')
  const grant = await mintViewerGrant()
  const verified = await new TokenVerifier(KEY, SECRET).verify(grant.token)
  return { grant, verified }
}

beforeEach(() => {
  vi.resetModules()
  process.env.LIVEKIT_URL = 'wss://example.livekit.cloud'
  process.env.LIVEKIT_API_KEY = KEY
  process.env.LIVEKIT_API_SECRET = SECRET
  delete process.env.LIVEKIT_ROOM
  listRooms.mockResolvedValue([
    { name: 'stale-room', numParticipants: 0, creationTime: 100n },
    { name: 'the-call', numParticipants: 2, creationTime: 200n },
  ])
})

afterEach(() => {
  delete process.env.LIVEKIT_URL
  delete process.env.LIVEKIT_API_KEY
  delete process.env.LIVEKIT_API_SECRET
  delete process.env.LIVEKIT_ROOM
  vi.clearAllMocks()
})

describe('the viewer grant', () => {
  it('can listen to one named room and nothing else', async () => {
    const { verified } = await claims()
    const video = verified.video

    expect(video?.roomJoin).toBe(true)
    expect(video?.room).toBe('the-call')
    expect(video?.canSubscribe).toBe(true)

    // The surface watches a call; it must not be able to put audio or data in
    // front of a buyer. A token that could is the API secret with extra steps.
    expect(video?.canPublish).toBe(false)
    expect(video?.canPublishData).toBe(false)
    expect(video?.hidden).toBe(true)
  })

  it('expires, so a token off the demo laptop stops working', async () => {
    const { verified } = await claims()
    const seconds = (verified.exp ?? 0) - Math.floor(Date.now() / 1000)
    expect(seconds).toBeGreaterThan(0)
    expect(seconds).toBeLessThanOrEqual(10 * 60)
  })

  it('never returns the signing secret to the caller', async () => {
    const { grant } = await claims()
    // What crosses to the browser is a URL, a room name and a token. The
    // secret that signed it stays on this side, and a serialised grant is the
    // most likely place for it to leak by accident.
    expect(JSON.stringify(grant)).not.toContain(SECRET)
    expect(Object.keys(grant).sort()).toEqual(['room', 'token', 'url'])
  })

  it('prefers a room with people in it over the residue of a finished call', async () => {
    const { verified } = await claims()
    expect(verified.video?.room).toBe('the-call')
  })

  it('honours a pinned room without asking LiveKit', async () => {
    process.env.LIVEKIT_ROOM = 'pinned'
    const { verified } = await claims()
    expect(verified.video?.room).toBe('pinned')
    expect(listRooms).not.toHaveBeenCalled()
  })

  it('refuses rather than guesses when no room is open', async () => {
    listRooms.mockResolvedValue([])
    const { mintViewerGrant } = await import('@/lib/livekit/room')
    await expect(mintViewerGrant()).rejects.toThrow(/no LiveKit room is open/)
  })

  it('reports LiveKit unconfigured rather than minting an unsigned token', async () => {
    delete process.env.LIVEKIT_API_SECRET
    const { mintViewerGrant } = await import('@/lib/livekit/room')
    await expect(mintViewerGrant()).rejects.toThrow(/not configured/)
  })
})
