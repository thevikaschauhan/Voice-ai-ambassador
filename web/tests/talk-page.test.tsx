import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TalkCall } from '@/components/talk-call'
import type { TalkEnding, TalkEvents, TalkLine } from '@/lib/talk/session'

/**
 * What the visitor SEES when a call ends, which is the half of this that a
 * session-level test cannot assert.
 *
 * `startTalking` is stubbed so the test can hand the page an ending on demand -
 * the mapping from `DisconnectReason` to that ending is `talk-session.test.ts`,
 * and this is about the page's answer to it.
 */

let captured: TalkEvents | null = null
const end = vi.fn(async () => {})
const setMuted = vi.fn(async () => {})

vi.mock('@/lib/talk/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/talk/session')>()
  return {
    ...actual,
    startTalking: vi.fn(async (_grant: unknown, events: TalkEvents) => {
      captured = events
      events.onPhase('live')
      return { end, setMuted }
    }),
  }
})

function mintOk(): typeof fetch {
  return (async () =>
    ({
      ok: true,
      status: 200,
      json: async () => ({
        url: 'wss://example.livekit.cloud',
        token: 'a-token',
        room: 'demo-abc',
        identity: 'visitor-1234',
        language: 'en',
      }),
    }) as Response) as typeof fetch
}

async function startACall() {
  await userEvent.type(screen.getByLabelText('Access code'), 'the-code')
  await userEvent.click(screen.getByRole('button', { name: 'Start call' }))
  await waitFor(() => expect(captured).not.toBeNull())
}

function say(line: TalkLine) {
  captured?.onLine(line)
}

function endWith(ending: TalkEnding) {
  captured?.onPhase('ended')
  captured?.onEnded(ending)
}

beforeEach(() => {
  captured = null
  end.mockClear()
  vi.stubGlobal('fetch', mintOk())
  render(<TalkCall names={{ en: 'Jane', ar: '', hi: '' }} />)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the talk page when the call ends', () => {
  it('keeps the farewell on screen and says the ambassador ended it', async () => {
    await startACall()
    say({ id: 's1', speaker: 'agent', text: 'It was good to speak with you. Goodbye.', final: true })
    endWith({ kind: 'ended', message: 'The ambassador ended the call.' })

    // The farewell is the last thing the visitor heard; losing it on the
    // disconnect would make the page disagree with the conversation.
    expect(await screen.findByText(/It was good to speak with you/)).toBeInTheDocument()
    expect(screen.getByText(/The ambassador ended the call/)).toBeInTheDocument()
    expect(screen.getByText('Call ended')).toBeInTheDocument()
  })

  it('offers another call, and going again goes back through the gate', async () => {
    await startACall()
    endWith({ kind: 'ended', message: 'The ambassador ended the call.' })

    const again = await screen.findByRole('button', { name: 'Start another call' })
    // The code is still in the field from the first call, so a visitor whose
    // call ended is one click from another - but the click still POSTs to
    // api/talk, which re-checks the code and the room cap server-side.
    const posted: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (_input: RequestInfo | URL, init?: RequestInit) => {
        posted.push(String(init?.method))
        return {
          ok: true,
          status: 200,
          json: async () => ({
            url: 'wss://x',
            token: 't',
            room: 'demo-2',
            identity: 'visitor-2',
            language: 'en',
          }),
        } as Response
      }) as typeof fetch,
    )
    await userEvent.click(again)
    await waitFor(() => expect(posted).toEqual(['POST']))
  })

  it('does not leave the previous call’s transcript under a new one', async () => {
    await startACall()
    say({ id: 's1', speaker: 'agent', text: 'First call words.', final: true })
    endWith({ kind: 'ended', message: 'The ambassador ended the call.' })
    await screen.findByRole('button', { name: 'Start another call' })
    await userEvent.click(screen.getByRole('button', { name: 'Start another call' }))
    await waitFor(() => expect(screen.queryByText('First call words.')).not.toBeInTheDocument())
  })

  it('hides Mute and End call once the call is over', async () => {
    await startACall()
    expect(screen.getByRole('button', { name: 'End call' })).toBeInTheDocument()
    endWith({ kind: 'ended', message: 'The ambassador ended the call.' })
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'End call' })).not.toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: 'Mute' })).not.toBeInTheDocument()
  })

  it('says something different when the connection dropped, not when it ended', async () => {
    await startACall()
    endWith({ kind: 'lost', message: 'The call stopped unexpectedly.' })
    expect(await screen.findByText(/stopped unexpectedly/)).toBeInTheDocument()
    // The distinction is the point of the card: a dropped call must not be
    // dressed up as a finished conversation.
    expect(screen.queryByText(/ambassador ended the call/)).not.toBeInTheDocument()
  })

  it('lets the visitor end it, and takes its ending from the session rather than assuming one', async () => {
    await startACall()
    await userEvent.click(screen.getByRole('button', { name: 'End call' }))
    expect(end).toHaveBeenCalledTimes(1)
    // The page does not invent 'ended' here: the handle reports it, which is
    // what keeps one source for why a call stopped.
    endWith({ kind: 'ended', message: 'Call ended.' })
    expect(await screen.findByText('Call ended.')).toBeInTheDocument()
  })
})
