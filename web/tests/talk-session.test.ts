// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * How a call ENDS, which is a different question from how it starts.
 *
 * The agent hangs up (`task-graceful-goodbye`), the duration cap fires, or the
 * room times out - and in every one of those the browser learns about it the
 * same way: a `Disconnected` event carrying a `DisconnectReason`. These drive
 * that event through the real `startTalking` against a fake `Room`, because the
 * thing under test is what this file decides, not what LiveKit transports.
 *
 * A real browser against a real SFU is `task-hosted-live-smoke`; that is where
 * "the agent's shutdown actually produces ROOM_DELETED" gets settled.
 */

type Handler = (...args: unknown[]) => void

class FakeRoom {
  static last: FakeRoom | null = null
  handlers = new Map<string, Handler[]>()
  connected = false
  disconnectCalls = 0
  micEnabled: boolean | null = null
  textHandlers = new Map<string, Handler>()
  unregistered: string[] = []
  options: Record<string, unknown>

  localParticipant = {
    setMicrophoneEnabled: async (on: boolean) => {
      this.micEnabled = on
    },
  }

  constructor(options: Record<string, unknown> = {}) {
    this.options = options
    FakeRoom.last = this
  }

  on(event: string, handler: Handler) {
    const list = this.handlers.get(event) ?? []
    list.push(handler)
    this.handlers.set(event, list)
    return this
  }

  emit(event: string, ...args: unknown[]) {
    for (const handler of this.handlers.get(event) ?? []) handler(...args)
  }

  async connect() {
    this.connected = true
  }

  async disconnect() {
    this.disconnectCalls += 1
    this.connected = false
  }

  registerTextStreamHandler(topic: string, handler: Handler) {
    this.textHandlers.set(topic, handler)
  }

  unregisterTextStreamHandler(topic: string) {
    this.unregistered.push(topic)
    this.textHandlers.delete(topic)
  }

  /**
   * Deliver one text stream, the way the framework would: an id, the
   * attributes on its header, and the pieces it writes.
   */
  deliver(
    identity: string,
    info: { id: string; attributes?: Record<string, string> },
    pieces: string[],
  ) {
    const handler = this.textHandlers.get('lk.transcription')
    if (handler === undefined) throw new Error('no transcription handler registered')
    const reader = {
      info: { id: info.id, attributes: info.attributes },
      [Symbol.asyncIterator]() {
        let i = 0
        return {
          next: async () =>
            i < pieces.length
              ? { done: false as const, value: pieces[i++] }
              : { done: true as const, value: undefined },
          return: async () => ({ done: true as const, value: undefined }),
        }
      },
    }
    handler(reader, { identity })
    // The handler body is async; let its microtasks run.
    return new Promise((resolve) => setTimeout(resolve, 0))
  }
}

vi.mock('livekit-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('livekit-client')>()
  return { ...actual, Room: FakeRoom }
})

const GRANT = {
  url: 'wss://example.livekit.cloud',
  token: 'a-token',
  room: 'demo-abc',
  identity: 'visitor-1234',
}

interface TalkLineShape {
  id: string
  speaker: string
  text: string
  final: boolean
}

async function connectWith(
  seen: ReturnType<typeof recorder>,
  lines: TalkLineShape[],
) {
  const { startTalking } = await import('@/lib/talk/session')
  const handle = await startTalking(GRANT, {
    onPhase: (phase) => seen.phases.push(phase),
    onEnded: (ending) => seen.endings.push(ending),
    onLine: (line) => lines.push(line as TalkLineShape),
    onTrouble: (reason) => seen.troubles.push(reason),
  })
  const room = FakeRoom.last
  if (room === null) throw new Error('no room was constructed')
  return { handle, room }
}

function recorder() {
  return {
    phases: [] as string[],
    endings: [] as { kind: string; message: string }[],
    troubles: [] as string[],
  }
}

async function connect() {
  const seen = recorder()
  const { startTalking } = await import('@/lib/talk/session')
  const handle = await startTalking(GRANT, {
    onPhase: (phase) => seen.phases.push(phase),
    onEnded: (ending) => seen.endings.push(ending),
    onLine: () => {},
    onTrouble: (reason) => seen.troubles.push(reason),
  })
  const room = FakeRoom.last
  if (room === null) throw new Error('no room was constructed')
  return { handle, room, seen }
}

