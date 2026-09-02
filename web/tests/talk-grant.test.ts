// @vitest-environment node
import { TokenVerifier } from 'livekit-server-sdk'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The talk grant IS the security boundary, and it is the one grant in this
 * repository that can publish - so the token is decoded and inspected rather
 * than trusted to have been written correctly, exactly as `room-grant.test.ts`
 * does for the viewer grant.
 *
 * The pair of files is the point: if a future edit ever loosens the viewer
 * grant into a publishing one, one of these two suites fails.
 */

const KEY = 'APItestkey'
const SECRET = 'a-secret-long-enough-for-hs256-signing-and-then-some'

const listRooms = vi.fn()
const createRoom = vi.fn()
vi.mock('livekit-server-sdk', async (importOriginal) => {
  const actual = await importOriginal<typeof import('livekit-server-sdk')>()
  return {
    ...actual,
    RoomServiceClient: class {
      listRooms = listRooms
      createRoom = createRoom
    },
  }
})

async function mint(language: 'en' | 'ar' | 'hi' = 'en') {
  const { mintTalkGrant } = await import('@/lib/livekit/talk')
  const grant = await mintTalkGrant(language)
  const verified = await new TokenVerifier(KEY, SECRET).verify(grant.token)
  return { grant, verified }
}

beforeEach(() => {
  vi.resetModules()
  listRooms.mockReset()
  createRoom.mockReset()
  process.env.LIVEKIT_URL = 'wss://example.livekit.cloud'
  process.env.LIVEKIT_API_KEY = KEY
  process.env.LIVEKIT_API_SECRET = SECRET
  delete process.env.DEMO_MAX_ROOMS
  listRooms.mockResolvedValue([])
  createRoom.mockImplementation(async (options: { name: string }) => ({ name: options.name }))
})

afterEach(() => {
  delete process.env.LIVEKIT_URL
  delete process.env.LIVEKIT_API_KEY
  delete process.env.LIVEKIT_API_SECRET
  delete process.env.DEMO_MAX_ROOMS
})

describe('the talk grant', () => {
  it('can publish, is not hidden, and is scoped to the room it just created', async () => {
    const { grant, verified } = await mint()
    const video = verified.video as Record<string, unknown>
    expect(video.roomJoin).toBe(true)
    expect(video.room).toBe(grant.room)
    expect(video.canPublish).toBe(true)
    expect(video.canSubscribe).toBe(true)
    // A visitor sends audio and nothing else: there is still no command
    // channel to the agent from a browser.
    expect(video.canPublishData).toBe(false)
    // A hidden participant is not a participant - the worker has to see the
    // visitor arrive or there is no call.
    expect(video.hidden).toBeFalsy()
    expect(video.canUpdateOwnMetadata).toBeFalsy()
  })

  it('creates a fresh room per call, so no visitor can be handed another one', async () => {
    const first = await mint()
    const second = await mint()
    expect(first.grant.room).not.toBe(second.grant.room)
    expect(createRoom).toHaveBeenCalledTimes(2)
    // The token is scoped to the room this call created, not to whatever room
    // happened to be newest.
    expect((first.verified.video as Record<string, unknown>).room).toBe(first.grant.room)
  })

  it('carries the language as the versioned metadata string the worker parses', async () => {
    await mint('ar')
    const options = createRoom.mock.calls[0][0] as { metadata: string }
    // A string, because createRoom's metadata is a string. Parsed rather than
    // compared as text, so key order is not part of the contract.
    expect(JSON.parse(options.metadata)).toEqual({ v: 1, language: 'ar' })
  })

  it('closes rooms nobody joined and rooms everybody left, and caps participants at two', async () => {
    await mint()
    const options = createRoom.mock.calls[0][0] as {
      emptyTimeout: number
      departureTimeout: number
      maxParticipants: number
    }
    expect(options.emptyTimeout).toBeGreaterThan(0)
    // The one that reclaims a room when the visitor closes the tab. Without it
    // the concurrency cap counts rooms nobody is in.
    expect(options.departureTimeout).toBeGreaterThan(0)
    expect(options.maxParticipants).toBe(2)
  })

  it('expires, and sooner than a working day', async () => {
    const { verified } = await mint()
    const ttl = (verified.exp ?? 0) - Math.floor(Date.now() / 1000)
    expect(ttl).toBeGreaterThan(60)
    expect(ttl).toBeLessThanOrEqual(20 * 60)
  })

  it('refuses when the demo is already at its cap, counting only demo rooms', async () => {
    process.env.DEMO_MAX_ROOMS = '2'
    listRooms.mockResolvedValue([
      { name: 'demo-one', numParticipants: 1 },
      { name: 'demo-two', numParticipants: 1 },
    ])
    const { mintTalkGrant, DemoAtCapacity } = await import('@/lib/livekit/talk')
    await expect(mintTalkGrant('en')).rejects.toBeInstanceOf(DemoAtCapacity)
    // Counted BEFORE creating, so the cap is a cap rather than a suggestion.
    expect(createRoom).not.toHaveBeenCalled()
  })

  it('does not let somebody else’s rooms lock the demo out', async () => {
    process.env.DEMO_MAX_ROOMS = '1'
    listRooms.mockResolvedValue([{ name: 'the-laptop-call', numParticipants: 2 }])
    const { grant } = await mint()
    expect(grant.room).toMatch(/^demo-/)
    expect(createRoom).toHaveBeenCalledTimes(1)
  })

  it('refuses a language that is not on the offered list', async () => {
    const { isTalkLanguage } = await import('@/lib/livekit/talk')
    expect(isTalkLanguage('en')).toBe(true)
    expect(isTalkLanguage('ar')).toBe(true)
    expect(isTalkLanguage('hi')).toBe(true)
    // This value is serialised into metadata the worker builds a voice and an
    // STT model from, so an unchecked string from a browser would reach the
    // agent's configuration.
    expect(isTalkLanguage('fr')).toBe(false)
    expect(isTalkLanguage('en; drop table')).toBe(false)
    expect(isTalkLanguage(undefined)).toBe(false)
  })
})