beforeEach(() => {
  vi.resetModules()
  FakeRoom.last = null
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('a call that the other side ends', () => {
  it('reports a room the agent closed as ENDED, not as a lost connection', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    // What dwight's graceful goodbye produces: the agent shuts its job down,
    // the room closes, and this is the reason the browser is handed.
    room.emit('disconnected', DisconnectReason.ROOM_DELETED)
    expect(seen.endings).toHaveLength(1)
    expect(seen.endings[0].kind).toBe('ended')
    expect(seen.endings[0].message).toMatch(/ended/i)
  })

  it('reports a room that timed out as ended too', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.ROOM_CLOSED)
    expect(seen.endings[0].kind).toBe('ended')
  })

  it('reports the visitor being removed as ended, which is what a duration cap looks like', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.PARTICIPANT_REMOVED)
    expect(seen.endings[0].kind).toBe('ended')
  })

  it('does NOT call a dropped signal an ending', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.SIGNAL_CLOSE)
    // Honest the other way round: telling a visitor the ambassador finished
    // when the network broke is as wrong as the reverse.
    expect(seen.endings[0].kind).toBe('lost')
    expect(seen.endings[0].message).not.toMatch(/ended the call/i)
  })

  it('names a second tab taking over, because "connection lost" would be a lie', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.DUPLICATE_IDENTITY)
    expect(seen.endings[0].kind).toBe('taken_over')
  })

  it('falls to an honest unknown rather than guessing, when there is no reason at all', async () => {
    const { room, seen } = await connect()
    room.emit('disconnected', undefined)
    expect(seen.endings[0].kind).toBe('lost')
  })

  it('releases the microphone and stops listening when the call ends', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.ROOM_DELETED)
    await Promise.resolve()
    // The mic indicator in the browser chrome stays lit until the track is
    // actually stopped, and a visitor whose call has ended should not be left
    // looking at one.
    expect(room.disconnectCalls).toBeGreaterThan(0)
    expect(room.unregistered).toContain('lk.transcription')
    expect(seen.endings).toHaveLength(1)
  })

  it('ends once, however many times the event arrives', async () => {
    const { room, seen } = await connect()
    const { DisconnectReason } = await import('livekit-client')
    room.emit('disconnected', DisconnectReason.ROOM_DELETED)
    room.emit('disconnected', DisconnectReason.ROOM_DELETED)
    expect(seen.endings).toHaveLength(1)
  })

  it('bounds its reconnect attempts rather than spinning on a room that is gone', async () => {
    const { room } = await connect()
    const { DefaultReconnectPolicy } = await import('livekit-client')
    const policy = room.options.reconnectPolicy as InstanceType<typeof DefaultReconnectPolicy>
    expect(policy).toBeDefined()
    // The library default is ten attempts climbing to its maximum delay. On a
    // deleted room every one of them fails, and the visitor watches
    // "Reconnecting" for the whole climb.
    let attempts = 0
    while (policy.nextRetryDelayInMs({ retryCount: attempts, elapsedMs: 0 }) !== null) {
      attempts += 1
      if (attempts > 20) break
    }
    expect(attempts).toBeLessThanOrEqual(5)
    expect(attempts).toBeGreaterThan(0)
  })
})


describe('the transcript rail, given how each side publishes', () => {
  /**
   * The asymmetry is not a guess: `livekit-agents` builds the USER output with
   * `is_delta_stream=False` and the AGENT output with `True`
   * (`room_io/room_io.py`), and the non-delta branch of
   * `_ParticipantStreamTranscriptionOutput` "always create a new writer" per
   * update, writing the whole text each time.
   */
  const SEG = 'lk.segment_id'
  const FINAL = 'lk.transcription_final'

  it('appends the agent’s deltas into one line', async () => {
    const seen = recorder()
    const lines: TalkLineShape[] = []
    const { room } = await connectWith(seen, lines)
    await room.deliver('agent-1', { id: 'TX_1', attributes: { [SEG]: 'SG_a' } }, [
      'Binghatti ',
      'Skyrise ',
      'starts at',
    ])
    const final = lines[lines.length - 1]
    expect(final.text).toBe('Binghatti Skyrise starts at')
    expect(final.speaker).toBe('agent')
  })

  it('does NOT pile up the visitor’s interim streams into separate lines', async () => {
    const seen = recorder()
    const lines: TalkLineShape[] = []
    const { room } = await connectWith(seen, lines)
    // One sentence, three streams, each carrying the whole text so far - which
    // is what a non-delta output produces. Keyed on the stream id this was
    // three growing lines; keyed on the segment it is one.
    await room.deliver('visitor-1234', { id: 'TX_1', attributes: { [SEG]: 'SG_b' } }, ['what'])
    await room.deliver('visitor-1234', { id: 'TX_2', attributes: { [SEG]: 'SG_b' } }, ['what does'])
    await room.deliver(
      'visitor-1234',
      { id: 'TX_3', attributes: { [SEG]: 'SG_b', [FINAL]: 'true' } },
      ['what does the Skyrise cost'],
    )
    const ids = new Set(lines.map((line) => line.id))
    expect(ids.size).toBe(1)
    const final = lines[lines.length - 1]
    expect(final.text).toBe('what does the Skyrise cost')
    expect(final.speaker).toBe('visitor')
    expect(final.final).toBe(true)
  })

  it('reads the final flag as the STRING "true", which is how it arrives', async () => {
    const seen = recorder()
    const lines: TalkLineShape[] = []
    const { room } = await connectWith(seen, lines)
    await room.deliver('agent-1', { id: 'TX_9', attributes: { [SEG]: 'SG_c' } }, ['interim'])
    expect(lines[lines.length - 1].final).toBe(false)
    await room.deliver(
      'agent-1',
      { id: 'TX_10', attributes: { [SEG]: 'SG_c', [FINAL]: 'true' } },
      [' and final'],
    )
    expect(lines[lines.length - 1].final).toBe(true)
    // Appended across the two streams of one segment, not restarted.
    expect(lines[lines.length - 1].text).toBe('interim and final')
  })

  it('falls back to the stream id when a stream carries no segment id', async () => {
    const seen = recorder()
    const lines: TalkLineShape[] = []
    const { room } = await connectWith(seen, lines)
    await room.deliver('agent-1', { id: 'TX_only' }, ['no segment attribute here'])
    expect(lines[lines.length - 1].id).toBe('TX_only')
  })
})
